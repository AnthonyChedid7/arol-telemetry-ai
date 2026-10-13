"""Extend Person A's observed-event table with shared metadata.

Decoded fields are preserved by default. Counter jumps cannot be
reconstructed from this event table, and completeness is not measured here.
Explicit redecode=True remains available for decoder comparisons.
"""
from __future__ import annotations

import re

import polars as pl

from ..common import schema

# Person A's committed event contract (his EVENT_SCHEMA).
PERSON_A_COLUMNS: dict[str, pl.DataType] = {
    "ts": pl.Datetime("us"),
    "machine_id": pl.String,
    "head_id": pl.String,
    "torque": pl.Float64,
    "status": pl.Int64,
    "error_class": pl.String,
    "reject_signal": pl.Boolean,
    "cap_present": pl.Boolean,
}

# Columns the contract requires that his frame does not carry.
SUPPLIED_BY_ADAPTER = ("pool_id", "head_index", "count_delta", "inferred")

_HEAD_DIGITS = re.compile(r"(\d+)")

# Representable range of the contract's status dtype, derived rather than
# hardcoded, so narrowing STATUS_DTYPE keeps the guard honest.
_INT_RANGES = {
    pl.Int8: (-128, 127),
    pl.Int16: (-32768, 32767),
    pl.Int32: (-2147483648, 2147483647),
    pl.Int64: (-9223372036854775808, 9223372036854775807),
}


def _status_range() -> tuple[int, int]:
    return _INT_RANGES.get(schema.STATUS_DTYPE, _INT_RANGES[pl.Int64])


class AdapterError(ValueError):
    """Raised when A's frame cannot be honestly converted."""


def _head_index(expr: pl.Expr) -> pl.Expr:
    """'H07' -> 7. Falls back to null rather than guessing on an odd id."""
    return expr.str.extract(_HEAD_DIGITS.pattern, 1).cast(pl.Int16, strict=False)


def adapt(events, *, pool_id: str, redecode: bool = False
          ) -> tuple[pl.DataFrame, list[str]]:
    """Convert one of Person A's event tables to the contract.

    Returns (frame, notes). `notes` is for pool_meta()["warnings"] - every
    entry describes something a reader of the report needs to know, not
    debug chatter.

    redecode=True re-derives error_class / reject_signal / cap_present from
    `status` using the contract's own decoder, and reports any disagreement
    with A's values instead of silently preferring one side. His decoder and
    ours were written independently from the same AROL table, so a mismatch
    is a real finding about the integration, not noise to suppress.
    """
    frame = events.collect() if isinstance(events, pl.LazyFrame) else events
    if not isinstance(frame, pl.DataFrame):
        raise AdapterError(f"expected a Polars frame, got {type(frame).__name__}")

    notes: list[str] = []

    missing = [c for c in PERSON_A_COLUMNS if c not in frame.columns]
    if missing:
        raise AdapterError(
            f"not one of Person A's event tables: missing {missing}. "
            f"Got columns {frame.columns}")

    if frame.is_empty():
        return schema.empty_events(), ["source event table was empty"]

    # --- the checked narrowing cast -------------------------------------
    low, high = _status_range()
    status_min = frame["status"].min()
    status_max = frame["status"].max()
    if status_min is not None and (status_min < low or status_max > high):
        raise AdapterError(
            f"status range [{status_min}, {status_max}] does not fit "
            f"{schema.STATUS_DTYPE}; casting would wrap and decode as a "
            f"nonsense category. Widen schema.STATUS_DTYPE with Person A "
            f"(a schema change needs both signatures).")

    out = frame.with_columns(
        pl.lit(pool_id, dtype=pl.String).alias("pool_id"),
        _head_index(pl.col("head_id")).alias("head_index"),
        # True of every row A emits: his detector keeps only delta == 1.
        pl.lit(1, dtype=pl.Int32).alias("count_delta"),
        pl.lit(False, dtype=pl.Boolean).alias("inferred"),
        pl.col("status").cast(schema.STATUS_DTYPE).alias("status"),
    )

    unparsed = out.filter(pl.col("head_index").is_null()).height
    if unparsed:
        notes.append(f"{unparsed} event(s) have a head_id with no number in it; "
                     f"head_index is null for those and numeric sort will "
                     f"place them last")

    # --- independent re-decode ------------------------------------------
    if redecode:
        decoded = schema.decode_status_series(out["status"]).drop("confirmed")
        for column in ("error_class", "reject_signal", "cap_present"):
            theirs, ours = out[column], decoded[column]
            # ne_missing so null != null counts as agreement, not a diff.
            diff = int(theirs.ne_missing(ours).sum())
            if diff:
                notes.append(
                    f"{diff} event(s) where Person A's {column} disagrees with "
                    f"the contract decoder; the contract's value was used")
        out = out.drop(["error_class", "reject_signal", "cap_present"]) \
                 .hstack(decoded)

    # nulls_last matches the note above (Polars sorts nulls first by default);
    # maintain_order keeps equal keys in their original order.
    out = out.sort(["ts", "head_index"], nulls_last=True, maintain_order=True)
    out = schema.conform(out)

    notes.append(
        "These are observed exact +1 closure events. count_delta=1 and "
        "inferred=False describe the supplied events. Counter discontinuities "
        "are not reconstructed, and total production completeness is not "
        "measured by this adapter.")
    return out, notes


def describe(frame: pl.DataFrame, *, pool_id: str, notes: list[str],
             source: str = "person-a", timezone: str | None = None) -> dict:
    """Build the pool_meta dict the report's 'data used' section reads.

    Cleaning counters are reported as 'not measured here' rather than 0.
    Zero would claim the adapter checked and found nothing; it did not check,
    because dedup and cleaning happen upstream in A's pipeline (objectives 2
    and 3, his deliverables).
    """
    return {
        "pool": pool_id,
        "source": source,
        "n_files": None,
        "n_events": int(frame.height),
        "ts_min": frame["ts"].min().isoformat() if frame.height else None,
        "ts_max": frame["ts"].max().isoformat() if frame.height else None,
        "machines": sorted(frame["machine_id"].drop_nulls().unique().to_list()),
        "heads": sorted(frame["head_id"].drop_nulls().unique().to_list()),
        "timezone": timezone,
        "rows_read": None,
        "rows_after_cleaning": None,
        "duplicates_removed": None,
        "schema_version": schema.SCHEMA_VERSION,
        "warnings": list(notes),
    }
