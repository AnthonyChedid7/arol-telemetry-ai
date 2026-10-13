# Project audit: AROL telemetry AI (branch `claude-planner`)

Date: 2026-10-13. Scope: all code, tests, scripts, configuration and documentation,
on top of `EddyKhoury/arol-telemetry-ai` `main` at `93b4a6d`.

## How the audit was done

Four independent reviews, each told to be harsh and to report only issues backed by
evidence (a command and its output, or numbers recomputed from data):

1. **Core analytics**: math recomputed independently from a real pool built from the
   first three February files (2,290,224 events, 36 heads).
2. **Agent layer and security**: planners, validation, crash paths, prompt injection,
   path handling, key handling, and cost and latency.
3. **Documentation**: every command run as written, every number and claim checked
   against the code.
4. **Reproducibility**: fresh clone and install, repeated and reordered test runs, repo
   hygiene, and performance.

Every confirmed finding was then reproduced before it was fixed. The decisions that
change what the numbers mean were made by the project owner (marked *decided*).

## Findings and status

Severity is the reviewer's. **Fixed** means fixed on this branch, with a test.

### Correctness of results

| # | Finding | Status |
|---|---|---|
| 1 | **High.** By default the torque tools pooled No Load cycles (head cycled without a cap, ~0 Nm) with closures (~2 Nm). For Feb 1: 314,793 of 961,153 events "anomalous" (32.8%, all No Load), mean 1.343 Nm. | Fixed, *decided*. Closures by default; `all_statuses` / "including No Load" restores the old population; every report states the population. Now 214 of 646,359 (0.03%), mean 1.997 Nm. |
| 2 | **High.** `head_correlation` reported r = 0.991 "strong" for H01–H02 because both heads idled together. | Fixed, *decided*. Closures only: r = 0.444. |
| 3 | **High.** Drift used a 1e-12 Nm/s epsilon, so any slope was "drift detected". | Fixed, *decided*. Needs `analytics.drift_threshold_nm_per_hour` (0.05, to be validated with AROL). |
| 4 | **Medium.** About 0.03% of events carry the previous cycle's status, because Status updates one reading after the count. | Flagged, *decided*. Counted in every torque report; nothing is corrected. |
| 5 | **Medium.** Timestamps were called "plant-local", but the files appear to be UTC (plant likely UTC+8). | Labelled, *decided*. Shown as "as recorded, apparently UTC" in config, reports, schemas and Claude's interpretation; no conversion. |

### Robustness and security (Claude planner)

| # | Finding | Status |
|---|---|---|
| 6 | **High.** "1000000 bins" was accepted and held the app for 10+ minutes. | Fixed: at most 200 bins, enforced in every path. |
| 7 | **High.** A malformed head argument from the model crashed the CLI. | Fixed. |
| 8 | **Medium.** Settings the tools don't accept (sigma, window) were silently ignored. | Fixed: Claude asks; verified live. |
| 9 | **Medium.** Nonexistent heads and dates outside the data returned "ok" with zero data. | Fixed: rejected before loading. |
| 10 | **Medium.** Any error fell back to the rules (hiding misconfiguration), and the worst case was a ~3 minute wait. | Fixed: falls back only when unreachable; 30 s timeout with one retry. |
| 11 | **Low.** The trace didn't record which model answered, and validation was looser than documented. | Fixed. |
| 12 | **Medium.** A test would make a paid live API call whenever a key was set. | Fixed: the test suite cannot reach the API. |

### Engineering and documentation

| # | Finding | Status |
|---|---|---|
| 13 | **High.** The README's "no private data" commands appended to tracked docs on every run. | Fixed: opt-in with `--record`. |
| 14 | **High.** The website could not start except on the original presentation machine; undocumented. | Fixed and documented. |
| 15 | **High.** A plain install of `requirements.txt` gave a failing test (Streamlit). | Fixed: test skipped; requirements split into runtime, dev and web. |
| 16 | **Medium.** Reports and traces written in the same second overwrote each other. | Fixed. |
| 17 | **Medium.** `config.local.yaml` not ignored; config keys never read; hard-coded defaults. | Fixed. |
| 18 | **Medium.** The docs said the demo never uses a model; stale examples, counts and Windows gaps. | Fixed (README, DEMO, METHODS-AND-LIMITS, INTEGRATION-REFERENCE, EVALUATION). |
| 19 | **Low.** `generate_sample_reports --help` overwrote tracked files. | Fixed. |

## Still open: for the team

These need a decision or more time; nothing on this list makes the current results
wrong, as long as the limits stated in the reports are respected.

- **Confirm with AROL:** the plant timezone, the torque limits (1.5–2.5 Nm), the drift
  threshold (0.05 Nm/h) and the disagreement boundary (0.5 Nm).
- **Status lag at the source:** the proper fix is in the pool builder (take Status from
  the following reading). It requires rebuilding pools and re-verifying the recorded counts.
- **`data/sample.csv` contains real telemetry** and is committed. Check the AROL contract,
  or replace it with synthetic data.
- **Dead code:** about 2,365 lines are reachable only from tests: the pandas reference
  pipeline, `validation_polars.py`, `capping_speed*.py`, and `agent/tool_schemas.py` with
  `tool_executor.py`. The routers also re-implement the head and time parsing differently.
- **Performance:** questions over more than 1,000,000 events are refused because events
  are materialized eagerly. Simple statistics could be computed lazily over the whole pool.
  A one-day trend returns every point (about 41 MB of JSON).
- **Plots:** the trend x-axis uses timestamp strings, so the spacing hides gaps.
- **Smaller items:**
  - Two success-rate denominators exist (documented in each report).
  - The adapter's sort note says nulls sort last, but they sort first.
  - Two time-bound parsers behave differently.
  - The `verify_*` scripts still append to the audit docs (they are evidence recorders).
  - There is no lock file and no CI.
- **Historical docs:**
  - Line numbers in INTEGRATION-REFERENCE.md and PERSON-B-REFERENCE.md are out of date.
  - The status headers in PROJECT-AUDIT.md and others read as unfinished work.
  - The evidence files in `benchmarks/integration/` that contain torque values predate
    finding 1.
- **Slides:**
  - Slide 7 describes only the rules and Ollama planners.
  - Slide 11 quotes 856 tests; the branch has 932 passing.

## Verification at the end

- Test suite: **932 passed, 4 skipped** (the skips need Person B's separate repository).
  It also passes with an API key set in the environment, without contacting the API.
- Controlled evaluation **28/28**; anomaly ground truth **TP=4, FP=0, FN=1** (unchanged).
  Both now leave the working tree clean.
- Live Claude checks on the Feb 1–3 pool:
  - **16/16** planning on everyday wording (the rules: 2/16).
  - **24/24** end-to-end, including typos, Italian, relative days, a prompt-injection
    attempt, unsupported settings, and out-of-range heads and dates.
  - About $0.27 in total. Evidence is in `benchmarks/integration/claude_*_20261013-17*.json`.
- Fresh clone with a new Python 3.14 environment:
  - With `requirements-dev.txt` only: **931 passed, 5 skipped** (the website test skips without Streamlit).
  - With `requirements-web.txt` added: **932 passed, 4 skipped**.
  - `scripts.check_clean_checkout`: **PASS**.
  - The working tree stayed clean.
