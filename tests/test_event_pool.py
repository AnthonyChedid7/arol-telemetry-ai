"""Partitioned production building must match A's continuous reference."""
import json
from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.ingestion.event_pool import append_to_event_pool, build_event_pool, scan_event_pool
from src.ingestion.event_table_polars import EVENT_SCHEMA, build_event_table


@pytest.fixture
def raw():
    return pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1) + timedelta(seconds=i) for i in range(8)],
        "H01 Count": [10.0, 11.0, 12.0, 14.0, 0.0, 1.0, 2.0, 3.0],
        "H01 AppTorque": [0.0, 1.1, 1.2, 99.0, 99.0, 1.5, 1.6, 1.7],
        "H01 Status": [0, 0, 3, 64, 0, 65, 999, 2],
        "H02 Count": [20.0, 20.0, 21.0, 22.0, 22.0, 40.0, 41.0, 41.0],
        "H02 AppTorque": [0.0, 99.0, 2.2, 2.3, 99.0, 99.0, 2.6, 99.0],
        "H02 Status": [0, 0, 64, 0, 0, 0, 65, 0],
    })


def inputs(tmp_path, frames):
    paths = []
    for i, frame in enumerate(frames):
        path = tmp_path / f"input-{i}.csv"
        frame.write_csv(path)
        paths.append(path)
    return paths


def build(tmp_path, frames, **kwargs):
    return build_event_pool(inputs(tmp_path, frames), tmp_path / "pool", machine_id="M1", **kwargs)


def test_partitioned_matches_continuous_reference_and_boundary_attributes(tmp_path, raw):
    manifest = build(tmp_path, [raw[:2], raw[2:5], raw[5:]])
    actual = scan_event_pool(manifest, verify_hashes=True).collect()
    assert_frame_equal(actual, build_event_table(raw, "M1"))
    assert dict(actual.schema) == EVENT_SCHEMA
    boundary = actual.filter((pl.col("ts") == raw["timestamp"][2]) & (pl.col("head_id") == "H01"))
    assert boundary["torque"][0] == 1.2
    assert boundary["status"][0] == 3
    assert boundary["cap_present"][0] is None
    assert boundary["reject_signal"][0] is True
    assert "Unknown (999)" in actual["error_class"].to_list()
    summary = json.loads(manifest.read_text())["summary"]
    assert summary["observed_events"] == summary["exact_plus_one"] == 8
    assert summary["boundary_exact_plus_one"] == 3
    assert summary["jump_rows"] == 2
    assert summary["decreases"] == 1
    assert summary["holds"] == 3
    assert summary["boundary_jump_rows"] == 1
    assert summary["input_rows"] == 8


def test_empty_partition_preserves_prior_baseline(tmp_path, raw):
    manifest = build(tmp_path, [raw[:2], raw.head(0), raw[2:]])
    assert_frame_equal(scan_event_pool(manifest).collect(), build_event_table(raw, "M1"))
    entries = json.loads(manifest.read_text())["partitions"]
    assert entries[1]["observed_events"] == 0
    assert entries[2]["boundary_counts"]["exact_plus_one"] == 2


def test_all_empty_partitions_produce_valid_empty_events(tmp_path, raw):
    manifest = build(tmp_path, [raw.head(0), raw.head(0)])
    events = scan_event_pool(manifest).collect()
    assert events.is_empty()
    assert dict(events.schema) == EVENT_SCHEMA
    summary = json.loads(manifest.read_text())["summary"]
    assert summary["ts_min"] is None and summary["ts_max"] is None


def test_one_row_files_establish_only_one_pool_baseline(tmp_path, raw):
    manifest = build(tmp_path, [raw[i:i+1] for i in range(len(raw))])
    assert_frame_equal(scan_event_pool(manifest).collect(), build_event_table(raw, "M1"))
    assert json.loads(manifest.read_text())["summary"]["boundary_exact_plus_one"] == 8


def test_equal_timestamps_are_preserved_across_files(tmp_path, raw):
    stamps = raw["timestamp"].to_list()
    stamps[2] = stamps[1]
    raw = raw.with_columns(pl.Series("timestamp", stamps))
    manifest = build(tmp_path, [raw[:2], raw[2:]])
    assert_frame_equal(scan_event_pool(manifest).collect(), build_event_table(raw, "M1"))
    assert json.loads(manifest.read_text())["summary"]["duplicate_timestamp_transitions"] == 1


def test_timestamp_gaps_do_not_erase_counter_continuity(tmp_path, raw):
    raw = raw.with_columns(pl.col("timestamp") + pl.when(pl.int_range(pl.len()) >= 2)
                           .then(pl.duration(seconds=9)).otherwise(pl.duration(seconds=0)))
    manifest = build(tmp_path, [raw[:2], raw[2:]])
    assert_frame_equal(scan_event_pool(manifest).collect(), build_event_table(raw, "M1"))
    assert json.loads(manifest.read_text())["summary"]["timestamp_gaps_over_1s"] == 1


@pytest.mark.parametrize("bad", [1.5, float("nan"), float("inf"), None, float(2**53)])
def test_invalid_counter_stops_without_publishing_manifest(tmp_path, raw, bad):
    values = raw["H01 Count"].to_list()
    values[3] = bad
    raw = raw.with_columns(pl.Series("H01 Count", values, dtype=pl.Float64))
    with pytest.raises(ValueError, match="H01 Count"):
        build(tmp_path, [raw[:2], raw[2:]])
    assert not (tmp_path / "pool/manifest.json").exists()
    failure = json.loads((tmp_path / "pool/FAILED.json").read_text())
    assert failure["complete"] is False
    assert failure["completed_partitions"] == 1


def test_fractional_status_is_not_truncated(tmp_path, raw):
    raw = raw.with_columns(pl.lit(0.5).alias("H01 Status"))
    with pytest.raises(ValueError, match="H01 Status"):
        build(tmp_path, [raw])


@pytest.mark.parametrize("split", [False, True])
def test_out_of_order_timestamps_are_rejected_not_sorted(tmp_path, raw, split):
    frames = [raw[4:], raw[:4]] if split else [raw.reverse()]
    with pytest.raises(ValueError, match="order|backwards"):
        build(tmp_path, frames)
    assert not (tmp_path / "pool/manifest.json").exists()


def test_changed_head_set_is_rejected(tmp_path, raw):
    changed = raw[2:].drop([name for name in raw.columns if name.startswith("H02")])
    with pytest.raises(ValueError, match="Head set changed"):
        build(tmp_path, [raw[:2], changed])


def test_missing_torque_column_is_rejected(tmp_path, raw):
    with pytest.raises(ValueError, match="Missing required"):
        build(tmp_path, [raw.drop("H01 AppTorque")])


def test_existing_output_is_never_overwritten(tmp_path, raw):
    paths = inputs(tmp_path, [raw])
    output = tmp_path / "pool"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        build_event_pool(paths, output, machine_id="M1")
    assert sentinel.read_text() == "keep"


def test_duplicate_inputs_rejected_before_writing(tmp_path, raw):
    path = inputs(tmp_path, [raw])[0]
    with pytest.raises(ValueError, match="Duplicate input"):
        build_event_pool([path, path], tmp_path / "pool", machine_id="M1")
    assert not (tmp_path / "pool").exists()


def test_missing_input_rejected_before_writing(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_event_pool([tmp_path / "missing.csv"], tmp_path / "pool", machine_id="M1")
    assert not (tmp_path / "pool").exists()


def test_wrong_machine_filename_rejected_before_writing(tmp_path, raw):
    path = tmp_path / "telemetry_M2_2026-02-01.csv"
    raw.write_csv(path)
    with pytest.raises(ValueError, match="machine_id"):
        build_event_pool([path], tmp_path / "pool", machine_id="M1")
    assert not (tmp_path / "pool").exists()


def test_lazy_scope_matches_filtering_the_continuous_reference(tmp_path, raw):
    manifest = build(tmp_path, [raw[:2], raw[2:]])
    scope = ((pl.col("head_id") == "H01") & (pl.col("ts") >= raw["timestamp"][2])
             & (pl.col("ts") < raw["timestamp"][7]))
    assert_frame_equal(scan_event_pool(manifest).filter(scope).collect(),
                       build_event_table(raw, "M1").filter(scope))


@pytest.mark.parametrize("kind", ["incomplete", "escape", "duplicate", "missing", "hash"])
def test_manifest_reader_rejects_invalid_or_changed_partitions(tmp_path, raw, kind):
    manifest = build(tmp_path, [raw])
    data = json.loads(manifest.read_text())
    if kind == "incomplete":
        data["complete"] = False
    elif kind == "escape":
        data["partitions"][0]["event_parquet"] = "../outside.parquet"
    elif kind == "duplicate":
        data["partitions"].append(dict(data["partitions"][0]))
    elif kind == "hash":
        data["partitions"][0]["event_sha256"] = "0" * 64
    else:
        (manifest.parent / data["partitions"][0]["event_parquet"]).unlink()
    manifest.write_text(json.dumps(data))
    with pytest.raises((ValueError, FileNotFoundError)):
        scan_event_pool(manifest, verify_hashes=True)


def test_input_mutation_is_detected(tmp_path, raw, monkeypatch):
    from src.ingestion import event_pool
    original = event_pool._convert_input
    def change_after_read(path, destination):
        original(path, destination)
        path.write_text(path.read_text() + "\n")
    monkeypatch.setattr(event_pool, "_convert_input", change_after_read)
    with pytest.raises(ValueError, match="Input changed"):
        build(tmp_path, [raw])
    assert not (tmp_path / "pool/manifest.json").exists()


# --- appending one file to an existing pool ------------------------------------------

def _without_times(manifest_path):
    data = json.loads(manifest_path.read_text())
    data.pop("created_utc"), data.pop("updated_utc", None)
    return data


@pytest.mark.parametrize("splits", [[2, 5], [3], [0, 4], [6, 8]])
def test_appending_equals_building_with_the_file_last(tmp_path, raw, splits):
    bounds = [0, *splits, len(raw)]
    frames = [raw[a:b] for a, b in zip(bounds, bounds[1:])]
    paths = inputs(tmp_path, frames)
    full = build_event_pool(paths, tmp_path / "full", machine_id="M1")
    grown = build_event_pool(paths[:1], tmp_path / "grown", machine_id="M1")
    for path in paths[1:]:
        append_to_event_pool(path, grown)
    assert _without_times(grown) == _without_times(full)
    assert_frame_equal(scan_event_pool(grown, verify_hashes=True).collect(),
                       scan_event_pool(full).collect())


def test_append_refuses_duplicates_and_backwards_files_without_side_effects(tmp_path, raw):
    paths = inputs(tmp_path, [raw[:4], raw[4:], raw[2:6]])
    manifest = build_event_pool(paths[:2], tmp_path / "pool", machine_id="M1")
    before = manifest.read_text()
    files = sorted(p.name for p in (tmp_path / "pool").rglob("*.parquet"))
    with pytest.raises(ValueError, match="already in the event pool"):
        append_to_event_pool(paths[1], manifest)
    with pytest.raises(ValueError, match="backwards"):
        append_to_event_pool(paths[2], manifest)
    assert manifest.read_text() == before
    assert sorted(p.name for p in (tmp_path / "pool").rglob("*.parquet")) == files


def test_append_checks_the_pool_machine(tmp_path, raw):
    manifest = build(tmp_path, [raw[:4]])
    other = tmp_path / "telemetry_M2_2026-02-01.csv"
    raw[4:].write_csv(other)
    with pytest.raises(ValueError, match="machine_id"):
        append_to_event_pool(other, manifest)
