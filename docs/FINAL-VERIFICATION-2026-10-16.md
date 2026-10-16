# Final local verification — AROL `audit-followups-release-fixes`

Run at 2026-10-16 12:19 UTC on macOS, Python 3.14.7, from
`/private/tmp/arol-audit-followups-review`. These checks ran on the complete
local repair tree derived from published
`audit-followups` commit `baddff1c4de3d822d454711c3ab0ecb767632c98`,
before that tree was committed on `audit-followups-release-fixes`. The original
checkout remained clean on `main` at
`93b4a6d016671baddb4b9c0d07d9422abf771a0a`. The results describe the
new branch's code and tests; the original GitHub branch was not changed.

| Command or check | Final observed result |
| --- | --- |
| `/private/tmp/arol-review-lock-venv/bin/python -m pytest tests -q` | **948 passed, 4 skipped**, exit 0, 10.49 s; 33 Matplotlib/NumPy deprecation warnings. The production suite includes planner and Streamlit AppTest coverage. |
| `/private/tmp/arol-review-lock-venv/bin/python -m scripts.evaluate_agent` | **28/28 passed**: 16 supported cases and 12 clarifications without data loading; exit 0. |
| `/private/tmp/arol-review-lock-venv/bin/python -m scripts.evaluate_anomaly_ground_truth` | TP 4, FP 0, FN 1, TN 59; precision 1.0, recall 0.8; exit 0. |
| `/private/tmp/arol-review-lock-venv/bin/python -m pip check` | No broken requirements found; exit 0. The pip-cache permission warning did not affect this check. |
| `git diff --check` | Passed; exit 0. |
| `/private/tmp/arol-review-lock-venv/bin/python -m scripts.check_clean_checkout` after committing | **PASS:** a temporary detached checkout passed 948 tests with 4 skips and the 28/28 controlled evaluation. The temporary checkout was removed. |
| `python -m scripts.demo_agent --manifest <original private manifest> --planner rules --question 'show me the failure rate for each head' --json` | **ok**, one `success_rate_per_head` call, 36 head groups, 1,068 rejection flags / 31,430,108 confirmed cap-present events = 0.003398015686% overall; exit 0. The 54,722,936 recorded exact `+1` events were aggregated lazily. The full structured response was generated under ignored `data/release_review/` and is not included in this commit. |

An independent direct event-Parquet scan of status 65 over statuses 0 and 65
previously matched the per-head rejection and cap-present counts for all 36
heads. The rates describe the recorded pool, not live machine operation or
total physical production. “Failure” here means the explicit cap-present
rejection-flag rate; No Load and unknown cap presence are separate.

**Demo verdict:** ready for a bounded local prototype demonstration with these
limitations stated. No `ANTHROPIC_API_KEY` was available, so current live Claude
behavior was not tested. Four tests require the separate Person B repository.
The anomaly evaluation missed one planted anomaly. The official Q3 cloud-sync
and decentralized-agent goals are not established by these local tests. The
post-commit clean-checkout verification confirms that the committed code and
tests also pass from a fresh detached checkout.

Full findings: `docs/INDEPENDENT-RELEASE-REVIEW-2026-10-16.md`. This report
is committed with the local source fixes on the new branch; the original
checkout and private telemetry files were not changed.
