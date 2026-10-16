"""The web diagnostic uses selected-machine scope and registered evidence."""

from datetime import datetime, timedelta
import json
from pathlib import Path

import polars as pl
import pytest

from scripts.demo_agent import answer_one
from src.agent.orchestrator import Orchestrator
from src.agent.planner import LLMPlanner, get_planner
from src.ingestion.event_pool import build_event_pool


@pytest.fixture
def cfg(tmp_path):
    raw = pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1) + timedelta(seconds=i) for i in range(9)],
        "H04 Count": list(range(9)), "H05 Count": list(range(9)),
        "H06 Count": list(range(9)),
        "H04 Status": [0, 0, 0, 65, 0, 0, 0, 0, 0],
        "H05 Status": [0] * 9, "H06 Status": [0] * 9,
        "H04 AppTorque": [2., 2., 2., 0., 2., 2., 2., 2., 2.],
        "H05 AppTorque": [2.] * 9, "H06 AppTorque": [2.] * 9,
    })
    path = tmp_path / "raw.csv"
    raw.write_csv(path)
    manifest = build_event_pool([path], tmp_path / "pool", machine_id="M1")
    return {
        "data": {"source": "person_a_pool", "person_a": {"demo": str(manifest)}},
        "agent": {"planner": "diagnostic"},
        "analytics": {"min_n": 2, "torque_expected_min": 1.5,
                      "torque_expected_max": 2.5, "anomaly_sigma": 3.0},
    }


QUESTION = "Is there any problem with head 4?"
SCOPED = ("Is there any problem with head 4 from 2026-02-01T00:00:00 "
          "until 2026-02-01T00:00:09?")


def test_missing_window_asks_before_loading_even_with_selected_machine(cfg, monkeypatch):
    engine = Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.source, "load_for_plan", lambda *args: pytest.fail("loaded data"))

    answer = engine.answer(QUESTION, pool="demo", selected_machine="M1")

    assert answer["status"] == "needs_clarification"
    assert "bounded time window" in answer["message"]
    assert "selected machine M1" in answer["message"]
    assert answer["plan"].calls == []
    assert answer["trace"].to_dict()["n_tool_calls"] == 0


def test_scoped_head4_uses_selected_machine_and_explains_both_results(cfg):
    answer = Orchestrator(cfg=cfg).answer(SCOPED, pool="demo", selected_machine="M1")

    assert answer["status"] == "ok", answer["message"]
    assert [name for name, _ in answer["plan"].calls] == [
        "compare_head_success", "detect_torque_anomalies"]
    assert all(args == {"head_id": "H04", "machine_id": "M1",
                        "start": "2026-02-01T00:00:00", "end": "2026-02-01T00:00:09"}
               for _, args in answer["plan"].calls)
    assert answer["trace"].to_dict()["n_tool_calls"] == 2
    assert answer["markdown"].startswith("## Diagnostic answer")
    assert "confirmed cap-present successes" in answer["markdown"]
    assert "eligible other heads" in answer["markdown"]
    assert "torque check flagged" in answer["markdown"]
    assert "No Load" in answer["markdown"]
    assert "cannot establish a yes/no fault verdict" in answer["markdown"]


@pytest.mark.parametrize("query", [
    "Is there any problem with head 4 for machine M2 from 2026-02-01T00:00:00 until 2026-02-01T00:00:09?",
    "Is there any problem with head 4 from 2026-02-01T00:00:00 until 2026-02-01T00:00:09 for successful closures?",
])
def test_conflicting_scope_stops_before_loading(cfg, monkeypatch, query):
    engine = Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.source, "load_for_plan", lambda *args: pytest.fail("loaded data"))

    answer = engine.answer(query, pool="demo", selected_machine="M1")

    assert answer["status"] == "needs_clarification"
    assert answer["trace"].to_dict()["n_tool_calls"] == 0


def test_web_answer_bridge_exposes_trace_and_tool_results(cfg, tmp_path):
    payload, status = answer_one(
        SCOPED, cfg, tmp_path / "runs", 1, json_output=True,
        selected_machine="M1",
    )
    result = json.loads(payload)

    assert status == "ok"
    assert result["planner"] == "diagnostic"
    assert result["trace"]["n_tool_calls"] == 2
    assert [name for name, _ in result["results"]] == [
        "compare_head_success", "detect_torque_anomalies"]
    assert result["artifacts"]["trace"]


def test_experimental_llm_still_uses_strict_torque_gate(monkeypatch):
    planner = get_planner({"agent": {"planner": "llm", "llm": {"fallback_to_rules": False}}})
    assert isinstance(planner, LLMPlanner)
    monkeypatch.setattr(planner, "_chat", lambda *args: pytest.fail("contacted model"))

    plan = planner.plan(QUESTION, {})

    assert plan.ambiguous and not plan.calls
    assert "five verified torque analyses" in plan.clarification


def test_web_page_asks_for_window_then_shows_evidence(cfg, tmp_path, monkeypatch):
    # The website is optional (requirements-web.txt); skip rather than fail without it.
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

    monkeypatch.setenv("AROL_MANIFEST", cfg["data"]["person_a"]["demo"])
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "scripts/web_demo.py",
        default_timeout=30,
    ).run()
    assert not app.exception
    assert "Selected machine: M1" in app.caption[1].value
    assert "Head diagnostic and scoped analytics" in app.caption[1].value

    app.session_state["output_dir"] = Path(tmp_path) / "web-runs"
    app.text_area[0].input(QUESTION).run()
    app.button[0].click().run()
    assert not app.exception
    assert "bounded time window" in app.info[-1].value
    assert not app.session_state["result"]["results"]

    app.text_area[0].input(SCOPED).run()
    app.button[0].click().run()
    assert not app.exception
    assert app.success[0].value == "Analysis completed"
    assert app.markdown[0].value.startswith("## Diagnostic answer")
    assert "on closures excluding No Load cycles by default" in app.markdown[0].value
    assert "across all observed statuses" not in app.markdown[0].value
    assert [item.label for item in app.expander] == [
        "Execution trace and planned tools", "Tool results"]
    assert app.session_state["result"]["trace"]["n_tool_calls"] == 2

    # An answer must disappear as soon as its question or planner changes.
    app.text_area[0].input("Is head 4 working properly?").run()
    assert "result" not in app.session_state
    assert not app.success

    app.text_area[0].input(SCOPED).run()
    app.button[0].click().run()
    assert "result" in app.session_state
    app.sidebar.radio[0].set_value("Claude (understands everyday wording)").run()
    assert "result" not in app.session_state
    assert not app.success
