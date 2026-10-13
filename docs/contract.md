# Project Contract — Shared Interfaces

> **Status note (added 2026-10-13).** The Polars/Parquet architecture this amendment introduces is implemented and integrated. The "required action" below records the request made at the time; whether it was formally re-confirmed is not recorded here. The definitions in this contract are what the code follows. One audit observation concerns it: about 0.03% of events show the controller updating Status one reading after the count, so the "status on the increment row" rule is followed and those events are flagged in reports, not changed (see `AUDIT-REPORT.md`).

**Project:** Agentic AI for Telemetry Analysis on AROL Capping Machines  
**Status:** AMENDED PERFORMANCE CONTRACT (v2) — Steps 4–9 were first completed with a pandas reference implementation and 70 regression tests; this version updates the shared architecture before Step 10.  
**Required action:** Person B must re-confirm the shared event-table/tool-input contract because the canonical in-memory table changes from pandas to Polars.

> This file defines the interfaces that let Person A's telemetry/analytics layer and Person B's agent layer integrate without ambiguity. It now also fixes the storage and processing architecture so the project has one defensible, non-redundant data path.

---

## What the real data looks like

Observed facts from the supplied AROL telemetry:

- One file = one machine (`MCC777…`), one day, about **86,400 rows** (approximately one sample per second), ~57 MB.
- The inspected files have **36 heads**. Each head contributes:
  - `H## Count`
  - `H## AppTorque`
  - `H## Status`
- One shared `timestamp` column gives 109 columns in the inspected files.
- Counters in the inspected data hold or increment by exactly `+1`.
- Counters continue across day-files, so a pool must be processed as one ordered continuous stream.
- The torque and status belonging to a closure are on the same row as the count increment.
- Observed real-data statuses are `0`, `2`, and `65`.
- The grading/brief status table includes additional known codes, so the decoder must remain extensible.
- The real project dataset is much larger than the development sample; therefore storage format, lazy execution, vectorization, and memory behavior are part of the architecture rather than afterthoughts.

---

# 0. Performance and storage architecture

## Canonical architecture

```text
AROL raw CSV files
        │
        │ one-time / incremental ingestion
        ▼
partitioned Parquet dataset
        │
        ▼
Polars LazyFrame
        │
        ├── validation
        ├── closure reconstruction
        ├── status decoding
        └── event assembly
        │
        ▼
clean event Parquet dataset
        │
        ▼
Polars DataFrame / LazyFrame analytics
        │
        ▼
JSON-serializable tool result
        │
        ▼
Person B's agent
```

## Technology responsibilities

| Technology | Responsibility | Decision |
|---|---|---|
| CSV | immutable/source format supplied by AROL | keep as source only |
| **Parquet** | canonical persisted working/event format | **required** |
| **Polars** | canonical processing and analytics engine | **required** |
| PyYAML | configuration | keep |
| pytest | correctness/regression testing | keep |
| PyArrow | optional Parquet/Arrow plumbing when required by dependencies/interchange | infrastructure only, not a second analytics engine |
| pandas | compatibility only if an external integration absolutely requires it | not part of the production processing path |
| DuckDB | not included by default | add only if a later measured requirement justifies SQL/out-of-core query functionality not already satisfied by Polars |
| Spark / Dask / Modin | not included | distributed/overlapping complexity is not justified by the current single-node workload |

## Why Parquet

Parquet is the canonical persisted format because it is column-oriented, typed, compressed, and designed for analytical retrieval. The telemetry is wide, repeated, and mostly consumed column-wise, making this a better working representation than repeatedly parsing CSV text.

Official reference:
- Apache Parquet overview: https://parquet.apache.org/docs/overview/

## Why Polars Lazy

Polars LazyFrame is the canonical processing abstraction because the query can be optimized before execution. The project specifically benefits from:
- predicate pushdown;
- projection pushdown;
- expression simplification;
- columnar/vectorized execution;
- streaming/batched execution where supported;
- avoiding Python-level row iteration over large telemetry.

Official references:
- Polars lazy API: https://docs.pola.rs/user-guide/concepts/lazy-api/
- Polars query optimizations: https://docs.pola.rs/user-guide/lazy/optimizations/
- Polars lazy usage / larger-than-memory streaming: https://docs.pola.rs/user-guide/lazy/using/

## Non-redundancy rule

The production path uses **one dataframe engine: Polars**.

Do not build a pipeline that repeatedly converts:

```text
Polars → pandas → Polars → pandas
```

If an external consumer requires pandas, convert once at that boundary only. No second analytics engine is added unless a benchmarked requirement proves it is necessary.

## Correctness-before-optimization rule

The completed pandas implementation and its **70 passing tests** are the behavioral reference. The Polars refactor is allowed to change implementation details, not semantics.

For every refactored component:

```text
same synthetic input
    ↓
old reference behavior
    =
new Polars behavior
```

All behavioral tests must continue to pass, adapted only where the dataframe type itself changes.

## Performance evidence rule

Do not claim a speedup from general benchmarks alone. Benchmark representative AROL data and record:
- input size;
- source format;
- load/scan time;
- validation time;
- closure/event reconstruction time;
- total runtime;
- peak memory if practical.

The report may claim only measured improvements from this project.

---

# 1. Event-table schema

**One row = one closure event** (one count increment on one head).

This is the stable artifact Person A produces and Person B's tools consume.

| column | type | source | notes |
|---|---|---|---|
| `ts` | datetime | timestamp on the increment row | source timezone is not yet proven; do not silently label naive timestamps UTC |
| `machine_id` | string | parsed from file/pool metadata | retained for future multi-machine support |
| `head_id` | string | head prefix | `H01`, `H02`, ...; head count is auto-detected |
| `torque` | float | `H## AppTorque` on increment row | Nm |
| `status` | integer | `H## Status` on increment row | raw code preserved |
| `error_class` | string | decoded from `status` | semantic status name |
| `reject_signal` | boolean / null | decoded from `status` | null only when an unknown code cannot be classified safely |
| `cap_present` | boolean / null | decoded where semantics are explicitly known | null when the source/brief does not define cap presence safely |

## Known status decoding

| status | error_class | reject_signal | cap_present |
|---:|---|---:|---|
| 0 | Closure OK | false | true |
| 2 | No Load | false | false |
| 3 | No Load | true | null |
| 4 | No Closure | false | null |
| 5 | No Closure | true | null |
| 8 | No InTorque | false | null |
| 9 | No InTorque | true | null |
| 16 | No CapTurns | false | null |
| 17 | No CapTurns | true | null |
| 32 | Following Error | false | null |
| 33 | Following Error | true | null |
| 64 | Bad Closure | false | null |
| 65 | Bad Closure | true | true |

Unknown/unlisted codes:
- `error_class = "Unknown (<code>)"`
- `reject_signal = null`
- `cap_present = null`

Unknown codes are flagged, never silently mapped to a known class.

## OPEN — success-rate denominator

Current proposal:

```text
success rate =
successful cap-present closures
/
all cap-present closures
```

For the actually observed statuses this means:

```text
status 0
/
(status 0 + status 65)
```

No Load (`2`) is excluded because no cap was present.

This remains a joint Person A / Person B decision before KPI reporting is frozen.

## OPEN — timezone semantics

The source timestamps observed so far are timezone-naive. Until the machine/source timezone is documented, the pipeline preserves the timestamp as parsed and does not invent UTC.

If UTC is required later, the source timezone must first be explicitly defined and then converted deterministically.

---

# 2. Tool signature convention

## Canonical input

The internal analytics contract is now Polars:

```python
import polars as pl

def tool_name(events: pl.DataFrame, **params) -> dict:
    ...
```

For very large persisted event datasets, a tool may start from a `pl.LazyFrame` internally, but the public tool contract should remain consistent and documented.

If Person B's current code requires pandas, the integration boundary may convert once. That compatibility conversion is not part of Person A's core processing pipeline.

**This change from pandas to Polars is a shared-interface change and must be communicated to Person B.**

## Output envelope

```python
{
  "result": { ... },
  "meta": {
    "n": <int>,
    "filters_applied": [ ... ],
    "notes": "<optional caveats>"
  }
}
```

Rules:
- always return a dict;
- never return only a printed string or bare number;
- result must be JSON-serializable;
- convert Polars/Arrow scalar values to plain Python `int`, `float`, `bool`, `str`, lists, or dicts before returning;
- `meta.n` is mandatory.

## Error / empty handling

Expected analytical empty/no-match situations return structured results rather than uncaught exceptions:

```python
{
  "result": None,
  "meta": {
    "n": 0,
    "filters_applied": ["head_id=H12"],
    "notes": "No matching events"
  },
  "error": "no cap-present closures for head H12 in window"
}
```

Programming errors and invalid internal states should still fail loudly during development/testing; do not hide bugs behind generic error dictionaries.

---

# 3. Configuration

One shared `config.yaml` remains the source of truth.

Recommended structure:

```yaml
data:
  raw_pools:
    sample:
      - data/raw/sample.csv

  canonical_format: parquet
  parquet_root: data/parquet
  event_parquet_root: data/events
  partition_by:
    - machine_id
    - date

  n_heads: null

  units:
    AppTorque: Nm

processing:
  engine: polars
  lazy: true
  streaming: auto

analytics:
  torque_expected_min: 1.5
  torque_expected_max: 2.5
  drift_window_seconds: 3600
  idle_window_seconds: 300
  anomaly_sigma: 3.0

agent:
  # Person B's settings
```

Rules:
- raw file locations come from config;
- canonical working/event storage is Parquet;
- head count is auto-detected;
- no absolute machine-specific file paths inside functions;
- analytics thresholds are config-driven;
- one shared config remains preferred to prevent configuration drift.

---

# 4. Cross-cutting agreements

1. **Pool = ordered continuous stream.**  
   Day-files belonging to one machine/pool are logically continuous. Boundary closures must be detectable.

2. **Raw CSV is source, not repeated working storage.**  
   Convert once/incrementally to Parquet, then perform repeated development/analytics from Parquet.

3. **Polars is the canonical engine.**  
   Heavy transformations must use Polars expressions/LazyFrame, not Python row loops.

4. **No unnecessary dataframe-engine stacking.**  
   pandas/DuckDB/Spark/Dask are not added merely because they are available.

5. **Vectorization is mandatory for large telemetry.**  
   Per-row Python loops over raw telemetry are forbidden in the optimized production path unless a benchmark proves no viable expression-based equivalent exists.

6. **The placeholder handshake remains.**  
   Person B may work from a synthetic event table, but it must match this schema exactly.

7. **Schema changes remain joint decisions.**  
   No silent changes to field names, types, semantics, or tool input type.

8. **The 70-test baseline is a regression contract.**  
   Performance refactoring is accepted only when correctness remains equivalent.

9. **Benchmark claims must be project-measured.**  
   The final report must distinguish official technology rationale from our own measured runtime/memory results.

---

# 5. Repository layout

Recommended layout after the performance refactor:

```text
src/
  ingestion/
    conversion.py        # raw CSV -> canonical Parquet
    loader.py            # Polars scan/load helpers
    validation.py
    closure_detection.py
    event_assembly.py
    event_table.py

  analytics/
    capping_speed.py
    ...

  agent/
  interface/

tests/
benchmarks/

docs/
  contract.md
  PERSON-A-WORKING-SPEC.md
  PROJECT-AUDIT.md
  schema.md
  methods.md

config.yaml

data/
  raw/                   # ignored except tiny test fixture
  parquet/               # ignored
  events/                # ignored
```

Large raw/canonical telemetry must not be committed to Git.

---

# 6. Sign-off checklist

- [x] Person A accepts Parquet as canonical persisted format.
- [x] Person A accepts Polars as canonical processing/analytics engine.
- [x] Pandas is removed from the production processing path.
- [x] DuckDB/Spark/Dask are intentionally excluded unless a later measured requirement justifies them.
- [x] Existing behavior is protected by the 70-test regression baseline.
- [ ] Person B confirms the Polars event-table/tool-input contract or requests a single compatibility conversion at the integration boundary.
- [ ] Person A and Person B resolve the success-rate denominator.
- [ ] Source timezone semantics are confirmed before any UTC normalization.
- [ ] Person A and Person B confirm the output envelope.
- [ ] The amended contract is committed to `docs/contract.md`.

_Once this amendment is re-signed, refactor Steps 4–9 against this architecture, re-run the complete regression suite, benchmark representative real AROL data, and only then begin Step 10._
