"""Parity, scope, configuration isolation and report checks for all four tools."""

import json
from copy import deepcopy
from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.analytics import registered_analytics  # noqa: F401
from src.analytics.torque_distribution import torque_distribution
from src.analytics.trend_analysis import torque_trend, _classify_drift
from src.analytics.anomaly_detection import detect_torque_anomalies
from src.analytics.head_correlation import head_correlation
from src.common import registry
from src.common.runtime import dispatch_tool
from src.agent.orchestrator import Orchestrator
from src.agent.planner import Plan
from src.agent.report import assemble
from src.agent.trace import Trace
from src.ingestion.event_table_polars import write_event_table_parquet


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


CASES = [
    ("torque_distribution", {"bins": 3}),
    ("torque_trend", {}),
    ("detect_torque_anomalies", {}),
    ("head_correlation", {"head_a": "H01", "head_b": "H02"}),
]


def closures(events):
    return events.filter(pl.col("error_class").fill_null("") != "No Load")


def direct(name, events, cfg, params):
    """What each registered tool must return, computed with the Person A functions.

    The registered tools describe closures (No Load cycles excluded) unless a
    status is requested, and the trend applies the configured drift threshold.
    """
    selected = closures(events) if params.get("status_filter") is None else events
    if name == "torque_distribution":
        return torque_distribution(selected, **params)
    if name == "torque_trend":
        result = torque_trend(selected, cfg, **params)
        per_hour = cfg["analytics"].get("drift_threshold_nm_per_hour", 0.05)
        slope = result["drift_slope_per_second"]
        result["drift_threshold_nm_per_hour"] = per_hour
        result["drift_slope_nm_per_hour"] = None if slope is None else slope * 3600
        result["drift_detected"], result["drift_direction"] = _classify_drift(slope, per_hour / 3600)
        return result
    if name == "detect_torque_anomalies":
        return detect_torque_anomalies(selected, cfg, **params)
    return head_correlation(closures(events), **params)


@pytest.mark.parametrize("name, params", CASES)
def test_existing_analytics_results_are_preserved(events, cfg, name, params):
    before = events.clone()
    response = dispatch_tool(name, events, config=cfg, arguments=params)
    assert response["ok"] is True, response["error"]
    assert response["result"] == direct(name, events, cfg, params)
    expected_n = response["result"].get(
        "sample_size", response["result"].get("matched_torque_samples")
    )
    assert response["meta"]["n"] == expected_n
    assert response["meta"]["agent"] == "analytics"
    assert response["meta"]["params"] == params
    json.dumps(response, allow_nan=False)
    assert_frame_equal(events, before)


@pytest.mark.parametrize("name", ["torque_distribution", "torque_trend", "detect_torque_anomalies"])
def test_scope_is_applied_before_calculation(events, cfg, name):
    params = {
        "head_id": "H01", "machine_id": "M1", "status_filter": "successful",
        "start": "2026-02-01T10:00:00", "end": "2026-02-01T10:00:02",
    }
    response = dispatch_tool(name, events, config=cfg, arguments=params)
    expected = events.filter((pl.col("head_id") == "H01") & (pl.col("status") == 0))
    assert response["ok"] is True, response["error"]
    assert response["result"] == direct(name, expected, cfg, {})
    assert response["meta"]["n"] == 2
    assert response["meta"]["data_window"] == {
        "ts_min": "2026-02-01T10:00:00", "ts_max": "2026-02-01T10:00:01",
    }


@pytest.mark.parametrize("name, params", CASES)
def test_empty_input_keeps_core_semantics(events, cfg, name, params):
    empty = events.head(0)
    response = dispatch_tool(name, empty, config=cfg, arguments=params)
    assert response["ok"] is True, response["error"]
    assert response["result"] == direct(name, empty, cfg, params)
    assert response["meta"]["n"] == 0
    assert response["meta"]["data_window"] == {"ts_min": None, "ts_max": None}


@pytest.mark.parametrize("params", [
    {"config": {"analytics": {"drift_window_seconds": 999}}},
    {"config": "null"}, {"events": "null"}, {"window_seconds": 999},
    {"sigma": 999}, {"torque_expected_min": -999},
])
def test_model_arguments_cannot_override_runtime_configuration(events, cfg, params):
    response = dispatch_tool("torque_trend", events, config=cfg, arguments=params)
    assert response["ok"] is False
    assert "does not accept" in response["error"]


def test_configuration_is_isolated_per_dispatch(events, cfg):
    before = deepcopy(cfg)
    first = dispatch_tool("torque_trend", events, config=cfg)
    other = deepcopy(cfg)
    other["analytics"]["drift_window_seconds"] = 10
    second = dispatch_tool("torque_trend", events, config=other)
    assert first["result"]["window_seconds"] == 2
    assert second["result"]["window_seconds"] == 10
    assert cfg == before
    unbound = registry.call_tool("torque_trend", events)
    assert unbound["ok"] is False
    assert "trusted runtime configuration" in unbound["error"]


def test_missing_runtime_settings_fail_without_defaults(events):
    response = dispatch_tool("detect_torque_anomalies", events, config={})
    assert response["ok"] is False
    assert "Missing anomaly configuration" in response["error"]


@pytest.mark.parametrize("bins", [0, -1, True, 2.5, "3", "null"])
def test_invalid_bins_are_not_silently_changed(events, cfg, bins):
    response = dispatch_tool("torque_distribution", events, config=cfg, arguments={"bins": bins})
    assert response["ok"] is False
    assert response["result"] is None


def test_correlation_never_pairs_different_machines(events, cfg):
    combined = pl.concat([events, events.with_columns(pl.lit("M2").alias("machine_id"))])
    params = {"head_a": "H01", "head_b": "H02"}
    response = dispatch_tool("head_correlation", combined, config=cfg, arguments=params)
    assert response["ok"] is False
    assert "one machine_id" in response["error"]
    selected = dispatch_tool(
        "head_correlation", combined, config=cfg,
        arguments=dict(params, machine_id="M1"),
    )
    assert selected["ok"] is True
    assert selected["result"] == head_correlation(closures(events), "H01", "H02")


def test_duplicate_pairing_is_rejected_without_removing_rows(events, cfg):
    duplicated = pl.concat([events, events.head(1)])
    before = duplicated.clone()
    response = dispatch_tool("head_correlation", duplicated, config=cfg,
                             arguments={"head_a": "H01", "head_b": "H02"})
    assert response["ok"] is False
    assert "ambiguous" in response["error"]
    assert_frame_equal(duplicated, before)


def test_missing_head_returns_insufficient_overlap(events, cfg):
    response = dispatch_tool("head_correlation", events, config=cfg,
                             arguments={"head_a": "H01", "head_b": "H99"})
    assert response["ok"] is True
    assert response["result"]["head_b"]["found"] is False
    assert response["result"]["torque_correlation"] is None
    assert response["meta"]["n"] == 0


def test_success_denominator_is_preserved_and_disclosed(events, cfg):
    changed = events.with_columns(
        pl.when(pl.col("status") == 2).then(pl.lit("No Closure"))
        .otherwise(pl.col("error_class")).alias("error_class"),
        pl.when(pl.col("status") == 2).then(pl.lit(None, dtype=pl.Boolean))
        .otherwise(pl.col("cap_present")).alias("cap_present"),
        pl.when(pl.col("status") == 2).then(4).otherwise(pl.col("status")).alias("status"),
    )
    response = dispatch_tool("head_correlation", changed, config=cfg,
                             arguments={"head_a": "H01", "head_b": "H02"})
    assert response["ok"] is True
    assert response["result"]["head_a"]["success_evaluable_count"] == 3
    assert response["result"]["head_a"]["success_rate"] == pytest.approx(2 / 3)
    assert "not the cap_present=True denominator" in response["meta"]["notes"]


def test_model_schemas_exclude_runtime_settings():
    specs = {item["name"]: item for item in registry.get_tool_specs()}
    assert {"torque_stats", *(name for name, _ in CASES)} <= set(specs)
    for name, _ in CASES:
        schema = specs[name]["input_schema"]
        assert schema["additionalProperties"] is False
        assert not ({"config", "events", "sigma", "window_seconds"} & set(schema["properties"]))
    assert specs["head_correlation"]["input_schema"]["required"] == ["head_a", "head_b"]


@pytest.mark.parametrize("name, params", CASES)
def test_reports_describe_results_without_claiming_machine_health(events, cfg, name, params):
    response = dispatch_tool(name, events, config=cfg, arguments=params)
    trace = Trace("controlled analysis")
    trace.tool_call(name, params, response)
    text = assemble("controlled analysis", Plan(goal="Describe the observations."),
                    [(name, response)], {}, trace)
    assert name in text
    assert "Nothing anomalous surfaced" not in text
    assert "machine is stable" not in text
    if name == "head_correlation":
        assert "Person A denominator" in text
        assert "Rates are computed over cap-present" not in text
    elif name == "torque_trend":
        assert "Nm per hour" in text
        assert "not a statistical significance test" in response["meta"]["notes"]
    if name != "head_correlation":
        assert "Events analysed: closures, excluding No Load cycles (default)" in text


def test_orchestrator_supplies_trusted_config(events, cfg, tmp_path):
    class TestPlanner:
        name = "test-fixed"

        def plan(self, query, context):
            return Plan(goal="Run the configured trend.", calls=[("torque_trend", {"head_id": "H01"})])

    path = write_event_table_parquet(events, tmp_path / "events.parquet")
    cfg["data"] = {"source": "person_a", "person_a": {"fixture": str(path)}}
    answer = Orchestrator(cfg=cfg, planner=TestPlanner()).answer("trend", pool="fixture")
    assert answer["status"] == "ok"
    assert answer["results"][0][1]["result"]["window_seconds"] == 2
    assert answer["results"][0][1]["meta"]["params"] == {"head_id": "H01"}
    assert answer["trace"].to_dict()["n_tool_calls"] == 1
