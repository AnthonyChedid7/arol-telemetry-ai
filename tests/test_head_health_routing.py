"""A head-health question needs scope and uses evidence from registered tools."""

from datetime import datetime, timedelta

import polars as pl
import pytest

from src.agent.orchestrator import Orchestrator
from src.agent.planner import RulePlanner
from src.common import datasource, registry
from src.ingestion.event_pool import build_event_pool


QUESTION = "Is anything wrong with head 5?"
WINDOW = {
    "head_id": "H05", "machine_id": "M1",
    "start": "2026-02-01T00:00:00", "end": "2026-02-01T00:00:09",
}
SCOPED = ("Is anything wrong with head 5 for machine M1 from "
          "2026-02-01T00:00:00 until 2026-02-01T00:00:09?")


@pytest.fixture
def cfg(tmp_path):
    raw = pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1) + timedelta(seconds=i) for i in range(9)],
        "H05 Count": list(range(9)), "H06 Count": list(range(9)),
        "H07 Count": list(range(9)),
        "H05 Status": [0, 0, 0, 65, 0, 0, 0, 0, 0],
        "H06 Status": [0] * 9, "H07 Status": [0] * 9,
        "H05 AppTorque": [2., 2., 2., 3., 2., 2., 2., 2., 2.],
        "H06 AppTorque": [2.] * 9, "H07 AppTorque": [2.] * 9,
    })
    path = tmp_path / "raw.csv"
    raw.write_csv(path)
    manifest = build_event_pool([path], tmp_path / "pool", machine_id="M1")
    return {
        "data": {"source": "person_a_pool", "person_a": {"fixture": str(manifest)}},
        "agent": {"planner": "rules"},
        "analytics": {"min_n": 2, "torque_expected_min": 1.5,
                      "torque_expected_max": 2.5, "anomaly_sigma": 3.0},
    }


def test_scoped_head_health_uses_peer_population_and_focus_torque(cfg):
    answer = Orchestrator(cfg=cfg).answer(SCOPED)

    assert answer["status"] == "ok", answer["message"]
    assert answer["plan"].calls == [
        ("compare_head_success", dict(WINDOW)),
        ("detect_torque_anomalies", dict(WINDOW)),
    ]
    assert all(registry.get(name) is not None for name, _ in answer["plan"].calls)
    assert answer["pool_meta"]["loaded_events"] == 24  # all three heads retained for peers
    assert answer["pool_meta"]["selection_parameters"] == {
        key: WINDOW[key] for key in ("machine_id", "start", "end")
    }
    comparison = answer["results"][0][1]["result"]
    anomalies = answer["results"][1][1]["result"]
    assert comparison["focus_head_id"] == "H05"
    assert comparison["comparison_available"]
    assert comparison["difference_from_peer_median_pp"] < 0
    assert anomalies["sample_size"] == 8
    assert anomalies["anomaly_count"] >= 1
    assert answer["trace"].to_dict()["n_tool_calls"] == 2
    assert "Focus minus peer median" in answer["markdown"]
    assert "Configured limits" in answer["markdown"]
    assert "excludes No Load cycles" in answer["markdown"]
    assert "do not support a yes/no fault verdict" in answer["markdown"]


@pytest.mark.parametrize("query", [
    QUESTION,
    "Is anything wrong with head 5 for machine M1?",
    "Is anything wrong with head 5 from 2026-02-01T00:00:00 until 2026-02-01T00:00:09?",
])
def test_missing_head_health_scope_asks_plainly_without_loading(cfg, monkeypatch, query):
    engine = Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.source, "load_for_plan", lambda *args: pytest.fail("must not load data"))

    answer = engine.answer(query)

    assert answer["status"] == "needs_clarification"
    assert answer["plan"].calls == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0
    assert "machine" in answer["message"] and "time window" in answer["message"]
    assert "anomaly_heads" not in answer["message"]


@pytest.mark.parametrize("query", [
    "Which head is worst?", "Any downtime?", "What is the machine speed?",
])
def test_generic_phrases_never_plan_unregistered_tools(query):
    plan = RulePlanner().plan(query, {})
    assert plan.ambiguous and not plan.calls
    assert "for machine M1 from" in plan.clarification


@pytest.mark.parametrize("bad_anomaly", [
    dict(WINDOW, head_id="H06"),
    dict(WINDOW, status_filter="successful"),
])
def test_head_health_pair_rejects_mismatched_scope_before_read(cfg, bad_anomaly):
    source = datasource.get_source(cfg)
    with pytest.raises(ValueError, match="same head, machine and time window"):
        source.load_for_plan("fixture", [
            ("compare_head_success", dict(WINDOW)),
            ("detect_torque_anomalies", bad_anomaly),
        ])
