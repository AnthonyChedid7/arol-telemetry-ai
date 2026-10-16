# Independent release review: `audit-followups`

Reviewed 2026-10-16 on macOS, Python 3.14.7. The fetched branch tip was
`baddff1c4de3d822d454711c3ab0ecb767632c98`. This document distinguishes
that published commit from repairs first made in an isolated review worktree
and then placed on `audit-followups-release-fixes`. The original checkout at
`/Users/Shared/Files From d.localized/Polito/Systems and Programming/Project Quer/arol-telemetry-ai`
was clean on `main` at `93b4a6d016671baddb4b9c0d07d9422abf771a0a`.
The review worktree is `/private/tmp/arol-audit-followups-review`.

## Verdict

**Published branch: not ready as-is for an unqualified release demo.** Rules
mode returned `ok` with zero samples for nonexistent heads, mismatched machines,
and out-of-pool dates. The website retained an earlier answer after its
question or planner changed. A one-bucket throughput chart used a multi-year
time axis. These were reproduced and repaired locally, then committed on the
new branch. The repaired tree is **ready to present with limitations**: no Anthropic
credential was available for a current live check, and this is a local
single-machine prototype, not a demonstrated cloud-sync or decentralized MAS
implementation of every item in the official Q3 brief.

## Scope and evidence

- Compared `origin/main...baddff1`: 67 changed files, including Claude planner,
  website selector, torque-population policy, lazy pool source, plots, lock file,
  CI, tests and docs. The project plan's model-generated prose is a historical
  proposal: the current Claude planner selects tools or asks for clarification;
  deterministic code computes numbers and assembles the report.
- Read `README.MD`, `AUDIT-REPORT.md`, `ANTHROPIC-SWITCH-STUDY.md`,
  `docs/PROJECT-AUDIT.md`, `docs/contract.md`, the official Q3 brief in
  `projects_quer.pdf`, and `AROL-project-plan.pdf`. The assignment PDF only
  identifies the assigned group/project, and is not the Q3 technical brief.
- The original private manifest exists at
  `data/event_pools/continuous-20260924-080513-930170/manifest.json` in the
  original checkout. The isolated worktree did not copy or change private data.
  It records 89 files, 7,623,968 raw rows, 36 heads, 54,722,936 exact `+1`
  events, and a 2026-01-31 to 2026-04-30 recorded UTC span.
- Historical Claude evidence files were committed in ancestor `ee53a17`, not
  produced on the final `baddff1` commit. Their JSON has no commit SHA or
  per-case served-model trace. They report 24/24 end-to-end and 16/16 planning
  for the separate `feb01-03` pool, which was not present locally. Thus they
  support a dated historical run, not a current live verification. GitHub
  Actions run 38883030215 succeeded for this exact branch SHA on Python 3.12
  and 3.14 on Ubuntu and Windows, including synthetic evaluation.

## Reproduced commands

Commands ran from `/private/tmp/arol-audit-followups-review` unless noted.
Generated output is under ignored `data/` or `/private/tmp`.

| Command | Actual outcome |
| --- | --- |
| `git fetch origin audit-followups` | Fetched `origin/audit-followups` at `baddff1`; original `main` stayed clean. |
| `python3 -m venv /private/tmp/arol-review-venv` and `/private/tmp/arol-review-venv/bin/python -m pip install -r requirements-dev.txt -r requirements-web.txt` | Installed runtime, dev, and website dependencies successfully on Python 3.14.7/macOS. `pip check`: no broken requirements. |
| `/private/tmp/arol-review-venv/bin/python -m pytest tests -q` on fetched commit | **936 passed, 4 skipped**, 27 Matplotlib deprecation warnings. Four skips require Person B's separate repository. |
| `/private/tmp/arol-review-venv/bin/python -m pytest tests/test_claude_planner.py tests/test_audit_followups.py tests/test_torque_population.py tests/test_report_plots.py tests/test_demo_agent.py -q` on fetched commit | **69 passed**; no model API call. |
| `/private/tmp/arol-review-venv/bin/python -m scripts.evaluate_agent` | **28/28**, including 16 supported plans and 12 safe clarifications. Same outcome after local repairs. |
| `/private/tmp/arol-review-venv/bin/python -m scripts.evaluate_anomaly_ground_truth` | TP=4, FP=0, FN=1, TN=59; precision 1.0, recall 0.8. Same after repairs. |
| `/private/tmp/arol-review-venv/bin/python -m scripts.check_clean_checkout` on the clean fetched commit | PASS in temporary detached checkout: 936 passed, 4 skipped, then 28/28. It reuses the environment; it does not test installation, private data or a live model. |
| `/private/tmp/arol-review-lock-venv/bin/python -m pip install -r requirements-lock.txt` and `pip check` | Exact lock installed successfully on Python 3.14.7/macOS; no broken requirements. |
| `/private/tmp/arol-review-lock-venv/bin/python -m pytest tests -q` after local repairs | **943 passed, 4 skipped**, 33 Matplotlib deprecation warnings. |
| `ollama list` and one `scripts.demo_agent --planner llm --json` bounded H04 torque question | Local service had `qwen2.5-coder:14b`; one call succeeded in 7.9 s, selected `torque_stats`, 844 samples. CLI only; the website does not offer Ollama. |

At the initial review stage, the clean-checkout verifier could not run on the
uncommitted repair worktree without violating its clean-tree guard; the full
repaired suite was rerun. After the new branch was committed, the verifier
passed from a temporary detached checkout with 948 tests passing, 4 skipped,
and 28/28 controlled cases. The final results are recorded in
`docs/FINAL-VERIFICATION-2026-10-16.md`.

## Question understanding

The complete 33-question, 66-row matrix with exact text, requested and actual
planner, status, calls, arguments, loaded scope, and assessment is in
`data/release_review/question_matrix.json`. The Claude column is an explicit
no-key fallback simulation, so it is **not live Claude inference**. For
supported, bounded questions, rules answered canonical torque statistics,
distribution, success rate, throughput, idle and H04 diagnostic wording. It
missed most natural paraphrases for those intents and the three head-comparison
phrasings. Missing-scope and unsupported questions asked for clarification.
After the pre-load repair, invalid heads, mismatched machine IDs and dates
outside the pool also clarify with zero tool calls. `head 4` and `H04` both
work in the canonical bounded diagnostic. Punctuation and word-order variants
frequently do not. The fallback trace says `claude->rules (fallback)`.

The 16/16 and 24/24 historical Claude files include correct selected calls,
arguments, interpretations, per-case latency and aggregate tokens, but the
current model's behavior, actual served model and cost could not be repeated
without a key. Schema validation cannot prove that a syntactically valid model
proposal faithfully reflects every part of the user's question; the visible
`Interpreted as` line remains important.

## Independent real-data checks

For H04, 2026-02-01 00:00:00 to 01:00:00 UTC, the first raw Parquet partition
has 1,341 exact counter `+1` rows. Their timestamp set matches the 1,341 H04
event records exactly. The raw-row statuses are 844 status 0 and 497 status 2;
the event classes are 844 `Closure OK` and 497 `No Load`. All torque values are
finite. Of the 1,341 values, exactly 497 are outside the configured 1.5-2.5 Nm
band, and every one is No Load. With No Load excluded, 844 values remain,
none is flagged, and mean torque is 1.998088862559242 Nm. Explicit
`all_statuses` restores 1,341 values and mean 1.257559284116331 Nm. The
cap-present success numerator and denominator are independently 844/844.
These are recorded observations, not a current machine-health conclusion.
The 1.5-2.5 Nm band, 0.05 Nm/hour drift boundary, and 0.5 Nm disagreement
boundary are configuration, not confirmed AROL engineering specifications.

At the first-to-second file boundary, raw counter differences are 14 exact
`+1`, 22 holds and no discontinuities. The second partition contains exactly
the 14 corresponding first-row event heads; its manifest says the same. The
one-hour sample uses an inclusive start and exclusive end. Whole-pool
`torque_stats` and `success_rate` ran lazily above the materialization limit;
an independent Parquet scan found 54,722,936 events, 31,430,144 finite
non-No-Load torques and mean 2.013312536939061 Nm, matching tool output.
Independent NumPy Pearson correlation for H01/H02 in a bounded hour was
0.03581928464816988 over 792 same-timestamp cap-present pairs, matching the
tool. A one-day H04 trend used 17,969 samples but returned 1,214 plot points
with endpoint preservation; the slope classification still uses all samples
per the targeted regression test.

## Website, plots, and failure paths

Launched Streamlit against the private manifest and used it in a local browser.
The page showed the selected machine, planner selector, examples, question
entry, missing-window clarification with zero tool calls, a bounded H04
diagnostic with 844/844 successes and 0/844 default torque flags, planned
calls, trace, structured results, figure and download controls. Switching to
Claude with no key produced a visible `claude->rules (fallback)` route and
no tool calls for unsupported wording. Changing the question cleared the old
answer after input commit; AppTest also verifies clearing on planner switch.
The browser downloaded Markdown and JSON trace files that parsed as the
expected clarification, and a downloaded diagnostic PNG had the same SHA-256
as its saved figure. Those test downloads were moved to `/private/tmp`.
The server was restarted after backend changes and the updated fallback text
was observed. Missing manifest produced an explicit page error, empty valid
scope produced a zero-sample report without invented numbers, 1,000,000 bins
clarified before loading, and an unbounded row-level distribution hit the
configured 1,000,000-event guard before materialization.

Regenerated and visually inspected real H04 distribution, trend, idle and
throughput PNGs. The default histogram's bins sum to 844; a synthetic
101-value sample with one extreme outlier matched NumPy histogram counts and
showed a full-range panel plus a 99-reading 1st-99th percentile zoom, with
two values labelled outside the zoom. Trend and idle figures preserve real
time-axis gaps and interval widths. The repaired one-hour throughput figure
now spans 00:00-01:00 UTC rather than several years. Saved Markdown figure
paths resolve relative to the report and match the generated PNG paths.

## Model and data boundary

The CLI defaults to deterministic rules; the website defaults to Claude only
when `ANTHROPIC_API_KEY` is set, and otherwise defaults to diagnostic rules.
The website selector can request Claude without a key; absence, authentication
rejection, rate limit or connection failure falls back to rules with an
explicit label. A 400-class misconfiguration (such as a bad model) yields a
visible clarification instead. The optional Ollama planner is CLI-only and
limits itself to one verified torque tool per request.

Claude's Messages API request contains the static system prompt, the 12 tool
schemas, selected machine ID, pool machine list, head list and min/max recorded
timestamps, and the user's question. The API key is sent as authentication.
The code sends no raw telemetry rows, event records, tool results, report
text, or local files in that request. A user's question itself can contain
telemetry information. Arithmetic, result interpretation text, report and
plots remain deterministic and local; Claude's prose is used only when it
asks a clarification. Without internet, the website still works through the
rules path, including a labelled fallback if Claude was selected.

## Remaining limits and demo

1. **Medium, presentation scope:** the official Q3 brief calls for cloud
   synchronization and a decentralized MAS. This repository demonstrates
   local ingestion, deterministic analytics and an orchestrator, but no
   cloud-pull integration or decentralized agents were found. Present it as
   the local prototype and explain the gap.
2. **Medium, live model evidence:** no Anthropic key was available. The saved
   24/24 and 16/16 runs are historical and tied to another pool. Recheck a
   small live suite with the chosen key/model before claiming current Claude
   reliability.
3. **Medium, natural wording offline:** rules are intentionally narrow. The
   33-question matrix records missed valid paraphrases. Use canonical demo
   wording as an offline backup.
4. **Low, status lag and limits:** some status/torque disagreement remains
   flagged per the team contract; engineering thresholds require AROL review.

Reliable bounded demo questions for the recorded H04 hour:

- `Is there any problem with head 4 from 2026-02-01T00:00:00 until 2026-02-01T01:00:00?`
  Expect 844/844 cap-present successes, peer median 100%, 0/844 default
  torque flags and a no-fault-verdict caveat.
- `Average torque for head 4 from 2026-02-01T00:00:00 until 2026-02-01T01:00:00`
  Expect 844 finite closures and mean 1.998088862559242 Nm.
- `Observed throughput by hour for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T01:00:00`
  Expect 48,279 observed exact `+1` events/hour with a one-hour UTC chart.

For an offline backup, select **Rules only** in the website (or
`--planner diagnostic` in CLI) and use these exact phrasings. The site stays
usable without an Anthropic connection.

## Follow-up: per-head failure rate (2026-10-16)

The exact question `show me the failure rate for each head` previously gave a
generic rules clarification. A narrow rules alias now routes it to the existing
`success_rate_per_head` tool. Its visible interpretation defines failure as
`reject_signal=True` among confirmed cap-present observed events, with No Load
and unknown cap presence reported separately. The website includes the exact
question as an example. Optional machine/time scope remains intact, and an
unsupported status prefilter still clarifies before loading.

The CLI ran the exact question against the original private manifest with one
tool call and no model request. It lazily scanned 54,722,936 observed events
without materializing them, returning 36 head groups. Across all heads, 1,068
rejection flags / 31,430,108 confirmed cap-present events = 0.003398015686%
for this recorded pool. H29 had 111/873,307 = 0.012710306914%; H04 had
5/872,986 = 0.000572746871%. An independent Polars scan of event Parquet
counted status 65 over statuses 0+65 per head and matched all 36 groups.
The complete structured response was saved locally as the ignored
`data/release_review/failure_rate_each_head.json` artifact; it is not committed.

Focused KPI tests passed 51/51; the full repaired suite now passes **948 tests,
4 skipped** with 33 Matplotlib deprecation warnings. Controlled agent
evaluation remains 28/28 and `git diff --check` passes. This is still a
historical observed-event proxy, not a live physical-failure rate or a measure
of all production. The fixes are committed on the local
`audit-followups-release-fixes` branch; the original `audit-followups` branch
was not modified.
