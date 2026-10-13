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
