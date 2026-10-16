"""The synthetic demo pool lets anyone run the project without private data."""
import json
from pathlib import Path

import pytest

from scripts import build_demo_pool, demo_agent
from src.agent.orchestrator import Orchestrator
from src.common import config as config_mod


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return build_demo_pool.build(tmp_path_factory.mktemp("demo"))


def _ask(manifest, question):
    cfg = config_mod.load()
    cfg.setdefault("data", {}).update(source="person_a_pool", person_a={"demo": str(manifest)})
    cfg["agent"]["planner"] = "rules"
    return Orchestrator(cfg=cfg).answer(question, pool="demo")


def test_demo_pool_is_a_complete_realistic_day(demo):
    summary = json.loads(Path(demo).read_text())["summary"]
    assert summary["machine_id"] == "DEMO01" and summary["heads"] == 36
    assert summary["input_rows"] == 86_400 and summary["files"] == 2
    assert 900_000 < summary["observed_events"] < 1_000_000  # close to a real day
    assert summary["jump_rows"] == 0 and summary["decreases"] == 0


def test_demo_pool_is_deterministic(demo, tmp_path):
    again = build_demo_pool.build(tmp_path / "again")
    assert json.loads(Path(again).read_text())["summary"] == json.loads(Path(demo).read_text())["summary"]


def test_planted_weak_head_is_found(demo):
    answer = _ask(demo, "Rank heads by cap-present success rate for machine DEMO01 on 2026-02-01")
    assert answer["status"] == "ok"
    assert answer["results"][0][1]["result"]["ranked_heads"][0]["head_id"] == "H07"


def test_planted_drift_and_idle_are_found(demo):
    trend = _ask(demo, "Show torque trend for head 12 for machine DEMO01 "
                       "from 2026-02-01T06:00:00 until 2026-02-01T12:00:00")
    result = trend["results"][0][1]["result"]
    assert result["drift_detected"] and result["drift_direction"] == "upward"
    idle = _ask(demo, "Machine idle for machine DEMO01 from 2026-02-01T00:00:00 until 2026-02-01T06:00:00")
    periods = idle["results"][0][1]["result"]["idle_periods"]
    assert len(periods) == 1 and periods[0]["start"].startswith("2026-02-01T01:00")


def test_planted_over_limit_closures_are_flagged(demo):
    answer = _ask(demo, "Find torque anomalies for head 3 for machine DEMO01 on 2026-02-01")
    high = [a["ts"][11:16] for a in answer["results"][0][1]["result"]["anomalies"] if a["torque"] >= 2.5]
    assert high == ["10:15", "14:30", "18:45"]


def test_cli_demo_flag(monkeypatch, tmp_path, capsys, demo):
    monkeypatch.setattr(build_demo_pool, "DEMO_DIR", Path(demo).parents[1])
    monkeypatch.chdir(demo_agent.ROOT)
    code = demo_agent.main(["--demo", "--output", str(tmp_path), "--json",
                            "--question", "show me the failure rate for each head"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_website_uses_demo_data_without_private_pool(monkeypatch, demo):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    monkeypatch.setattr(build_demo_pool, "DEMO_DIR", Path(demo).parents[1])
    monkeypatch.setenv("AROL_DEMO", "1")
    app = AppTest.from_file(str(Path(demo_agent.ROOT) / "app" / "streamlit_app.py"),
                            default_timeout=120).run()
    assert not app.exception, app.exception
    assert any("Demo data" in w.value for w in app.warning)
    assert any("Selected machine: DEMO01" in c.value for c in app.caption)
    # Without a key the public page answers with the rules.
    assert app.sidebar.radio[0].value.startswith("Rules")
