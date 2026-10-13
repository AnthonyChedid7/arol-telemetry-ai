"""Execute planned tools against a selected event pool.

Failures remain visible. Requested filters are never removed on retry.
"""
from __future__ import annotations

from pathlib import Path

from ..common import config as config_mod
from ..common import datasource, registry
from ..common.event_pool_source import ScopeTooLarge
from ..common.runtime import dispatch_tool
from ..analytics import registered_torque, registered_analytics, registered_kpi, registered_head_kpi, registered_temporal_kpi, registered_idle  # noqa: F401
from . import report as report_mod
from .planner import get_planner
from .trace import Trace

class Orchestrator:
    def __init__(self, cfg=None, source=None, planner=None):
        self.cfg = cfg if cfg is not None else config_mod.load()
        self.source = source or datasource.get_source(self.cfg)
        self.planner = planner or get_planner(self.cfg)
        self.min_n = config_mod.get(self.cfg, "analytics.min_n", 30)
        self.max_steps = config_mod.get(self.cfg, "agent.max_steps", 8)

    # -- the loop ----------------------------------------------------------

    def answer(self, query: str, pool: str | None = None,
               selected_machine: str | None = None) -> dict:
        trace = Trace(query, planner=self.planner.name)

        if pool is None:
            available = self.source.list_pools()
            if len(available) != 1:
                message = (
                    "Select one event pool explicitly. Configured pools: "
                    + (", ".join(available) or "none")
                )
                trace.note("stopped for explicit pool selection")
                return {
                    "status": "needs_clarification",
                    "query": query,
                    "message": message,
                    "markdown": message,
                    "results": [],
                    "plan": None,
                    "trace": trace,
                }
            pool = available[0]
        context = {"pool": pool, "selected_machine": selected_machine}
        describe_pool = getattr(self.source, "describe_pool", None)
        if callable(describe_pool):
            try:
                context["pool_summary"] = describe_pool(pool)
            except Exception as exc:  # the summary is a planning hint, never evidence
                trace.note(f"pool summary unavailable for planning ({type(exc).__name__})")
        plan = self.planner.plan(query, context)

        # A planner that quietly fell back must say so: the report header
        # names the planner, and naming the one we asked for rather than the
        # one that answered would misrepresent how the answer was produced.
        fallback_error = getattr(self.planner, "last_error", None)
        if fallback_error:
            trace.planner = f"{self.planner.name}->rules (fallback)"
            trace.note(f"{self.planner.name} planner unavailable "
                       f"({fallback_error}); routed by keyword rules instead")

        for dropped in getattr(self.planner, "dropped_args", []) or []:
            trace.note(f"planner proposed {dropped}; dropped before dispatch")

        trace.step("plan", goal=plan.goal, rationale=plan.rationale,
                   calls=[c[0] for c in plan.calls], filters=plan.filters,
                   ambiguous=plan.ambiguous)
        if plan.interpretation:
            trace.note(f"interpreted as: {plan.interpretation}")
        usage = getattr(self.planner, "last_usage", None)
        if usage:
            trace.note("planner tokens: " + ", ".join(
                f"{key}={value}" for key, value in usage.items() if value is not None))

        if plan.ambiguous:
            trace.note("stopped for clarification")
            return {
                "status": "needs_clarification",
                "query": query,
                "message": plan.clarification,
                "markdown": f"# Clarification needed\n\n{plan.clarification}\n",
                "results": [],
                "plan": plan,
                "trace": trace,
            }

        if (type(self.max_steps) is not int or self.max_steps < 1
                or not plan.calls or len(plan.calls) > self.max_steps):
            message = "The complete analysis plan exceeds the configured tool-call budget or is empty. No analysis was run."
            trace.note("stopped before loading; the plan was not truncated")
            return {
                "status": "needs_clarification", "query": query, "message": message,
                "markdown": f"# Clarification needed\n\n{message}\n",
                "results": [], "plan": plan, "trace": trace,
            }

        unavailable = [name for name, _ in plan.calls if registry.get(name) is None]
        if unavailable:
            message = "Requested analyses are not integrated: " + ", ".join(unavailable) + ". No partial report was produced."
            trace.note("stopped before loading; unavailable tools in complete plan")
            return {
                "status": "needs_clarification", "query": query, "message": message,
                "markdown": message, "results": [], "plan": plan, "trace": trace,
            }

        try:
            if callable(getattr(self.source, "load_for_plan", None)):
                events, meta = self.source.load_for_plan(pool, plan.calls)
            else:
                events = self.source.load_pool(pool)
                meta = self.source.pool_meta(pool)
            trace.step("load_pool", pool=pool, n_events=int(len(events)),
                       source=self.source.name,
                       pool_total_events=meta.get("pool_total_events"),
                       selection_parameters=meta.get("selection_parameters"),
                       comparison_focus_head=meta.get("comparison_focus_head"),
                       comparison_population=meta.get("comparison_population"))
        except ScopeTooLarge as exc:
            message = str(exc)
            trace.step("load_pool", pool=pool, ok=False, error=message)
            trace.note("stopped for narrower scope before event materialization or dispatch")
            return {
                "status": "needs_clarification", "query": query,
                "message": message, "markdown": f"# Clarification needed\n\n{message}\n",
                "results": [], "plan": plan, "trace": trace,
            }
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            trace.step("load_pool", pool=pool, ok=False, error=message)
            trace.note("degraded: no data could be loaded")
            return {
                "status": "degraded",
                "query": query,
                "message": f"Could not load pool {pool!r}: {message}",
                "markdown": (f"# Report unavailable\n\nCould not load pool "
                             f"`{pool}`.\n\n> {message}\n"),
                "results": [],
                "plan": plan,
                "trace": trace,
            }

        results = []
        for tool_name, params in plan.calls:
            result = dispatch_tool(
                tool_name, events, config=self.cfg, arguments=params
            )
            trace.tool_call(tool_name, params, result)

            if not result["ok"]:
                trace.note(
                    f"{tool_name} failed; requested filters were preserved"
                )
            results.append((tool_name, result))

        ok = [r for _, r in results if r["ok"]]
        if not ok:
            trace.note("degraded: every tool returned an error")
            status = "degraded"
        elif len(ok) < len(results):
            trace.note("partial: some tools returned no result")
            status = "partial"
        else:
            status = "ok"
        trace.step("validate", status=status, n_ok=len(ok), n_total=len(results))

        markdown = report_mod.assemble(query, plan, results, meta, trace,
                                       min_n=self.min_n)
        if status == "ok" and callable(getattr(self.planner, "explain", None)):
            explanation = self.planner.explain(query, results, markdown)
            if explanation:
                trace.step("explain", method=self.planner.name, ok=True)
                markdown = explanation + "\n" + markdown
        trace.step("assemble", chars=len(markdown))

        return {
            "status": status,
            "query": query,
            "message": "",
            "markdown": markdown,
            "results": results,
            "plan": plan,
            "trace": trace,
            "pool_meta": meta,
        }

    # -- delivery ----------------------------------------------------------

    def deliver(self, answer: dict, *, save=True, formats=None) -> dict:
        """Write the report, its figures and its trace. Returns the paths.

        Markdown and the trace always. Plots and HTML/PDF are opt-in per call
        or via agent.export in config, because rendering figures costs a
        second or so and the terminal user usually does not want them.
        """
        paths = {}
        if not save:
            return paths

        report_dir = Path(config_mod.get(self.cfg, "agent.report_dir", "reports"))
        wanted = formats if formats is not None else             (config_mod.get(self.cfg, "agent.export") or ["markdown"])

        md_path = report_mod.save(answer["markdown"], report_dir)
        paths["report"] = str(md_path)
        paths["trace"] = str(answer["trace"].save(
            config_mod.get(self.cfg, "agent.trace_dir", "logs")))

        figures = []
        if "plots" in wanted or "html" in wanted or "pdf" in wanted:
            from ..interface import plots as plots_mod
            figures = plots_mod.render(answer["results"],
                                       report_dir / md_path.stem)
            paths["figures"] = [str(p) for _, p in figures]
            if figures:
                additions = ["", "## Figures", ""]
                additions.extend(f"![{name}]({p.relative_to(report_dir).as_posix()})"
                                 for name, p in figures)
                md_path.write_text(answer["markdown"].rstrip() + "\n" +
                                   "\n".join(additions) + "\n", encoding="utf-8")

        if "html" in wanted or "pdf" in wanted:
            from ..interface import export as export_mod
            html_path = export_mod.save_html(
                answer["markdown"], report_dir / md_path.stem,
                figures=figures, stem=md_path.stem)
            paths["html"] = str(html_path)
            if "pdf" in wanted:
                pdf = export_mod.save_pdf(html_path)
                if pdf is not None:
                    paths["pdf"] = str(pdf)
        return paths
