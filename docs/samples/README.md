# Generated sample reports

These three reports use the synthetic two-file `DEMO` machine created by
`python -m scripts.generate_sample_reports`. They contain no private AROL
telemetry. Each report names its exact question, starts with the direct
**Answer** (and its table when the question asks for one), and includes a PNG
figure where the tool returned chartable results.

- [Torque statistics and histogram](torque-and-distribution.md)
- [Per-head KPI fractions](head-kpis.md)
- [All-head No Load candidate](all-head-no-load.md)

The values describe this fixture only. On an actual data pool, use the CLI
with `--manifest`, `--question`, and `--plots` to create new reports and a
JSON tool-call trace. The image filenames in this directory are stable, while
generation timestamps and runtimes inside reports vary between reruns.
