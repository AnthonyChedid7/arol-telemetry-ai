"""Entry point for hosting the website (e.g. Streamlit Community Cloud).

It runs scripts/web_demo.py unchanged. With no private telemetry on the
server the page uses the synthetic demo dataset, and with no ANTHROPIC_API_KEY
it answers with the offline rules, so hosting it costs nothing. This folder's
requirements.txt is what the host installs.

Locally, from the repository root:  streamlit run app/streamlit_app.py
"""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "web_demo.py"),
               run_name="__main__")
