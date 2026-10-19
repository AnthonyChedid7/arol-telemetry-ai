"""The direct answer: exactly what the question asked for, before any detail.

The report sections describe every tool result in full. Engineers asking
"how many rejects did head 22 have on Feb 3?" want "Head H22 had 3 rejects on
2026-02-03", and "plot ..." wants the plot. This module produces that answer:

  * a sentence (or two) that answers the question in plain words, and
  * the form the question asked for: none, the chart, a per-head table, a
    head x day table, a per-day table or an interval table.

Built-in sentences are written from the tool results here. With the Claude
planner, Claude may write the sentence and choose the form instead (see
ClaudePlanner.compose_answer); every number it writes is checked against the
tool results first, and any number not found there rejects its answer in
favour of the built-in one. The tools still compute every number.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re

ARTIFACTS = ("none", "chart", "head_table", "head_day_table", "day_table", "interval_table")
METRICS = ("success_rate", "failure_rate", "rejects", "closures")

# Which forms each tool can produce.
TOOL_ARTIFACTS = {
    "success_rate_per_head": ("none", "chart", "head_table"),
    "rank_heads_by_success": ("none", "chart", "head_table"),
    "compare_head_success": ("none", "chart", "head_table"),
    "head_success_by_day": ("none", "chart", "head_day_table", "day_table"),
    "kpi_over_time": ("none", "chart", "interval_table"),
    "observed_throughput": ("none", "chart", "interval_table"),
    "torque_distribution": ("none", "chart"),
    "torque_trend": ("none", "chart"),
    "machine_idle": ("none", "chart"),
}

_CHART = re.compile(r"\b(?:plot|plots|chart|charts|graph|graphs|visuali[sz]e|visual|draw|heat ?map|histogram|diagram)\b", re.I)
_EACH_HEAD = re.compile(r"\b(?:(?:each|every|all|all the|per|by)\s+(?:of the\s+)?heads?|heads? by heads?|for (?:each|every) head)\b", re.I)
_EACH_DAY = re.compile(r"\b(?:(?:each|every|per|by)\s+day|daily|day by day)\b", re.I)
_COUNT = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b", re.I)
_TABLE = re.compile(r"\b(?:table|list|breakdown|all the numbers|every number)\b", re.I)


def _cap(text):
    """Upper-case the first letter only (str.capitalize would lower-case H22)."""
    return text[:1].upper() + text[1:]


def _pct(value, digits=2):
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _ok(results):
    return [(name, reply["result"]) for name, reply in results if reply.get("ok")]


# --- which form the question asks for ----------------------------------------

def choose_artifact(query, plan, results):
    """(artifact, metric) the question asks for, from its wording."""
    names = [name for name, _ in _ok(results)]
    if not names:
        return "none", "success_rate"
    name = names[-1] if names[-1] in TOOL_ARTIFACTS else names[0]
    allowed = TOOL_ARTIFACTS.get(name, ("none",))
    text = query or ""
    focus = getattr(plan, "focus", "")
    metric = ("rejects" if _COUNT.search(text) and focus == "failure" else
              "failure_rate" if focus == "failure" else "success_rate")
    if _CHART.search(text) and "chart" in allowed:
        return "chart", metric
    if name == "head_success_by_day":
        superlative = re.search(r"\b(?:lowest|highest|best|worst|least|most|fewest|minimum|maximum|top)\b", text, re.I)
        if _EACH_HEAD.search(text) or (not superlative and re.search(r"\bperformance|\brates?\b", text, re.I)):
            return "head_day_table", metric
        return "day_table", metric
    if "head_table" in allowed and (_EACH_HEAD.search(text) or _TABLE.search(text)):
        return "head_table", metric
    if "interval_table" in allowed and (_EACH_DAY.search(text) or re.search(r"\b(?:hour|hourly)\b", text, re.I)
                                        or _TABLE.search(text)):
        return "interval_table", metric
    return "none", metric


# --- tables -------------------------------------------------------------------

def _cell(row, metric):
    if metric == "failure_rate":
        return _pct(row.get("reject_rate_cap_present"), 4)
    if metric == "rejects":
        return f"{row.get('n_reject_cap_present', 0):,}"
    if metric == "closures":
        return f"{row.get('n_cap_present', 0):,}"
    return _pct(row.get("success_rate_cap_present"), 4)


METRIC_NAMES = {"success_rate": "success rate", "failure_rate": "failure (error) rate",
                "rejects": "rejects", "closures": "closures with a cap"}


def render_table(artifact, metric, results, view=("", "")):
    """Markdown table for the chosen form, or '' when the form is not a table.

    view = (focus, extreme) selects the per-day pick for day_table.
    """
    found = dict(_ok(results))
    if artifact == "head_day_table" and "head_success_by_day" in found:
        days = found["head_success_by_day"]["days"]
        heads = sorted({h["head_id"] for d in days for h in d["heads"]})
        by_day = [{h["head_id"]: h for h in d["heads"]} for d in days]
        header = ["Head"] + [d["date"] + (" (partial)" if d["partial_day"] else "") for d in days]
        lines = ["| " + " | ".join(header) + " |", "|---|" + "---:|" * len(days)]
        for head in heads:
            cells = [_cell(day[head], metric) if head in day else "-" for day in by_day]
            lines.append(f"| {head} | " + " | ".join(cells) + " |")
        lines.append("| **All heads** | " + " | ".join(_cell(d, metric) for d in days) + " |")
        return "\n".join(lines)
    if artifact == "head_table":
        rows = (found.get("success_rate_per_head", {}).get("per_head")
                or found.get("rank_heads_by_success", {}).get("ranked_heads")
                or found.get("compare_head_success", {}).get("ranked_heads") or [])
        rows = sorted(rows, key=lambda r: (r["machine_id"], r["head_id"]))
        failure = metric in ("failure_rate", "rejects")
        lines = [("| Head | Failure rate | Rejects | Closures with a cap |" if failure else
                  "| Head | Success rate | Successful | Closures with a cap |"), "|---|---:|---:|---:|"]
        for r in rows:
            rate = r.get("reject_rate_cap_present" if failure else "success_rate_cap_present")
            count = r.get("n_reject_cap_present" if failure else "n_success_cap_present", 0)
            lines.append(f"| {r['head_id']} | {_pct(rate, 4)} | {count:,} | {r.get('n_cap_present', 0):,} |")
        return "\n".join(lines) if rows else ""
    if artifact == "day_table" and "head_success_by_day" in found:
        from ..analytics.registered_daily_kpi import daily_view
        asked, _ = daily_view(*view)
        rate_word = "failure rate" if asked["rate"].startswith("reject") else "success rate"
        lines = [f"| Day | {asked['label']} rate | Head(s) | All heads ({rate_word}) |", "|---|---:|---|---:|"]
        for d in found["head_success_by_day"]["days"]:
            head, tied = d[f"{asked['key']}_head"], d[f"{asked['key']}_tied_heads"]
            if head is None:
                lines.append(f"| {d['date']} | n/a | not ranked | {_pct(d.get(asked['rate']), 4)} |")
                continue
            who = ("all heads" if len(tied) == d["n_eligible_heads"] else
                   tied[0] if len(tied) == 1 else f"{len(tied)} heads: {_names(tied, 6)}")
            lines.append(f"| {d['date']}{' (partial)' if d['partial_day'] else ''} | {_pct(head[asked['rate']], 4)} | "
                         f"{who} | {_pct(d.get(asked['rate']), 4)} |")
        return "\n".join(lines)
    if artifact == "interval_table":
        result = found.get("observed_throughput") or found.get("kpi_over_time")
        if not result:
            return ""
        lines = ["| Interval start | Observed cycles | Closures with a cap | Success rate | Failure rate |",
                 "|---|---:|---:|---:|---:|"]
        for r in result["by_bucket"][:200]:
            lines.append(f"| {r['bucket_start']} | {r['n_observed']:,} | {r.get('n_cap_present', 0):,} | "
                         f"{_pct(r.get('success_rate_cap_present'), 4)} | {_pct(r.get('reject_rate_cap_present'), 4)} |")
        return "\n".join(lines)
    return ""


# --- built-in sentences --------------------------------------------------------

def _when(params, meta):
    start, end = params.get("start"), params.get("end")
    if not start and not end:
        lo, hi = (meta or {}).get("ts_min"), (meta or {}).get("ts_max")
        return (f"over all the data ({lo[:16].replace('T', ' ')} to {hi[:16].replace('T', ' ')})"
                if lo and hi else "over all the data")
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if a.time() == b.time() == datetime.min.time() and b - a == timedelta(days=1):
            return f"on {a.date().isoformat()}"
    except (TypeError, ValueError):
        pass
    return f"from {start} to {end}"


def _who(params):
    return f"head {params['head_id']}" if isinstance(params.get("head_id"), str) else "the machine (all heads)"


def _extremes(rows, key, highest):
    rows = [r for r in rows if r.get(key) is not None and r.get("eligible_for_ranking", not r.get("below_min_cap_present_n"))]
    if not rows:
        return None, []
    target = (max if highest else min)(r[key] for r in rows)
    tied = sorted(r["head_id"] for r in rows if r[key] == target)
    return target, tied


def _names(heads, limit=5):
    return ", ".join(heads[:limit]) + (f" and {len(heads) - limit} more" if len(heads) > limit else "")


def _head_sentence(rows, plan, query, when):
    focus, extreme = getattr(plan, "focus", ""), getattr(plan, "extreme", "") or "worst"
    key = "reject_rate_cap_present" if focus == "failure" else "success_rate_cap_present"
    highest = (extreme == "worst") == (focus == "failure")
    value, tied = _extremes(rows, key, highest)
    if value is None:
        return "No head had enough closures to compare."
    word = ("highest" if highest else "lowest") + (" failure (error) rate" if focus == "failure" else " success rate")
    head = (f"head {tied[0]}" if len(tied) == 1 else f"{len(tied)} heads ({_names(tied)})")
    verb = "had" if len(tied) == 1 else "shared"
    return f"{_cap(when)}, {head} {verb} the {word}: {_pct(value, 4)}."


def deterministic_sentence(query, plan, results, meta):
    ok = _ok(results)
    if not ok:
        return "No analysis produced a result, so the question cannot be answered; see Details."
    calls = dict(plan.calls) if plan is not None else {}
    names = [name for name, _ in ok]
    found = dict(ok)
    params = calls.get(names[0], {})
    when, who = _when(params, meta), _who(params)
    focus = getattr(plan, "focus", "")
    artifact, metric = choose_artifact(query, plan, results)
    if {"compare_head_success", "detect_torque_anomalies"} <= set(names):
        c, a = found["compare_head_success"], found["detect_torque_anomalies"]
        focus_row = c.get("focus") or {}
        return (f"{_cap(when)}, head {c['focus_head_id']} had a success rate of "
                f"{_pct(focus_row.get('success_rate_cap_present'), 4)} against a median of "
                f"{_pct(c.get('peer_median_success_rate'), 4)} for the other heads, and "
                f"{a['anomaly_count']:,} of its {a['sample_size']:,} torque readings were flagged.")
    name, r = ok[-1] if names[-1] in TOOL_ARTIFACTS else ok[0]
    if name in ("success_rate", "success_rate_per_head") and names[0] == "success_rate" and "success_rate" in found:
        name, r = "success_rate", found["success_rate"]
    if name == "success_rate":
        o = r["overall"]
        if _COUNT.search(query or "") and focus == "failure":
            return (f"{_cap(who)} had {o['n_reject_cap_present']:,} rejects {when} "
                    f"out of {o['n_cap_present']:,} closures with a cap (failure rate {_pct(o['reject_rate_cap_present'], 4)}).")
        if focus == "failure":
            return (f"{_cap(when)}, the failure (error) rate of {who} was {_pct(o['reject_rate_cap_present'], 4)}: "
                    f"{o['n_reject_cap_present']:,} rejects out of {o['n_cap_present']:,} closures with a cap.")
        return (f"{_cap(when)}, the success rate of {who} was {_pct(o['success_rate_cap_present'], 4)}: "
                f"{o['n_success_cap_present']:,} successful closures out of {o['n_cap_present']:,} with a cap.")
    if name in ("success_rate_per_head", "rank_heads_by_success"):
        rows = r.get("per_head") or r.get("ranked_heads") or []
        if artifact in ("head_table", "chart") and not re.search(r"\b(?:lowest|highest|best|worst|which)\b", query or "", re.I):
            return f"Here is the {METRIC_NAMES[metric]} of each of the {len(rows)} heads {when}."
        return _head_sentence(rows, plan, query, when)
    if name == "compare_head_success":
        f = r.get("focus") or {}
        if not r.get("comparison_available"):
            return f"Head {r['focus_head_id']} could not be compared {when}: {str(r.get('comparison_reason')).replace('_', ' ')}."
        diff = r["difference_from_peer_median_pp"]
        return (f"{_cap(when)}, head {r['focus_head_id']} had a success rate of {_pct(f['success_rate_cap_present'], 4)} "
                f"({f['n_success_cap_present']:,}/{f['n_cap_present']:,}), {abs(diff):.2f} percentage points "
                f"{'below' if diff < 0 else 'above'} the median of the other {r['n_eligible_peers']} heads "
                f"({_pct(r['peer_median_success_rate'], 4)}).")
    if name == "head_success_by_day":
        days = r["days"]
        if artifact in ("head_day_table", "chart"):
            heads = sorted({h["head_id"] for d in days for h in d["heads"]})
            return (f"Here is the {METRIC_NAMES[metric]} of each of the {len(heads)} heads on each of the "
                    f"{len(days)} days.")
        from ..analytics.registered_daily_kpi import daily_view
        asked, _ = daily_view(focus, getattr(plan, "extreme", ""))
        ranked = [d for d in days if d[f"{asked['key']}_head"]]
        values = {d[f"{asked['key']}_head"][asked["rate"]] for d in ranked}
        if ranked and len(values) == 1:
            shared = sorted(len(d[f"{asked['key']}_tied_heads"]) for d in ranked)
            span = str(shared[0]) if shared[0] == shared[-1] else f"{shared[0]} to {shared[-1]}"
            return (f"On every one of the {len(ranked)} days the {asked['label'].lower()} rate was "
                    f"{_pct(values.pop(), 4)}, shared by {span} heads each day; the table lists them.")
        parts = []
        for d in days:
            head, tied = d[f"{asked['key']}_head"], d[f"{asked['key']}_tied_heads"]
            if head is None:
                continue
            who_day = ("all heads" if len(tied) == d["n_eligible_heads"] else
                       tied[0] if len(tied) == 1 else f"{len(tied)} heads")
            parts.append(f"{d['date']}: {who_day} {_pct(head[asked['rate']], 4)}")
        return f"{asked['label']} rate each day — " + "; ".join(parts) + "."
    if name == "torque_stats":
        if not r["sample_size"]:
            return f"No torque readings matched for {who} {when}."
        return (f"The average torque of {who} {when} was {r['mean']:.3f} Nm over {r['sample_size']:,} closures "
                f"(minimum {r['min']:.3f} Nm, maximum {r['max']:.3f} Nm, standard deviation "
                f"{(r['std'] or 0):.4f} Nm).")
    if name == "torque_distribution":
        return (f"The torque of {who} {when} ranged from {r['bin_edges'][0]:.3f} to {r['bin_edges'][-1]:.3f} Nm "
                f"over {r['sample_size']:,} closures; the histogram is below.")
    if name == "torque_trend":
        if not r["sample_size"]:
            return f"No torque readings matched for {who} {when}, so drift cannot be assessed."
        if r.get("drift_detected"):
            return (f"Yes: the torque of {who} {when} drifted {r['drift_direction']} at "
                    f"{r['drift_slope_nm_per_hour']:.4f} Nm per hour, beyond the "
                    f"{r['drift_threshold_nm_per_hour']:g} Nm per hour threshold.")
        return (f"No: the torque of {who} {when} did not drift; the slope was "
                f"{(r.get('drift_slope_nm_per_hour') or 0):.4f} Nm per hour, below the "
                f"{r.get('drift_threshold_nm_per_hour', 0):g} Nm per hour threshold.")
    if name == "detect_torque_anomalies":
        return (f"{r['anomaly_count']:,} of the {r['sample_size']:,} torque readings of {who} {when} were flagged "
                f"as anomalies ({_pct(r['anomaly_rate'], 3)}).")
    if name == "head_correlation":
        return (f"The torque of {r['head_a']['head_id']} and {r['head_b']['head_id']} {when} has a correlation of "
                f"{r['torque_correlation']:.3f} ({str(r['torque_correlation_interpretation']).replace('_', ' ')}) "
                f"over {r['matched_torque_samples']:,} matched closures.")
    if name in ("observed_throughput", "kpi_over_time"):
        o = r["overall"]
        if name == "observed_throughput" or _COUNT.search(query or ""):
            return (f"{_cap(when)}, {o['n_cap_present']:,} caps were closed (closures with a cap); with "
                    f"cycles without a cap (No Load) the machine recorded {o['n_observed']:,} cycles.")
        return (f"{_cap(when)}, the success rate was {_pct(o['success_rate_cap_present'], 4)} over "
                f"{o['n_cap_present']:,} closures with a cap; the {r['n_buckets']} intervals are in the table.")
    if name == "machine_idle":
        periods = r.get("idle_periods") or []
        if not periods:
            return f"The machine had no idle period {when} (no stretch where every head ran without a cap)."
        longest = max(periods, key=lambda p: p["duration_seconds"])
        return (f"The machine had {r['n_periods']} idle period(s) {when} (all heads without a cap); the longest "
                f"lasted {longest['duration_seconds']:,} seconds, from {longest['start']} to {longest['end']}.")
    return "The analysis finished; see Details for the full result."


# --- checking Claude's numbers -------------------------------------------------

_DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
_MONTH_DAY = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
                        r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_IDS = re.compile(r"\b(?:head|spindle|heads)\s+\d+\b|\bH\d+\b|\bMCC\w+\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def _values(node, out):
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for key, value in node.items():
            _values(value, out)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and key.endswith("seconds"):
                out.extend([value / 60, value / 3600])
    elif isinstance(node, (list, tuple)):
        out.append(float(len(node)))
        for value in node:
            _values(value, out)


def unmatched_numbers(text, results, meta=None, query=""):
    """Numbers in `text` that no tool result (or the question) supports."""
    values = []
    for _, reply in results:
        _values(reply.get("result"), values)
    _values({k: v for k, v in (meta or {}).items() if isinstance(v, (int, float))}, values)
    for token in _NUMBER.findall(_IDS.sub(" ", _MONTH_DAY.sub(" ", _DATES.sub(" ", query or "")))):
        values.append(float(token.replace(",", "")))
    cleaned = _IDS.sub(" ", _MONTH_DAY.sub(" ", _DATES.sub(" ", text or "")))
    missing = []
    for token in _NUMBER.findall(cleaned):
        number = float(token.replace(",", ""))
        digits = len(token.split(".")[1]) if "." in token else 0
        if number in (0.0, 1.0, 100.0):
            continue  # 0%, 100%, "one head": trivially true descriptors
        tolerance = 0.5 * 10 ** -digits + 1e-12
        if not any(abs(v - number) <= tolerance or abs(v * 100 - number) <= tolerance for v in values):
            missing.append(token)
    return missing


# --- what Claude is shown --------------------------------------------------------

_DROP_KEYS = {"rate_definitions", "notes", "method", "ordering", "comparison_method", "day_definition"}


def _compact(node, max_items):
    if isinstance(node, dict):
        return {k: _compact(v, max_items) for k, v in node.items() if k not in _DROP_KEYS}
    if isinstance(node, list):
        if node and all(isinstance(v, (int, float, str)) or v is None for v in node) and len(node) > 60:
            return {"n_values": len(node), "first": node[0], "last": node[-1]}
        if len(node) > max_items:
            return [_compact(v, max_items) for v in node[:max_items]] + [f"... {len(node) - max_items} more"]
        return [_compact(v, max_items) for v in node]
    return node


def compact_results(results, limit=80_000):
    """Tool results as compact JSON for the answer writer: summary values only.

    Long per-reading series (trend points) are reduced to their length and ends;
    no raw telemetry rows are part of any tool result.
    """
    import json
    for max_items in (400, 120, 40):
        out = []
        for name, reply in results:
            if not reply.get("ok"):
                out.append({"tool": name, "error": reply.get("error")})
                continue
            out.append({"tool": name, "result": _compact(reply.get("result"), max_items)})
        text = json.dumps(out, separators=(",", ":"), default=str)
        if len(text) <= limit:
            return text
    return text[:limit]


# --- the answer ----------------------------------------------------------------

def compose(query, plan, results, meta, planner=None, trace=None):
    """{sentence, artifact, metric, table, source} for the question."""
    artifact, metric = choose_artifact(query, plan, results)
    answer = dict(sentence=deterministic_sentence(query, plan, results, meta),
                  artifact=artifact, metric=metric, source="built-in")
    writer = getattr(planner, "compose_answer", None)
    if callable(writer) and _ok(results):
        try:
            proposal = writer(query, plan, results, meta, draft=answer)
        except Exception as exc:  # the built-in answer always stands
            proposal = None
            if trace is not None:
                trace.note(f"answer writer unavailable ({type(exc).__name__}); built-in answer used")
        if proposal:
            names = [name for name, _ in _ok(results)]
            allowed = set().union(*(TOOL_ARTIFACTS.get(n, ("none",)) for n in names))
            missing = unmatched_numbers(proposal.get("sentence", ""), results, meta, query)
            if not proposal.get("sentence") or missing:
                if trace is not None:
                    trace.note("Claude's answer was not used: "
                               + (f"numbers not in the results ({', '.join(missing[:5])})" if missing else "empty"))
            else:
                answer.update(sentence=proposal["sentence"].strip(), source="claude")
                if proposal.get("artifact") in allowed:
                    answer["artifact"] = proposal["artifact"]
                if proposal.get("metric") in METRICS:
                    answer["metric"] = proposal["metric"]
                if trace is not None:
                    trace.note("answer written by Claude from the tool results; every number checked")
    answer["table"] = render_table(answer["artifact"], answer["metric"], results,
                                   (getattr(plan, "focus", ""), getattr(plan, "extreme", "")))
    return answer
