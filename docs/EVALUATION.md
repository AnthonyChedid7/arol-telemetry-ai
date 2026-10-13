# Controlled agent evaluation

Run from the repository root after the full tests pass:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m scripts.evaluate_agent
```

The evaluator reads the fixed questions in
`benchmarks/evaluation/prompts_v1.json`. These are **16 new supported
phrasings** with exact expected tool names and arguments, plus **12 requests
that should stop for clarification**. It records each result, including any
failure, in `agent_evaluation.json` inside its run folder under ignored
`data/integration_smoke/agent-evaluation-*/`, together with complete case
results, reports, traces, synthetic CSVs and the event-pool manifest. Add
`--record` to also save `benchmarks/integration/agent_evaluation_*.json` and
append a checkpoint to the audit documents (this changes tracked files).

The run creates two tiny synthetic telemetry CSVs with four heads. Each head
has twelve observed exact +1 events. Four events per machine occur at the
boundary between CSVs. H05 has four status-65 events, eight status-0 events,
and two torque measurements deliberately outside the configured 1.5–2.5 Nm
interval. The other three heads have twelve status-0 events each. An
independent scalar CSV scan checks the event count, boundary carry, status,
torque and decoded fields before any question is evaluated.

For every supported question, the evaluator checks the complete plan, selected
scope, tool response, report, trace and the planted signals where applicable.
The resulting head ranking is a descriptive cap-present success comparison;
threshold flags identify the planted values. Neither result establishes why
a head failed, whether the machine was operating, or whether other physical
closures were missed. Ambiguous requests must produce zero tool calls and must
not open the event pool. No live LLM request is made.

This set is new relative to the original 30 diagnostic questions, but the
authors knew the supported grammar while writing it. Its pass rate is a
controlled integration measure, **not** an estimate of success on arbitrary
language. The earlier real-data and live-model evidence remains separate in
`benchmarks/integration/`. A presentation should show the case results and a
saved trace as well as any score; report unexpected failures rather than
silently deleting a case.

## Planted anomaly ground-truth measurement

From the repository root, run a second, **separate** evaluation:

```bash
.venv/bin/python -m scripts.evaluate_anomaly_ground_truth
```

The frozen labels and fixture specifications are in
[`benchmarks/evaluation/anomaly_truth_v1.json`](../benchmarks/evaluation/anomaly_truth_v1.json).
The script builds two synthetic two-file pools, verifies observed events
against an independent scalar read of the CSVs, asks the production rules
planner for anomaly flags, and matches returned `(timestamp, machine_id,
head_id)` identifiers to the predeclared positive labels. Identical torque
values alone never establish identity. The original four-head fixture has
two out-of-range H05 values among 12 H05 events and retains four cross-file
events across all heads. A separate 52-event H05 fixture has three planted
deviations: two out of range and one **2.3 Nm** contextual deviation within
the configured 1.5–2.5 Nm limits. The latter is intentionally harder for a
pooled mean/standard-deviation rule to detect.

The script saves a JSON summary and each case's CSV files, manifest, report
and trace under ignored `data/integration_smoke/anomaly-truth-*/`. Pass
`--evidence PATH` to choose a JSON destination. The first measurement on the
uploaded source ZIP is retained as
[`benchmarks/integration/anomaly_ground_truth_v1.json`](../benchmarks/integration/anomaly_ground_truth_v1.json).

| Case | Observations | True positive | False positive | Missed positive | True negative |
| --- | ---: | ---: | ---: | ---: | ---: |
| Existing two-file fixture, H05 | 12 | 2 | 0 | 0 | 10 |
| In-range contextual deviation, H05 | 52 | 2 | 0 | 1 | 49 |
| **Combined** | **64** | **4** | **0** | **1** | **59** |

**Precision = TP/(TP+FP) = 100%; recall = TP/(TP+FN) = 80%; false positive
rate = FP/(FP+TN) = 0%** on these planted labels. The missed 2.3 Nm value is
evidence of a limitation, not an input to retune the algorithm after looking
at the answers. The second label represents a deliberately planted torque
deviation relative to fixture baseline; neither label set is independent
ground truth for physical cap faults. These small synthetic rates cannot be
generalized to factory data, other thresholds, or unknown failure prevalence.
