"""Integration checks for the registered Person A torque tool."""

import json
from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.analytics import registered_torque  # noqa: F401
from src.analytics.torque_stats import torque_stats
from src.common.registry import call_tool, get_tool_specs


@pytest.fixture
def events():
    start = datetime(2026, 2, 1, 10)
    return pl.DataFrame({
        "ts": [start + timedelta(seconds=i) for i in range(6)],
        "machine_id": ["M1"] * 6,
        "head_id": ["H01"] * 6,
        "torque": [1.0, 3.0, None, float("nan"), float("inf"), 5.0],
        "status": [0, 65, 0, 0, 0, 0],
        "error_class": [
            "Closure OK", "Bad Closure", "Closure OK",
            "Closure OK", "Closure OK", "Closure OK",
        ],
        "reject_signal": [False, True, False, False, False, False],
        "cap_present": [True] * 6,
    })


@pytest.mark.parametrize("status_filter", [None, "successful", 0, 65, 999])
def test_matches_existing_calculation(events, status_filter):
    before = events.clone()
    response = call_tool(
        "torque_stats", events, status_filter=status_filter
    )
    expected = torque_stats(events, status_filter=status_filter)

    assert response["ok"] is True
    assert response["error"] is None
    assert response["result"] == expected
    assert response["meta"]["n"] == expected["sample_size"]
    assert response["meta"]["agent"] == "analytics"
    assert response["meta"]["params"] == {"status_filter": status_filter}
    assert set(response) == {"ok", "result", "error", "meta"}
    json.dumps(response, allow_nan=False)
    assert_frame_equal(events, before)


def test_finite_values_and_sample_standard_deviation(events):
    response = call_tool("torque_stats", events)
    assert response["result"] == {
        "mean": 3.0, "min": 1.0, "max": 5.0,
        "std": 2.0, "sample_size": 3,
    }
    assert response["meta"]["units"]["std"] == "Nm"


def test_window_uses_only_matching_finite_observations(events):
    response = call_tool("torque_stats", events, status_filter=65)
    timestamp = events["ts"][1].isoformat()
    assert response["meta"]["data_window"] == {
        "ts_min": timestamp, "ts_max": timestamp,
    }
    assert response["meta"]["n"] == 1
    assert response["result"]["std"] is None


def test_empty_input_preserves_existing_result(events):
    empty = events.head(0)
    response = call_tool("torque_stats", empty)
    assert response["ok"] is True
    assert response["result"] == torque_stats(empty)
    assert response["meta"]["n"] == 0
    assert response["meta"]["data_window"] == {
        "ts_min": None, "ts_max": None,
    }


@pytest.mark.parametrize("invalid", [True, 1.5, "65", "unknown", [], {}])
def test_invalid_status_returns_failure(events, invalid):
    response = call_tool("torque_stats", events, status_filter=invalid)
    assert response["ok"] is False
    assert response["result"] is None
    assert response["error"]


def test_unknown_argument_returns_failure(events):
    response = call_tool("torque_stats", events, invented_argument=1)
    assert response["ok"] is False
    assert "does not accept" in response["error"]


def test_missing_torque_returns_failure(events):
    response = call_tool("torque_stats", events.drop("torque"))
    assert response["ok"] is False
    assert "Missing required event columns" in response["error"]


def test_schema_exposes_supported_filters():
    spec = next(
        item for item in get_tool_specs()
        if item["name"] == "torque_stats"
    )
    properties = spec["input_schema"]["properties"]
    assert set(properties) == {
        "start", "end", "head_id", "machine_id", "status_filter"
    }
    assert properties["status_filter"]["oneOf"] == [
        {"type": "null"},
        {"type": "string", "enum": ["successful", "all_statuses"]},
        {"type": "integer"},
    ]
