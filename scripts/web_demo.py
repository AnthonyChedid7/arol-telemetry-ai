"""Browser UI for the integrated AROL telemetry agent. Run from the repo root.

    streamlit run app/streamlit_app.py      (or scripts/web_demo.py)

Branded engineering dashboard: ask a question, see key figures, charts, the
full traceable report, and download a styled HTML report. Works for anyone:
without private telemetry it uses the synthetic demo pool, and Claude needs
either ANTHROPIC_API_KEY (environment or .env) or a key typed into the sidebar
for this browser session only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

import streamlit as st

# Streamlit runs this file as a script; make repository imports explicit.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.demo_agent import answer_one, resolve_manifest  # noqa: E402
from src.common import config as config_mod  # noqa: E402
from src.common import pool_store  # noqa: E402
from src.interface import brand  # noqa: E402
from src.interface.dashboard import key_figures  # noqa: E402

DEFAULT_MANIFEST = (
    "data/event_pools/continuous-20260924-080513-930170/manifest.json"
)

config_mod.load_dotenv()  # a key or pool in the project's ignored .env file
# On a host (e.g. Streamlit Community Cloud) the owner's key, storage and upload
# password live in the app's Secrets settings, never in the repository. The
# environment still wins.
for _name in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY",
              "AROL_STORAGE_BUCKET", "AROL_UPLOAD_PASSWORD"):
    if os.environ.get(_name):
        continue
    try:
        _hosted = str(st.secrets.get(_name, "")).strip()
    except Exception:  # no secrets.toml locally
        break
    if _hosted:
        os.environ[_name] = _hosted
st.set_page_config(page_title="AROL Telemetry Analysis", layout="wide",
                   page_icon=str(brand.LOGO) if brand.LOGO.is_file() else None)
st.markdown(f"<style>{brand.HEADER_CSS}"
            ".block-container{padding-top:1.4rem;max-width:1200px}"
            "section[data-testid='stSidebar']{background:#0C2340}"
            "section[data-testid='stSidebar'] h3,section[data-testid='stSidebar'] p,"
            "section[data-testid='stSidebar'] label,section[data-testid='stSidebar'] small,"
            "section[data-testid='stSidebar'] [data-testid='stCaptionContainer']{color:#E8EEF5}"
            "section[data-testid='stSidebar'] code{color:#F28C28;background:rgba(255,255,255,.08)}"
            "section[data-testid='stSidebar'] [data-testid='stAlert'] p{color:#1B2430}"
            "section[data-testid='stSidebar'] [data-testid='stAlert']{background:#F4F6F9;"
            "border-radius:.5rem}"
            "</style>", unsafe_allow_html=True)
st.markdown(brand.header_html(
    "Telemetry Analysis Console",
    "Ask about capping-machine telemetry and get a traceable engineering report"),
    unsafe_allow_html=True)

if Path.cwd().resolve() != ROOT:
    st.error(f"Start the app from the repository root: {ROOT}")
    st.stop()


@st.cache_resource(show_spinner="Building the synthetic demo dataset (first start only)...")
def _demo_manifest() -> str:
    from scripts.build_demo_pool import build
    return str(build())


STORED_DIR = ROOT / "data" / "stored_pool"
store = pool_store.store_from_env()


@st.cache_resource(show_spinner="Loading the uploaded telemetry...")
def _stored_manifest() -> str | None:
    path = pool_store.pull(store, STORED_DIR)
    return None if path is None else str(path)


# The manifest is selected by the operator, not by an untrusted web input:
# AROL_MANIFEST if set, else the uploaded pool when a store is configured
# (SUPABASE_* or AROL_STORE_DIR), else the presentation pool if present, else
# the one local pool under data/event_pools (resolve_manifest refuses to guess
# among several). With no pool at all (a fresh clone, the hosted demo, or an
# empty store), or with AROL_DEMO=1, the page uses the synthetic demo pool.
forced_demo = os.environ.get("AROL_DEMO", "").strip() == "1"
use_store = store is not None and not forced_demo and not os.environ.get("AROL_MANIFEST")
stored_manifest = store_error = None
if use_store:
    try:
        stored_manifest = _stored_manifest()
    except Exception as exc:
        store_error = f"{type(exc).__name__}: {exc}"
uploaded_mode = stored_manifest is not None
local_pools = sorted((ROOT / "data/event_pools").glob("*/manifest.json"))
requested_manifest = os.environ.get("AROL_MANIFEST") or stored_manifest or (
    DEFAULT_MANIFEST if (ROOT / DEFAULT_MANIFEST).is_file() else None)
demo_mode = forced_demo or (use_store and not uploaded_mode) or (
    requested_manifest is None and not local_pools)
try:
    manifest_path, manifest = resolve_manifest(
        _demo_manifest() if demo_mode else requested_manifest, ROOT)
except ValueError as exc:
    st.error(str(exc))
    st.info("Set AROL_MANIFEST to a complete local pool manifest "
            "(for example data/event_pools/<pool>/manifest.json), or AROL_DEMO=1 "
            "for the synthetic demo data, and restart.")
    st.stop()

summary = manifest["summary"]
machine = summary["machine_id"]

# --- sidebar: data source, Claude key, planner --------------------------------
with st.sidebar:
    if brand.LOGO.is_file():
        st.image(str(brand.LOGO), width=150)
    st.markdown("### Data source")
    source_label = ("Synthetic demo data" if demo_mode else
                    f"Uploaded telemetry ({summary.get('files', 0)} "
                    f"file{'' if summary.get('files') == 1 else 's'})" if uploaded_mode else
                    "Private event pool")
    st.markdown(
        f"**{source_label}**  \n"
        f"Machine `{machine}`  \n"
        f"{(summary.get('ts_min') or '?')[:16]} to {(summary.get('ts_max') or '?')[:16]} UTC  \n"
        f"{summary.get('observed_events', 0):,} observed events, {summary.get('heads', '?')} heads")
    if store_error:
        st.error(f"The stored telemetry could not be loaded, so demo data is shown. {store_error}")
    if use_store:
        upload_password = os.environ.get("AROL_UPLOAD_PASSWORD", "")
        with st.expander("Add a day of telemetry (CSV)"):
            notice = st.session_state.pop("upload_notice", None)
            if notice:
                st.success(notice)
            if not upload_password:
                st.caption("Uploads are switched off. Set AROL_UPLOAD_PASSWORD in the app's "
                           "secrets to allow them.")
            else:
                st.caption(f"Each file is checked, converted and kept in {store.describe()}. "
                           "Use the AROL export name (telemetry_<machine>_<YYYY-MM-DD>.csv) "
                           "and add days in date order.")
                entered = st.text_input("Upload password", type="password", key="upload_password")
                round_ = st.session_state.get("upload_round", 0)
                upload = st.file_uploader("Daily telemetry CSV", type=["csv"], key=f"upload_{round_}")
                if st.button("Add to the data", disabled=upload is None):
                    if not hmac.compare_digest(entered.encode(), upload_password.encode()):
                        st.error("Wrong upload password.")
                    else:
                        try:
                            with st.spinner(f"Checking and converting {upload.name}..."):
                                entry = pool_store.add_uploaded_csv(
                                    store, STORED_DIR, upload.name, upload.getvalue())
                        except Exception as exc:
                            st.error(f"Not added. {exc}")
                        else:
                            _stored_manifest.clear()
                            st.session_state["upload_round"] = round_ + 1
                            span = (f"{entry['ts_min'][:16]} to {entry['ts_max'][:16]} UTC"
                                    if entry.get("ts_min") else "no rows")
                            st.session_state["upload_notice"] = (
                                f"Added {entry['input_file']} ({span}): "
                                f"{entry['observed_events']:,} events.")
                            st.rerun()
    st.divider()
    st.markdown("### Claude (optional)")
    env_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if env_key:
        st.caption("Using the Anthropic key configured for this app "
                   "(environment variable, the project's .env file or the host's secrets).")
    else:
        typed = st.text_input(
            "Your Anthropic API key", type="password", key="session_api_key",
            placeholder="sk-ant-...",
            help="Lets Claude understand everyday wording. Kept only in this browser "
                 "session: never saved, logged or written to reports.")
        if typed and not typed.strip().startswith("sk-ant-"):
            st.warning("That does not look like an Anthropic key (they start with 'sk-ant-').")
        st.caption("No key? Everything works with the offline rules and the example "
                   "questions. Keys: console.anthropic.com. You pay for your own "
                   "questions (well under one cent each).")
    session_key = (st.session_state.get("session_api_key") or "").strip()
    key_available = env_key or session_key.startswith("sk-ant-")
    st.divider()
    PLANNERS = {
        "Claude (understands everyday wording)": "claude",
        "Rules only (offline, exact phrasing)": "diagnostic",
    }
    default_planner = os.environ.get("AROL_PLANNER") or ("claude" if key_available else "diagnostic")
    planner_labels = list(PLANNERS)
    planner_label = st.radio(
        "Question understanding", planner_labels,
        index=next((i for i, label in enumerate(planner_labels)
                    if PLANNERS[label] == default_planner), 1),
    )
    planner_kind = PLANNERS[planner_label]
    if planner_kind == "claude":
        st.caption("Only the question text and the pool's machine/time range go to the "
                   "Anthropic API. Telemetry stays where this page runs.")
        if not key_available:
            st.warning("No API key is available, so the rules will answer. "
                       "Paste your own key above to use Claude.")
    st.divider()
    st.caption(brand.DISCLAIMER)

# --- main: data banner and question ------------------------------------------
if demo_mode:
    st.warning(
        "**Demo data.** This page is running on a synthetic, made-up dataset "
        "(machine DEMO01, 2026-02-01, UTC), not AROL telemetry. Planted signals to "
        "find: head 7 is weak (about 3% rejects), head 12 drifts upward 08:00-12:00, "
        "head 3 has three over-limit closures, and 01:00-01:30 the machine runs "
        "without caps. Try the examples below.")
    if use_store and not store_error:
        st.info("No AROL telemetry has been uploaded yet. Add a daily CSV under "
                "'Add a day of telemetry' in the sidebar.")
st.caption(f"Selected machine: {machine} | Head diagnostic and scoped analytics (registered tools)")
if planner_kind == "claude":
    st.info("Ask in your own words, for example: 'which head performs worst on Feb 1?' "
            "or 'is spindle 12 drifting in the morning?'. Check the 'Interpreted as' line.")
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
    "Rank heads by success (demo: head 7 is weak)": (
        f"Rank heads by cap-present success rate for machine {machine} on 2026-02-01"),
    "Head 7 versus its peers": (
        f"Compare head 7 with peers by cap-present success rate for machine {machine} on 2026-02-01"),
    "Torque trend for head 12 (demo: drift)": (
        f"Show torque trend for head 12 for machine {machine} "
        "from 2026-02-01T06:00:00 until 2026-02-01T12:00:00"),
    "Torque anomalies for head 3": (
        f"Find torque anomalies for head 3 for machine {machine} on 2026-02-01"),
    "Machine idle periods": (
        f"Machine idle for machine {machine} from 2026-02-01T00:00:00 until 2026-02-01T06:00:00"),
}

st.markdown("#### Ask a question")
chosen = st.selectbox("Choose an example", list(examples))
question = st.text_area(
    "Question",
    value=examples[chosen],
    key=f"question_{chosen}",
    height=100,
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
        if planner_kind == "claude" and not env_key and session_key.startswith("sk-ant-"):
            # In memory for this call only; config files can never hold a key.
            cfg["agent"].setdefault("claude", {})["session_api_key"] = session_key

        try:
            with st.spinner("Analyzing the selected event window..."):
                response_json, status = answer_one(
                    query, cfg, st.session_state["output_dir"], sequence,
                    json_output=True, plots=True, selected_machine=machine,
                )
            payload = json.loads(response_json)
            if status != payload["status"]:
                raise ValueError("Inconsistent analysis status")
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


def _section(markdown, heading):
    """Text of one '## heading' section of the report, without the heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", markdown, re.S | re.M)
    return match.group(1).strip() if match else ""


# --- result -------------------------------------------------------------------
payload = st.session_state.get("result")
if payload is not None:
    status = payload["status"]
    st.markdown("#### Result")
    st.caption(f"Analysis route: {payload['planner']}")
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
    diagnostic, _, report = display_markdown.partition("# Telemetry report")
    report = ("# Telemetry report" + report) if report else display_markdown
    diagnostic = diagnostic.strip() if report is not display_markdown else ""
    interpreted = re.search(r"\*Interpreted as:\*\s*(.+)", report)
    if interpreted:
        st.markdown(f"<div class='interp'><b>Interpreted as:</b> "
                    f"{interpreted.group(1).replace('`', '')}</div>", unsafe_allow_html=True)

    figures = key_figures(payload["results"])
    if figures:
        st.markdown(brand.kpi_cards_html(figures), unsafe_allow_html=True)

    figure_paths = [Path(name) for name in payload["artifacts"].get("figures", [])]
    tab_summary, tab_charts, tab_report, tab_trace, tab_downloads = st.tabs(
        ["Summary", f"Charts ({len(figure_paths)})", "Full report", "Trace & data", "Downloads"])
    with tab_summary:
        if diagnostic:
            st.markdown(diagnostic)
        findings = _section(report, "4. Findings")
        if findings:
            st.markdown("#### Findings")
            st.markdown(findings)
        limits = _section(report, "5. Confidence and limits")
        if limits:
            with st.expander("Confidence and limits"):
                st.markdown(limits)
        if not (diagnostic or findings):
            st.markdown(report)
    with tab_charts:
        if not figure_paths:
            st.caption("This analysis has no chart.")
        for image_path in figure_paths:
            st.image(str(image_path), caption=image_path.stem.replace("_", " "))
    with tab_report:
        st.markdown(report)
    with tab_trace:
        st.write("Planned calls:", payload["planned_calls"])
        with st.expander("Execution trace", expanded=False):
            st.json(payload["trace"])
        with st.expander("Tool results (raw)", expanded=False):
            st.json(payload["results"])
    with tab_downloads:
        from src.interface.export import to_html
        html_report = to_html(display_markdown, key_figures=figures, embed=True,
                              figures=[(p.stem, p) for p in figure_paths],
                              title=f"AROL telemetry report - {payload['query'][:60]}")
        st.download_button("Download report (HTML, printable)", html_report,
                           file_name="arol-report.html", mime="text/html",
                           key="report-html", type="primary")
        st.download_button("Download report (Markdown)", saved_markdown,
                           file_name="arol-report.md", mime="text/markdown", key="report")
        st.download_button("Download trace (JSON)",
                           json.dumps(payload["trace"], indent=2, ensure_ascii=False),
                           file_name="arol-trace.json", mime="application/json", key="trace")
        for index, image_path in enumerate(figure_paths):
            st.download_button(f"Download chart {index + 1} (PNG)", image_path.read_bytes(),
                               file_name=image_path.name, mime="image/png", key=f"figure-{index}")
        st.caption("Open the HTML report in a browser and print it to PDF if needed. "
                   "Reports and traces are also saved under data/web_demo_runs/.")
