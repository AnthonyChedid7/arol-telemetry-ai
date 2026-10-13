"""Live check: does the Claude planner understand everyday wording?

Asks each question to the Claude planner (live Anthropic API) and to the
deterministic diagnostic planner, and compares both with the expected tool
calls. Planning only: no event data is loaded and no telemetry is sent.

From the repository root (needs ANTHROPIC_API_KEY and an event pool):
    python -m scripts.verify_claude_paraphrases --manifest data/event_pools/<pool>/manifest.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.demo_agent import resolve_manifest
from src.agent.claude_planner import ClaudePlanner
from src.agent.head_diagnostic import HeadDiagnosticPlanner
from src.common import config as config_mod, registry
from src.common.event_pool_source import EventPoolSource

ROOT = Path(__file__).resolve().parents[1]


def day(d, lo="00", hi=None):
    """Window helper: day('02-01') or day('02-01', '06', '12')."""
    start = f"2026-{d}T{lo}:00:00"
    if hi is None:
        nxt = f"2026-02-{int(d[-2:]) + 1:02d}"
        return {"start": start, "end": f"{nxt}T00:00:00"}
    return {"start": start, "end": f"2026-{d}T{hi}:00:00"}


def cases(machine):
    m = {"machine_id": machine}
    return [
        ("how hard did spindle 5 tighten caps on February 1st?",
         [("torque_stats", {"head_id": "H05", **day("02-01")})]),
        ("show me the spread of tightening force for head 12 on Feb 2, 20 bins",
         [("torque_distribution", {"head_id": "H12", "bins": 20, **day("02-02")})]),
        ("is the torque drifting on spindle 3 during Feb 2?",
         [("torque_trend", {"head_id": "H03", **day("02-02")})]),
        ("any weird torque values on head 7 between 8 and 10 am on Feb 1?",
         [("detect_torque_anomalies", {"head_id": "H07", **day("02-01", "08", "10")})]),
        ("compare the torque of heads 1 and 2 on Feb 1",
         [("head_correlation", {"head_a": "H01", "head_b": "H02", **day("02-01")})]),
        ("what's the yield of the machine on February 2nd?",
         [("success_rate", {**m, **day("02-02")})]),
        ("give me the reject rate for each spindle on Feb 1",
         [("success_rate_per_head", {**day("02-01")})]),
        ("which spindle performed worst on Feb 2?",
         [("rank_heads_by_success", {**m, **day("02-02")})]),
        ("is head 9 doing worse than the others on the morning of Feb 1?",
         [("compare_head_success", {"head_id": "H09", **m, **day("02-01", "06", "12")})]),
        ("how many caps per hour did we make on Feb 1 between 6 and 10?",
         [("observed_throughput", {**m, "bucket": "hour", **day("02-01", "06", "10")})]),
        ("success rate hour by hour on Feb 2 from noon to 4pm",
         [("kpi_over_time", {**m, "bucket": "hour", **day("02-02", "12", "16")})]),
        ("was the machine standing still at any point on the afternoon of Feb 1?",
         [("machine_idle", {**m, **day("02-01", "12", "18")})]),
        ("is something off with spindle 4 on Feb 1 between 9 and 10?",
         [("compare_head_success", {"head_id": "H04", **m, **day("02-01", "09", "10")}),
          ("detect_torque_anomalies", {"head_id": "H04", **m, **day("02-01", "09", "10")})]),
        ("average torque of the good closures on head 6, Feb 3 morning",
         [("torque_stats", {"head_id": "H06", "status_filter": "successful",
                            **day("02-03", "06", "12")})]),
        ("is head 4 ok?", None),  # no time reference: must ask, not guess
        ("what's the weather in Turin?", None),  # out of scope
    ]


def normalise(calls, machine):
    """Optional machine_id equal to the only pool machine changes nothing."""
    out = []
    for name, args in calls or []:
        args = dict(args)
        spec = registry.get(name)
        if (args.get("machine_id") == machine and spec is not None
                and "machine_id" not in spec.required):
            args.pop("machine_id")
        out.append((name, args))
    return sorted(out, key=lambda c: c[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Event-pool manifest (optional if exactly one exists)")
    args = parser.parse_args(argv)
    path, record = resolve_manifest(args.manifest, ROOT)
    machine = record["summary"]["machine_id"]

    cfg = config_mod.load()
    cfg.setdefault("data", {})["person_a"] = {"demo": str(path)}
    summary = EventPoolSource(cfg, repo_root=ROOT).describe_pool("demo")
    context = {"pool": "demo", "selected_machine": machine, "pool_summary": summary}

    claude = ClaudePlanner(dict(cfg, agent=dict(cfg.get("agent", {}),
                                                claude=dict(cfg["agent"].get("claude", {}),
                                                            fallback_to_rules=False))))
    rules = HeadDiagnosticPlanner()
    rows, totals = [], {"input_tokens": 0, "output_tokens": 0,
                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for question, expected in cases(machine):
        plan = claude.plan(question, context)
        rule_plan = rules.plan(question, context)
        for key in totals:
            totals[key] += (claude.last_usage or {}).get(key) or 0
        if expected is None:
            claude_ok = plan.ambiguous and not plan.calls
            rules_ok = rule_plan.ambiguous and not rule_plan.calls
        else:
            want = normalise(expected, machine)
            claude_ok = normalise(plan.calls, machine) == want
            rules_ok = normalise(rule_plan.calls, machine) == want
        rows.append({
            "question": question,
            "expected": expected,
            "claude_calls": plan.calls,
            "claude_interpretation": plan.interpretation,
            "claude_clarification": plan.clarification,
            "claude_correct": claude_ok,
            "rules_calls": rule_plan.calls,
            "rules_correct": rules_ok,
        })
        mark = "OK  " if claude_ok else "MISS"
        print(f"[{mark}] rules={'ok' if rules_ok else '--'} | {question}")
        if not claude_ok:
            print(f"        claude: {plan.calls or plan.clarification}")

    # Opus 5.5 list prices per million tokens; cache writes 1.25x, reads 0.05x input.
    cost = (totals["input_tokens"] * 4 + totals["cache_creation_input_tokens"] * 5
            + totals["cache_read_input_tokens"] * 0.2 + totals["output_tokens"] * 20) / 1e6
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": claude.model, "effort": claude.effort,
        "manifest": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
        "pool_summary": summary,
        "claude_correct": sum(r["claude_correct"] for r in rows),
        "rules_correct": sum(r["rules_correct"] for r in rows),
        "n_questions": len(rows),
        "token_usage": totals, "estimated_cost_usd": round(cost, 4),
        "limits": [
            "Planning only: tool choice and arguments are compared; no event data is loaded.",
            "Expected calls were written by hand for this pool; machine_id is ignored where it is "
            "optional and equals the pool's only machine.",
            "A small hand-written question set, not a representative sample of operator language.",
        ],
        "cases": rows,
    }
    out = ROOT / "benchmarks" / "integration" / (
        "claude_paraphrases_" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nClaude {result['claude_correct']}/{len(rows)} | rules {result['rules_correct']}/{len(rows)} "
          f"| ~${result['estimated_cost_usd']} | {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
