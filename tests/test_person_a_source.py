"""Verify the real Person A event-builder to data-source boundary."""

from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.analytics import registered_torque  # noqa: F401
from src.common import datasource, schema
from src.common.registry import call_tool
from src.ingestion.adapter import adapt
from src.ingestion.event_table_polars import (
    build_event_table,
    write_event_table_parquet,
)


@pytest.fixture
def handoff(tmp_path):
    start = datetime(2026, 2, 1, 10)
    raw = pl.DataFrame({
        "timestamp": [start + timedelta(seconds=i) for i in range(8)],
        "H01 Count": [100, 101, 103, 0, 104, 105, 106, 107],
        "H01 AppTorque": [9000.0, 1.0, 9000.0, 9000.0, 9000.0, 3.0, 5.0, 7.0],
        "H01 Status": [0, 0, 0, 0, 0, 3, 64, 999],
    })
    events = build_event_table(raw, machine_id="M1")
    path = tmp_path / "events.parquet"
    write_event_table_parquet(events, path)
    cfg = {
        "data": {
            "source": "person_a",
            "person_a": {"fixture": str(path)},
        }
    }
    return cfg, events, path


def test_counter_discontinuities_do_not_become_closures(handoff):
    cfg, _, _ = handoff
    source = datasource.get_source(cfg)
    events = source.load_pool("fixture")

    assert len(events) == 4
    assert events["torque"].to_list() == [1.0, 3.0, 5.0, 7.0]
    assert events["count_delta"].to_list() == [1] * 4
    assert events["inferred"].to_list() == [False] * 4

    response = call_tool("torque_stats", events, status_filter="all_statuses")
    assert response["ok"] is True
    assert response["result"]["mean"] == 4.0
    assert response["result"]["sample_size"] == 4
    # By default the No Load event (status 3, 3.0 Nm) is not a closure.
    closures = call_tool("torque_stats", events)
    assert closures["result"]["sample_size"] == 3
    assert closures["result"]["mean"] == pytest.approx(13 / 3)


def test_source_preserves_original_fields_and_honest_metadata(handoff):
    cfg, original, _ = handoff
    source = datasource.get_source(cfg)
    events = source.load_pool("fixture")

    assert_frame_equal(events.select(original.columns), original)
    assert schema.validate_events(events) == []
    assert events["cap_present"].to_list() == [True, None, None, None]
    assert events["reject_signal"][-1] is None

    meta = source.pool_meta("fixture")
    assert meta["n_events"] == 4
    assert meta["n_files"] == 1
    assert meta["duplicates_removed"] is None
    warnings = " ".join(meta["warnings"])
    assert "completeness is not measured" in warnings
    assert "8 per machine-day" not in warnings
    assert "slight undercount" not in warnings


def test_adapter_default_preserves_decoding(handoff):
    _, original, _ = handoff
    adapted, _ = adapt(original, pool_id="fixture")
    assert_frame_equal(adapted.select(original.columns), original)


def test_duplicate_events_are_preserved(handoff):
    cfg, original, path = handoff
    duplicated = pl.concat([original, original.tail(1)])
    write_event_table_parquet(duplicated, path)
    events = datasource.get_source(cfg).load_pool("fixture")
    assert_frame_equal(events.select(original.columns), duplicated)


def test_empty_event_table_remains_valid(handoff):
    cfg, original, path = handoff
    write_event_table_parquet(original.head(0), path)
    source = datasource.get_source(cfg)
    events = source.load_pool("fixture")
    assert events.is_empty()
    assert schema.validate_events(events) == []
    assert source.pool_meta("fixture")["n_events"] == 0


def test_unknown_pool_does_not_fall_back(handoff):
    cfg, _, _ = handoff
    with pytest.raises(KeyError, match="Unknown"):
        datasource.get_source(cfg).load_pool("wrong-pool")


def test_missing_file_is_not_hidden(handoff):
    cfg, _, path = handoff
    path.unlink()
    source = datasource.get_source(cfg)
    assert source.list_pools() == ["fixture"]
    with pytest.raises(FileNotFoundError):
        source.load_pool("fixture")


def test_raw_telemetry_cannot_be_mistaken_for_events(handoff):
    cfg, _, path = handoff
    pl.DataFrame({"H01 Count": [100, 101]}).write_parquet(path)
    with pytest.raises(ValueError, match="eight-column"):
        datasource.get_source(cfg).load_pool("fixture")


def test_relative_paths_resolve_from_repository_root(handoff, monkeypatch):
    cfg, original, path = handoff
    cfg["data"]["person_a"]["fixture"] = path.name
    monkeypatch.setattr(datasource, "REPO_ROOT", path.parent)
    other = path.parent / "other-directory"
    other.mkdir()
    monkeypatch.chdir(other)

    events = datasource.get_source(cfg).load_pool("fixture")
    assert_frame_equal(events.select(original.columns), original)
