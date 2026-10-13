"""Planners: decide which tools to call, with which parameters.

Four planner modes behind one interface:

  RulePlanner  deterministic keyword routing. No network, no API key, no model.
  LLMPlanner   strict single-torque verification for an experimental model path.
  HeadDiagnosticPlanner  scoped head checks from registered tool evidence.
  ClaudePlanner  everyday wording -> any registered tool via the Anthropic API
                 (claude_planner.py); validated, with rules as the fallback.

The rule planner is built first on purpose. It proves the whole dispatch and
report path end to end without a model in the way, it is the determinism
baseline the LLM planner is checked against, and it is the fallback if the API
is unreachable during the demo. `agent.planner` in config.yaml selects one.

No planner ever computes a number. It chooses tools and arguments; the
tools do the arithmetic. That separation is the project's central claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common import registry, timeutils
from ..analytics import registered_torque, registered_analytics, registered_kpi, registered_head_kpi, registered_temporal_kpi, registered_idle  # noqa: F401


@dataclass
class Plan:
    """What the planner decided to do."""
    goal: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    ambiguous: bool = False
    clarification: str = ""
    rationale: str = ""
    interpretation: str = ""


class Planner:
    name = "abstract"

    def plan(self, query: str, context: dict) -> Plan:
        raise NotImplementedError


def _plan_torque_stats(query):
    """Compatibility hook for complete deterministic torque routing."""
    from .torque_routing import parse_torque_request

    parsed = parse_torque_request(query)
    return None if parsed is None else Plan(**parsed)



class RulePlanner(Planner):
    """Deterministic keyword routing. Same query in, same plan out, always."""

    name = "rules"

    def plan(self, query: str, context: dict) -> Plan:
        from .head_health_routing import parse_head_health_request
        health_plan = parse_head_health_request(query)
        if health_plan is not None:
            return Plan(**health_plan)

        from .idle_routing import parse_idle_request
        idle_plan = parse_idle_request(query)
        if idle_plan is not None:
            return Plan(**idle_plan)
        torque_plan = _plan_torque_stats(query)
        if torque_plan is not None:
            return torque_plan

        from .head_kpi_routing import parse_head_kpi_request
        head_plan = parse_head_kpi_request(query)
        if head_plan is not None:
            return Plan(**head_plan)

        from .temporal_kpi_routing import parse_temporal_kpi_request
        temporal_plan = parse_temporal_kpi_request(query)
        if temporal_plan is not None:
            return Plan(**temporal_plan)

        from .kpi_routing import parse_kpi_request
        kpi_plan = parse_kpi_request(query)
        if kpi_plan is not None:
            return Plan(**kpi_plan)

        return Plan(
            goal="Clarify the requested analysis and scope.",
            ambiguous=True,
            clarification=(
                "I could not identify a supported analysis. Ask for scoped torque "
                "analysis, success rate, head comparison, observed throughput, or "
                "machine idle. For a head check, try: 'Is anything wrong with "
                "head 5 for machine M1 from 2026-02-01T00:00:00 until "
                "2026-02-01T12:00:00'."
            ),
            rationale="no complete supported request matched; no tools called",
        )


SYSTEM_PROMPT = 'You propose exactly one tool call for the supplied torque question. Use only the provided tools. Never compute or invent findings. Preserve every explicit head, machine, status and time filter exactly. Use canonical parameter names from the schema, not aliases. Head 5 means H05. Machine identifiers are case-sensitive. A date introduced by on means midnight that day inclusive until midnight the next day exclusive; use naive ISO timestamps with seconds. Do not add filters, default parameters, nulls, empty strings, or inferred scope. Omit parameters the user did not request. Successful closures means status_filter="successful"; explicit status 0 means integer 0. All closures means omit status_filter. Head comparison requires head_a/head_b and permits machine/time scope only. Configured windows, thresholds, sigma and limits are application-owned and cannot be arguments. If unable to produce the exact requested analysis and scope, return no tool calls. Your free-form response is not used as a report finding.'


def _to_ollama_tools(specs: list[dict]) -> list[dict]:
    """registry's Anthropic-style specs -> the OpenAI/Ollama function shape."""
    return [
        {"type": "function",
         "function": {"name": s["name"],
                      "description": s["description"],
                      "parameters": s["input_schema"]}}
        for s in specs
    ]


class LLMPlanner(Planner):
    """Validate model proposals against the verified torque request grammar.

    Unknown language needs clarification; the LLM does not expand grammar
    coverage in this checkpoint. Model prose is not report evidence.
    """

    name = "llm"

    def __init__(self, cfg):
        self.cfg = cfg
        agent = cfg.get("agent", {}) or {}
        llm = agent.get("llm", {}) or {}
        self.host = llm.get("host", "http://localhost:11434").rstrip("/")
        self.model = llm.get("model", agent.get("model", "llama3.2:3b"))
        self.temperature = float(llm.get("temperature", agent.get("temperature", 0.0)))
        self.seed = llm.get("seed", 42)
        self.timeout = float(llm.get("timeout_seconds", 60))
        self.fallback = bool(llm.get("fallback_to_rules", True))
        self._rules = RulePlanner()
        self.last_error: str | None = None
        self.dropped_args: list[str] = []
        self.validation_error: str | None = None

    def _chat(self, query: str, tools: list[dict]) -> dict:
        """One non-streaming call to Ollama. Overridden in tests."""
        import json
        import urllib.request

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": query}],
            "tools": tools,
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed},
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def available(self) -> bool:
        """Require the configured model tag, allowing Ollama's implicit :latest."""
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as response:
                names = {item.get("name", "") for item in json.loads(response.read()).get("models", [])}
            expected = self.model if ":" in self.model else self.model + ":latest"
            return expected in names or self.model in names
        except Exception:
            return False


    def plan(self, query: str, context: dict) -> Plan:
        from .torque_routing import parse_torque_request
        from .llm_validation import VERIFIED_TOOLS

        self.last_error = None
        self.validation_error = None
        self.dropped_args = []
        requested = parse_torque_request(query)
        if requested is None:
            return self._reject("This LLM checkpoint supports the five verified torque analyses only.")
        expected = Plan(**requested)
        if expected.ambiguous:
            expected.rationale = "LLM scope gate requested clarification before contacting the model"
            return expected

        if len(expected.calls) != 1:
            return Plan(
                goal="Select deterministic routing for the requested combined analyses.",
                ambiguous=True,
                clarification=("This combined question is supported by deterministic rules. "
                               "Select the rules planner or ask each analysis separately; "
                               "live LLM validation currently accepts one tool per request."),
                rationale="combined analysis stopped before contacting the model",
            )

        specs = [spec for spec in registry.get_tool_specs() if spec["name"] in VERIFIED_TOOLS]
        # Only transport failure may fall back. Invalid proposals never trigger a
        # different analysis or a second attempt with fewer filters.
        try:
            reply = self._chat(query, _to_ollama_tools(specs))
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if not self.fallback:
                raise
            expected.rationale = (f"LLM transport failed ({self.last_error}); fell back to rules "
                                  "with the complete verified request scope")
            return expected

        try:
            if not isinstance(reply, dict):
                raise ValueError("Model reply must be an object")
            calls = self._extract_calls(reply.get("message"))
            if calls != expected.calls:
                raise ValueError("Model tool or arguments differ from the verified requested analysis and scope")
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return self._reject(str(exc))
        # Free-form model content is not report evidence and cannot become a goal
        # containing unverified findings or causal claims.
        return Plan(goal=expected.goal, calls=calls, filters=dict(expected.filters),
                    rationale=f"{self.model} proposed one tool; analysis and complete scope verified")


    def _reject(self, reason):
        self.validation_error = reason
        return Plan(
            goal="Clarify the requested analysis without changing its scope.",
            ambiguous=True,
            clarification=("The LLM proposal could not be verified; no analysis was run. "
                           "Use a supported torque question with explicit head, machine and time scope, "
                           "or select deterministic rules. Reason: " + reason),
            rationale="LLM proposal rejected: " + reason,
        )


    def _extract_calls(self, message):
        from .llm_validation import strict_calls
        return strict_calls(message, registry.get)



def get_planner(cfg) -> Planner:
    kind = (cfg.get("agent", {}).get("planner") or "rules").lower()
    if kind == "rules":
        return RulePlanner()
    if kind == "diagnostic":
        from .head_diagnostic import HeadDiagnosticPlanner
        return HeadDiagnosticPlanner()
    if kind == "llm":
        return LLMPlanner(cfg)
    if kind == "claude":
        from .claude_planner import ClaudePlanner
        return ClaudePlanner(cfg)
    raise ValueError(f"agent.planner must be 'rules', 'diagnostic', 'llm' or 'claude', got {kind!r}")
