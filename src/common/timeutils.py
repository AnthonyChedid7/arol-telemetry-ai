"""Shared time bucketing.

Audit finding F5: if each side writes its own flooring, "per day" means one
thing in Person A's drift tool and another in Person B's KPI tool, and the
numbers stop agreeing for reasons nobody can find. All bucketing goes through
here.

Timestamps are plant-local and naive (schema.TIMEZONE_POLICY). We never
localise or convert them here - a UTC shift would move closures across
midnight and every per-day KPI would stop matching what the operator saw on
the line. Conversion, if the plant turns out not to be UTC, belongs in the
loader, once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

# The `bucket` parameter vocabulary, shared by every tool that buckets time.
BUCKETS = ("hour", "shift", "day", "week")

# Plant shifts, as (name, start_hour). Provisional - confirm with AROL.
# A shift boundary that does not match the plant's makes per-shift KPIs
# meaningless, so this is a question for Person A to put to the company.
SHIFTS = (("A", 6), ("B", 14), ("C", 22))

# Polars truncate intervals for the simple buckets.
_TRUNCATE = {"hour": "1h", "day": "1d", "week": "1w"}


def _as_series(expr_fn, ts: pl.Series):
    """Evaluate an expression-returning helper against a Series.

    The hour/day/week branches happen to work on a Series because
    Series.dt.truncate returns a Series. The shift branch does not: it builds
    pl.when() chains, which are expressions whatever you feed them, so it
    returned an Expr even when handed a Series - and the caller got
    "TypeError: 'Expr' object is not subscriptable" from a function whose
    docstring promised both forms.

    Nothing in src/ hits this today (kpi.py passes pl.col(...)), but this is
    the module both halves of the project share by design (audit F5), and a
    shared helper that behaves differently depending on which branch you take
    is exactly the class of bug F5 exists to prevent.
    """
    name = ts.name or "ts"
    return (pl.DataFrame({name: ts})
            .select(expr_fn(pl.col(name)).alias(name))[name])


def floor_to(ts: pl.Series | pl.Expr, bucket: str):
    """Floor a timestamp series or expression to the start of its bucket.

    Works on both a Series and an Expr, so callers can use it inside a
    `select`/`with_columns` or directly on a column.
    """
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r}; expected one of {BUCKETS}")

    if isinstance(ts, pl.Series):
        return _as_series(lambda col: floor_to(col, bucket), ts)

    if bucket in _TRUNCATE:
        return ts.dt.truncate(_TRUNCATE[bucket])

    # shift: floor to the most recent shift start, rolling back over midnight
    # for timestamps before the first start of the day.
    starts = sorted(h for _, h in SHIFTS)
    day = ts.dt.truncate("1d")
    hour = ts.dt.hour()

    # Walk the starts in order; each one overrides for hours at or past it.
    shift_hour = pl.lit(starts[-1], dtype=pl.Int32)
    for h in starts:
        shift_hour = pl.when(hour >= h).then(pl.lit(h, dtype=pl.Int32)).otherwise(shift_hour)

    floored = day + pl.duration(hours=shift_hour)
    return (
        pl.when(hour < starts[0])
        .then(floored - pl.duration(days=1))
        .otherwise(floored)
    )


def shift_name(ts: pl.Series | pl.Expr):
    """Name of the shift each timestamp falls in ("A"/"B"/"C")."""
    if isinstance(ts, pl.Series):
        return _as_series(shift_name, ts)

    starts = sorted(SHIFTS, key=lambda s: s[1])
    hour = ts.dt.hour()
    name = pl.lit(starts[-1][0], dtype=pl.String)
    for label, h in starts:
        name = pl.when(hour >= h).then(pl.lit(label, dtype=pl.String)).otherwise(name)
    return name


def parse_bound(value) -> datetime | None:
    """Parse a `start`/`end` parameter. Returns None for None/empty.

    Accepts an ISO-8601 string, a date, or a naive datetime. A timezone-aware
    value is rejected, exactly like the bound parser the registered tools use
    (analytics.event_filters._bound): event timestamps are naive UTC, and
    silently dropping an offset would shift the user's window.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError("timezone-aware bounds are not supported; use naive UTC timestamps")
        return value

    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = (
                pl.Series([text])
                .str.to_datetime(strict=True, time_unit="us")
                .item()
            )
        except Exception as exc:
            raise ValueError(
                f"could not parse timestamp {value!r}; expected ISO-8601"
            ) from exc
    if parsed.tzinfo is not None:
        raise ValueError("timezone-aware bounds are not supported; use naive UTC timestamps")
    return parsed
