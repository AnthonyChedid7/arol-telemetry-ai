"""Route a scoped head-health question to descriptive registered analyses."""

import re

from .torque_routing import _parse_canonical_torque_request


def parse_head_health_request(query, *, selected_machine=None):
    text = " ".join((query or "").split()).rstrip(".?!")
    match = re.match(
        r"(?:is anything wrong with|is there any (?:problem|issue) with) "
        r"(?:head\s*[-#]?\s*|h)(\d{1,2})(?=\s|$)",
        text, re.I,
    )
    if match is None:
        return None

    number = int(match.group(1))
    machine_example = selected_machine or "M1"
    example = (
        f"Is there any problem with head {number} for machine {machine_example} from "
        "2026-02-01T00:00:00 until 2026-02-01T12:00:00"
    )
    scope_request = (f"give a bounded time window for the selected machine {selected_machine}"
                     if selected_machine else "give one machine and a bounded time window")
    clarification = dict(
        goal="Specify the head-health observation scope.",
        ambiguous=True,
        clarification=(
            f"To examine head {number}, {scope_request}. "
            f"For example: '{example}'. I can compare observed cap-present "
            "success with peer heads and check torque flags, but these checks "
            "cannot by themselves confirm a fault. This combined check does "
            "not accept extra status filters or threshold overrides."
        ),
        rationale="head-health check requires an explicit machine and bounded time; no data loaded",
    )
    if number < 1:
        clarification["clarification"] = "Name a valid head number, one machine, and a bounded time window."
        return clarification

    tail = text[match.end():].strip()
    if selected_machine is not None:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", selected_machine):
            return clarification
        if not re.search(r"\bmachine\b", tail, re.I):
            tail = f"{tail} for machine {selected_machine}".strip()
    parsed = _parse_canonical_torque_request(f"Average torque for head {number} {tail}")
    if parsed is None or parsed.get("ambiguous"):
        return clarification
    params = parsed["calls"][0][1]
    if set(params) != {"head_id", "machine_id", "start", "end"}:
        return clarification
    if selected_machine is not None and params["machine_id"] != selected_machine:
        clarification["clarification"] = (
            f"This page has selected machine {selected_machine}, but the question "
            f"names {params['machine_id']}. Use the selected machine or change the "
            "event pool before analyzing. No data was loaded."
        )
        return clarification

    return dict(
        goal=(f"Describe observed evidence for {params['head_id']}: compare its "
              "cap-present success with eligible peer heads and check its torque "
              "on closures excluding No Load cycles by default against configured "
              "anomaly rules. "
              "These checks do not establish a fault."),
        calls=[("compare_head_success", dict(params)),
               ("detect_torque_anomalies", dict(params))],
        filters=dict(params),
        rationale="head and bounded time with explicit or selected machine; descriptive peer and torque checks",
    )
