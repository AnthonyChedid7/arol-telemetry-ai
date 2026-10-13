"""Complete, deterministic grammar for the five registered torque analyses.

No numerical calculation or configuration override is performed here.
Unknown clauses cause clarification rather than broadening a request.
"""

import re
from datetime import datetime, timedelta

from ..common.registry import MAX_BINS


def _parse_canonical_torque_request(query):
    """Return None for other domains, or a plan dictionary for torque queries."""
    text = " ".join((query or "").split()).rstrip(".?!")
    if not re.search(r"\btorque\b", text, re.I):
        return None

    def clarify():
        return dict(
            goal="Clarify the requested torque analysis and scope.",
            ambiguous=True,
            clarification=(
                "Ask for torque statistics, distribution, trend, anomalies, or "
                "a comparison between two distinct heads. Optional scope uses "
                "'for head 5', 'for machine M1', 'on YYYY-MM-DD', or "
                "'from <ISO timestamp> until <ISO timestamp>' (end exclusive). "
                "Statistics, distribution, trend and anomalies also accept "
                "'for successful closures' or 'for status 0'. Distribution "
                "accepts 'with 10 bins'. Head comparison accepts machine and "
                "time scope only. Supported combinations are statistics with distribution, "
                "or trend with anomalies. Relative dates, exclusions, other combinations "
                "and changes to configured thresholds/windows need clarification."
            ),
            rationale="unsupported or conflicting torque request; no tool called",
        )

    prefix = r"(?:(?:what is|what are|show me|show) (?:the )?)?"
    head = r"(?:head\s*[-#]?\s*|h)(\d+)"
    patterns = [
        ("head_correlation", r"(?:compare torque between|torque (?:comparison|correlation) between) " + head + r" (?:and|versus|vs) " + head),
        ("torque_stats", prefix + r"(?:torque (?:statistics|stats|summary)|(?:mean|average|minimum|maximum|standard deviation)(?: of)? torque)"),
        ("torque_distribution", prefix + r"(?:torque (?:distribution|histogram)|(?:distribution|histogram) of torque)"),
        ("torque_trend", prefix + r"(?:torque (?:trend|drift)|(?:trend|drift) (?:in|of) torque)|is torque drifting"),
        ("detect_torque_anomalies", prefix + r"torque (?:anomalies|outliers)|(?:detect|find) torque (?:anomalies|outliers)"),
    ]
    matched = None
    for tool, pattern in patterns:
        base = re.match(r"(?:" + pattern + r")(?=\s|$)", text, re.I)
        if base:
            matched = (tool, base)
            break
    if matched is None:
        return clarify()
    tool, base = matched
    params = {}
    if tool == "head_correlation":
        try:
            a, b = map(int, base.groups())
        except ValueError:
            return clarify()
        if a < 1 or b < 1 or a == b:
            return clarify()
        params.update(head_a=f"H{a:02d}", head_b=f"H{b:02d}")

    clauses = [
        ("head", r"(?:for\s+)?(?:head\s*[-#]?\s*|h)(\d+)(?=\s|$)"),
        ("machine", r"(?:for\s+)?machine\s+([A-Za-z0-9_][A-Za-z0-9_-]*)(?=\s|$)"),
        ("success", r"(?:for\s+)?successful\s+closures(?=\s|$)"),
        ("status", r"(?:for\s+)?status\s+(-?\d+)(?=\s|$)"),
        ("all", r"(?:for\s+)?all\s+closures(?=\s|$)"),
        ("bins", r"with\s+(\d+)\s+bins(?=\s|$)"),
        ("date", r"on\s+(\d{4}-\d{2}-\d{2})(?=\s|$)"),
        ("range", r"from\s+(\S+)\s+until\s+(\S+)(?=\s|$)"),
    ]
    tail = text[base.end():].strip()
    while tail:
        matched = next(((kind, m) for kind, pattern in clauses
                        if (m := re.match(pattern, tail, re.I))), None)
        if matched is None:
            return clarify()
        kind, match = matched
        if tool == "head_correlation" and kind not in {"machine", "date", "range"}:
            return clarify()
        try:
            if kind == "head":
                number = int(match.group(1))
                if number < 1:
                    return clarify()
                added = {"head_id": f"H{number:02d}"}
            elif kind == "machine":
                value = match.group(1)
                if value.lower() in {"null", "none", "nan", "all", "any"}:
                    return clarify()
                added = {"machine_id": value}
            elif kind == "success":
                added = {"status_filter": "successful"}
            elif kind == "status":
                added = {"status_filter": int(match.group(1))}
            elif kind == "all":
                added = {"status_filter": None}
            elif kind == "bins":
                number = int(match.group(1))
                if tool != "torque_distribution" or not 1 <= number <= MAX_BINS:
                    return clarify()
                added = {"bins": number}
            else:
                lo = datetime.fromisoformat(match.group(1))
                hi = (lo + timedelta(days=1) if kind == "date"
                      else datetime.fromisoformat(match.group(2)))
                if lo.tzinfo is not None or hi.tzinfo is not None or lo >= hi:
                    return clarify()
                added = {"start": lo.isoformat(), "end": hi.isoformat()}
        except (ValueError, OverflowError):
            return clarify()
        if params.keys() & added.keys():
            return clarify()
        params.update(added)
        tail = tail[match.end():].strip()
        if tail.lower() == "and":
            return clarify()
        if tail.lower().startswith("and "):
            tail = tail[4:].strip()
            if not tail:
                return clarify()

    params = {key: value for key, value in params.items() if value is not None}
    return dict(
        goal=f"Run {tool} within the explicitly requested event scope.",
        calls=[(tool, params)], filters=dict(params),
        rationale="parsed complete torque request with explicit scope",
    )


def parse_torque_request(query):
    """Recognise diagnostic phrases, then validate the entire remaining scope."""
    from .diagnostic_routing import rewrite_diagnostic_question
    text = " ".join((query or "").split()).rstrip(".?!")
    rewritten = rewrite_diagnostic_question(text)
    if rewritten is None:
        return _parse_canonical_torque_request(query)
    if rewritten.get("ambiguous"):
        return rewritten
    plan = _parse_canonical_torque_request(rewritten["canonical"])
    if plan is None or plan.get("ambiguous"):
        return plan
    parameters = plan["calls"][0][1]
    calls = []
    for tool in rewritten["tools"]:
        args = dict(parameters)
        if tool != "torque_distribution":
            args.pop("bins", None)  # Bin count belongs to the histogram only.
        calls.append((tool, args))
    plan["calls"] = calls
    plan["goal"] = "Run " + " and ".join(rewritten["tools"]) + " within the explicitly requested event scope."
    plan["rationale"] = "matched complete diagnostic phrase and validated every scope clause"
    return plan
