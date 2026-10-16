"""Branded UI, bring-your-own API key, and the power test's independent checks."""
import os
from pathlib import Path

import pytest

from src.common import config as config_mod
from src.interface import brand
from src.interface.dashboard import key_figures
from src.interface.export import to_html


# --- API key handling: anyone can use their own key, nobody can commit one -----------

def test_dotenv_sets_only_allowed_keys_and_never_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test\nPATH=evil\n# comment\nAROL_DEMO='1'\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AROL_DEMO", "0")
    applied = config_mod.load_dotenv(env)
    assert applied == ["ANTHROPIC_API_KEY"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert os.environ["AROL_DEMO"] == "0"  # an existing value wins
    assert os.environ["PATH"] != "evil"


def test_config_files_can_never_hold_a_key(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("data: {pools: {}}\nagent:\n  claude:\n    api_key: sk-ant-x\n", encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="API key"):
        config_mod.load(bad)


def test_session_key_reaches_the_client_only_in_memory(monkeypatch):
    import anthropic
    from src.agent.claude_planner import ClaudePlanner

    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: seen.update(kwargs) or object())
    planner = ClaudePlanner({"agent": {"claude": {"session_api_key": "sk-ant-visitor"}}})
    planner._client_or_create()
    assert seen["api_key"] == "sk-ant-visitor"
    without = ClaudePlanner({"agent": {"claude": {}}})
    seen.clear()
    without._client_or_create()
    assert "api_key" not in seen  # falls back to ANTHROPIC_API_KEY / .env


# --- key figures and the branded report ---------------------------------------------

def _envelope(result):
    return {"ok": True, "result": result, "meta": {}}


def test_key_figures_and_tones():
    compare = {"focus_head_id": "H07", "focus": {"success_rate_cap_present": 0.97,
               "n_success_cap_present": 970, "n_cap_present": 1000},
               "peer_median_success_rate": 0.9998, "n_eligible_peers": 35,
               "difference_from_peer_median_pp": -2.98}
    figures = key_figures([("compare_head_success", _envelope(compare))])
    assert [f["label"] for f in figures] == ["H07 success", "Peer median", "Difference"]
    assert figures[0]["value"] == "97.00%" and figures[0]["tone"] == "bad"
    trend = {"drift_detected": True, "drift_direction": "upward", "drift_threshold_nm_per_hour": 0.05,
             "drift_slope_nm_per_hour": 0.1, "sample_size": 10, "window_seconds": 3600}
    assert key_figures([("torque_trend", _envelope(trend))])[0]["tone"] == "warn"
    assert key_figures([("torque_stats", {"ok": False, "result": None})]) == []


def test_branded_html_is_self_contained(tmp_path):
    png = tmp_path / "chart.png"
    png.write_bytes(brand.LOGO.read_bytes())
    html = to_html("# Telemetry report\n\n## 1. Goal\n\nX\n\n## 5. Confidence and limits\n\n- ok",
                   figures=[("chart", png)], embed=True,
                   key_figures=[dict(label="Mean torque", value="2.000 Nm", note="", tone="ok")])
    assert brand.DISCLAIMER in html
    assert html.count("data:image/png;base64,") == 2  # logo + embedded chart
    assert "Mean torque" in html and html.index("kpi-grid") < html.index("1. Goal")


# --- the power test's independent oracles agree with the tools ----------------------

@pytest.fixture(scope="module")
def demo_pool(tmp_path_factory):
    from scripts.build_demo_pool import build
    from scripts.showcase import Pool
    return Pool(build(tmp_path_factory.mktemp("demo")), "demo")


def test_showcase_accuracy_on_demo_with_rules(demo_pool, monkeypatch):
    from scripts import showcase
    monkeypatch.chdir(showcase.ROOT)
    rows = showcase.run_accuracy(demo_pool, ["rules"])
    failed = [(r["title"], r["detail"]) for r in rows if not (r["understood"] and r["verified"])]
    assert len(rows) == 12 and not failed


def test_showcase_robustness_rules_never_invent(demo_pool, monkeypatch):
    from scripts import showcase
    monkeypatch.chdir(showcase.ROOT)
    rows = showcase.run_robustness(demo_pool, "rules")
    assert all(r["safe"] for r in rows), [r for r in rows if not r["safe"]]
    html = showcase.scorecard(dict(created="2026-01-01T00:00:00", planners=["rules"],
                                   environment=dict(python="3", platform="x"),
                                   accuracy=[], robustness=rows, scale=[]))
    assert "Power Test Scorecard" in html and brand.DISCLAIMER in html
