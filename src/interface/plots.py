"""Render successful tool envelopes without rereading or recalculating events."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from datetime import datetime

# Audit finding: ISO strings were plotted as categories, so points were evenly
# spaced and gaps disappeared. Times are parsed; the axis names the data clock.
TIME_LABEL = "Recorded time (UTC)"


def _times(values):
    return [datetime.fromisoformat(str(value)) for value in values]


def _population(meta, default="Observed events"):
    label = (meta or {}).get("population")
    return f"Events ({label})" if label else default


def _finish(ax, title, ylabel, target):
    ax.set_title(title, loc="left")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.2)
    ax.figure.autofmt_xdate()
    ax.figure.tight_layout()
    ax.figure.savefig(target, dpi=150, facecolor="white")
    plt.close(ax.figure)
    return target


def _histogram(data, meta, target):
    edges, counts = data.get("bin_edges", []), data.get("counts", [])
    if not counts or len(edges) != len(counts) + 1:
        return None
    zoom = data.get("display_zoom")
    if zoom:
        z_edges, z_counts = zoom.get("bin_edges", []), zoom.get("counts", [])
        if len(z_edges) != len(z_counts) + 1 or sum(z_counts) != zoom["sample_size"] \
                or zoom["sample_size"] + zoom["outside_count"] != sum(counts):
            zoom = None
    if zoom:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
        panels = ((axes[0], edges, counts, f"Full range · all {sum(counts):,} readings"),
                  (axes[1], zoom["bin_edges"], zoom["counts"],
                   f"Central 1st–99th percentiles · {zoom['sample_size']:,} readings"))
        for ax, panel_edges, panel_counts, title in panels:
            ax.bar(panel_edges[:-1], panel_counts,
                   width=[b - a for a, b in zip(panel_edges, panel_edges[1:])],
                   align="edge", edgecolor="white", color="#29627f")
            ax.set_title(title, loc="left", fontsize=10)
            ax.set_xlabel("Closure torque (Nm)")
            ax.set_ylabel(_population(meta))
            ax.grid(axis="y", alpha=0.2)
        axes[1].text(0, -0.28,
                     f"{zoom['outside_count']:,} readings outside zoom; included at left.",
                     transform=axes[1].transAxes, fontsize=9)
        fig.suptitle("Observed torque distribution", x=0.08, ha="left")
        fig.tight_layout()
        fig.savefig(target, dpi=150, facecolor="white")
        plt.close(fig)
        return target
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(edges[:-1], counts, width=[b - a for a, b in zip(edges, edges[1:])],
           align="edge", edgecolor="white", color="#29627f")
    ax.set_xlabel("Closure torque (Nm)")
    return _finish(ax, "Observed torque distribution", _population(meta), target)


def _trend(data, meta, target):
    x, y = data.get("timestamps", []), data.get("moving_average", [])
    if not x or len(x) != len(y):
        return None
    fig, ax = plt.subplots(figsize=(9, 4.5))
    # A long trace is represented by evenly sampled *returned points*, with
    # endpoints retained. The analytical result and report remain complete.
    step = max(1, (len(x) - 1) // 999)
    indices = sorted(set(range(0, len(x), step)) | {len(x) - 1})
    ax.plot(_times([x[i] for i in indices]), [y[i] for i in indices], color="#29627f")
    ax.set_xlabel(TIME_LABEL)
    return _finish(ax, "Trailing torque mean", "Torque (Nm)", target)


def _heads(data, meta, target):
    rows = data.get("ranked_heads", data.get("per_head", []))
    rows = [row for row in rows if row.get("success_rate_cap_present") is not None]
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: (row["machine_id"], row["head_id"]))
    labels = [row["head_id"] for row in rows]
    values = [100 * row["success_rate_cap_present"] for row in rows]
    fig, ax = plt.subplots(figsize=(max(9, len(rows) * 0.27), 4.5))
    ax.bar(labels, values, color="#29627f")
    ax.tick_params(axis="x", labelrotation=90)
    ax.set_ylim(0, 100.5)
    ax.set_xlabel("Capping head")
    return _finish(ax, "Confirmed cap-present success by head", "Success (%)", target)


def _time(data, meta, target):
    buckets = data.get("by_bucket", [])
    if not buckets:
        return None
    x = [item["bucket_start"] for item in buckets]
    y = [item["observed_events_per_hour"] for item in buckets]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(_times(x), y, marker="o", color="#29627f")
    ax.set_xlabel("Requested interval start (UTC)")
    return _finish(ax, "Observed event throughput", "Observed events / hour", target)


def _idle(data, meta, target):
    periods = data.get("idle_periods", [])
    if not periods:
        return None
    fig, ax = plt.subplots(figsize=(9, 4.5))
    # Bars span each interval on a real time axis (widths are in days).
    ax.bar(_times([p["start"] for p in periods]), [p["duration_seconds"] for p in periods],
           width=[p["duration_seconds"] / 86400 for p in periods], align="edge",
           color="#ad7631")
    ax.set_xlabel("Interval start (UTC)")
    ax.tick_params(axis="x", labelrotation=35)
    return _finish(ax, "All-head No Load intervals", "Observed seconds", target)


PLOTTERS = {"torque_distribution": _histogram, "torque_trend": _trend,
            "success_rate_per_head": _heads, "rank_heads_by_success": _heads,
            "compare_head_success": _heads, "kpi_over_time": _time,
            "observed_throughput": _time, "machine_idle": _idle}


def render(results, out_dir):
    """Return (tool name, PNG path) for every plot with actual observations."""
    output = Path(out_dir)
    made = []
    for name, reply in results:
        if reply.get("ok") and name in PLOTTERS:
            output.mkdir(parents=True, exist_ok=True)
            path = PLOTTERS[name](reply["result"], reply.get("meta"), output / f"{name}.png")
            if path is not None:
                made.append((name, path))
    return made
