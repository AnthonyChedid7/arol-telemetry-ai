"""Evidence-based head checks using registered analytics, without an LLM."""

from .head_health_routing import parse_head_health_request
from .planner import Plan, RulePlanner


class HeadDiagnosticPlanner(RulePlanner):
    name = "diagnostic"

    def plan(self, query: str, context: dict) -> Plan:
        selected_machine = context.get("selected_machine")
        diagnostic = parse_head_health_request(
            query, selected_machine=selected_machine,
        )
        if diagnostic is not None:
            return Plan(**diagnostic)
        return super().plan(query, context)

    def explain(self, query: str, results: list, report: str) -> str | None:
        if [name for name, _ in results] != [
            "compare_head_success", "detect_torque_anomalies",
        ] or any(not value.get("ok") for _, value in results):
            return None
        comparison = results[0][1]["result"]
        torque = results[1][1]["result"]
        head = comparison["focus_head_id"]
        if comparison["comparison_available"]:
            focus = comparison["focus"]
            difference = comparison["difference_from_peer_median_pp"]
            if abs(difference) < 0.000001:
                comparison_line = "This matches the peer median in the selected window."
            else:
                direction = "below" if difference < 0 else "above"
                comparison_line = (
                    f"That is {abs(difference):.2f} percentage points "
                    f"{direction} the peer median."
                )
            peer_line = (
                f"{head} had {focus['n_success_cap_present']:,}/"
                f"{focus['n_cap_present']:,} confirmed cap-present successes "
                f"({focus['success_rate_cap_present'] * 100:.2f}%). The median "
                f"among {comparison['n_eligible_peers']} eligible other heads "
                f"was {comparison['peer_median_success_rate'] * 100:.2f}%. "
                f"{comparison_line}"
            )
        else:
            reasons = {
                "focus_head_not_found": "no observations were recorded for it",
                "focus_below_minimum_sample": "too few cap-present events were observed",
                "fewer_than_two_eligible_peers": "fewer than two eligible peer heads were observed",
            }
            reason = reasons.get(comparison["comparison_reason"], "the peer baseline is unavailable")
            peer_line = f"A peer success comparison is unavailable for {head}: {reason}."
        torque_line = (
            f"The torque check flagged {torque['anomaly_count']:,} of "
            f"{torque['sample_size']:,} finite observations "
            "(" + results[1][1]["meta"].get("population", "all recorded statuses, including No Load")
            .replace(" (default)", "") + ")."
        )
        limits = (
            "These are recorded exact +1 events, so production coverage is not "
            "confirmed. The configured torque bounds are not confirmed operating "
            "limits, and the flags need review by event class and operating "
            "condition. This evidence cannot establish a yes/no fault verdict."
        )
        return f"## Diagnostic answer\n\n{peer_line} {torque_line}\n\n**Limits:** {limits}\n"
