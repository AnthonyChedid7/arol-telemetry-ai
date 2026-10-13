"""Verify filtered materialization through the real production orchestrator."""
import json
from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.agent.orchestrator import Orchestrator
from src.agent.planner import Plan
from src.common import datasource, event_pool_source
from src.common.runtime import dispatch_tool
from src.ingestion.event_pool import build_event_pool
from src.ingestion.event_table_polars import build_event_table, EVENT_SCHEMA


@pytest.fixture
def pool(tmp_path):
    raw = pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1) + timedelta(seconds=i) for i in range(7)],
        "H01 Count": list(range(10, 17)), "H02 Count": list(range(20, 27)),
        "H01 AppTorque": [0., 1., 2., 3., 4., 5., 6.],
        "H02 AppTorque": [0., 2., 4., 6., 8., 10., 12.],
        "H01 Status": [0, 0, 0, 65, 0, 3, 0],
        "H02 Status": [0, 0, 65, 0, 0, 0, 0],
    })
    paths = []
    for index, frame in enumerate([raw[:3], raw[3:]]):
        path = tmp_path / f"raw-{index}.csv"
        frame.write_csv(path)
        paths.append(path)
    manifest = build_event_pool(paths, tmp_path / "pool", machine_id="M1")
    cfg = {
        "data": {"source": "person_a_pool", "person_a": {"fixture": str(manifest)}},
        "agent": {"planner": "rules", "report_dir": str(tmp_path / "reports"),
                  "trace_dir": str(tmp_path / "traces")},
        "analytics": {"torque_expected_min": 1.5, "torque_expected_max": 2.5,
                      "drift_window_seconds": 3, "anomaly_sigma": 3.0},
    }
    return cfg, manifest, build_event_table(raw, "M1")


SCOPED = ("Average torque for head 1 for machine M1 from 2026-02-01T00:00:02 "
          "until 2026-02-01T00:00:05 for successful closures")


def test_scoped_orchestration_loads_only_requested_rows(pool, monkeypatch):
    cfg, _, original = pool
    observed = []
    adapt = event_pool_source.adapt
    def recording_adapt(frame, **kwargs):
        observed.append(frame.clone())
        return adapt(frame, **kwargs)
    monkeypatch.setattr(event_pool_source, "adapt", recording_adapt)
    def forbidden(*args, **kwargs):
        raise AssertionError("Eager whole-file read is forbidden in this source")
    monkeypatch.setattr(pl, "read_parquet", forbidden)
    answer = Orchestrator(cfg=cfg).answer(SCOPED)
    assert answer["status"] == "ok"
    assert len(observed) == 1 and observed[0].height == 2
    expected = original.filter((pl.col("head_id") == "H01") & (pl.col("status") == 0)
                               & (pl.col("ts") >= datetime(2026, 2, 1, 0, 0, 2))
                               & (pl.col("ts") < datetime(2026, 2, 1, 0, 0, 5)))
    assert_frame_equal(observed[0], expected)
    meta = answer["pool_meta"]
    assert meta["pool_total_events"] == 12
    assert meta["loaded_events"] == meta["n_events"] == 2
    assert meta["n_files"] == 2
    assert meta["selection_parameters"]["status_filter"] == "successful"
    assert answer["results"][0][1]["result"]["mean"] == 3.0
    assert "2 of 12 stored observed events" in answer["markdown"]
    step = next(s for s in answer["trace"].steps if s["kind"] == "load_pool")
    assert step["n_events"] == 2 and step["pool_total_events"] == 12


@pytest.mark.parametrize("query,tool", [
    ("Average torque for head 1", "torque_stats"),
    ("Show torque distribution with 3 bins for head 1", "torque_distribution"),
    ("Show torque trend for head 1", "torque_trend"),
    ("Find torque anomalies for head 1", "detect_torque_anomalies"),
    ("Compare torque between H01 and H02", "head_correlation"),
])
def test_all_five_tools_match_dispatch_on_the_continuous_reference(pool, query, tool):
    cfg, _, original = pool
    answer = Orchestrator(cfg=cfg).answer(query)
    assert answer["status"] == "ok"
    name, arguments = answer["plan"].calls[0]
    assert name == tool
    reference = dispatch_tool(tool, original, config=cfg, arguments=arguments)
    assert reference["ok"] is True
    assert answer["results"][0][1]["result"] == reference["result"]
    assert answer["results"][0][1]["meta"]["params"] == arguments


@pytest.mark.parametrize("params", [{"head_id": "H99"}, {"machine_id": "other"},
                                   {"status_filter": 999}, {"start": "2027-01-01"}])
def test_empty_selection_is_not_broadened(pool, params):
    cfg, _, _ = pool
    source = datasource.get_source(cfg)
    events, meta = source.load_for_plan("fixture", [("torque_stats", params)])
    assert events.is_empty() and meta["loaded_events"] == 0
    assert meta["pool_total_events"] == 12
    response = dispatch_tool("torque_stats", events, config=cfg, arguments=params)
    assert response["ok"] and response["result"]["sample_size"] == 0


def test_unknown_head_report_stays_empty(pool):
    cfg, _, _ = pool
    answer = Orchestrator(cfg=cfg).answer("Average torque for head 99")
    assert answer["status"] == "ok"
    assert "No finite torque observations matched" in answer["markdown"]
    assert answer["pool_meta"]["loaded_events"] == 0


def test_correlation_loads_both_heads_without_status_prefilter(pool):
    cfg, _, original = pool
    source = datasource.get_source(cfg)
    events, meta = source.load_for_plan("fixture", [("head_correlation", {"head_a": "H01", "head_b": "H02"})])
    assert_frame_equal(events.select(list(EVENT_SCHEMA)), original)
    assert meta["selection_parameters"] == {"head_id": ["H01", "H02"]}
    assert set(events["status"].to_list()) == {0, 3, 65}


def test_repeated_scopes_do_not_reuse_a_narrowed_frame(pool):
    cfg, _, _ = pool
    source = datasource.get_source(cfg)
    first, _ = source.load_for_plan("fixture", [("torque_stats", {"head_id": "H01"})])
    second, _ = source.load_for_plan("fixture", [("torque_stats", {"head_id": "H02"})])
    assert first.height == second.height == 6
    assert first["head_id"].unique().to_list() == ["H01"]
    assert second["head_id"].unique().to_list() == ["H02"]


def test_size_guard_stops_before_adapter_or_tool_dispatch(pool, monkeypatch):
    cfg, _, _ = pool
    cfg["data"]["max_loaded_events"] = 1
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not materialize or dispatch")
    monkeypatch.setattr(event_pool_source, "adapt", forbidden)
    # A histogram needs its rows in memory, so the materialization limit applies.
    answer = Orchestrator(cfg=cfg).answer(SCOPED.replace("Average torque", "Show torque distribution"))
    assert answer["status"] == "needs_clarification"
    assert "2 observed events" in answer["message"]
    assert answer["results"] == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0


def test_aggregate_over_the_limit_runs_lazily_without_materializing(pool, monkeypatch):
    cfg, _, _ = pool
    cfg["data"]["max_loaded_events"] = 1
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not materialize events")
    monkeypatch.setattr(event_pool_source, "adapt", forbidden)
    answer = Orchestrator(cfg=cfg).answer(SCOPED)
    assert answer["status"] == "ok"
    response = answer["results"][0][1]
    assert response["ok"] and response["result"]["sample_size"] == 2
    assert answer["pool_meta"]["materialization"] == "aggregated lazily; events not loaded into memory"
    assert "without loading the events into memory" in answer["markdown"]
    # Same numbers as the materialized path with a generous limit.
    cfg["data"]["max_loaded_events"] = 1_000_000
    monkeypatch.undo()
    eager = Orchestrator(cfg=cfg).answer(SCOPED)["results"][0][1]["result"]
    assert eager == response["result"]


@pytest.mark.parametrize("limit", [0, -1, True, "100", 1.5])
def test_invalid_materialization_limit(pool, limit):
    cfg, _, _ = pool
    cfg["data"]["max_loaded_events"] = limit
    with pytest.raises(ValueError, match="positive integer"):
        datasource.get_source(cfg)


@pytest.mark.parametrize("params", [
    {"start": "not-a-date"}, {"start": "2026-02-02", "end": "2026-02-01"},
    {"start": "2026-02-01T00:00:00Z"}, {"head_id": None}, {"machine_id": "null"},
    {"status_filter": "0"}, {"status_filter": True}, {"head": "H01"},
    {"sigma": 4},
])
def test_invalid_scope_fails_before_scanning(pool, monkeypatch, params):
    cfg, _, _ = pool
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid scope must not open the pool")
    monkeypatch.setattr(event_pool_source, "scan_event_pool", forbidden)
    with pytest.raises(ValueError):
        datasource.get_source(cfg).load_for_plan("fixture", [("torque_stats", params)])


def test_multiple_calls_are_rejected_without_dropping_one(pool):
    cfg, _, _ = pool
    with pytest.raises(ValueError, match="exactly one"):
        datasource.get_source(cfg).load_for_plan("fixture", [("torque_stats", {}), ("torque_trend", {})])


def test_missing_partition_is_visible_even_outside_selected_time(pool):
    cfg, path, _ = pool
    data = json.loads(path.read_text())
    (path.parent / data["partitions"][-1]["event_parquet"]).unlink()
    answer = Orchestrator(cfg=cfg).answer(
        "Average torque from 2026-02-01T00:00:00 until 2026-02-01T00:00:02")
    assert answer["status"] == "degraded"
    assert answer["results"] == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0


def test_unsupported_question_never_opens_manifest(pool):
    cfg, path, _ = pool
    path.unlink()
    answer = Orchestrator(cfg=cfg).answer("Average torque yesterday")
    assert answer["status"] == "needs_clarification"
    assert answer["results"] == []


def test_unknown_pool_does_not_substitute_another(pool):
    cfg, _, _ = pool
    answer = Orchestrator(cfg=cfg).answer("Average torque", pool="wrong")
    assert answer["status"] == "degraded"
    assert "Unknown Person A event pool" in answer["message"]


def test_manifest_total_mismatch_stops_analysis(pool):
    cfg, path, _ = pool
    data = json.loads(path.read_text())
    data["summary"]["observed_events"] += 1
    path.write_text(json.dumps(data))
    answer = Orchestrator(cfg=cfg).answer("Average torque")
    assert answer["status"] == "degraded"
    assert "inconsistent" in answer["message"]


def test_optional_hash_verification_rejects_changed_partition(pool):
    cfg, path, _ = pool
    data = json.loads(path.read_text())
    data["partitions"][0]["event_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    cfg["data"]["verify_pool_hashes"] = True
    answer = Orchestrator(cfg=cfg).answer("Average torque")
    assert answer["status"] == "degraded"
    assert "hash mismatch" in answer["message"]


def test_relative_paths_resolve_from_repo_root(pool, tmp_path, monkeypatch):
    cfg, path, _ = pool
    monkeypatch.setattr(datasource, "REPO_ROOT", path.parent)
    cfg["data"]["person_a"]["fixture"] = "manifest.json"
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    answer = Orchestrator(cfg=cfg).answer(SCOPED)
    assert answer["status"] == "ok"


def test_report_and_trace_delivery(pool):
    from pathlib import Path
    cfg, _, _ = pool
    engine = Orchestrator(cfg=cfg)
    answer = engine.answer(SCOPED)
    paths = engine.deliver(answer, formats=["markdown"])
    assert Path(paths["report"]).read_text() == answer["markdown"]
    saved = json.loads(Path(paths["trace"]).read_text())
    step = next(s for s in saved["steps"] if s["kind"] == "load_pool")
    assert step["selection_parameters"]["head_id"] == "H01"
    assert step["n_events"] == 2


def test_scoped_source_failure_never_retries_whole_pool(pool):
    cfg, _, _ = pool
    class BrokenSource:
        name = "broken"
        def list_pools(self): return ["fixture"]
        def load_for_plan(self, pool, calls): raise ValueError("Scoped read failed")
        def load_pool(self, pool): raise AssertionError("Must not fall back to the whole pool")
    answer = Orchestrator(cfg=cfg, source=BrokenSource()).answer("Average torque")
    assert answer["status"] == "degraded"
    assert "Scoped read failed" in answer["message"]
    assert answer["results"] == []
