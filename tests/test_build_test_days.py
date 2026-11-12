"""The 50-day test data generator: dates, file format and continuous counters."""
from datetime import date

import numpy as np
import polars as pl

from scripts import build_test_days as btd


def test_fifty_days_with_one_gap():
    days = btd.days_to_build()
    assert len(days) == 50 and days[0] == date(2026, 3, 1) and days[-1] == date(2026, 4, 20)
    assert date(2026, 4, 2) not in days


def test_one_day_has_the_export_columns_and_counters_continue():
    rng = np.random.default_rng(1)
    counts = {head: 100 for head in btd.HEADS}
    first = btd._day(date(2026, 3, 1), 0, counts, rng)
    end_of_first = dict(counts)
    second = btd._day(date(2026, 3, 2), 1, counts, rng)
    assert first.height == btd.SECONDS and first.columns[0] == "timestamp"
    assert {f"H01 {k}" for k in ("Count", "AppTorque", "Status")} <= set(first.columns)
    assert first["timestamp"][0] == "2026-03-01T08:00:00.000"
    assert second["H01 Count"][0] >= end_of_first["H01"]
    steps = first["H01 Count"].diff().drop_nulls()
    assert steps.is_in([0, 1]).all()
    assert set(first["H01 Status"].unique().to_list()) <= {0, 2, 65}
    assert isinstance(first, pl.DataFrame)
