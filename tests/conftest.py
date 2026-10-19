"""Suite-wide guard: tests never reach a live model API.

With ANTHROPIC_API_KEY set (as on a demo machine) the website defaults to the
Claude planner, so a UI test would otherwise make a paid, network-dependent
request. Live checks belong in scripts/verify_claude_*.py, run deliberately.
"""
import pytest

_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                "ANTHROPIC_BASE_URL", "AROL_PLANNER", "SUPABASE_URL", "SUPABASE_KEY",
                "AROL_STORAGE_BUCKET", "AROL_STORE_DIR", "AROL_UPLOAD_PASSWORD")


@pytest.fixture(autouse=True)
def _no_live_model_requests(monkeypatch, tmp_path):
    for name in _CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AROL_DEMO", raising=False)
    # A developer's own .env must not leak a key (or a demo switch) into tests.
    from src.common import config
    monkeypatch.setattr(config, "DOTENV", tmp_path / "no.env")
