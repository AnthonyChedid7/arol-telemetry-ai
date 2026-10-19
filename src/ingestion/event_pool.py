"""Build an ordered event pool one raw file at a time.

The preceding final observation is prepended solely as a counter baseline.
The existing event builder owns event detection, decoding and the eight-field
schema. A manifest is published only after all partitions pass validation.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from .conversion import convert_csv_to_parquet
from .event_table_polars import (
    EVENT_SCHEMA, build_event_table_frame, detect_head_ids,
    write_event_table_parquet,
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _convert_input(path, destination):
    # Datetime inference cannot infer a format from a header-only CSV. Give
    # that zero-row partition an explicit schema; no values are invented.
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = (row for row in csv.reader(stream) if row)
        header = next(rows, None)
        first = next(rows, None)
    if header is None:
        raise ValueError(f"CSV has no header: {path.name}")
    if first is not None:
        convert_csv_to_parquet(path, destination)
        return
    if len(header) != len(set(header)):
        raise ValueError("Duplicate CSV column names")
    schema = {name: (pl.Datetime("us") if name == "timestamp" else
                     pl.Int64 if name.endswith((" Count", " Status")) else
                     pl.Float64 if name.endswith(" AppTorque") else pl.String)
              for name in header}
    pl.DataFrame(schema=schema).write_parquet(destination)


def _integer_values(series, *, counter):
    """Validate before casting: Polars casts alone can truncate floats."""
    if series.null_count():
        raise ValueError(f"{series.name}: null values are not accepted")
    dtype = series.dtype
    if not (dtype.is_integer() or dtype.is_float()):
        raise ValueError(f"{series.name}: expected numeric whole numbers")
    if dtype.is_float():
        limit = 2 ** (24 if dtype == pl.Float32 else 53) - 1
        if (not series.is_finite().all()
                or ((series % 1) != 0).any()
                or (series.abs() > limit).any()):
            raise ValueError(f"{series.name}: expected finite, exactly representable whole numbers")
    # Keep signed subtraction safe even when a counter decreases sharply.
    lower, upper = (-(2**62), 2**62 - 1) if counter else (-(2**63), 2**63 - 1)
    if len(series) and (series.min() < lower or series.max() > upper):
        raise ValueError(f"{series.name}: outside the supported integer range")
    return series.cast(pl.Int64, strict=True)


def _prepare(raw, expected_heads):
    heads = detect_head_ids(raw)
    if not heads:
        raise ValueError("No counter heads found")
    if expected_heads is not None and heads != expected_heads:
        raise ValueError("Head set changed between input files")
    names = ["timestamp"] + [
        f"{head} {field}"
        for head in heads for field in ("Count", "AppTorque", "Status")
    ]
    missing = sorted(set(names) - set(raw.columns))
    if missing:
        raise ValueError(f"Missing required telemetry columns: {missing}")
    frame = raw.select(names)
    dtype = frame.schema["timestamp"]
    if dtype != pl.Datetime("us"):
        raise ValueError("Expected naive microsecond timestamps; no timezone conversion is applied")
    if frame["timestamp"].null_count():
        raise ValueError("Null timestamps are not accepted")
    if not frame["timestamp"].is_sorted():
        raise ValueError("Timestamps are out of order within an input file")
    for head in heads:
        for field in ("Count", "Status"):
            name = f"{head} {field}"
            # A header-only CSV can infer String for its empty numeric columns.
            values = (frame[name].cast(pl.Int64) if frame.is_empty()
                      else _integer_values(frame[name], counter=field == "Count"))
            frame = frame.with_columns(values)
        frame = frame.with_columns(pl.col(f"{head} AppTorque").cast(pl.Float64, strict=True))
    return frame, heads


def _counter_counts(frame, heads, previous):
    totals = dict(exact_plus_one=0, holds=0, jump_rows=0, decreases=0)
    boundary = dict(exact_plus_one=0, holds=0, jump_rows=0, decreases=0)
    for head in heads:
        values = frame[f"{head} Count"]
        delta = values.diff()
        totals["exact_plus_one"] += int((delta == 1).sum())
        totals["holds"] += int((delta == 0).sum())
        totals["jump_rows"] += int((delta > 1).sum())
        totals["decreases"] += int((delta < 0).sum())
        if previous is not None and len(values):
            difference = int(values[0]) - int(previous[f"{head} Count"][0])
            key = ("exact_plus_one" if difference == 1 else
                   "holds" if difference == 0 else
                   "jump_rows" if difference > 1 else "decreases")
            boundary[key] += 1
            totals[key] += 1
    return totals, boundary


def _timestamp_counts(frame, previous):
    if frame.is_empty():
        return {"timestamp_gaps_over_1s": 0, "duplicate_timestamp_transitions": 0}
    timestamps = frame["timestamp"]
    differences = timestamps.diff().dt.total_microseconds()
    gaps = int((differences > 1_000_000).sum())
    duplicates = int((differences == 0).sum())
    if previous is not None:
        difference = timestamps[0] - previous["timestamp"][0]
        gaps += difference.total_seconds() > 1
        duplicates += difference.total_seconds() == 0
    return {"timestamp_gaps_over_1s": gaps, "duplicate_timestamp_transitions": duplicates}


def _iso(value):
    return None if value is None else value.isoformat()


def _check_input(path, machine_id):
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Expected a CSV input: {path}")
    # When using AROL's machine-bearing filenames, check the declared scope.
    if path.name.startswith("telemetry_"):
        prefix = f"telemetry_{machine_id}_"
        if not path.name.startswith(prefix):
            raise ValueError(f"Input filename does not match machine_id: {path.name}")


def _last_row(frame):
    # Reconstruct a one-row frame so carry state does not retain a
    # view of the full preceding file's buffers.
    return pl.DataFrame(frame.tail(1).to_dict(as_series=False), schema=frame.schema)


def _build_partition(path, output, index, machine_id, heads, previous):
    """Convert one input file into partition `index`; return (entry, heads, previous)."""
    fingerprint = sha256_file(path)
    stem = f"{index:04d}-{path.stem}.parquet"
    raw_path = output / "raw" / stem
    event_path = output / "events" / stem
    _convert_input(path, raw_path)
    if sha256_file(path) != fingerprint:
        raise ValueError(f"Input changed during conversion: {path}")
    raw = pl.read_parquet(raw_path)
    frame, heads = _prepare(raw, heads)
    del raw
    if previous is not None and not frame.is_empty():
        if frame["timestamp"][0] < previous["timestamp"][0]:
            raise ValueError(f"Timestamps go backwards across files at {path.name}")
    counts, boundary = _counter_counts(frame, heads, previous)
    timing = _timestamp_counts(frame, previous)
    # No event can arise from the baseline row: it has no predecessor
    # in this temporary frame. All emitted attributes are current-row values.
    stitched = (frame.lazy() if previous is None else
                pl.concat([previous.lazy(), frame.lazy()], how="vertical"))
    write_event_table_parquet(build_event_table_frame(stitched, machine_id), event_path)
    event_summary = pl.scan_parquet(event_path).select(
        pl.len().alias("n"), pl.col("ts").min().alias("lo"),
        pl.col("ts").max().alias("hi"),
    ).collect().row(0, named=True)
    if event_summary["n"] != counts["exact_plus_one"]:
        raise ValueError("Event count differs from independent exact +1 counter count")
    ts_min = None if frame.is_empty() else frame["timestamp"][0]
    ts_max = None if frame.is_empty() else frame["timestamp"][-1]
    entry = {
        "input_file": path.name, "input_sha256": fingerprint,
        "raw_parquet": raw_path.relative_to(output).as_posix(),
        "event_parquet": event_path.relative_to(output).as_posix(),
        "event_sha256": sha256_file(event_path),
        "input_rows": len(frame), "observed_events": event_summary["n"],
        "ts_min": _iso(ts_min), "ts_max": _iso(ts_max),
        "event_ts_min": _iso(event_summary["lo"]),
        "event_ts_max": _iso(event_summary["hi"]),
        "counter_counts": counts, "boundary_counts": boundary, **timing,
    }
    if not frame.is_empty():
        previous = _last_row(frame)
    return entry, heads, previous


def _accumulate(summary, entry, n_heads):
    summary["heads"] = n_heads
    for key in ("input_rows", "observed_events"):
        summary[key] += entry[key]
    for key, value in entry["counter_counts"].items():
        summary[key] += value
        summary[f"boundary_{key}"] += entry["boundary_counts"][key]
    for key in ("timestamp_gaps_over_1s", "duplicate_timestamp_transitions"):
        summary[key] += entry[key]
    if entry["ts_min"] is not None:
        if summary["ts_min"] is None:
            summary["ts_min"] = entry["ts_min"]
        summary["ts_max"] = entry["ts_max"]


def build_event_pool(csv_paths, output_dir, *, machine_id, progress=None):
    """Build a fresh single-machine pool in the supplied file order.

    Never sorts, deduplicates, imputes or repairs raw observations. Files must
    be chronological (equal timestamps remain intact). Empty files retain the
    previous baseline. Output must not already exist. On failure, partial
    files remain for diagnosis but there is no completed manifest.
    """
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise ValueError("A nonempty machine_id is required")
    paths = [Path(path).resolve() for path in csv_paths]
    if not paths:
        raise ValueError("The input file list is empty")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate input paths are not accepted")
    for path in paths:
        _check_input(path, machine_id)
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    (output / "raw").mkdir()
    (output / "events").mkdir()
    previous = None
    heads = None
    entries = []
    summary = dict(machine_id=machine_id, files=len(paths), heads=0, input_rows=0,
                   observed_events=0, exact_plus_one=0, holds=0, jump_rows=0,
                   decreases=0, boundary_exact_plus_one=0, boundary_holds=0,
                   boundary_jump_rows=0, boundary_decreases=0,
                   timestamp_gaps_over_1s=0, duplicate_timestamp_transitions=0,
                   ts_min=None, ts_max=None)
    try:
        for index, path in enumerate(paths, 1):
            if progress:
                progress(f"{index}/{len(paths)} Reading {path.name}")
            entry, heads, previous = _build_partition(path, output, index, machine_id, heads, previous)
            entries.append(entry)
            _accumulate(summary, entry, len(heads))
            if progress:
                progress(f"    Saved {entry['observed_events']:,} events; "
                         f"boundary +1={entry['boundary_counts']['exact_plus_one']}")
        manifest = {
            "format": "arol-event-pool-v1", "complete": True,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "summary": summary, "partitions": entries,
            "policy": {
                "event_rule": "exact_plus_one", "redecode": False,
                "input_order": "supplied order; never automatically sorted",
                "timestamp_timezone": "unspecified; values used as stored",
                "baseline": "first observation of the whole supplied pool",
                "duplicates": "preserved; adjacent equal timestamps counted",
                "discontinuities": "measured; causes and missing closures not inferred",
                "memory_scope": "one raw file plus event-building workspace; no full-pool collect",
            },
        }
        temporary = output / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(output / "manifest.json")
    except Exception as exc:
        (output / "FAILED.json").write_text(json.dumps({
            "complete": False, "error": f"{type(exc).__name__}: {exc}",
            "completed_partitions": len(entries),
        }, indent=2) + "\n", encoding="utf-8")
        raise
    return output / "manifest.json"


def _carry_state(output, entries):
    """Head set and counter baseline after the last stored partition.

    Re-derived from the stored raw Parquet exactly as the build derived them,
    so appending a file equals having built the pool with that file last.
    """
    previous = None
    heads = None
    for entry in reversed(entries):
        raw_path = (output / entry["raw_parquet"]).resolve()
        if not raw_path.is_relative_to(output) or not raw_path.is_file():
            raise ValueError(f"Raw partition missing from the pool: {entry['raw_parquet']}")
        frame, found = _prepare(pl.read_parquet(raw_path), None)
        heads = heads or found
        if not frame.is_empty():
            previous = _last_row(frame)
            break
    return heads, previous


def append_to_event_pool(csv_path, manifest_path, *, progress=None):
    """Add one CSV as the next partition of an existing completed pool.

    The result equals building the pool with this file supplied last: the same
    validation, baseline carry-over and summary. Files must be added in
    chronological order; a file already in the pool is refused. On failure the
    new partition's files are removed and the manifest is unchanged.
    """
    manifest_path = Path(manifest_path).resolve()
    output = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "arol-event-pool-v1" or manifest.get("complete") is not True:
        raise ValueError("Expected a completed AROL event-pool manifest")
    entries = manifest["partitions"]
    machine_id = manifest["summary"]["machine_id"]
    path = Path(csv_path).resolve()
    _check_input(path, machine_id)
    fingerprint = sha256_file(path)
    if any(entry["input_sha256"] == fingerprint for entry in entries):
        raise ValueError(f"This file is already in the event pool: {path.name}")
    if any(entry["input_file"] == path.name for entry in entries):
        raise ValueError(f"A file with this name is already in the event pool: {path.name}")
    heads, previous = _carry_state(output, entries)
    index = len(entries) + 1
    if progress:
        progress(f"Adding {path.name} as partition {index}")
    try:
        entry, heads, _ = _build_partition(path, output, index, machine_id, heads, previous)
    except Exception:
        stem = f"{index:04d}-{path.stem}.parquet"
        for folder in ("raw", "events"):
            (output / folder / stem).unlink(missing_ok=True)
        raise
    summary = dict(manifest["summary"])
    summary["files"] += 1
    _accumulate(summary, entry, len(heads))
    updated = {**manifest, "summary": summary, "partitions": [*entries, entry],
               "updated_utc": datetime.now(timezone.utc).isoformat()}
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(updated, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    if progress:
        progress(f"    Saved {entry['observed_events']:,} events")
    return entry


def scan_event_pool(manifest_path, *, verify_hashes=False):
    """Return a lazy, globally ordered eight-column view of completed events.

    Filters can be applied to this view before collection. This function does
    not alter PersonASource or the orchestrator; that integration is separate.
    """
    manifest_path = Path(manifest_path).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("format") != "arol-event-pool-v1" or data.get("complete") is not True:
        raise ValueError("Expected a completed AROL event-pool manifest")
    entries = data.get("partitions")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest has no event partitions")
    root = manifest_path.parent
    paths = []
    frames = []
    for entry in entries:
        relative = Path(entry["event_parquet"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Event partition must be inside the pool directory")
        if path in paths:
            raise ValueError("Duplicate event partition in manifest")
        paths.append(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if verify_hashes and sha256_file(path) != entry["event_sha256"]:
            raise ValueError(f"Event partition hash mismatch: {path.name}")
        frame = pl.scan_parquet(path)
        if dict(frame.collect_schema()) != EVENT_SCHEMA:
            raise ValueError(f"Invalid eight-column event schema: {path.name}")
        frames.append(frame.select(list(EVENT_SCHEMA)))
    return pl.concat(frames, how="vertical").sort(["ts", "head_id"], maintain_order=True)
