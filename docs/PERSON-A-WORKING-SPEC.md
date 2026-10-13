# Person A — Working Spec

> **Historical record (note added 2026-10-13).** The "being refactored" status below describes the time of writing; the Parquet + Polars data path is implemented and is the production path. For the current state see `README.MD`.

**Project:** Agentic AI for Telemetry Analysis on AROL Capping Machines  
**Role:** Person A — data pipeline, statistical analytics, and the tool interface the agent reasons through.  
**Architecture amendment:** After completing Step 9 with 70 passing regression tests, the production data path is being refactored to **Parquet + Polars Lazy** before Step 10.

---

## How to use this file

This is the working implementation specification for Person A.

For each step:
- **Build** describes what should exist and why.
- **Audit** defines the evidence required before moving on.

The original Steps 4–9 were built first for correctness and understanding. Their 70 tests now act as a behavioral specification while the internals are optimized for the much larger real AROL dataset.

**Learning rule:** the closure-detection semantics in Step 6 must remain fully understandable and explainable. The optimized vectorized implementation may change how the logic executes, but it may not change what counts as a closure.

---

# Non-negotiable design principles

## 1. The language model never does arithmetic

Every reported number comes from a deterministic tested function. The agent selects tools and explains their outputs.

## 2. Correctness before optimization

Optimization is accepted only when the same behavioral tests continue to pass.

```text
reference implementation
        ↓
70 tests define behavior
        ↓
optimized implementation
        ↓
same behavior + measured performance improvement
```

## 3. One production dataframe engine

The production pipeline uses **Polars**.

Do not create a redundant chain of processing engines. pandas may exist only as a temporary compatibility boundary if Person B explicitly requires it.

## 4. Canonical persisted format = Parquet

AROL CSV remains the immutable/raw source. Repeated processing should operate on Parquet rather than re-parsing large CSV files on every run.

## 5. Large telemetry must be columnar/vectorized

No Python-level loop over every raw row in the production implementation. Prefer Polars expressions, `shift`, filters, aggregations, and lazy scans.

---

# Architecture choice and evidence

## Canonical stack

| Layer | Technology |
|---|---|
| Raw input | AROL CSV |
| Canonical persisted data | Parquet |
| Processing/analytics | Polars LazyFrame / DataFrame |
| Config | PyYAML |
| Tests | pytest |
| Agent exchange | JSON-serializable dictionaries |

PyArrow may remain installed for Parquet/Arrow compatibility, but it is not treated as a second analytics engine.

DuckDB, Spark, Dask, and Modin are intentionally excluded unless a later benchmark demonstrates a distinct unmet requirement.

Official references used to defend the choice:
- Apache Parquet overview: https://parquet.apache.org/docs/overview/
- Polars lazy API: https://docs.pola.rs/user-guide/concepts/lazy-api/
- Polars query optimizations: https://docs.pola.rs/user-guide/lazy/optimizations/
- Polars lazy usage / streaming: https://docs.pola.rs/user-guide/lazy/using/

## Performance claim rule

Do not write “Polars is X times faster” based on someone else's machine.

Benchmark representative AROL pools and report:
- rows / files / GB;
- CSV vs Parquet source;
- total runtime;
- phase runtimes;
- peak memory when practical;
- exact hardware/software environment.

---

# Dependency tags

- **[SOLO]** — Person A can build independently.
- **[SYNC]** — shared decision with Person B.
- **[CONSUMES B]** — depends on Person B output.
- **[FEEDS B]** — Person B consumes this output.
- **[PERF]** — performance-sensitive; benchmark and avoid row-wise Python.

---

# Milestones

| Milestone | Steps | Definition of done |
|---|---|---|
| **M0 — Contract locked** | 1–3 | schema, Polars tool input, config/storage contract agreed |
| **M1 — Data loads & validates** | 4–5 | raw CSV is ingested to canonical Parquet; validation executes with Polars |
| **M2 — Event table exists** | 6–9 | vectorized closure/event pipeline produces deterministic clean Polars event table and Parquet |
| **M2.5 — Performance refactor verified** | after 9 | refactored Steps 4–9 preserve behavior and benchmark successfully |
| **M3 — Analytics complete** | 10–14 | deterministic Polars analytics functions tested |
| **M4 — Agent can use my tools** | 15–17 | tool schemas and agent selection verified |
| **M5 — Proven & documented** | 18–20 | evaluation, methods, confidence limits |
| **M6 — Integrated** | 21–22 | real pipeline + agent end-to-end |

---

# Partner touchpoints

| Step | Tag | Person B interaction |
|---|---|---|
| 1 | SYNC | final event schema |
| 2 | SYNC | Polars event-table input / one-time compatibility boundary |
| 3 | SYNC | shared config |
| 9 | FEEDS B | clean event table contract |
| 15 | FEEDS B | tool schemas |
| 17 | CONSUMES B | real agent loop |
| 18 | CONSUMES B | synthetic fault ground truth |
| 20 | FEEDS B | caveats/confidence fields |
| 21 | SYNC | pipeline-agent integration |
| 22 | SYNC | joint demo |

---

# Phase 1 — Contract (M0)

## Step 1 — Event-table schema
`[SYNC]`

Canonical event fields:

| column | type | meaning |
|---|---|---|
| `ts` | datetime | timestamp of increment row; preserve naive source timestamp until timezone is known |
| `machine_id` | string | machine identifier |
| `head_id` | string | head prefix such as `H01` |
| `torque` | float | closure torque in Nm |
| `status` | integer | raw status |
| `error_class` | string | decoded semantic class |
| `reject_signal` | bool / null | reject meaning when known |
| `cap_present` | bool / null | cap presence when explicitly known |

Known brief codes:
`0, 2, 3, 4, 5, 8, 9, 16, 17, 32, 33, 64, 65`.

Unknown codes must be flagged as `Unknown (<code>)`.

**Audit**
- [ ] fields/types match `docs/contract.md`;
- [ ] unknown codes are explicit;
- [ ] timezone semantics are documented, not guessed;
- [ ] Person B confirms the shape.

## Step 2 — Tool signature convention
`[SYNC]`

Canonical internal analytics signature:

```python
import polars as pl

def function_name(events: pl.DataFrame, **params) -> dict:
    ...
```

For very large persisted event datasets a function may use a LazyFrame internally, but outputs remain JSON-serializable dictionaries.

**Audit**
- [ ] Person B knows the input is Polars;
- [ ] no repeated Polars↔pandas conversion;
- [ ] all outputs are plain JSON-safe Python values;
- [ ] `meta.n` exists.

## Step 3 — Configuration
`[SYNC]`

Config must include:
- raw pool paths;
- canonical Parquet root;
- event Parquet root;
- partition strategy;
- head auto-detection;
- units;
- analytics thresholds;
- processing engine settings.

No absolute dataset path may appear in production functions.

---

# Phase 2a — Data pipeline (M1 → M2)

## Step 4 — Raw ingestion and canonical Parquet loader
`[SOLO] [PERF]`

### Build

Separate source ingestion from repeated analytical loading.

1. Raw AROL CSV is scanned/read with Polars.
2. Timestamp is parsed deterministically.
3. Machine/date metadata is attached from explicit pool/file metadata.
4. Raw data is written once/incrementally into canonical Parquet partitions.
5. Repeated processing uses `pl.scan_parquet(...)` and remains lazy as long as possible.

Small CSV fixtures remain in tests so the source-ingestion path is covered.

JSON compatibility may remain if already implemented, but it is not part of the production AROL path and must not complicate the core architecture.

### Audit

- [ ] config drives all paths;
- [ ] CSV source ingestion works;
- [ ] canonical Parquet is created/read correctly;
- [ ] repeated pipeline runs can start from Parquet;
- [ ] timestamp dtype is correct;
- [ ] missing/corrupt files have clear errors;
- [ ] no full eager materialization without a reason;
- [ ] benchmark CSV source scan vs Parquet scan.

---

## Step 5 — Vectorized/lazy validation
`[SOLO] [PERF]`

### Build

Rewrite validation with Polars expressions/LazyFrame:
- null counts;
- duplicate timestamps;
- out-of-order timestamps;
- timestamp gaps;
- Count integer semantics;
- Status integer semantics;
- AppTorque numeric type;
- units/config metadata.

Validation reports issues and does not silently modify data.

### Audit

- [ ] existing validation semantics preserved;
- [ ] injected defects still produce the expected report;
- [ ] no Python loop over every raw row;
- [ ] validation collects only the small results needed for the report;
- [ ] bad data is reported rather than silently corrected.

---

## Step 6 — Vectorized closure detection
`[SOLO] [PERF]`

### Semantic rule

For one head, a closure exists only where:

```text
current Count == previous Count + 1
```

Torque, status, and timestamp come from the current/increment row.

### Production implementation

Use Polars column expressions conceptually equivalent to:

```text
Count - Count.shift(1)
```

and filter where the delta is exactly `1`.

A small loop over detected head IDs is acceptable. A Python loop over every raw row is not.

### Edge-case rules

- hold: no closure;
- +1: one closure;
- first row: no closure;
- status change during hold: no closure;
- jump >1: do not fabricate events;
- decrease/reset: no spurious event;
- stitched-file boundary +1: detect normally.

### Audit

- [ ] all original closure tests pass;
- [ ] output is equivalent to the reference implementation;
- [ ] no `.iloc`/row loop in production path;
- [ ] boundary continuity preserved;
- [ ] benchmark closure detection on representative real data;
- [ ] logic remains explainable at defense.

---

## Step 7 — Vectorized status decoding and event assembly
`[SOLO] [PERF]`

### Build

Decode statuses using Polars expressions or a small mapping table/join rather than Python work per event row.

Known mapping:

| status | error_class | reject |
|---:|---|---:|
| 0 | Closure OK | false |
| 2 | No Load | false |
| 3 | No Load | true |
| 4 | No Closure | false |
| 5 | No Closure | true |
| 8 | No InTorque | false |
| 9 | No InTorque | true |
| 16 | No CapTurns | false |
| 17 | No CapTurns | true |
| 32 | Following Error | false |
| 33 | Following Error | true |
| 64 | Bad Closure | false |
| 65 | Bad Closure | true |

`cap_present`:
- `0 -> true`
- `2 -> false`
- `65 -> true`
- other known/unseen codes -> null unless semantics are explicitly agreed.

Unknown status:
- `error_class = "Unknown (<code>)"`
- reject/cap flags null.

### Audit

- [ ] all status tests pass;
- [ ] one closure -> one event;
- [ ] mapping is vectorized;
- [ ] unknown codes never crash;
- [ ] output schema matches Step 1.

---

## Step 8 — Timestamp and capping speed
`[SOLO] [PERF]`

### Build

Preserve the increment-row timestamp end-to-end.

Capping speed remains defined as:

```text
closures in interval × 3600 / interval_seconds
```

and the running mean must be mathematically equivalent to the existing incremental-average implementation.

For large event tables, use Polars group/aggregation/cumulative expressions rather than Python loops where practical.

### Audit

- [ ] timestamp integration test passes;
- [ ] hand-calculated running-average test passes;
- [ ] zero interval is rejected;
- [ ] empty input is handled;
- [ ] units remain pieces/hour;
- [ ] semantics match the reference implementation.

---

## Step 9 — Final clean Polars event table + event Parquet
`[SOLO] [FEEDS B] [PERF]`

### Build

Produce the final clean **Polars DataFrame** and persist the reusable event dataset as Parquet.

Requirements:
- auto-detect heads;
- one row per closure;
- all heads combined;
- sort deterministically by `ts`, then `head_id`;
- same-timestamp events on different heads are retained;
- empty result still has the exact schema;
- event Parquet can be scanned directly by Steps 10–14.

### Audit

- [ ] all existing Step 9 behavioral tests pass after Polars adaptation;
- [ ] deterministic output;
- [ ] expected Polars dtypes;
- [ ] persisted event Parquet round-trips exactly;
- [ ] Person B can consume the agreed event-table contract.

> **M2 is complete only after the optimized implementation passes the behavioral regression suite.**

---

# M2.5 — Mandatory performance-refactor gate before Step 10

This is not a new numbered project deliverable; it is the verification gate created by the architecture amendment.

## Required work

1. Install/pin Polars.
2. Refactor Steps 4–9 to the architecture above.
3. Keep the existing behavioral test intent.
4. Add Parquet round-trip tests.
5. Benchmark representative real AROL data.
6. Compare at minimum:
   - reference pandas + CSV pipeline;
   - Polars + CSV;
   - Polars + Parquet.
7. Record runtime and memory where possible.
8. Do not begin Step 10 until correctness passes and the benchmark is recorded.

## Required evidence

```text
dataset / size:
hardware:
Python:
Polars:
reference runtime:
Polars CSV runtime:
Polars Parquet runtime:
speedup:
peak memory:
tests passed:
```

Do not pre-write the speedup value. Fill it with actual measurements.

---

# Phase 2b — Statistical analysis (M3)

All analytics consume the clean Polars event table / event Parquet, never raw wide telemetry.

## Step 10 — Torque statistics
`[SOLO]`

```python
def torque_stats(events: pl.DataFrame, status_filter=None) -> dict:
    ...
```

Return mean, min, max, standard deviation, and sample size.

**Audit**
- [ ] hand-computed example matches;
- [ ] filter changes result correctly;
- [ ] JSON-safe plain Python output;
- [ ] no raw telemetry access.

## Step 11 — Torque distribution
`[SOLO]`

Return histogram bin edges and counts as data, not a plot.

**Audit**
- [ ] configurable bins;
- [ ] counts sum to events considered;
- [ ] JSON-safe output.

## Step 12 — Trend analysis
`[SOLO]`

Moving average and drift signal using event timestamps.

**Audit**
- [ ] synthetic upward drift fires;
- [ ] flat synthetic data does not;
- [ ] window comes from config.

## Step 13 — Anomaly detection
`[SOLO]`

Threshold + statistical-deviation detection.

**Audit**
- [ ] reason attached to each anomaly;
- [ ] thresholds from config;
- [ ] planted anomalies detected;
- [ ] false-positive rate measured.

## Step 14 — Head correlation
`[SOLO]`

Compare heads using deterministic statistics.

**Audit**
- [ ] unequal event counts handled;
- [ ] interpretable metrics;
- [ ] synthetic similar/dissimilar heads verified.

> **M3 reached:** deterministic analytics complete.

---

# Phase 3 — Agent tool interface (M4)

## Step 15 — Tool schemas
`[SOLO] [FEEDS B]`

Write clear tool schemas matching the actual Polars-backed analytical functions.

## Step 16 — Diagnostic prompts
`[SOLO]`

Test query families such as:
- which head behaves differently?
- why is head 4 failing more?
- compare head 1 and head 2.

The agent explains tool outputs; it never invents calculations.

## Step 17 — Verify tool selection
`[CONSUMES B]`

Run real questions through Person B's agent and correct tool descriptions where selection is wrong.

---

# Phase 4 — Evaluation and documentation (M5)

## Step 18 — Anomaly ground-truth evaluation
`[CONSUMES B]`

Measure precision and recall against planted faults.

## Step 19 — Data schema, methods, and performance documentation
`[SOLO]`

Document:
- event schema;
- closure assumptions;
- Polars/Parquet architecture;
- why extra engines were intentionally excluded;
- benchmark methodology;
- measured runtime/memory results;
- analytics methods.

Do not claim performance numbers that were not measured on this project.

## Step 20 — Confidence and limits
`[SOLO] [FEEDS B]`

Return caveats and sample-size limitations that Person B's report assembler can surface.

---

# Phase 5 — Integrate and present (M6)

## Step 21 — Real end-to-end run
`[SYNC]`

Person B's synthetic event source is replaced by Person A's real Parquet/Polars event pipeline without downstream analytical changes.

## Step 22 — Demo and slides
`[SYNC]`

Person A presents:
- raw-to-Parquet architecture;
- vectorized closure reconstruction;
- event-table contract;
- measured performance benchmark;
- deterministic analytics;
- anomaly evaluation.

Person B presents the agent/interface side.

---

# Order that matters most

```text
contract
→ raw ingestion
→ Parquet
→ lazy validation
→ vectorized closure detection
→ event assembly
→ clean event Parquet
→ benchmark/refactor gate
→ analytics
→ agent tools
→ evaluation
→ documentation
→ integration
```

---

# Session checklist

- [ ] I know whether the current change affects a shared contract.
- [ ] I can explain what the function does and why.
- [ ] I did not add a second processing engine without a measured reason.
- [ ] Large-data logic is expression/vector based, not Python row iteration.
- [ ] Behavioral tests pass.
- [ ] Performance-sensitive changes are benchmarked on representative data.
- [ ] Tool outputs are JSON-safe.
- [ ] I committed the work before moving to the next step.
