# Presentation rehearsal

[`AROL_Telemetry_Project_Review.pptx`](AROL_Telemetry_Project_Review.pptx)
contains 13 editable slides. Slides 8 and 10 use the **synthetic** example
figures in `docs/samples/`; slide 9 uses the recorded real-data H05 KPI
window. Keep those populations distinct when presenting.

1. Explain the offline CSV to Parquet pool builder and its manifest. An exact
   counter increment of +1 creates an observed event. Other jumps cannot be
   converted into a known number of individual closures.
2. Walk through the question, scoped source, registered tool, report and trace.
   Rules routing is the reproducible default. The optional local model has a
   scope validator, but a prior successful prompt is not a general accuracy
   guarantee.
3. Open `docs/samples/torque-and-distribution.md` and one PNG for a safe
   demonstration that needs no private data. If original local data are
   available, follow `docs/DEMO.md` and show a generated report and trace.
4. On slide 9, name both denominators: 7,575 confirmed cap-present events and
   13,167 observed events for the specified H05 window. A 100% cap-present
   success fraction does not mean every observed event was status 0.
5. On slide 10, say "candidate all-head No Load interval." The detector needs
   consecutive per-second raw statuses and treats gaps as breaks. Confirm
   status codes and operating schedule before calling it downtime.
6. End with the measured test/evaluation scope and the unresolved physical
   interpretation, counter discontinuities and timestamp timezone.

The deck predates the hosted website, shared uploads, per-day head results and
direct answers (2026-10-19). To show them live, use the run-through in
[`docs/DEMO.md`](../DEMO.md#presenting-with-the-hosted-website): each question
there shows a different answer form, and *Show details for further
investigation* leads back to the report and trace described in step 2.

Before presentation on the actual Mac checkout, run
`.venv/bin/python -m pytest tests -q`, then a short `--plots` demo from
`docs/DEMO.md`. Verify that the PPTX opens, the chart PNGs appear, and the
real telemetry remains under the ignored `data/` directory.
