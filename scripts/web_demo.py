"""Local browser UI for the integrated AROL demo. Run from the repo root."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import streamlit as st

# Streamlit runs this file as a script; make repository imports explicit.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.demo_agent import answer_one, resolve_manifest  # noqa: E402
from src.common import config as config_mod  # noqa: E402

DEFAULT_MANIFEST = (
    "data/event_pools/continuous-20260924-080513-930170/manifest.json"
)

st.set_page_config(page_title="AROL telemetry demo", layout="wide")
st.title("AROL telemetry analysis")
st.caption("Local, scoped analysis of observed capping-machine telemetry")

if Path.cwd().resolve() != ROOT:
    st.error(f"Start the app from the repository root: {ROOT}")
    st.stop()

# The manifest is selected by the operator, not by an untrusted web input:
# AROL_MANIFEST if set, else the presentation pool if present, else the one
# local pool under data/event_pools (resolve_manifest refuses to guess among several).
requested_manifest = os.environ.get("AROL_MANIFEST") or (
    DEFAULT_MANIFEST if (ROOT / DEFAULT_MANIFEST).is_file() else None)
try:
    manifest_path, manifest = resolve_manifest(requested_manifest, ROOT)
except ValueError as exc:
    st.error(str(exc))
    st.info("Set AROL_MANIFEST to a complete local pool manifest "
            "(for example data/event_pools/<pool>/manifest.json) and restart.")
    st.stop()

machine = manifest["summary"]["machine_id"]
st.caption(f"Selected machine: {machine} | Head diagnostic and scoped analytics (registered tools)")

# Claude understands everyday wording; the rules need the documented phrasing.
PLANNERS = {
    "Claude (understands everyday wording)": "claude",
    "Rules only (offline, exact phrasing)": "diagnostic",
}
default_planner = os.environ.get("AROL_PLANNER") or (
    "claude" if os.environ.get("ANTHROPIC_API_KEY") else "diagnostic")
planner_labels = list(PLANNERS)
planner_label = st.sidebar.radio(
    "Question understanding", planner_labels,
    index=next((i for i, label in enumerate(planner_labels)
                if PLANNERS[label] == default_planner), 1),
)
planner_kind = PLANNERS[planner_label]
if planner_kind == "claude":
    st.sidebar.caption("Your question text is sent to the Anthropic API to choose the "
                       "analysis. Telemetry data stays on this computer. Without a key "
                       "or internet, the rules answer instead.")
    st.info("Ask in your own words, for example: 'how hard were caps tightened on "
            "spindle 5 in the first hour?'. Check the 'Interpreted as' line in the report.")
else:
    st.info("For a head diagnostic, give a bounded time window in your question. "
            "The selected machine above will be used if you do not name one.")

window = (
    f"for machine {machine} from 2026-02-01T00:00:00 "
    "until 2026-02-01T12:00:00"
)
examples = {
    "Write my own question": "",
    "Head 4 diagnostic — ask for window": "Is there any problem with head 4?",
    "Head 4 diagnostic — sample hour": (
        "Is there any problem with head 4 from 2026-02-01T00:00:00 "
        "until 2026-02-01T01:00:00?"
    ),
    "Torque summary and distribution": (
        "Summarize torque and show its distribution with 10 bins "
        f"{window} for head 5 for successful closures"
    ),
    "Head 5 success rate": f"Success rate for head 5 {window}",
    "Failure rate for each head — full pool": "show me the failure rate for each head",
    "Head 5 evidence check": f"Is anything wrong with head 5 {window}",
}

chosen = st.selectbox("Choose an example", list(examples))
question = st.text_area(
    "Question",
    value=examples[chosen],
    key=f"question_{chosen}",
    height=110,
    placeholder="Ask for a machine, time window, head, and analysis.",
)

# Streamlit reruns when inputs change. Keep a displayed answer tied to the
# exact question and planner that produced it, including before Analyze is clicked.
request_key = (planner_kind, question.strip())
if st.session_state.get("result_request") != request_key:
    st.session_state.pop("result", None)

if st.button("Analyze", type="primary"):
    st.session_state.pop("result", None)
    query = question.strip()
    if not query:
        st.error("Enter a question or select an example.")
    elif len(query) > 1000:
        st.error("Keep the question under 1,000 characters.")
    else:
        # Each browser session has a separate directory and numbered questions.
        if "output_dir" not in st.session_state:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            st.session_state["output_dir"] = (
                ROOT / "data" / "web_demo_runs"
                / f"session-{stamp}-{uuid4().hex[:8]}"
            )
        sequence = st.session_state.get("sequence", 0) + 1
        st.session_state["sequence"] = sequence

        cfg = config_mod.load()
        cfg.setdefault("data", {}).update(
            source="person_a_pool", person_a={"demo": str(manifest_path)}
        )
        cfg.setdefault("agent", {})["planner"] = planner_kind

        try:
            with st.spinner("Analyzing the selected event window..."):
                response_json, status = answer_one(
                    query, cfg, st.session_state["output_dir"], sequence,
                    json_output=True, plots=True, selected_machine=machine,
                )
            payload = json.loads(response_json)
            assert status == payload["status"]
            st.session_state["result"] = payload
            st.session_state["result_request"] = request_key
        except ValueError as exc:
            if "agent.planner must be" in str(exc):
                st.error("The website has newer code than the running analysis server. "
                         "Restart Streamlit, then refresh this page and try again.")
            else:
                st.error(f"Analysis could not be completed: {type(exc).__name__}: {exc}")
        except Exception as exc:
            # Keep errors visible during this local presentation demo.
            st.error(f"Analysis could not be completed: {type(exc).__name__}: {exc}")

payload = st.session_state.get("result")
if payload is not None:
    st.caption(f"Analysis route: {payload['planner']}")
    status = payload["status"]
    if status == "ok":
        st.success("Analysis completed")
    elif status == "needs_clarification":
        st.info(payload.get("message") or "Please narrow or clarify the question.")
    else:
        st.warning(f"Analysis status: {status}")

    # deliver() appends relative PNG links to the saved Markdown file.
    # Streamlit displays their absolute local files below instead.
    saved_markdown = payload["markdown"]
    display_markdown = saved_markdown.split("\n## Figures\n", 1)[0]
    st.markdown(display_markdown)

    for index, name in enumerate(payload["artifacts"].get("figures", [])):
        image_path = Path(name)
        st.image(str(image_path), caption=image_path.stem.replace("_", " "))
        st.download_button(
            f"Download figure {index + 1}", image_path.read_bytes(),
            file_name=image_path.name, mime="image/png", key=f"figure-{index}",
        )

    trace_json = json.dumps(payload["trace"], indent=2, ensure_ascii=False)
    with st.expander("Execution trace and planned tools", expanded=True):
        st.write("Planned calls:", payload["planned_calls"])
        st.json(payload["trace"])
    with st.expander("Tool results", expanded=True):
        st.json(payload["results"])

    st.download_button(
        "Download Markdown report", saved_markdown,
        file_name="arol-report.md", mime="text/markdown", key="report",
    )
    st.download_button(
        "Download JSON trace", trace_json,
        file_name="arol-trace.json", mime="application/json", key="trace",
    )
    st.caption("Report and trace are also saved locally under data/web_demo_runs/.")
