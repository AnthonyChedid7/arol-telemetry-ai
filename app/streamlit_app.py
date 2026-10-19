"""Entry point for hosting the website (e.g. Streamlit Community Cloud).

It runs scripts/web_demo.py unchanged. With no private telemetry on the
server the page uses the synthetic demo dataset, and with no ANTHROPIC_API_KEY
it answers with the offline rules, so hosting it costs nothing. This folder's
requirements.txt is what the host installs.

Locally, from the repository root:  streamlit run app/streamlit_app.py
"""
from pathlib import Path
import runpy
import sys

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from module_refresh import drop_changed_project_modules, record_project_modules  # noqa: E402


@st.cache_resource
def _seen_versions() -> dict:
    """File versions of loaded project modules, kept for the server's lifetime."""
    return {}


# After a redeploy, load the new project code instead of keeping old modules.
drop_changed_project_modules(_seen_versions())
try:
    runpy.run_path(str(APP_DIR.parent / "scripts" / "web_demo.py"), run_name="__main__")
finally:
    # Also after st.stop()/st.rerun(), which end a run with an exception.
    record_project_modules(_seen_versions())
