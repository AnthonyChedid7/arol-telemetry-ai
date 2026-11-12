"""The website's data sources, run through Streamlit's own test harness."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.common import pool_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "scripts" / "web_demo.py")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("AROL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("AROL_MANIFEST", raising=False)

    def run(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        at = AppTest.from_file(SCRIPT, default_timeout=120)
        return at.run()
    return run


def _sidebar_text(at):
    return " ".join(str(m.value) for m in at.sidebar.markdown)


def test_without_a_store_the_demo_and_session_sources_are_offered(app, monkeypatch, tmp_path):
    monkeypatch.setattr("src.common.config.DOTENV", tmp_path / "none.env")
    at = app(AROL_DEMO="0")
    assert not at.exception
    labels = at.sidebar.radio(key="data_source").options
    assert "Demo data (synthetic)" in labels and "My own CSV files (this session only)" in labels
    assert not any("shared" in label for label in labels)


def test_stored_source_shows_the_shared_days_and_refresh(app, tmp_path, days_store):
    at = app(AROL_STORE_DIR=str(days_store))
    assert not at.exception
    assert at.sidebar.radio(key="data_source").value == "stored"
    assert "2 files" in _sidebar_text(at)
    assert any(b.label == "Check for new days" for b in at.sidebar.button)


def test_session_source_waits_for_files_without_touching_the_store(app, days_store):
    at = app(AROL_STORE_DIR=str(days_store))
    before = (days_store / "manifest.json").read_bytes()
    at.sidebar.radio(key="data_source").set_value("session").run()
    assert not at.exception
    assert any("Choose one or more daily CSV files" in str(i.value) for i in at.info)
    assert (days_store / "manifest.json").read_bytes() == before


@pytest.fixture
def days_store(tmp_path):
    """A store folder holding two small days for machine M1."""
    from datetime import datetime, timedelta

    import polars as pl
    rows = 8
    frame = pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1, 20) + timedelta(hours=i) for i in range(rows)],
        "H01 Count": [float(10 + i) for i in range(rows)],
        "H01 AppTorque": [1.0] * rows, "H01 Status": [0] * rows,
    })
    store = pool_store.LocalStore(tmp_path / "store")
    for index, chunk in enumerate((frame[:4], frame[4:]), start=1):
        path = tmp_path / f"telemetry_M1_2026-02-0{index}.csv"
        chunk.write_csv(path)
        pool_store.add_csv(store, tmp_path / "mirror", path)
    return store.root


def test_sidebar_shows_the_dates_covered_and_no_key_box_when_a_key_is_set(app, days_store):
    at = app(AROL_STORE_DIR=str(days_store), ANTHROPIC_API_KEY="sk-ant-test-not-real")
    assert not at.exception
    text = _sidebar_text(at)
    assert "2026-02-01 20:00 to 2026-02-02 03:00" in text and "2 calendar days with data" in text
    assert "Claude (optional)" not in text and not at.sidebar.text_input


def test_local_model_option_only_when_ollama_answers(app, days_store, monkeypatch):
    import json as json_mod
    import urllib.request

    import streamlit as st

    class Reply:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def no_ollama(request, timeout=0):
        raise OSError("connection refused")

    st.cache_data.clear()
    monkeypatch.setattr(urllib.request, "urlopen", no_ollama)
    at = app(AROL_STORE_DIR=str(days_store))
    assert not at.exception
    assert at.sidebar.radio(key="planner").options == ["Claude (Anthropic API)"]

    models = {"models": [{"name": "qwen2.5-coder:14b", "size": 9_000_000_000},
                         {"name": "llama3.2:3b", "size": 2_000_000_000}]}
    st.cache_data.clear()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=0: Reply(json_mod.dumps(models).encode()))
    at = app(AROL_STORE_DIR=str(days_store))
    label = "Local model (Ollama, on this computer)"
    assert label in at.sidebar.radio(key="planner").options
    at.sidebar.radio(key="planner").set_value(label).run()
    assert not at.exception
    box = at.sidebar.selectbox(key="ollama_model")
    assert box.options == ["llama3.2:3b", "qwen2.5-coder:14b"] and box.value == "llama3.2:3b"
    st.cache_data.clear()
