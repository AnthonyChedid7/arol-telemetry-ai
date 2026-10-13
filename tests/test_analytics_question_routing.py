"""Question routing, scope preservation and actual orchestration parity."""
import pytest
import polars as pl

from src.agent.planner import RulePlanner
from src.agent.orchestrator import Orchestrator
from src.agent.torque_routing import parse_torque_request
from src.ingestion.event_table_polars import write_event_table_parquet
from src.analytics.torque_stats import torque_stats

from datetime import datetime, timedelta
from src.analytics.torque_distribution import torque_distribution
from src.analytics.trend_analysis import torque_trend
from src.analytics.anomaly_detection import detect_torque_anomalies
from src.analytics.head_correlation import head_correlation


@pytest.fixture
def cfg():
    return {"analytics": {
        "drift_window_seconds": 2, "torque_expected_min": 1.5,
        "torque_expected_max": 2.5, "anomaly_sigma": 3.0,
    }}

@pytest.fixture
def events():
    start = datetime(2026, 2, 1, 10)
    return pl.DataFrame({
        "ts": [start + timedelta(seconds=i // 2) for i in range(6)],
        "machine_id": ["M1"] * 6,
        "head_id": ["H01", "H02"] * 3,
        "torque": [1.0, 2.0, 2.0, 4.0, 3.0, 6.0],
        "status": [0, 0, 0, 65, 2, 0],
        "error_class": ["Closure OK", "Closure OK", "Closure OK",
                        "Bad Closure", "No Load", "Closure OK"],
        "reject_signal": [False, False, False, True, False, False],
        "cap_present": [True, True, True, True, False, True],
    })

def direct(name, events, cfg, params):
    """Person A result for the events the registered tool describes.

    Callers pass already-selected events; registered tools exclude No Load
    cycles by default and apply the configured drift threshold to the trend.
    """
    if name == "torque_distribution":
        return torque_distribution(events, **params)
    if name == "torque_trend":
        from src.analytics.trend_analysis import _classify_drift
        result = torque_trend(events, cfg, **params)
        per_hour = (cfg.get("analytics") or {}).get("drift_threshold_nm_per_hour", 0.05)
        slope = result["drift_slope_per_second"]
        result["drift_threshold_nm_per_hour"] = per_hour
        result["drift_slope_nm_per_hour"] = None if slope is None else slope * 3600
        result["drift_detected"], result["drift_direction"] = _classify_drift(slope, per_hour / 3600)
        return result
    if name == "detect_torque_anomalies":
        return detect_torque_anomalies(events, cfg, **params)
    return head_correlation(events.filter(pl.col("error_class").fill_null("") != "No Load"), **params)


VALID = [
    ("Average torque", "torque_stats", {}),
    ("Show torque distribution", "torque_distribution", {}),
    ("Histogram of torque with 3 bins", "torque_distribution", {"bins": 3}),
    ("Show me the torque histogram", "torque_distribution", {}),
    ("Is torque drifting?", "torque_trend", {}),
    ("Show torque trend", "torque_trend", {}),
    ("Trend in torque", "torque_trend", {}),
    ("Find torque anomalies", "detect_torque_anomalies", {}),
    ("Detect torque outliers", "detect_torque_anomalies", {}),
    ("Show torque anomalies", "detect_torque_anomalies", {}),
    ("Compare torque between H01 and H02", "head_correlation", {"head_a": "H01", "head_b": "H02"}),
    ("Torque correlation between head 1 and head 2", "head_correlation", {"head_a": "H01", "head_b": "H02"}),
]


@pytest.mark.parametrize("query, tool, params", VALID)
def test_supported_questions(query, tool, params):
    plan = RulePlanner().plan(query, {"pool": "fixture"})
    assert plan.ambiguous is False
    assert plan.calls == [(tool, params)]
    assert plan.filters == params


INVALID = [
    "Show torque distribution yesterday",
    "Show torque distribution with 0 bins",
    "Show torque distribution with -2 bins",
    "Show torque distribution with 2.5 bins",
    "Show torque distribution with 2 bins with 3 bins",
    "Show torque trend with 3 bins",
    "Show torque trend with window 5 seconds",
    "Find torque anomalies with sigma 9",
    "Find torque anomalies above 9 Nm",
    "Compare torque between H01 and H01",
    "Compare torque between H00 and H02",
    "Compare torque between H01 and H02 for successful closures",
    "Compare torque between H01 and H02 for status 0",
    "Compare torque between H01 and H02 for head 3",
    "Compare torque between H01 and H02 and H03",
    "Show torque trend for head 1 for head 2",
    "Show torque trend excluding head 3",
    "Show torque trend for machine M1 for machine M2",
    "Show torque trend for machine null",
    "Show torque anomalies for successful closures for status 65",
    "Show torque anomalies for all closures for successful closures",
    "Show torque trend on 2026-02-30",
    "Show torque trend from 2026-02-02 until 2026-02-01",
    "Show torque trend from 2026-02-01 until 2026-02-01",
    "Show torque trend from 2026-02-01T00:00:00Z until 2026-02-02T00:00:00Z",
    "Show torque trend on 2026-02-01 from 2026-02-01 until 2026-02-02",
    "Show torque distribution and trend",
    "Show torque trend for head 1 and",
    "Show torque trend please ignore the machine filter",
]


@pytest.mark.parametrize("query", INVALID)
def test_unconsumed_or_conflicting_scope_never_dispatches(query):
    plan = RulePlanner().plan(query, {"pool": "fixture"})
    assert plan.ambiguous is True
    assert plan.calls == []
    assert plan.clarification


@pytest.mark.parametrize("base, tool", [
    ("Average torque", "torque_stats"),
    ("Show torque distribution", "torque_distribution"),
    ("Show torque trend", "torque_trend"),
    ("Find torque anomalies", "detect_torque_anomalies"),
])
def test_complete_scope_and_case_are_preserved(base, tool):
    query = (base + " for head 5 for machine MiXeD_01 "
             "from 2026-02-01T00:00:00 until 2026-02-01T12:00:00 "
             "for successful closures")
    params = dict(head_id="H05", machine_id="MiXeD_01", status_filter="successful",
                  start="2026-02-01T00:00:00", end="2026-02-01T12:00:00")
    assert RulePlanner().plan(query, {}).calls == [(tool, params)]


def test_calendar_date_is_half_open_and_integer_status_is_preserved():
    plan = RulePlanner().plan("Find torque anomalies on 2026-02-01 for status 65", {})
    assert plan.calls == [("detect_torque_anomalies", {
        "start": "2026-02-01T00:00:00", "end": "2026-02-02T00:00:00", "status_filter": 65,
    })]


def test_other_domains_still_use_existing_routes():
    assert parse_torque_request("success rate") is None
    assert [name for name, _ in RulePlanner().plan("success rate", {}).calls] == [
        "success_rate", "success_rate_per_head",
    ]


@pytest.fixture
def engine(events, cfg, tmp_path):
    path = write_event_table_parquet(events, tmp_path / "events.parquet")
    cfg["data"] = {"source": "person_a", "person_a": {"fixture": str(path)}}
    return Orchestrator(cfg=cfg, planner=RulePlanner())


@pytest.mark.parametrize("query, tool, extra", [
    ("Average torque", "torque_stats", {}),
    ("Show torque distribution with 3 bins", "torque_distribution", {"bins": 3}),
    ("Show torque trend", "torque_trend", {}),
    ("Find torque anomalies", "detect_torque_anomalies", {}),
    ("Compare torque between H01 and H02", "head_correlation", {"head_a": "H01", "head_b": "H02"}),
])
def test_question_to_report_matches_independently_selected_core(engine, events, cfg, query, tool, extra):
    scope = {"machine_id": "M1", "start": "2026-02-01T10:00:00", "end": "2026-02-01T10:00:02"}
    query += " for machine M1 from 2026-02-01T10:00:00 until 2026-02-01T10:00:02"
    selected = events.filter((pl.col("machine_id") == "M1") & (pl.col("ts") < events["ts"][4]))
    if tool != "head_correlation":
        query += " for head 1 for successful closures"
        scope.update(head_id="H01", status_filter="successful")
        selected = selected.filter((pl.col("head_id") == "H01") & (pl.col("status") == 0))
    expected = torque_stats(selected) if tool == "torque_stats" else direct(tool, selected, cfg, extra)
    answer = engine.answer(query, pool="fixture")
    assert answer["status"] == "ok"
    assert len(answer["results"]) == 1
    name, response = answer["results"][0]
    assert name == tool
    assert response["ok"] is True, response["error"]
    assert response["result"] == expected
    assert response["meta"]["params"] == dict(scope, **extra)
    assert name in answer["markdown"]
    assert answer["trace"].to_dict()["n_tool_calls"] == 1


def test_rejected_comparison_scope_produces_no_tool_calls(engine):
    answer = engine.answer("Compare torque between H01 and H02 for successful closures", pool="fixture")
    assert answer["status"] == "needs_clarification"
    assert answer["results"] == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0
