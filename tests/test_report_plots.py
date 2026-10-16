"""Figures derive from successful tool results and the saved report links them."""

from pathlib import Path
from datetime import datetime

from matplotlib.dates import date2num

from src.interface.plots import render
from src.interface import plots


def test_histogram_and_temporal_figures_from_envelopes(tmp_path):
    replies = [
        ("torque_distribution", {"ok": True, "result": {
            "bin_edges": [1.0, 2.0, 3.0], "counts": [3, 5]}}),
        ("observed_throughput", {"ok": True, "result": {"by_bucket": [
            {"bucket_start": "2026-02-01T00:00:00", "observed_events_per_hour": 12.0},
            {"bucket_start": "2026-02-01T01:00:00", "observed_events_per_hour": 9.0}]}}),
    ]
    figures = render(replies, tmp_path)
    assert [name for name, _ in figures] == ["torque_distribution", "observed_throughput"]
    for _, path in figures:
        assert Path(path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_empty_and_failed_analyses_do_not_fabricate_plots(tmp_path):
    assert render([("torque_distribution", {"ok": True, "result": {
        "bin_edges": [], "counts": []}}),
        ("torque_trend", {"ok": False, "result": {}})], tmp_path) == []


def test_one_bucket_throughput_plot_uses_requested_hour(monkeypatch, tmp_path):
    seen = {}

    def capture(ax, title, ylabel, target):
        seen["xlim"] = ax.get_xlim()
        return target

    monkeypatch.setattr(plots, "_finish", capture)
    plots._time({
        "requested_window": {"start": "2026-02-01T00:00:00",
                             "end": "2026-02-01T01:00:00"},
        "by_bucket": [{"bucket_start": "2026-02-01T00:00:00",
                       "bucket_end": "2026-02-01T01:00:00",
                       "observed_events_per_hour": 48279.0}],
    }, {}, tmp_path / "throughput.png")
    assert seen["xlim"] == (date2num(datetime(2026, 2, 1)),
                            date2num(datetime(2026, 2, 1, 1)))
