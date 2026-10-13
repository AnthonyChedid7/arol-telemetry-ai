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

## Follow-up branch `audit-followups`

The open items listed further down were reviewed against the project's requirements:
the course brief for Q3 (*Multi-Agent System for Industrial IoT Data Refinement and
Analytics*), the team project plan, and the team contract (`docs/contract.md`). Only
changes those documents support were made. Each is a separate commit on this branch;
`main` and `claude-planner` are unchanged.

| Item | Done on this branch | Requirement it serves |
|---|---|---|
| Plots | Real time axis (gaps visible), idle bars span their interval, labels name the population | Plan WP4: plot generation; explainable outputs |
| Timestamps | Labelled **UTC** everywhere, as the contract defines `ts` | Contract: event-table schema |
| `n_heads: null` | Restored in `config.yaml` (auto-detect), part of the contract's config layout | Contract: configuration |
| README | Plain ASCII | Brief: deliverables |
| Time bounds | One rule: tz-aware bounds rejected everywhere | Plan WP1: timestamp consistency |
| Sort note | Unparseable head ids sort last, as documented | Docs accuracy |
| Trend size | Returned series at most 2,000 points; drift still on every point (Feb 1: 41 MB to 0.06 MB) | Brief: scalability |
| Whole-pool totals | Torque statistics and KPI counts computed lazily above the 1,000,000-event limit (2,290,224 events in about 0.2 s) | Brief: scalability with increasing data volumes |
| Success denominator | `head_correlation` uses the contract's cap-present denominator, same as the KPI tools | Contract: success-rate denominator |
| CI and lock file | GitHub Actions on Python 3.12/3.14, Linux/Windows (runs once pushed); `requirements-lock.txt` | Plan WP5: reproducible run |
| Docs | INTEGRATION-REFERENCE line references regenerated (76 of 158 fixed); dated notes on historical documents | Deliverables: technical documentation |
| Slides | Slide 7 names the Claude planner; slide 11 has the current test and live-check numbers | Deliverables: presentation |

Deliberately **not** done, because the brief and the contract do not ask for them:

- **Plant-timezone conversion.** The contract defines `ts` as UTC; the labels now say so.
- **Replacing `data/sample.csv`.** Not required; whether real telemetry may be in the repo is
  for the team and AROL to decide.
- **Moving old code to `legacy/`.** Not required, and the pandas implementation may back
  the brief's "scales better than monolithic scripts" comparison.
- **Changing event building for the status lag.** The contract says Status sits on the
  increment row. The brief's "Cleaning Agents" objective describes exactly this
  polling-versus-production-cycle mismatch, so it stays flagged in every torque report
  for Person A and Person B to decide together (the contract requires a two-person decision).
- **`verify_*` scripts.** Recording evidence into `benchmarks/` and the audit log is their
  documented purpose; the README's everyday commands no longer change tracked files.

## Still open: for the team

These need a decision or more time; nothing on this list makes the current results
wrong, as long as the limits stated in the reports are respected. Items struck through
were resolved on the `audit-followups` branch (see the section above).

- **Confirm with AROL:** the torque limits (1.5-2.5 Nm), the drift threshold (0.05 Nm/h)
  and the disagreement boundary (0.5 Nm). ~~Plant timezone~~: the contract defines UTC.
- **Status lag** (about 0.03% of events): a joint Person A / Person B decision whether to
  amend the contract and take Status from the following reading (pool rebuild needed).
  Until then it is flagged, not changed.
- **`data/sample.csv` contains real telemetry** and is committed: a team/AROL decision.
- **Unused code:** about 2,365 lines are reachable only from tests and benchmarks (pandas
  reference pipeline, `validation_polars.py`, `capping_speed*.py`, `agent/tool_schemas.py`,
  `tool_executor.py`); the routers parse heads and times with slightly different patterns.
  Kept as is; not required by the brief.
- ~~Performance~~: whole-pool totals now run lazily; the trend series is thinned. Row-level
  tools still need a scope under 1,000,000 events.
- ~~Plots~~: real time axis.
- **Smaller items:** ~~two success-rate denominators~~, ~~sort note~~, ~~two time-bound
  parsers~~ and ~~no lock file / CI~~ are fixed. The `verify_*` scripts remain evidence
  recorders by design.
- **Historical docs:** ~~INTEGRATION-REFERENCE line numbers~~ and ~~status headers~~ are
  fixed; PERSON-B-REFERENCE keeps its snapshot line numbers, now clearly labelled. The
  evidence files in `benchmarks/integration/` that contain torque values predate finding 1.
- ~~Slides 7 and 11~~: updated.

## Verification at the end of `audit-followups`

- Test suite: **936 passed, 4 skipped** (the skips need Person B's separate repository).
- Live Claude checks re-run on this branch: **24/24** end-to-end and **16/16** planning
  (`benchmarks/integration/claude_*_20261013-1759*.json`).
- Fresh clone (commit `1b571e9`) installed from `requirements-lock.txt` in a new Python 3.14
  environment: **936 passed, 4 skipped**; controlled evaluation **28/28**; anomaly ground
  truth **TP=4, FP=0, FN=1**; `scripts.check_clean_checkout` **passed**; the working tree
  stayed clean.

## Verification at the end of `claude-planner`

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
