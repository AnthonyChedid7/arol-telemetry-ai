"""Suite-wide guard: tests never reach a live model API.

With ANTHROPIC_API_KEY set (as on a demo machine) the website defaults to the
Claude planner, so a UI test would otherwise make a paid, network-dependent
request. Live checks belong in scripts/verify_claude_*.py, run deliberately.
"""
import pytest

_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                "ANTHROPIC_BASE_URL", "AROL_PLANNER")


@pytest.fixture(autouse=True)
def _no_live_model_requests(monkeypatch):
    for name in _CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
