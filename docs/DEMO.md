# AROL integrated agent demo

Run from the repository root with the project's Python environment. The event
pool contains observed exact +1 counter events from the supplied CSVs. It is
generated data under `data/`, so a new checkout needs the original telemetry
files and a locally built pool before the demo can run. The 89-file pool used
for the integration checks is at
`data/event_pools/continuous-20260924-080513-930170/manifest.json` on the
verification machine. The code never downloads the raw data.

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m scripts.demo_agent --examples
.venv/bin/python -m scripts.demo_agent --question \
  'Average torque for head 5 for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T12:00:00 for successful closures'
```

Without `--question` the command opens an interactive prompt; type `exit` to
finish. It selects the sole complete manifest under `data/event_pools/`. If you
have more than one local pool, supply `--manifest data/event_pools/.../manifest.json`.
Each question writes a Markdown report and JSON trace into a separate folder
inside a new session under `data/demo_runs/`. The directory is ignored by Git.
Use `--output DIR` to select another parent directory. Add `--json` to a
scripted `--question` for one JSON object per question. A command with any
clarification or degraded outcome exits with status 2 and still saves a trace.
Pass `--plots` on a chartable analysis to save PNG figures linked from the
Markdown report. A fresh checkout also includes three [synthetic sample
reports](samples/README.md) with figures and a documented generator.

To compare one short idle window directly with the original CSV status rows
after installing this update, run from the project root (change `--csv-dir`
if the 89 original telemetry CSVs are elsewhere):

```bash
.venv/bin/python -m scripts.verify_idle_real \
  --manifest data/event_pools/continuous-20260924-080513-930170/manifest.json \
  --csv-dir data \
  --start 2026-02-01T15:55:00 --end 2026-02-01T16:05:00
```

It checks the original CSV hashes in the manifest and compares an independent
raw-status loop with the integrated `machine_idle` result. A matching result
with zero qualifying periods verifies this window's computation; it does not
establish whether there was a qualifying idle interval elsewhere in the pool.

Try a bounded success-rate question or a daily observed-throughput question:

```bash
.venv/bin/python -m scripts.demo_agent --question \
  'Success rate for head 5 for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T12:00:00'
.venv/bin/python -m scripts.demo_agent --question \
  'Observed throughput by hour for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T01:00:00'
.venv/bin/python -m scripts.demo_agent --plots --question \
  'Machine idle for machine MCC777eda3db57348ef8a3113a642ae74db from 2026-02-01T00:00:00 until 2026-02-01T01:00:00'
```

The CLI defaults to deterministic rules. `--planner diagnostic` adds a
head-health answer from registered peer-success and torque-anomaly results.
It requires an explicit machine and bounded time window in the CLI. The web
demo shows its selected machine and can use it for this diagnostic; an
unbounded question asks for a window before loading data. The page shows the
direct answer first; the evidence-based diagnostic, detailed report, trace and
tool results are behind *Show details for further investigation*.
`--planner llm` remains an experimental single-tool torque planner. Its
existing scope validator rejects head-health and KPI questions; the web demo
does not present it as a general answer mode.

`--planner claude` (and the website's default when `ANTHROPIC_API_KEY` is set)
lets Claude read everyday wording, such as "is spindle 4 behaving normally on
Feb 1 between 9 and 10?", and choose among all registered tools, then words
the direct answer from the computed results (every number checked; see the
README). Check the report's "Interpreted as" line. Without a key or internet,
the deterministic rules answer and the trace says `claude->rules (fallback)`.
For a reliable live demo, set the key in the terminal that starts Streamlit (or
in the hosted app's Secrets) and keep the rules switch in the sidebar as the
offline backup.

The event source caps loaded rows using `data.max_loaded_events` (default
1,000,000) for tools that need individual rows (histogram, trend, anomalies,
time buckets, idle); a broad request for those asks for narrower scope without
collecting the entire 54,722,936-event pool. Totals, head rankings, peer
comparisons and per-day head results have no such cap: they are aggregated
while scanning, per-day results one day at a time. Capping KPIs report explicit
cap-present denominators and unknowns. Throughput measures recorded exact +1
events per requested duration; it does not measure total physical production,
sensor coverage, operating time, downtime, or fault causes. Timestamps are used
exactly as stored; the team contract defines them as UTC, and no timezone or
daylight-saving conversion is applied.

## Presenting with the hosted website

The hosted site (see [HOSTING.md](HOSTING.md)) answers in plain words with the
owner's key. A run-through that shows each answer form, verified on the shared
Feb 1-5 data with Claude on 2026-10-19:

| Ask | Expect |
| --- | --- |
| can you now give me the performance of each head please on each day? | One sentence, then a table of all 36 heads on each of the 6 days plus an *All heads* row |
| give me the highest success rate overall | "10 heads tied for the highest success rate at 100% ..." with the heads named |
| plot the success rate of each head | One sentence and the per-head chart |
| how many rejects did head 22 have on Feb 3? | "H22 had 3 rejects on 2026-02-03 ..." |
| give me the lowest error rates on each day | 0% on every day, shared by 19 to 36 heads, with a per-day table |
| which head has the lowest success rate for each day? | The lowest head per day, ties stated |

Then open *Show details for further investigation* to show the key figures,
the full six-section report, the trace and the downloads. To show uploads,
choose *My own CSV files (this session only)* and load one daily CSV: it is
analysed for that session only and never added to the shared data. Switch
*Question understanding* to *Rules only* to show the offline backup (it
answers the documented phrasings, such as the example list).

For a fresh checkout with the original CSVs available locally, build a pool
once with `python -m scripts.build_event_pool --help`; do not put raw telemetry
or built pools in Git. The project audit and `benchmarks/integration/` contain
the reproducible comparison methods and results from the verification machine.
