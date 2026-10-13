"""Audit fixes: torque tools describe closures, drift needs a practical rate,
correlation ignores idle cycles, and status/torque disagreements are flagged."""
from datetime import datetime, timedelta

import polars as pl
import pytest

from src.agent.planner import RulePlanner
from src.agent.llm_validation import validate_tool_call
from src.analytics import registered_analytics  # noqa: F401
from src.common import registry
from src.common.runtime import dispatch_tool

CFG = {"analytics": {"drift_window_seconds": 3600, "torque_expected_min": 1.5,
                     "torque_expected_max": 2.5, "anomaly_sigma": 3.0,
                     "drift_threshold_nm_per_hour": 0.05, "status_torque_mismatch_nm": 0.5}}


def events(rows):
    """rows: (seconds, head, torque, status)."""
    start = datetime(2026, 2, 1)
    classes = {0: "Closure OK", 2: "No Load", 65: "Bad Closure"}
    return pl.DataFrame({
        "ts": [start + timedelta(seconds=s) for s, *_ in rows],
        "machine_id": ["M1"] * len(rows),
        "head_id": [h for _, h, _, _ in rows],
        "torque": [t for *_, t, _ in rows],
        "status": [s for *_, s in rows],
        "error_class": [classes[s] for *_, s in rows],
        "reject_signal": [s == 65 for *_, s in rows],
        "cap_present": [s != 2 for *_, s in rows],
    })


# Real-shaped mix: closures near 2 Nm, No Load cycles at 0 Nm.
MIXED = events([(i, "H01", 2.0 + (i % 3) * 0.01, 0) for i in range(30)]
               + [(100 + i, "H01", 0.0, 2) for i in range(20)])


def run(tool, data, **arguments):
    response = dispatch_tool(tool, data, config=CFG, arguments=arguments)
    assert response["ok"], response["error"]
    return response


def test_no_load_cycles_are_excluded_by_default():
    default = run("torque_stats", MIXED)
    assert default["result"]["sample_size"] == 30
    assert default["result"]["mean"] == pytest.approx(2.01)
    assert default["meta"]["population"] == "closures, excluding No Load cycles (default)"
    everything = run("torque_stats", MIXED, status_filter="all_statuses")
    assert everything["result"]["sample_size"] == 50
    assert everything["meta"]["population"] == "every status, including No Load cycles"


def test_no_load_cycles_are_not_torque_anomalies_by_default():
    assert run("detect_torque_anomalies", MIXED)["result"]["anomaly_count"] == 0
    # Explicitly including them reproduces the old behaviour: every idle cycle is "low".
    assert run("detect_torque_anomalies", MIXED, status_filter="all_statuses")["result"]["anomaly_count"] == 20


def test_explicit_status_codes_keep_their_meaning():
    assert run("torque_stats", MIXED, status_filter=2)["result"]["sample_size"] == 20
    assert run("torque_stats", MIXED, status_filter="successful")["result"]["sample_size"] == 30


def test_drift_requires_the_configured_rate():
    slow = events([(i * 60, "H01", 2.0 + i * 0.0001, 0) for i in range(120)])  # 0.006 Nm/h
    fast = events([(i * 60, "H01", 2.0 + i * 0.01, 0) for i in range(120)])    # 0.6 Nm/h
    quiet = run("torque_trend", slow)["result"]
    assert quiet["drift_detected"] is False and quiet["drift_direction"] == "stable"
    assert 0 < quiet["drift_slope_nm_per_hour"] < 0.05
    moving = run("torque_trend", fast)["result"]
    assert moving["drift_detected"] is True and moving["drift_direction"] == "upward"
    assert moving["drift_threshold_nm_per_hour"] == 0.05


def test_correlation_ignores_heads_idling_together():
    rows = []
    for i in range(40):
        idle = i % 2 == 0
        rows.append((i, "H01", 0.0 if idle else 2.0 + (i % 5) * 0.01, 2 if idle else 0))
        rows.append((i, "H02", 0.0 if idle else 2.0 + ((i * 7) % 5) * 0.01, 2 if idle else 0))
    result = run("head_correlation", events(rows), head_a="H01", head_b="H02")
    # With the idle cycles the two heads would look almost perfectly correlated.
    assert result["result"]["matched_torque_samples"] == 20
    assert abs(result["result"]["torque_correlation"]) < 0.9
    assert result["meta"]["population"] == "closures, excluding No Load cycles"


def test_status_torque_disagreements_are_counted_not_changed():
    data = events([(0, "H01", 2.0, 0), (1, "H01", 0.0, 0), (2, "H01", 0.0, 2), (3, "H01", 1.9, 2)])
    response = run("torque_stats", data)
    assert response["meta"]["status_torque_mismatches"] == {
        "threshold_nm": 0.5, "ok_with_low_torque": 1, "no_load_with_torque": 1}
    assert response["result"]["sample_size"] == 2  # still analysed as recorded, not repaired


def test_rules_and_validators_accept_all_statuses():
    plan = RulePlanner().plan("Average torque for head 1 including No Load", {})
    assert plan.calls == [("torque_stats", {"head_id": "H01", "status_filter": "all_statuses"})]
    assert RulePlanner().plan("Average torque for head 1 for all closures", {}).calls == [
        ("torque_stats", {"head_id": "H01"})]
    assert validate_tool_call("torque_stats", {"status_filter": "all_statuses"}, registry.get)
