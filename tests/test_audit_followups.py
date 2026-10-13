"""Regression tests for the audit follow-up fixes."""
from datetime import datetime, timezone

import polars as pl
import pytest

from src.common.timeutils import parse_bound
from src.ingestion.adapter import adapt


def test_bound_parser_matches_the_tools_rule():
    assert parse_bound("2026-02-01T06:00:00") == datetime(2026, 2, 1, 6)
    assert parse_bound(None) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_bound("2026-02-01T06:00:00+02:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_bound(datetime(2026, 2, 1, tzinfo=timezone.utc))


def test_unparsed_heads_sort_last_as_documented():
    ts = datetime(2026, 2, 1)
    events = pl.DataFrame({
        "ts": [ts, ts], "machine_id": ["M1", "M1"], "head_id": ["HX", "H02"],
        "torque": [2.0, 2.0], "status": [0, 0], "error_class": ["Closure OK"] * 2,
        "reject_signal": [False, False], "cap_present": [True, True],
    })
    adapted, notes = adapt(events, pool_id="t", redecode=False)
    assert adapted["head_id"].to_list() == ["H02", "HX"]
    assert any("place them last" in note for note in notes)


def test_trend_series_is_thinned_but_drift_uses_every_point():
    from datetime import timedelta
    from src.analytics import registered_analytics  # noqa: F401
    from src.common.runtime import dispatch_tool
    start = datetime(2026, 2, 1)
    n = 5000
    events = pl.DataFrame({
        "ts": [start + timedelta(seconds=i) for i in range(n)],
        "machine_id": ["M1"] * n, "head_id": ["H01"] * n,
        "torque": [2.0 + i * 1e-5 for i in range(n)], "status": [0] * n,
        "error_class": ["Closure OK"] * n, "reject_signal": [False] * n, "cap_present": [True] * n,
    })
    cfg = {"analytics": {"drift_window_seconds": 3600, "trend_max_points": 2000}}
    small = dict(cfg, analytics=dict(cfg["analytics"], trend_max_points=10_000))
    thinned = dispatch_tool("torque_trend", events, config=cfg)
    full = dispatch_tool("torque_trend", events, config=small)
    assert thinned["ok"] and full["ok"]
    series = thinned["result"]["timestamps"]
    assert len(series) <= 2000 and len(full["result"]["timestamps"]) == n
    assert series[0] == full["result"]["timestamps"][0]
    assert series[-1] == full["result"]["timestamps"][-1]
    assert thinned["result"]["drift_slope_per_second"] == full["result"]["drift_slope_per_second"]
    assert thinned["result"]["drift_detected"] == full["result"]["drift_detected"]
    assert thinned["meta"]["series_points"] == {"returned": len(series), "total": n}
    assert "thinned" in thinned["meta"]["notes"]
