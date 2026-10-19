"""Entry point for hosting the website (e.g. Streamlit Community Cloud).

It runs scripts/web_demo.py unchanged. With no private telemetry on the
server the page uses the synthetic demo dataset, and with no ANTHROPIC_API_KEY
it answers with the offline rules, so hosting it costs nothing. This folder's
requirements.txt is what the host installs.

Locally, from the repository root:  streamlit run app/streamlit_app.py
"""
from pathlib import Path
import runpy

import streamlit as st

APP_DIR = Path(__file__).resolve().parent


@st.cache_resource
def _seen_versions() -> dict:
    """Fallback file versions of loaded project modules, kept for the server's lifetime."""
    return {}


# Executed from its file on every run (not imported once), so a redeploy can
# also update this refresh logic without a restart.
refresh = runpy.run_path(str(APP_DIR / "module_refresh.py"))
refresh["install_import_stamps"]()
# After a redeploy, load the new project code instead of keeping old modules.
refresh["drop_changed_project_modules"](_seen_versions())
try:
    runpy.run_path(str(APP_DIR.parent / "scripts" / "web_demo.py"), run_name="__main__")
finally:
    # Also after st.stop()/st.rerun(), which end a run with an exception.
    refresh["record_project_modules"](_seen_versions())
