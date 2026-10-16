"""Exercise the orchestrator with actual Person A event Parquet."""

import json
from datetime import datetime, timedelta

import polars as pl
import pytest

from src.agent.orchestrator import Orchestrator
from src.agent.planner import Plan
from src.ingestion.event_table_polars import (
    build_event_table,
    write_event_table_parquet,
)


@pytest.fixture
def setup(tmp_path):
    start = datetime(2026, 2, 1, 10)
    raw = pl.DataFrame({
        "timestamp": [start + timedelta(seconds=i) for i in range(8)],
        "H01 Count": [100, 101, 103, 0, 104, 105, 106, 107],
        "H01 AppTorque": [9000.0, 1.0, 9000.0, 9000.0, 9000.0, 3.0, 5.0, 7.0],
        "H01 Status": [0, 0, 0, 0, 0, 3, 64, 999],
    })
    events = build_event_table(raw, machine_id="M1")
    path = tmp_path / "events.parquet"
    write_event_table_parquet(events, path)
    cfg = {
        "data": {
            "source": "person_a",
            "person_a": {"fixture": str(path)},
        },
        "agent": {
            "planner": "rules",
            "report_dir": str(tmp_path / "reports"),
            "trace_dir": str(tmp_path / "logs"),
        },
    }
    return cfg, path


class FixedPlanner:
    name = "test-fixed"

    def __init__(self, params):
        self.params = params

    def plan(self, query, context):
        return Plan(
            goal="Exercise dispatch without changing the requested scope.",
            calls=[("torque_stats", self.params)],
            filters=dict(self.params),
        )


def test_orchestrator_answers_from_actual_event_parquet(setup):
    cfg, _ = setup
    answer = Orchestrator(cfg=cfg).answer("Average torque", pool="fixture")
    assert answer["status"] == "ok"
    response = answer["results"][0][1]
    # The status-3 event (No Load, 3.0 Nm) is not a closure and is excluded by default.
    assert response["result"]["sample_size"] == 3
    assert response["result"]["mean"] == pytest.approx(13 / 3)
    assert "Mean: **4.33333 Nm**" in answer["markdown"]
    assert "Events analysed: closures, excluding No Load cycles (default)" in answer["markdown"]
    assert answer["pool_meta"]["n_events"] == 4
    assert answer["trace"].to_dict()["n_tool_calls"] == 1


def test_report_and_trace_are_saved(setup):
    cfg, _ = setup
    engine = Orchestrator(cfg=cfg)
    answer = engine.answer("Average torque", pool="fixture")
    paths = engine.deliver(answer, formats=["markdown"])

    from pathlib import Path
    assert Path(paths["report"]).read_text(encoding="utf-8") == answer["markdown"]
    trace = json.loads(Path(paths["trace"]).read_text(encoding="utf-8"))
    assert trace["n_tool_calls"] == 1
    call = next(step for step in trace["steps"] if step["kind"] == "tool_call")
    assert call["tool"] == "torque_stats"
    assert call["n"] == 3
    assert call["ok"] is True


def test_unsupported_scope_stops_before_loading(setup):
    cfg, path = setup
    path.unlink()
    answer = Orchestrator(cfg=cfg).answer(
        "Average torque for head 5 yesterday", pool="fixture"
    )
    assert answer["status"] == "needs_clarification"
    assert answer["results"] == []
    assert not any(
        step["kind"] == "load_pool" for step in answer["trace"].steps
    )


def test_unknown_pool_does_not_substitute_another_pool(setup):
    cfg, _ = setup
    answer = Orchestrator(cfg=cfg).answer("Average torque", pool="wrong")
    assert answer["status"] == "degraded"
    assert answer["results"] == []
    assert "Unknown Person A event pool" in answer["message"]


def test_missing_data_is_reported(setup):
    cfg, path = setup
    path.unlink()
    answer = Orchestrator(cfg=cfg).answer("Average torque", pool="fixture")
    assert answer["status"] == "degraded"
    assert answer["results"] == []
    assert "Event Parquet not found" in answer["message"]


def test_malformed_filter_is_rejected_before_load(setup):
    cfg, _ = setup
    engine = Orchestrator(cfg=cfg, planner=FixedPlanner({"start": "not-a-date"}))
    answer = engine.answer("Scoped request", pool="fixture")
    assert answer["status"] == "needs_clarification"
    assert not answer["results"]
    assert answer["trace"].to_dict()["n_tool_calls"] == 0
    assert not any(step["kind"] == "retry" for step in answer["trace"].steps)
    assert "Invalid isoformat string" in answer["message"]


def test_empty_status_selection_stays_empty(setup):
    cfg, _ = setup
    engine = Orchestrator(
        cfg=cfg, planner=FixedPlanner({"status_filter": 123456})
    )
    answer = engine.answer("Exact status request", pool="fixture")
    response = answer["results"][0][1]
    assert response["ok"] is True
    assert response["result"]["sample_size"] == 0
    assert response["meta"]["params"] == {"status_filter": 123456}
    assert "No finite torque observations matched" in answer["markdown"]
    assert answer["trace"].to_dict()["n_tool_calls"] == 1


def test_multiple_pools_require_explicit_selection(setup):
    cfg, path = setup
    cfg["data"]["person_a"]["another"] = str(path)
    answer = Orchestrator(cfg=cfg).answer("Average torque")
    assert answer["status"] == "needs_clarification"
    assert "Select one event pool" in answer["message"]
    assert answer["results"] == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0
