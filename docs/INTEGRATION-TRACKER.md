

## Integration checkpoint — verified separate baselines

> **Historical record (note added 2026-10-13).** Checkpoints are appended in chronological order; earlier entries describe the project at that time. For the current state see `README.MD` and `AUDIT-REPORT.md`.

Branch: `integration/person-a-person-b`

### Verified results
- Person A: 287 passed in 0.57 seconds after installing B's dependencies.
- Person B: 254 passed, 3 skipped in 2.30 seconds.
- Person A working tree was clean before this documentation update.
- B's dependencies were installed with A's requirements as constraints.
- Installing llama3.2:3b resolved B's live model-availability test.
- Model availability does not establish correct natural-language tool selection.

### Skipped tests
- Two adapter tests expect a sibling folder named person-a.
  Our actual Person A folder is named arol-telemetry-ai.
- One benchmark test expects the real telemetry archive.
- Real-data A-to-B integration is therefore still unverified.

### Integration progress
- Phase 0: source inspection complete for both current ZIPs.
- Phase 1: separate baselines and integration branch established.
- Phase 2: adapter contract verification is next.
- Phases 3–18: pending.

### Open contract issues
- Adapter re-decoding can change Person A's decoded status fields.
- Counter increments greater than one need an agreed policy.
- A's head comparison and B's KPIs use different success denominators
  for some statuses beyond the common 0, 2 and 65.
- Tool arguments, trusted configuration and report handling need integration.

Next action: verify A's actual event output through B's adapter with a
small controlled fixture, recording row preservation and semantic differences.
No analytics or event semantics have been changed.


## Integration checkpoint — adapter characterization verified

### Implementation
Added tests/test_person_b_adapter_contract.py.

The fixture builds 16 events using Person A's actual event builder:
two heads, eight status codes, and exact +1 counter increments.

run_person_b_adapter executes Person B's actual adapter in a separate
Python process and exchanges temporary Parquet files. This avoids the
two repositories' conflicting src package names.

### Verified results
- Focused adapter tests: 4 passed in 0.46 seconds; no skips.
- Full Person A suite: 291 passed in 0.92 seconds.
- redecode=False preserves all eight Person A columns and their types.
- The default adapter changes cap_present for statuses 3 and 64,
  and reject_signal for unknown status 999, while reporting disagreements.
- Duplicate event rows are preserved.
- Empty input produces a conforming empty twelve-column table.

### Status and limitations
Phase 2 characterization is complete for the controlled fixture.
Semantic acceptance remains pending; these tests describe existing behaviour.
No production analytics, adapter logic or event semantics were changed.
This is not a real-telemetry or agent-tool-selection evaluation.
These tests require Person B's repository; they skip if it is unavailable.

Next: document the counter-jump policy and quantify jumps on real telemetry.
Status decoding and success-rate denominator differences remain open.

## Integration checkpoint — full counter audit measured

Completed the full CSV counter diagnostic. Whole-number values were validated before integer conversion. Comparisons included file boundaries.

```json
{
  "machine_id": "MCC777eda3db57348ef8a3113a642ae74db",
  "files": 89,
  "heads": 36,
  "input_rows": 7623968,
  "ts_min": "2026-01-31T16:00:00",
  "ts_max": "2026-04-30T16:59:59",
  "exact_plus_one": 54722936,
  "holds": 219329712,
  "jump_rows": 408076,
  "counter_units_in_jumps": 156189350,
  "extra_units_beyond_one_per_jump": 155781274,
  "decreases": 2088,
  "positive_delta_rows": 55131012,
  "positive_counter_units": 210912286,
  "timestamp_gaps_over_1s": 87,
  "boundary_exact_plus_one": 667,
  "boundary_jump_rows": 0,
  "boundary_counter_units_in_jumps": 0,
  "boundary_decreases": 0
}
```

Interpretation and limits:
- Production event detection remains exactly +1.
- Jump rows and counter units within jumps are distinct measurements.
- Positive counter units are not verified individual closure events.
- Causes of counter decreases remain undetermined.
- The fixed eight-per-machine-day warning does not describe this dataset.
- Independent per-file processing omits the 667 exact +1 transitions measured across file boundaries.
- Timestamps come from CSV contents; no timezone conversion was made.
- No production code or event semantics changed.
- Detailed results: data/counter_jump_audit.json.

Status: measurement complete; interpretation and integration policy pending.

Next action: inspect large jumps and decreases before deciding how to represent and report counter discontinuities.

## Integration decision — preserve exact +1 event semantics

Evidence:
- The full 89-file counter audit completed successfully.
- Inspection of H17 on the file dated 2026-03-23 showed decreases
  from large counter values to zero.
- One observed decrease was 68928 to 0.
- The largest inspected positive jump was 0 to 71271 in one second.
- These observations are consistent with counter-reading discontinuities.
  Their underlying cause has not been established.
- Findings from this selected head/day do not classify every jump
  in the full dataset.

Policy for the first integration:
- Person A's exact +1 rule remains authoritative for observed events.
- Preserve existing event timestamps, torque, status and decoded fields.
- Do not interpolate, forward-fill zeros, or reconstruct individual
  closures from jumps.
- Keep jump/decrease diagnostics separate from event-based analytics.
- Report event counts as observed exact +1 closures, not guaranteed
  total physical production.
- Consume Person A events through the adapter with redecode=False.
- Adapter count_delta=1 and inferred=False describe these input events;
  they do not establish completeness of the underlying telemetry.
- Do not use Person B's raw positive-delta weighted throughput path
  for the first integration without correcting discontinuity handling.

Remaining implementation work:
- Apply this policy explicitly in the integration path.
- Replace the unconditional eight-per-machine-day warning.
- Resolve KPI denominator and decoding differences explicitly.
- Retain file-boundary comparisons when building events from multiple files.
- Integrate and test the first torque_stats tool through a thin wrapper.

Status:
Counter audit and initial integration policy documented.
Production integration changes remain pending.

## Integration checkpoint — registered torque_stats verified

Implemented:
- Added Person B's shared envelope and registry infrastructure.
- Extended the registry vocabulary with status_filter:
  null, "successful", or an integer status code.
- Added src/analytics/registered_torque.py.
- The wrapper delegates calculations to Person A's existing torque_stats.
- Results retain the original statistics dictionary inside B's envelope.
- Metadata includes finite sample size, units, applied filters,
  contributing observation timestamps, and execution timing.

Verified locally:
- Focused wrapper tests: 17 passed in 0.10s.
- Full project regression: 308 passed in 1.18s.
- Tests cover calculation parity, finite values, sample standard deviation,
  status selection, empty input, metadata, invalid arguments,
  missing columns, JSON serialization, and unchanged input data.

Scope:
- Existing analytics and ingestion code remain unchanged.
- This verifies registry dispatch directly.
- Planner routing, orchestrator wiring, report rendering, and real-data
  end-to-end execution remain pending.
- The four adapter characterization tests still require the sibling
  Person B checkout.

Next action:
Connect torque_stats to deterministic planner routing and report output,
then verify the complete request-to-result path.

## Integration checkpoint — torque routing and reports verified

Implemented:
- Imported Person B's planner, report, trace and time utilities.
- Added deterministic routing for aggregate torque statistics,
  optionally restricted to successful closures or an integer status code.
- Unsupported torque requests, including head/date filters, request
  clarification rather than silently broadening the analysis.
- Added a torque report template using registered tool results.
- Reports handle empty samples and undefined single-observation standard
  deviation explicitly.
- Small-sample notices remain visible alongside tool notes.
- Torque-only reports omit unrelated rate-denominator statements.
- Torque summaries do not claim machine stability or engineering compliance.

Verification:
- Added 15 planner tests and 7 report integration tests.
- Combined torque checks: 39 passed in 0.09s.
- Full project regression: 330 passed in 1.19s.
- Controlled-data tests cover question -> plan -> registry dispatch ->
  existing analytics -> report assembly, plus tool-call tracing.

Scope and limitations:
- Tests compose the components directly.
- Production orchestrator and data-source integration remain pending.
- No live LLM routing or real-data report was verified in this checkpoint.
- Existing analytics calculations and ingestion semantics remain unchanged.
- Non-torque planner routes are preserved, but their tools are not yet
  integrated into this branch.

Next action:
Connect the production orchestration path to Person A's event data,
preserve requested scope, and verify a torque report end to end.

## Integration checkpoint — event source and orchestrator verified

Implemented:
- Added PersonASource for eight-column event Parquet produced by A's pipeline.
- Validates input columns and dtypes before adaptation.
- Resolves relative event paths from the repository root.
- Preserves A's decoded fields through explicit redecode=False.
- Changed the integrated adapter's default to preserve decoding.
- Replaced the fixed eight-per-machine-day warning with an explicit
  statement that production completeness is not measured here.
- Connected Person B's orchestrator to the registered torque tool.
- Removed retries that discard requested filters.
- Requires explicit pool selection when multiple pools are configured.
- Supports Markdown report and JSON trace delivery.
- Updated report failure guidance to preserve requested scope.

Verification:
- Added 9 event-source tests and 8 orchestrator tests.
- Focused checks: 17 passed in 0.14s.
- Full project regression: 347 passed in 1.01s.
- Tests use A's actual event builder and Parquet writer with controlled
  telemetry containing exact +1 increments, larger jumps, and decreases.
- Verified decoding preservation, duplicate preservation, empty input,
  missing files, invalid input schema, pool selection, report delivery,
  and failures without filter relaxation.

Scope and limitations:
- Actual orchestration is verified on controlled telemetry.
- Real telemetry execution and live LLM routing remain unverified.
- Filter preservation is verified for deterministic routing and dispatch;
  the imported LLM planner still needs separate review.
- Only torque_stats is currently connected as an analytics tool.
- Raw ingestion and existing analytics calculations remain unchanged.

Next action:
Run one real telemetry file through A's event builder, event Parquet,
PersonASource, deterministic planner, orchestrator, report and trace.
Compare the wrapped statistics against a direct torque_stats calculation.

## Integration checkpoint — first real-data torque report verified

Completed the first deterministic real-data vertical slice:

CSV -> Person A event builder -> event Parquet -> PersonASource ->
RulePlanner -> registry -> torque_stats -> report and trace.

Evidence:
- Input: telemetry_MCC777eda3db57348ef8a3113a642ae74db_2026-02-01.csv
- Input SHA-256: 679622a73fd54a051fc0507ed5faff75cc1575fb0b5261f517d9314840a1911e
- Tested code commit: ec766753c06d7c4291af7a9a96e25c284d49cd75
- Raw rows: 86,399
- Raw timestamp range: 2026-01-31T16:00:00 to 2026-02-01T15:59:59
- Observed exact +1 events: 765,703
- Event count agrees with the independent counter audit.
- All eight original event fields were preserved.
- Query: Average torque for successful closures
- Planner: rules; tool calls: 1; outcome: ok.
- Finite successful-closure torque observations: 427,772
- Mean torque: 1.996996423328315 Nm.
- Minimum / maximum: 0.0 / 2.317 Nm.
- Sample standard deviation: 0.035846607419704545 Nm.
- Direct and reported statistics matched exactly in this run.
- Verification permits floating-point relative/absolute tolerance of 1e-12.

Artifacts:
- Portable verification evidence: benchmarks/integration/torque_stats_real_2026-02-01.json
- Report and trace remain local under data/integration_smoke.
- Last full regression: 347 passed in 1.01s.

Limits:
- Verified one supplied CSV file, not the complete 89-file pool.
- First observation is a counter baseline; no preceding-file boundary tested.
- Filename date is not assumed to be a calendar-day window.
- Source timezone remains unconfirmed; no timezone conversion was applied.
- Zero torque was retained by the existing finite-value calculation.
  Its physical interpretation requires separate investigation.
- Live LLM routing and the other analytics tools remain pending.

Status:
First deterministic torque_stats vertical slice verified on real telemetry.

Next action:
Extend the tool's supported head, machine and time filters with explicit
scope-preservation tests, then expand routing and the remaining tools.

## Integration checkpoint — scoped torque queries verified

Implemented:
- Added shared event filtering for head_id, machine_id, start and end.
- Registered torque_stats accepts these filters alongside status_filter.
- Start is inclusive; end is exclusive.
- Tool-level head filtering accepts one head or a list of heads.
- Metadata describes the finite observations used after all filters.
- Blank, null-like and incorrectly typed scope arguments return errors
  instead of silently becoming omitted filters.
- Valid filters with no matching events return empty statistics.
- Timezone-aware bounds and invalid time ranges are rejected.

Deterministic routing:
- Supports a single named head, an exact machine identifier,
  successful closures or a numeric status, and explicit dates/ranges.
- Head 5 maps to H05; machine identifier case is preserved.
- Calendar dates select midnight to the following midnight using
  timestamps as stored, without timezone conversion.
- Explicit ranges use "from ... until ..." with an exclusive end.
- Conflicting filters, unsupported qualifiers and relative dates
  request clarification without executing tools.

Verification:
- Added 21 scope tests and 16 scoped-planner tests.
- Updated earlier tests whose head/date requests are now supported.
- Full regression: 384 passed in 1.04s.
- End-to-end fixtures verify exclusion of other heads, machines,
  dates and statuses, including exact time boundaries.
- Unknown heads return empty results rather than broader statistics.
- Existing analytics calculations remain unchanged.

Limits:
- Scoped routing is verified on controlled data.
- The earlier real-data verification covered successful closures
  across the supplied file, without head or time restrictions.
- Live LLM routing, multi-head natural-language routing, relative dates
  and the remaining analytics tools are still pending.

Next action:
Verify a scoped head query against the saved real event Parquet,
comparing its result with an explicitly filtered direct calculation.

## Integration checkpoint — scoped real-data torque verified

- Verified clean commit: b4ddff87c360e5e25b0533168aebbf53fd09ad97.
- Query: Average torque for head 5 for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T12:00:00 for successful closures
- Source events: 765,703.
- Selected successful events / finite torque samples: 7,575 / 7,575.
- Mean torque: 1.9957486468646866 Nm.
- Minimum / maximum torque: 0.0 / 2.173 Nm.
- Sample standard deviation: 0.03983402226443763 Nm.
- Direct and reported results agree within relative/absolute tolerance 1e-12.
  The last floating-point digits of standard deviation differ; equality is not claimed.
- All five requested parameters reached the tool unchanged.
- One deterministic tool call; no live LLM routing tested.
- Evidence, including source-code hashes: benchmarks/integration/torque_stats_scoped_real_2026-02-01.json.
- Reports and trace remain under data/integration_smoke.
- Last completed regression before the new batch: 384 passed in 1.04s.

## Integration batch — four analytics tools added; tests pending

Prepared wrappers for torque_distribution, torque_trend,
detect_torque_anomalies and head_correlation, using the existing A functions.
Added deterministic report templates and application-owned configuration
dispatch. Config, thresholds and rolling windows are not model parameters.
Unknown arguments are rejected before placeholder coercion can remove them.
Correlation requires one machine and rejects duplicate head/timestamp
pairings; it does not deduplicate or change A's underlying calculation.
A's non-No-Load success denominator is retained and explicitly distinguished
from B's cap-present KPI denominator; final denominator alignment is pending.
Trend direction remains A's numerical-epsilon classification, not a statement
of engineering or statistical significance. Selected trend/anomaly data are
pooled, and no pre-window history is added to a scoped trend calculation.

New tests cover parity with all four existing functions, scope, empty input,
configuration isolation, invalid arguments, ambiguous correlations, reports,
and trusted configuration passing through the orchestrator.

Status: implementation installed; run pytest before marking this batch verified.
Natural-language routing for these four tools is the next task. The existing
scoped torque_stats route remains active. No merge or live LLM validation done.

## Integration checkpoint — all five analytics wrappers verified

This checkpoint supersedes the preceding batch's verification-pending status.

Verified:
- Registered wrappers for torque_distribution, torque_trend,
  detect_torque_anomalies and head_correlation.
- All five analytics tools now have wrappers and report templates.
- Existing Person A analytics functions remain unchanged.
- Configuration is supplied by trusted runtime dispatch and isolated
  between calls; tool arguments cannot override runtime settings.
- Unknown arguments are rejected before placeholder coercion.
- Head correlation rejects cross-machine and duplicate-timestamp pairings.
- A's success denominator is preserved and explicitly distinguished
  from B's cap-present KPI denominator.
- Reports describe results without asserting machine health or causation.

Tests:
- Added 35 tests covering calculation parity, scope, empty data,
  configuration isolation, invalid arguments, correlation safeguards,
  report output and orchestrator configuration passing.
- Full regression: 419 passed in 1.12s.

Real-data evidence:
- The preceding scoped torque_stats verification is now recorded in
  benchmarks/integration/torque_stats_scoped_real_2026-02-01.json.

Remaining:
- Natural-language routing for the four newly registered tools.
- Real-data verification of those tools.
- Live LLM routing and broader diagnostic evaluation.
- Final agreement on KPI denominators and other shared assumptions.

Next action:
Add deterministic question routes for distributions, trends, anomalies
and head comparisons while preserving requested scope.

## Integration batch — deterministic routing for five analytics tools

Implemented complete-query routing for torque statistics, distribution,
trend, anomalies, and two-head comparison. Scope is preserved; unsupported
clauses, conflicting filters, timezone-aware bounds, relative dates and
configuration overrides request clarification before any tool call.
Comparison accepts only machine/time scope; status or extra-head scope is
not silently discarded. Histogram bins must be positive integers.

Added controlled tests for question routing and the actual event-Parquet ->
source -> RulePlanner -> orchestrator -> existing analytics -> report path,
with independently selected event scopes and direct calculation parity.
Old negative cases whose analyses are now supported retain negative-scope
coverage through an explicit unsupported exclusion. Migrated cases:
[
  {
    "file": "tests/test_torque_planner.py",
    "old": "Is torque drifting?",
    "new": "Is torque drifting excluding head 3"
  },
  {
    "file": "tests/test_torque_planner.py",
    "old": "Compare torque between H01 and H02",
    "new": "Compare torque between H01 and H02 excluding head 3"
  },
  {
    "file": "tests/test_torque_planner.py",
    "old": "Show torque distribution",
    "new": "Show torque distribution excluding head 3"
  }
]

Previous verified regression: 419 passed in 1.12s.
This routing batch: installed, pytest results pending. No live LLM or new
real-data execution is claimed. Core analytics, event semantics, and other
domain routes are unchanged. KPI denominator alignment remains pending.

## Integration checkpoint — five-tool question routing verified

- Full project test suite: 472 passed in 1.13s.
- Command: .venv/bin/python -m pytest tests -q
- Added 53 routing and orchestration tests.
- Deterministic questions now reach all five analytics tools.
- Tests verify scope preservation, direct-calculation parity,
  report delivery, and clarification without tool calls.
- Core analytics and exact +1 event semantics remain unchanged.
- Running pytest without an explicit tests directory collected installer
  backups and caused a module-name collision. Use pytest tests -q.
- Live LLM routing and real-data execution of the four new routes
  remain unverified.

Next action:
Verify the four new routes against real event data and save comparison
evidence, reports, and traces.

## Integration checkpoint — four additional real-data routes verified

- Verified source commit: 7e5a42a82e27e49f55da607ae3d4f6a26df01edd.
- Deterministic planner; all four routes passed complete nested-result parity checks.
- Float tolerance: relative/absolute 1e-12; counts and labels compared exactly.
- Original event fields preserved; requested parameters preserved; one call per route.
- torque_distribution: n=7,575; result parity verified.
- torque_trend: n=7,575; result parity verified.
- detect_torque_anomalies: n=7,575; result parity verified.
- head_correlation: n=12,178; result parity verified.
- Portable evidence: benchmarks/integration/analytics_routes_real_2026-02-01.json.
- Detailed comparisons, reports and traces: data/integration_smoke/analytics-20260923-184831-981277.
- Last full project regression: 472 passed in 1.13s (before this evidence run).

Scope and limits:
One previously built event file; H05 successful events for three tools; H05/H06 all statuses for comparison; specified machine; start inclusive/end exclusive; timestamps as stored.
- No live LLM routing tested.
- No preceding-file boundary tested.
- Only observed exact +1 events; total production completeness is not measured.
- Configured limits and numeric trend direction do not establish machine health or root cause.
- Head comparison retains Person A's non-No-Load success denominator; KPI alignment remains pending.

Next action:
Review live LLM scope preservation and unsupported-argument handling before enabling model routing.

## Integration batch — strict LLM proposal validation

Previous verified regression: 472 passed in 1.13s. All five deterministic
analytics routes have real-data parity evidence (torque_stats earlier;
four additional routes at commit 7e5a42a82e27e49f55da607ae3d4f6a26df01edd).

LLM proposals now require one registered tool, canonical argument names,
valid types, and an exact match to the independently parsed requested
analysis and scope. No argument or bound is dropped, no head-detail call
is prepended, and invalid proposals stop before data loading or dispatch.
Free-form model prose cannot supply report goals or findings.
Transport/response-decoding failure can use the complete verified rules
plan, with the existing fallback label in report/trace; disabling fallback
preserves exceptions. Invalid structured proposals do not trigger fallback.
Model availability requires the configured tag, not a matching name prefix.

Deliberate scope boundary:
- LLM planning is gated by the current deterministic torque grammar.
- This does not demonstrate broader natural-language understanding.
- Unverified phrasing and other KPI domains request clarification before
  contacting the model. RulePlanner's other-domain behavior is unchanged.
- No core analytics, configured limits, event semantics, or live LLM default
  settings were changed. Runtime configuration stays application-owned.

Mocked tests cover all five tools, omitted/changed/extra scope, malformed
replies and JSON, duplicate JSON keys, exact model tags, configuration
arguments, trace labels, and accepted/rejected actual orchestration.
Status: installed; full regression pending. No live Ollama routing tested.
Next action: run pytest tests -q, then evaluate live model proposals with
fallback disabled and explicit accepted/rejected results.

## Integration checkpoint — LLM scope validation verified

- Full regression: 537 passed in 1.17s.
- Command: .venv/bin/python -m pytest tests -q
- Added 65 mocked LLM validation and orchestration tests.
- Accepted proposals preserve the verified analysis and complete scope.
- Invalid proposals request clarification without data loading or dispatch.
- Transport fallback preserves scope and is labelled in the trace.
- Model prose cannot supply report findings.
- LLM acceptance remains bounded by the deterministic torque grammar.
- Live model routing accuracy and latency remain unverified.

Next action:
Evaluate live Ollama proposals with fallback disabled, recording accepted,
rejected and failed requests separately.

## Integration measurement — live LLM routing 20260923-170337-075169

- Source commit: fd58df79670490e5ae6479e3423074deb2a643c5.
- Model: llama3.2:3b; fallback disabled; temperature 0; seed 42.
- Completed all planned cases: True.
- Supported questions attempted: 10 / 10.
- Exact accepted: 1; rejected proposals: 9; request errors: 0.
- Invariant failures across all cases: 0.
- Exact acceptance / attempted supported cases: 0.1.
- Separate pre-inference scope-gate outcomes: {'gate_blocked': 4}.
- Latency (seconds, including load/errors): {'median': 0.85145, 'maximum': 1.9573}.
- Evidence: benchmarks/integration/live_llm_routing_20260923-170337-075169.json.
- Full replies and offered schemas: data/integration_smoke/live-llm-20260923-170337-075169.
- Last full regression: 537 passed in 1.17s; this measurement does not change source.

Limits:
- Small hand-authored integration sample, not a held-out language benchmark.
- One attempt per question; repeatability and statistical confidence are not measured.
- Model acceptance is limited to the verified deterministic grammar.
- Scope-gate blocks happen before inference and are excluded from model acceptance rate.
- Requests that error or are rejected remain in the supported-case denominator.
- Planning only: no analytics execution, report delivery, or real-data LLM end-to-end claim.

Next action:
Review rejected/error cases before deciding whether to adjust the prompt/model or proceed to a live orchestration smoke test. Keep scope validation unchanged.

## Integration measurement — live LLM routing 20260923-170639-310633

- Source commit: 0c283b432e5cc7bb685666c3cf49684196aa9948.
- Model: qwen2.5-coder:14b; fallback disabled; temperature 0; seed 42.
- Completed all planned cases: True.
- Supported questions attempted: 10 / 10.
- Exact accepted: 0; rejected proposals: 10; request errors: 0.
- Invariant failures across all cases: 0.
- Exact acceptance / attempted supported cases: 0.0.
- Separate pre-inference scope-gate outcomes: {'gate_blocked': 4}.
- Latency (seconds, including load/errors): {'median': 4.3464, 'maximum': 7.9462}.
- Evidence: benchmarks/integration/live_llm_routing_20260923-170639-310633.json.
- Full replies and offered schemas: data/integration_smoke/live-llm-20260923-170639-310633.
- Last full regression: 537 passed in 1.17s; this measurement does not change source.

Limits:
- Small hand-authored integration sample, not a held-out language benchmark.
- One attempt per question; repeatability and statistical confidence are not measured.
- Model acceptance is limited to the verified deterministic grammar.
- Scope-gate blocks happen before inference and are excluded from model acceptance rate.
- Requests that error or are rejected remain in the supported-case denominator.
- Planning only: no analytics execution, report delivery, or real-data LLM end-to-end claim.

Next action:
Review rejected/error cases before deciding whether to adjust the prompt/model or proceed to a live orchestration smoke test. Keep scope validation unchanged.

## Integration batch — complete JSON text tool proposals

Observed live replies distinguish two problems:
- Qwen2.5-Coder 14B returned JSON proposals in message.content. Its sampled
  scoped torque_stats proposal preserved the requested parameters.
- Qwen's sampled unscoped proposal added blank filters and an unrequested
  successful status. Those arguments remain invalid.
- Llama3.2 3B emitted native calls with string bins, null placeholders, and
  string status "0". Those values remain invalid.

Added a format adapter for one complete JSON object containing exactly
name and arguments, optionally inside one complete JSON fence. It runs
only when native tool calls are absent. It does not extract JSON from
prose, accept multiple objects, repair values, or drop parameters.
Duplicate JSON keys and extra proposal keys are rejected. Native calls
retain priority and existing validation. The complete proposed analysis
and scope must still match the independently parsed request.

New tests cover observed examples, lossless format adaptation, malformed
or ambiguous text, duplicate keys, scope changes, missing arguments,
configuration overrides, and invalid native calls alongside valid text.
Previous regression: 537 passed in 1.17s. This batch: tests pending.
Next action: run pytest tests -q, then replay saved replies offline. No new
live model performance or analytics execution is claimed by this install.

## Integration measurement — saved LLM reply replay 20260923-191530-649100

Parsed complete JSON text proposals when native calls are absent. Existing strict argument and scope checks remain in force.

- llama3.2:3b: supported=10; outcomes={'proposal_rejected': 9, 'accepted_exact': 1}; separate gates={'gate_blocked': 4}.
- qwen2.5-coder:14b: supported=10; outcomes={'proposal_rejected': 1, 'accepted_exact': 9}; separate gates={'gate_blocked': 4}.
- Evidence: benchmarks/integration/llm_json_reply_replay_20260923-191530-649100.json.
- Live model requests: 0.
- Source hashes and working-tree status are recorded; this replay may use uncommitted adapter changes.

- Post-hoc adaptation using observed response formats; not a fresh or held-out evaluation.
- No arguments were dropped, coerced, or supplied; requested scope must still match exactly.
- No analytics or report generation executed.
- Native calls are authoritative; complete JSON text is parsed only when native calls are absent.

Next action: review replay outcomes and regression results before changing prompts, model configuration, or running a fresh evaluation.

## Integration checkpoint — JSON proposal adapter verified

- Full regression: 570 passed in 1.42s.
- Added 33 JSON proposal validation tests.
- Offline replay: Qwen2.5-Coder 14B accepted 9/10 saved proposals.
- Offline replay: Llama3.2 3B accepted 1/10 saved proposals.
- All four scope-gate cases remained blocked for each model.
- Qwen's unscoped torque_stats proposal remains rejected because it
  introduces blank filters and an unrequested successful status.
- Argument types and exact scope validation remain unchanged.
- Replay used existing responses; no new inference or analytics ran.
- Evidence: benchmarks/integration/llm_json_reply_replay_20260923-191530-649100.json

Next action:
Run a fresh scoped Qwen request through the real-data orchestrator,
verify calculation parity, and save its report and trace.

## Integration measurement — fresh Qwen real-data report 20260923-172053-963106

- Source commit: aaade91a6c10739cac8b8772ff1d3d933353bb61.
- Model: qwen2.5-coder:14b; one fresh request; fallback disabled.
- Complete verification passed: True.
- Outcome: ok; error: None.
- Live model requests: 1; analytics calls: 1.
- Model latency: 7.75316091591958 seconds.
- Trace planner: llm.
- Direct finite sample size: 7575; mean: 1.9957486468646866 Nm.
- Direct/reported parity passed: True; float tolerance relative/absolute 1e-12.
- When verified, checks include exact requested scope, preservation of original
  event fields, contributing window, saved report, and saved LLM-labelled trace.
- Evidence: benchmarks/integration/live_qwen_real_torque_20260923-172053-963106.json.
- Full model reply, report, trace and runner: data/integration_smoke/live-qwen-report-20260923-172053-963106.
- Last full regression: 570 passed in 1.42s; this run does not change source.

Limits:
- One fresh request using a question from the earlier evaluation; not held-out accuracy evidence.
- LLM acceptance remains bounded by the verified deterministic grammar.
- One previously verified event file; no preceding-file boundary tested.
- Only observed exact +1 events; no reconstruction or total-production claim.
- Statistics do not establish engineering compliance, machine health, or root cause.

Next action: review remaining multi-file, diagnostic-routing and delivery gaps before final integration.

## Integration implementation — continuous partitioned event building

Implemented a fresh, ordered single-machine CSV-to-event pool builder.
- src/ingestion/event_pool.py: build_event_pool converts and processes one raw
  file at a time; keeps the preceding final observation as the next baseline;
  calls Person A's existing exact +1 event builder and status decoder; persists
  one raw Parquet and one eight-column event Parquet per input file.
- scan_event_pool validates a completed manifest and returns a lazy ordered
  event view. Callers can filter before collecting. Optional hash verification
  detects changed event partitions.
- scripts/build_event_pool.py: reproducible real-data runner with source/input
  hashes, independent exact +1 counts, optional comparison against the existing
  full counter audit, evidence output, and audit/tracker updates.
- Added 27 test cases for continuous-reference parity, boundary attributes,
  empty/one-row partitions, duplicate timestamps, gaps, discontinuities,
  decoding, invalid counters/statuses, input ordering, changed heads, machine
  filenames, failed publication, scoped lazy reads, and manifest integrity.

Decisions and limitations:
- No input sorting, deduplication, interpolation, or discontinuity reconstruction.
- Whole-number floats are validated for finiteness and exact representability
  before integer conversion. Unsafe integer ranges are rejected explicitly.
- Null/fractional counters and statuses, reversed timestamps and changed head
  schemas stop publication. Equal timestamps and gaps remain in the input.
- Empty files retain the preceding observation. Only the first observation of
  the supplied pool is the baseline. The core event/analytics functions are unchanged.
- A completed manifest is published only after all partitions pass checks;
  existing output directories are never overwritten. Failures leave clearly
  marked partial output without a completed manifest.
- This batch builds a fresh pool; incremental cache reuse/resume is not implemented.
- The builder keeps one raw file plus processing workspace; no measured peak
  memory or speed claim is made. The reader remains lazy until collected.
- PersonASource and the orchestrator are not changed in this batch; they still
  require the next step for manifest-backed, scope-filtered loading.

Verification status:
- Previous user-run full regression: 570 passed in 1.42s.
- New Python files compile; installer preflight/idempotence checked separately.
- Polars/pytest execution of this batch is pending in the user's environment.
- No new real-data build or live LLM request has been claimed by this install.

Next action:
Run .venv/bin/python -m pytest tests -q. If green, run the downloaded installer
with --build-real to build all audited CSVs and compare totals, including the
previously measured 667 cross-file exact +1 events. Then connect the manifest
and scoped lazy loading to the production orchestrator.

## Integration measurement — continuous event pool 20260924-080513-930170

- Source commit: 50146f69d5b13ccb896165547fe50a60eeca0415; working-tree state recorded in evidence.
- Files: 89; raw rows: 7623968; heads: 36.
- Observed exact +1 events: 54722936.
- Cross-file exact +1 events retained: 667.
- Agreement with prior full counter audit: True.
- Evidence: benchmarks/integration/continuous_event_pool_20260924-080513-930170.json.
- Pool: data/event_pools/continuous-20260924-080513-930170/manifest.json.

The first observation is the pool baseline. Later partitions carry the preceding observation. Empty files preserve it. Discontinuities are measured, not reconstructed. No source event or decoding function was changed. Timestamps remain as stored.

Limits: this is a data-build verification, not agent execution or a measured memory benchmark. The current PersonASource still reads a single event file eagerly.

Next action: inspect audit comparison results, then connect manifest-backed, scope-filtered event loading to the orchestrator.

## Integration checkpoint — continuous event pool verified

- Full regression: 597 passed in 1.41s.
- All 89 files built successfully.
- Observed exact +1 events: 54,722,936.
- Cross-file closures retained: 667.
- Counter-audit agreement: True.
- Agent integration with the partitioned pool remains pending.

## Integration implementation — scoped manifest event source

- Added src/common/event_pool_source.py: EventPoolSource consumes a completed
  event-pool manifest. It validates canonical tool arguments and applies the
  requested time/head/machine/status scope before collecting event rows.
- Head comparison reads both requested heads with all statuses, preserving
  Person A's existing summary denominator and pairing guards.
- data.source=person_a_pool selects this source. data.person_a maps pool names
  to manifest.json paths, resolved relative to the repository root. The prior
  person_a single-Parquet source retains its existing behavior.
- Orchestrator uses load_for_plan when provided. Read failures never trigger a
  whole-pool retry. Oversized selections ask for narrower scope without dispatch.
- data.max_loaded_events defaults to 1,000,000. It must be a positive integer.
  The size check returns a scalar before event-row collection; a second limit
  bounds the final collected result. No claim of measured peak memory is made.
- Data-used report and trace distinguish scoped loaded events from the full
  pool count declared in the manifest. They retain requested filters.
- data.verify_pool_hashes optionally rehashes all event partitions; default is
  false to avoid reading all bytes for each scoped question. Every partition's
  schema/path is checked, and manifest/file-stat changes during reads are rejected.
- No partition is skipped using manifest date summaries; Parquet predicate
  pushdown controls physical reads. There is no whole-pool DataFrame cache.
- Current source supports exactly one verified torque analysis per plan.
  Combined analyses, additional KPI tools and broader question grammar remain pending.
- Core calculations, exact +1 detection, status decoding, registry declarations
  and LLM scope validation are unchanged.

Verification:
- Added 37 tests for all-five-tool parity, scoped materialization, field
  preservation, empty results, correlation scope, repeated queries, invalid
  arguments, size limits, missing/changed files, trace/report delivery and no
  fallback to whole-pool loading.
- Added scripts/verify_scoped_event_pool.py: three deterministic real-data
  queries compared with an independent two-CSV continuous reference. It saves
  field/result parity evidence, reports and traces, and updates both documents.
- Previous full regression: 597 passed in 1.41s; new execution tests pending.
- Source compiles and installer safeguards checked; no new real-data run or
  live LLM result is claimed by installation.

Next action:
Run .venv/bin/python -m pytest tests -q. Once green, run the installer with
--verify-real to verify the known scoped torque query, first file boundary,
and two-head comparison through the partitioned production source.

## Integration measurement — scoped event-pool orchestration 20260924-083523-257131

- Three deterministic real-data queries passed.
- Scoped statistics, file-boundary statistics, and head comparison matched an independently filtered two-CSV reference.
- All eight original event fields were compared; each request executed one tool.
- Agent source collected only the requested scope from the configured full event pool.
- No live model request or measured peak-memory claim.
- Evidence: benchmarks/integration/scoped_event_pool_20260924-083523-257131.json.

Next action: review the results and regression, then record the scoped-source checkpoint.

## Integration checkpoint — scoped event-pool orchestration verified

- Full regression: 634 passed in 1.88s.
- Three real-data queries passed field and complete-result comparisons.
- Loaded events: 7,575; 72; and 26,334 respectively.
- Boundary query retained 14 cross-file events.
- Head comparison used 12,178 matched finite torque pairs.
- Full configured pool: 54,722,936 observed exact +1 events.
- Deterministic routing was used; no live LLM request was made.
- Next action: expand diagnostic question coverage.

## Integration implementation — diagnostic aliases and combined analyses

- Expanded deterministic routing for the existing diagnostic catalogue.
  Pure grammar checks route 24 of the original 30 questions to their original
  expected tools. Six fleet-comparison or failure-explanation questions ask
  for clarification; those diagnostic features remain deferred.
- Anchored analysis phrases are rewritten while retaining the complete scope
  suffix for strict validation. Unsupported exclusions, extra clauses,
  contradictory filters and configuration overrides remain blocked.
- Added statistics/distribution and trend/anomalies two-tool plans. Every call
  preserves the same requested head, machine, time and status filters. Histogram
  bins apply only to the histogram. The partitioned source validates both calls
  before performing one scoped load. Different scopes are rejected.
- Plans exceeding the configured tool budget stop before loading instead of
  silently dropping calls. Reports identify reused observations; per-tool sample
  sizes must not be added as a distinct-event total.
- Combined LLM plans request clarification before inference. Single-tool LLM
  proposals retain strict type and exact-scope validation. No live model accuracy
  or general language-understanding improvement is claimed.
- Core analytics, exact +1 event detection and status decoding are unchanged.

Verification:
- Added 69 test cases covering the original catalogue, scope preservation,
  rejected suffixes, both event sources, two-result delivery, mismatched source
  scopes, tool budgets, deferred diagnostics and LLM guards.
- Added scripts/verify_diagnostic_routing.py. After regression passes, it checks
  actual catalogue plans and two scoped real-data combined reports against direct
  calculations on an independent original-CSV reference. It records full field
  and result comparisons, reports, traces and benchmark evidence.
- Previous full regression: 634 passed in 1.88s. New runtime tests and real-data
  verification are pending. Installer and pure grammar checks are separate from
  Polars execution and do not establish runtime success.
- Catalogue coverage is known-question regression, not held-out accuracy.

Next action:
Run .venv/bin/python -m pytest tests -q, then this installer with --verify-real.
The six deferred diagnostic questions and KPI denominator semantics remain open.

## Integration measurement — diagnostic routing 20260924-085303-176845

- Known catalogue: 24/30 routed to original expected tools.
- Remaining 6 questions require clarification; their diagnostic features are deferred.
- Two real combined queries passed: statistics/distribution and trend/anomalies.
- Each used one scoped load, preserved all eight event fields, and executed two tools.
- Complete results matched direct calculations with 1e-12 relative/absolute float tolerance.
- Combined LLM proposals remain blocked before inference; no new live model requests.
- Evidence: benchmarks/integration/diagnostic_routing_20260924-085303-176845.json.

Limits: this is known-question regression coverage and one real scope, not held-out accuracy or root-cause validation.

Next action: record regression results, then resolve KPI denominator semantics and remaining diagnostic requirements.

## Integration checkpoint — diagnostic routing verified

- Full regression: 703 passed in 2.12s.
- Known catalogue: 24/30 questions reached their expected tools.
- Six questions require clarification; their diagnostic features remain deferred.
- Two real combined reports passed complete-result and event-field comparisons.
- Each report used one scoped load of 7,575 events and two tool calls.
- Deterministic routing only; no live model requests.
- Evidence: benchmarks/integration/diagnostic_routing_20260924-085303-176845.json
- Next action: resolve KPI denominator semantics and remaining diagnostic requirements.

## Integration measurement — KPI denominators 20260924-085749-215016

- Observed events counted: 54,722,936.
- Existing A denominator: 31,430,144.
- Existing B denominator: 31,430,108.
- Unknown cap-presence events: 36.
- Reject-flagged events outside the cap-present denominator: 24.
- This measures current formulas; no denominator policy was adopted or changed.
- These counts describe supplied exact +1 observations; production completeness remains unknown.
- Evidence: benchmarks/integration/kpi_denominators_20260924-085749-215016.json.
- Next action: use the measured status coverage to define explicitly labelled KPI rates and integrate their tools.

## Integration implementation — explicit-denominator KPI tools

- Added registered success_rate and success_rate_per_head tools for observed
  exact +1 events. Scope accepts head, machine and half-open naive time bounds.
  All statuses and all torque values in the requested scope are retained.
- Cap-present success fraction = status 0 AND cap_present=True / cap_present=True.
- Cap-present rejection fraction = reject_signal=True AND cap_present=True /
  cap_present=True. Rejection flags outside that population are counted separately.
- Reports include observed totals, confirmed cap presence/absence, unknown cap
  presence, unknown rejection flags, No Load class counts and explicitly labelled
  status-0/No-Load fractions of all observed events. No Load class and confirmed
  cap absence remain distinct (for example status 3).
- Reports separately reproduce A's legacy success fraction excluding No Load
  classes. Existing A analytics and decoding are unchanged. This implementation
  makes definitions explicit; it does not claim the old joint contract was signed.
- Empty denominators produce null rates, never zero or 100% by substitution.
  Inconsistent status-0 events without confirmed cap presence fail visibly.
- Per-head results group by BOTH machine and head; report all selected groups
  sorted by identifiers. Small cap-present samples are labelled using trusted
  analytics.min_n. No ranking, statistical significance or fault-cause claim.
- Deterministic success-rate, rejection-rate, No-Load-rate and capping-KPI
  questions preserve their full scope. Status prefilters, bucket overrides,
  relative dates and unsupported clauses request clarification.
- The partitioned source supports the two-tool KPI plan with the same scope and
  one bounded load. The current loaded-event limit still applies. Full-pool
  aggregate-only orchestration has not been introduced.
- Any plan containing an unregistered tool stops before loading and dispatch,
  rather than returning a partial answer. The existing LLM gate still supports
  only its five verified single torque tools; no KPI model acceptance is added.
- KPI metadata n is the cap-present denominator; data_window covers all selected
  observations, and result.n_observed records that distinct population.
- Reports use six decimal places for percentages so measured differences are
  visible and explain that repeated tool samples are not additive.

Measured motivation (previous audit, not a new run):
- 54,722,936 observed events; A denominator 31,430,144; cap-present denominator
  31,430,108; 36 cap-unknown events; 24 rejection flags outside cap-present rows.
- Reference: benchmarks/integration/kpi_denominators_20260924-085749-215016.json.

Verification:
- Added 46 test cases for denominators, unknown/no-load statuses, non-finite
  torque preservation, empty scopes, invalid schemas, inconsistent inputs,
  grouping across machines, small samples, scope grammar, dispatch rejection,
  both event sources, report delivery and no partial execution/LLM inference.
- Pure-Python arithmetic/report/grammar and installer checks were run separately.
  Polars runtime tests and real-data verification remain pending on the Mac.
- Previous regression: 703 passed in 2.12s.
- Added scripts/verify_explicit_kpi.py: compares new rate arithmetic with the saved
  full-pool grouped audit, then verifies two real scoped questions and per-head
  results against independent Python counts from the original CSV. It records
  scope/field/count/rate comparisons, reports and traces. Saved-count arithmetic
  is explicitly distinguished from running the full pool through the agent.

Next action:
Run .venv/bin/python -m pytest tests -q, then this installer with --verify-real.
Time buckets, fleet anomaly ranking, failure explanations, idle/throughput tools
and live LLM KPI routing remain unintegrated; the six deferred catalogue questions
are not declared complete by this batch.

## Integration measurement — explicit KPI rates 20260924-091145-276511

- Saved full-pool grouped counts verified the new denominator arithmetic, including rejects outside the cap-present population.
- Two real scoped questions matched independent Python counts and rates, including per-machine/head groups.
- Event fields and requested scope were preserved; reports and traces were saved.
- Deterministic routing only. This does not establish full-pool agent execution, head ranking or fault causes.
- Evidence: benchmarks/integration/explicit_kpi_20260924-091145-276511.json.
- Next action: record the regression result and review remaining diagnostic requirements.

## Integration checkpoint — explicit KPI reports verified

- Full regression: 749 passed in 2.34s.
- H05 query: 13,167 observed events; 7,575 cap-present.
- Per-head query: 473,993 observed events; 272,416 cap-present; 36 groups.
- Both queries preserved event fields and matched independent counts and rates.
- Full-pool rate arithmetic was checked against saved grouped counts;
  real orchestration was verified on one twelve-hour window.
- Deterministic routing only; no live LLM KPI requests.
- Evidence: benchmarks/integration/explicit_kpi_20260924-091145-276511.json
- Next action: integrate descriptive head ranking and diagnostic comparisons,
  preserving explicit denominators and avoiding unsupported causal claims.

## Integration implementation — descriptive head success comparisons

- Added rank_heads_by_success and compare_head_success. Both require an explicit
  single machine and start/end time window. The metric is confirmed cap-present
  success fraction; previously implemented denominator/count semantics remain.
- Eligible heads have at least trusted analytics.min_n cap-present observations.
  Small-sample and zero-denominator heads remain visible but are not ranked.
  Fewer than two eligible heads means comparative ranks are unavailable.
- Sorting uses exact integer ratios, not rounded percentages. Equal fractions
  share competition ranks (for example 1, 1, 3). Head IDs provide stable display
  order within ties without breaking the tied rank.
- Focus comparisons use the unweighted median of eligible OTHER observed heads
  within the same selected machine/time population. The focus is excluded from
  its baseline and at least two eligible peers are required. Missing/undersized
  focus heads or too few peers return explicit unavailable results, not a
  substituted head, invented baseline or zero difference.
- Head rankings and percentage-point differences are descriptive. Sample-size
  eligibility does not establish statistical significance, comparable operating
  conditions, production completeness or a fault cause.
- Explicit grammar supports 'rank heads by cap-present success rate' and
  'compare head 5 with peers by cap-present success rate', followed by machine
  and date/range scope. Missing scope, selected peer subsets, status prefilters,
  threshold overrides and unsupported trailing clauses request clarification.
- In compare_head_success ONLY, head_id names the focus and does not remove
  requested peer rows. The source retains all available heads in the explicit
  machine/time window, records actual population and focus separately in its
  metadata and trace, and explains this distinction in the report. Its existing
  row limit applies to the full requested comparison population; no peer subset
  is silently selected to fit the limit.
- Existing single-head KPI filtering, torque routes, exact +1 event detection,
  status decoding and core analytics are unchanged. LLM routing remains limited
  to its five verified single-tool torque analyses.
- The six original general diagnostic questions remain deferred; these explicit
  metric/scope routes are not recorded as completing unspecified fault questions.

Verification:
- Added 44 runtime test cases for exact ties, rounded-float edge cases, peer
  exclusion/median, absent or small samples, invalid counts, multiple machines,
  strict grammar, both event sources, explicit population tracing, row limits
  without peer removal, and blocking LLM inference.
- Pure-Python arithmetic/oracle/grammar and guarded-installer checks passed.
  Runtime Polars/pytest and real telemetry execution are pending on the Mac.
- Previous full regression: 749 passed in 2.34s.
- scripts/verify_head_kpi.py compares two real orchestration queries with an
  independent original-CSV count reference, cross-product ranking oracle and
  peer-median calculation. It verifies original event fields and requested
  population, and saves reports, traces and benchmark evidence.

Next action:
Run .venv/bin/python -m pytest tests -q, then this installer with --verify-real.
Temporal KPI requirements, broader evaluation and presentation interface work
remain pending; no new live LLM accuracy result is claimed.

## Integration repair — restore the supported two-tool KPI plan

- The head-comparison installer accidentally expanded COMBINED_TOOLS' KPI pair
  to a four-tool set while adding the new standalone tools to SUPPORTED_TOOLS.
- The existing regression caught the issue: 1 failed, 792 passed in 2.34s.
- Restored exactly success_rate + success_rate_per_head as a supported pair.
  rank_heads_by_success and compare_head_success remain supported standalone.
- Updated the local installer payload to match the repaired source so its
  --verify-real integrity check remains usable. No tests were weakened.
- Pure plan-validation checks cover all three valid pairs, the standalone head
  tools, invalid combinations and differing-scope rejection. Full runtime
  regression and the real-data verifier must be rerun.

## Integration measurement — descriptive head comparisons 20260924-092755-624939

- Two real queries matched independent per-head counts, exact-ratio competition ranks and the focus-versus-peer calculation.
- Original event fields and explicit machine/time population were preserved. Reports and traces were saved.
- Focus was excluded from the unweighted peer median. Small-sample exclusions and tied ranks remain visible.
- Deterministic descriptive results only; no significance, root-cause or new LLM routing claim.
- Evidence: benchmarks/integration/head_kpi_20260924-092755-624939.json.
- Next action: record regression results and continue with remaining temporal KPI requirements.

## Integration checkpoint — descriptive head comparisons verified

- Full regression after the KPI-pair repair: 793 passed in 2.34s.
- Both real queries preserved event fields and matched independent counts,
  competition ranks and peer-comparison calculations.
- Population: 473,993 observed events; 36 heads, all sample-size eligible.
- H05 minus the eligible-other-head median: 0.0 percentage points.
- Descriptive results for one twelve-hour window; no fault-cause,
  statistical-significance or live LLM routing claim.
- Evidence: benchmarks/integration/head_kpi_20260924-092755-624939.json
- Next action: integrate temporal KPI requirements.

## Integration implementation — bounded hourly and daily KPI reports

- Added kpi_over_time and observed_throughput tools for a single machine and
  explicit start/end interval, optionally scoped to a head. Hourly and daily
  calendar buckets partition the requested half-open interval. The first and
  last buckets are clipped to its bounds, and empty buckets remain in the output.
- Bucket counts include all observed exact +1 events regardless of status or
  finite torque. Success and reject fractions use only the cap-present
  denominator; no-load and unknown-cap observations remain explicit. Existing
  overall and per-head KPI definitions and event construction are preserved.
- Observed events per hour divide event counts by each requested bucket's full
  duration, including partial buckets. The overall rate uses total events /
  entire requested duration, rather than averaging per-bucket rates. An empty
  bucket describes absent observed events; telemetry coverage, operating time,
  downtime, production completeness and time-zone/DST handling are unconfirmed.
- Registered schemas accept hour/day only. A trusted
  analytics.max_time_buckets setting (default 1000) rejects excessive grids
  before the scoped event source opens its manifest. Reports show up to 48
  buckets and tell the user when rows are abbreviated; complete rows remain in
  tool results. Unsupported window overrides, relative dates, unknown scope and
  other filters request clarification. LLM routes remain limited to the five
  previously verified single-tool torque analyses.

Verification:
- Added 46 runtime test cases for partial and empty buckets, daily boundaries,
  exact count reconciliation, full-window rate arithmetic, source selection,
  scope validation, bucket budgets, deterministic routing and LLM gating.
- The original KPI validation/filtering statements and existing supported
  two-tool pairs were checked against their prior implementation. Pure-Python
  arithmetic, grammar and installer checks passed locally. Polars/pytest and
  real telemetry execution await the Mac run.
- Previous full regression: 793 passed in 2.34s.
- scripts/verify_temporal_kpi.py checks two independent references from the
  original CSV against real source loading and complete reports/traces, then
  saves benchmark evidence. It does not rebuild the event pool.

Next action:
Run .venv/bin/python -m pytest tests -q, then use this installer with
--verify-real. Assess supported time semantics and operating schedules before
offering downtime or utilization estimates.

## Integration measurement — temporal KPIs 20260925-081458-607317

- Hourly KPIs and daily observed-event throughput matched independent scalar counts and requested-interval arithmetic.
- Partial intervals were clipped to requested bounds; all bucket counts reconciled with totals. Event fields and scope were preserved.
- Reports distinguish recorded-event rates from production completeness, telemetry coverage and machine downtime.
- Evidence: benchmarks/integration/temporal_kpi_20260925-081458-607317.json.
- No live model calls. Timezone/operating schedules, idle classification and broader evaluation remain pending.

## Integration implementation — scoped demo command and presentation runbook

- Added scripts/demo_agent.py as a repository-root command for an existing
  complete event pool. It selects the sole local pool or requires --manifest
  when there are several, generates three machine-scoped examples, and accepts
  explicit --question arguments or interactive input.
- The default is the verified rules planner. Optional --planner llm uses the
  existing bounded five-single-torque validator; the trace names any fallback.
  Neither path silently changes the requested head/machine/time/status scope.
- Each question writes a separate Markdown report and JSON trace under one
  ignored data/demo_runs session. --json emits one structured object per
  question. Clarifications/degraded answers have nonzero scripted exit status
  and still write a trace. Markdown only avoids unintegrated plot/PDF modules.
- The source continues to enforce its configured row limit, preserving the
  existing cap-present denominators, observed-event semantics and source
  validation. No global anomaly diagnosis or causal finding was added.
- The three unqualified 'which head behaves differently' questions need an
  explicit comparison metric/reference; the three 'why H04 failing' questions
  need a failure definition, peer baseline and bounded window. Existing
  clarification behavior is preserved. The new explicit ranking/peer tools
  answer narrower questions and do not imply a root cause.
- docs/DEMO.md documents command use, event-pool setup on a new checkout,
  local report/trace files, example questions and evidence limits.

Verification:
- Seven runtime cases exercise real manifest loading through the production
  CLI, two sequential questions, saved report/trace parity, clarification
  without a data scan, LLM KPI gate, example selection and invalid manifests.
- A local pure-Python check verifies CLI selection and separate output paths;
  transactional installer checks exercise clean and conflicting repos.
- Previous full regression: 839 passed in 3.20s. New Polars/pytest and
  real-data CLI smoke verification remain pending on the Mac.
- scripts/verify_demo_agent.py reruns a previously independently validated H05
  real question through the public CLI and compares its complete statistics,
  scope, Markdown and trace to the saved reference. It does not claim a new
  independent full CSV audit or held-out language evaluation.

Next action:
Run .venv/bin/python -m pytest tests -q, then this installer with
--verify-real; inspect the CLI reports for the presentation and record
additional held-out evaluation separately.

## Integration measurement — demo CLI 20260925-083037-682363

- The public command answered the verified H05 scoped torque question on the saved real event pool. Report, trace, scope and statistics matched the previous independent reference.
- Rules planner; one tool call; no live model requests.
- Evidence: benchmarks/integration/demo_cli_20260925-083037-682363.json.
- Remaining diagnostic causes require explicit definitions; evaluate held-out queries and prepare the clean checkout before presenting.

## Integration implementation — fixed new questions and controlled planted signals

- Added a fixed, versioned 28-question catalogue under
  benchmarks/evaluation/prompts_v1.json: 16 supported requests with exact
  expected tool names/arguments and 12 requests that should clarify. Each
  required scope is written in full. Original diagnostic questions are not
  re-labelled as newly answered.
- scripts/evaluate_agent.py builds a two-file, four-head synthetic telemetry
  pool with 48 observed exact +1 events and four cross-file events. H05 has
  four status-65 records and two values beyond configured torque limits;
  others have status-0 records. A separate scalar CSV oracle checks event
  attributes and file-boundary carry before the agent runs.
- Each supported request runs the production planner, scoped data source,
  registered tool, deterministic report and trace. Predeclared planted checks
  verify the two torque flags, H05 descriptive cap-present ranking, peer
  median difference and full-duration observed throughput. The 12 negative
  requests must clarify without opening event data or executing tool calls.
  Legacy unregistered keyword plans count as safe only when the whole plan is
  rejected before load. Case outcomes, including failures, are all recorded.
- Every case has a separate saved report, trace and detailed result in an
  ignored data/integration_smoke/agent-evaluation-* directory. Aggregated
  results go to benchmarks/integration/ and both audit documents. No source
  status decoder, counter rule, tool calculations or production routing changed.
- docs/EVALUATION.md specifies the fixture, run procedure, scores and limits.
  The phrasings were authored with knowledge of supported grammar; success
  on them is a controlled integration result, not broad language accuracy.
  A planted signal is not evidence of a physical fault cause. No live model
  or operating-time inference is evaluated here.

Verification:
- Added six production-path test cases for independent event fields, boundary
  carry, planted anomaly/rank/peer outcomes and a causal question blocked
  before data loading; catalogue shape is checked.
- Pure-Python checks of 28 grammar expectations, scalar field/count truth and
  guarded-installer transactions pass locally. Mac Polars/pytest and the full
  production-path run remain pending.
- Previous full regression: 846 passed in 2.86s. Earlier real-data evidence
  covers scoped KPI, torque and CLI calculations separately.

Next action:
Run .venv/bin/python -m pytest tests -q and the installer with --evaluate.
Then inspect failures (if any), a report and its trace before clean-checkout
review and presentation preparation.

## Integration measurement — fixed new questions and planted signals 20260925-084539-470253

- Fixed new question catalogue: 28/28 cases passed; 16/16 supported plans and 12/12 clarification cases passed.
- Planted counter events matched an independent scalar field oracle, including four cross-file events.
- H05 had four status-65 records and two planted torque limit breaches; describe these as signals, not causes.
- Evidence: benchmarks/integration/agent_evaluation_20260925-084539-470253.json. No live LLM requests.
- Remaining: clean checkout, presentation review, and any explicitly defined causal/operating-time inputs.

## Integration checkpoint — planted anomaly precision and recall (2026-09-26)

- New fixed truth: `benchmarks/evaluation/anomaly_truth_v1.json`, two synthetic two-file fixtures with five predeclared torque-deviation labels over 64 selected observed events.
- New runner and tests: `scripts/evaluate_anomaly_ground_truth.py`, `tests/test_anomaly_ground_truth.py`; selected event fields are checked independently before production planner/source/registered anomaly execution. The results, reports and traces remain separately inspectable.
- Measurement on uploaded archive: TP 4, FP 0, FN 1, TN 59; precision 100%, recall 80%, false positive rate 0% on **synthetic deviations**, not physical fault labels. One in-range 2.3 Nm planted deviation is missed. Evidence: `benchmarks/integration/anomaly_ground_truth_v1.json`.
- Full suite on uploaded archive: 851 passed, 4 skipped. App production functions remain unchanged.
- Added detailed root README and previously missing Person A, Person B and integration references under `docs/`; evaluation methods are recorded in `docs/EVALUATION.md`.
- Next: install changed files on actual Git `main`, run tests and the new evaluation, inspect the diff and share the exact commit and rubric before final grading claims.

## Proposal follow-up: idle, figures and sample deliverables (2026-10-08)

- The integrated idle tool now reads scoped *raw* status rows. All heads must
  indicate No Load for the configured number of consecutive observed seconds;
  gaps and duplicates break continuity. Tests include a cross-file run.
- `--plots` generates PNGs from successful tool outputs and links them in
  Markdown. Synthetic sample reports demonstrate histogram, head KPI and
  all-head No Load intervals.
- Temporal tools also report a separate incremental, unweighted average of
  bucket rates. `docs/METHODS-AND-LIMITS.md` records the event and denominator
  definitions relevant to the professor's illustrative examples.
- Uploaded snapshot regression after changes: 856 passed, 4 skipped. These
  changes have not been verified on the user's Mac Git checkout or original
  full raw telemetry pool.

## Presentation draft and handoff (2026-10-08)

- Added a validated 13-slide editable deck and rehearsal notes under
  `docs/presentation/`. Charts in the slides are synthetic and are labelled;
  recorded real-data numbers retain their named scope.
- Remaining handoff: install this snapshot in the user's Git checkout, rerun
  tests and the new `scripts.verify_idle_real` original-data check, inspect the PPTX in the presentation app,
  and rehearse the live demo.

## Raw-status float compatibility fix (2026-10-08)

- First original-CSV idle check stopped before dispatch because raw Parquet
  status columns can be Float64 even after the event builder validates the
  whole-number codes for its internal frame.
- The raw idle source now accepts integer or float status columns and verifies
  finite, exactly representable whole-number values in the selected window.
  Fractional or invalid values stop the analysis without a tool call.
- Added real-schema regression and fractional-status rejection tests. Snapshot
  suite: 858 passed, 4 standalone-repository skips. The bounded original-CSV
  comparison must be repeated on the user's Mac after installing this fix.

## Hosted website, shared uploads, per-day heads and direct answers (2026-10-19, `pro-ui`)

- Website hosted on Streamlit Community Cloud from the fork `AnthonyChedid7/arol-telemetry-ai` (the original repository is a personal repository, where only the owner can deploy). The owner's Anthropic key, Supabase settings and an upload password are Streamlit Secrets.
- Shared data: daily CSVs appended with `append_to_event_pool` (equal to a full build; verified on the real Feb 1-5 files) and kept in a private Supabase bucket via `src/common/pool_store.py`; partitions are written before the manifest; the site re-reads the store at most once a minute. Session-only uploads build a throwaway pool per browser session.
- `head_success_by_day`: per-day head results, one lazy aggregation per day (10 M real events over 15 days in about 7 s); ties listed; partial days flagged. `rank_heads_by_success` and `compare_head_success` are aggregated while scanning (no event limit).
- Direct answers (`src/agent/answer.py`): sentence plus the asked form (chart, head x day table, per-head table, per-day table, interval table, or sentence). Claude may word it from the computed results; any number not found in the results rejects Claude's wording. An 11-question audit went from 0 direct answers (2 failures) to 11 direct answers.
- Presentation from wording: failure/error focus and best/worst direction (`Plan.focus`, `Plan.extreme`).
- `app/module_refresh.py`: changed project modules (namespace packages included) are re-imported after a deploy, using import-time file stamps; found after a live `AttributeError` caused by a half-pulled deploy.
- Suite: 1,028 passed, 4 skipped (Windows, Python 3.14.3).
