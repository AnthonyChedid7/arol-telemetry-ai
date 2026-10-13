"""Thin registered adapters for the remaining four Person A analytics tools."""

import polars as pl

from ..common.envelope import data_window, envelope
from ..common.registry import tool
from ..common.runtime import current_config
from . import registered_torque  # noqa: F401 - register the first tool too
from .event_filters import filter_events
from .torque_distribution import torque_distribution, _prepare_torque_events
from .trend_analysis import torque_trend, _classify_drift
from .anomaly_detection import detect_torque_anomalies
from .head_correlation import (
    head_correlation, _validate_head_ids, _matched_torque_pairs,
)
from .torque_population import NO_LOAD_CLASS, describe, optional_config, select_population

SCOPE = ["start", "end", "head_id", "machine_id", "status_filter"]
# Same value as config.yaml analytics.drift_threshold_nm_per_hour; used only
# when a configuration omits the key.
DEFAULT_DRIFT_NM_PER_HOUR = 0.05
DEFAULT_TREND_MAX_POINTS = 2000  # config.yaml analytics.trend_max_points
CAP_PRESENT_LABEL = "cap-present closures (status 0 or 65; No Load excluded)"
OBSERVED_NOTE = (
    "Only supplied observed exact +1 events are analysed. "
    "Counter discontinuities are not reconstructed. "
)


def _finish(result, scoped, population, inner, label, applied, *, units, notes):
    used = _prepare_torque_events(population, inner).collect()
    filters = [f"population: {label}"] + list(applied)
    filters.append("torque is non-null and finite")
    if result["sample_size"] == 0:
        notes += " No finite torque observations matched."
    return describe(envelope(
        result, n=result["sample_size"], filters_applied=filters,
        units=units, window=data_window(used), notes=OBSERVED_NOTE + notes,
    ), label, scoped, optional_config())


@tool(name="torque_distribution", description=(
    "Equal-width histogram of finite torque observations in the requested scope."
), params=SCOPE + ["bins"], owner="A")
def registered_distribution(events, *, bins=10, status_filter=None,
                            start=None, end=None, head_id=None, machine_id=None):
    scoped, applied = filter_events(
        events, start=start, end=end, head_id=head_id, machine_id=machine_id,
    )
    population, inner, label = select_population(scoped, status_filter)
    result = torque_distribution(population, bins=bins, status_filter=inner)
    return _finish(
        result, scoped, population, inner, label, applied,
        units={"bin_edges": "Nm", "counts": "observations"},
        notes=("Bins are left-inclusive and right-exclusive except the final bin, "
               "which includes its right edge. A histogram alone does not diagnose a fault."),
    )


@tool(name="torque_trend", description=(
    "Time-based torque moving average and numerical drift signal. "
    "The trailing window is supplied by trusted project configuration."
), params=SCOPE, owner="A")
def registered_trend(events, *, status_filter=None,
                     start=None, end=None, head_id=None, machine_id=None):
    scoped, applied = filter_events(
        events, start=start, end=end, head_id=head_id, machine_id=machine_id,
    )
    population, inner, label = select_population(scoped, status_filter)
    config = current_config()
    result = torque_trend(population, config, status_filter=inner)
    # Audit finding: a numerical epsilon called any non-zero slope "drift".
    # Drift now needs the configured practical rate of change.
    per_hour = (config.get("analytics") or {}).get(
        "drift_threshold_nm_per_hour", DEFAULT_DRIFT_NM_PER_HOUR)
    if not (isinstance(per_hour, (int, float)) and not isinstance(per_hour, bool) and per_hour > 0):
        raise ValueError("analytics.drift_threshold_nm_per_hour must be a positive number of Nm per hour")
    slope = result["drift_slope_per_second"]
    result["drift_threshold_nm_per_hour"] = float(per_hour)
    result["drift_slope_nm_per_hour"] = None if slope is None else slope * 3600
    result["drift_detected"], result["drift_direction"] = _classify_drift(
        slope, threshold_per_second=per_hour / 3600)
    # The slope and drift above use every point. Only the returned series is
    # thinned: a one-day trend was ~41 MB of JSON (brief: scale with volume).
    max_points = (config.get("analytics") or {}).get("trend_max_points", DEFAULT_TREND_MAX_POINTS)
    if type(max_points) is not int or max_points < 2:
        raise ValueError("analytics.trend_max_points must be an integer of at least 2")
    total = len(result["timestamps"])
    result["timestamps"], result["moving_average"] = _thin_series(
        result["timestamps"], result["moving_average"], max_points)
    thinned = len(result["timestamps"]) < total
    response = _finish(
        result, scoped, population, inner, label, applied,
        units={"moving_average": "Nm", "drift_slope_per_second": "Nm/s",
               "drift_slope_nm_per_hour": "Nm/h", "drift_threshold_nm_per_hour": "Nm/h",
               "window_seconds": "s"},
        notes=(f"The configured trailing window is {result['window_seconds']} seconds. "
               f"Drift is reported only when the latest-window slope of the moving average "
               f"exceeds {per_hour:g} Nm per hour (configured practical threshold, not yet "
               "validated with AROL, and not a statistical significance test). "
               "Selected observations are pooled; this is not a separate trend per head. "
               "The requested scope supplies all available history for this calculation."
               + (f" The returned moving-average series is thinned to {len(result['timestamps']):,} "
                  f"of {total:,} points, evenly over time with first and last kept; the slope "
                  "and drift use every point." if thinned else "")),
    )
    response["meta"]["series_points"] = {"returned": len(result["timestamps"]), "total": total}
    return response


def _thin_series(timestamps, values, max_points):
    """Keep at most max_points, one per equal time slice, plus first and last."""
    if len(timestamps) <= max_points:
        return timestamps, values
    from datetime import datetime
    times = [datetime.fromisoformat(t) for t in timestamps]
    first, span = times[0], (times[-1] - times[0]).total_seconds()
    if span <= 0:
        step = (len(times) - 1) / (max_points - 1)
        keep = sorted({round(i * step) for i in range(max_points)})
    else:
        slices = max_points - 1
        seen, keep = set(), []
        for index, moment in enumerate(times):
            slot = min(slices - 1, int((moment - first).total_seconds() / span * slices))
            if slot not in seen:  # first point of each time slice
                seen.add(slot)
                keep.append(index)
        if keep[-1] != len(times) - 1:
            keep.append(len(times) - 1)
    return [timestamps[i] for i in keep], [values[i] for i in keep]


@tool(name="detect_torque_anomalies", description=(
    "Flag finite torque observations outside configured limits or a configured "
    "sample-standard-deviation threshold. Settings come from trusted configuration."
), params=SCOPE, owner="A")
def registered_anomalies(events, *, status_filter=None,
                         start=None, end=None, head_id=None, machine_id=None):
    scoped, applied = filter_events(
        events, start=start, end=end, head_id=head_id, machine_id=machine_id,
    )
    population, inner, label = select_population(scoped, status_filter)
    result = detect_torque_anomalies(
        population, current_config(), status_filter=inner,
    )
    return _finish(
        result, scoped, population, inner, label, applied,
        units={"mean": "Nm", "std": "Nm", "expected_min": "Nm",
               "expected_max": "Nm", "anomaly_rate": "fraction"},
        notes=(f"Configured limits: {result['expected_min']} to {result['expected_max']} Nm; "
               f"sigma={result['sigma']}. Statistical comparison pools the selected "
               "observations and uses sample standard deviation (ddof=1). "
               "Configuration values require process validation; flags do not establish a cause."),
    )


@tool(name="head_correlation", description=(
    "Compare two heads using same-timestamp finite torque pairs on one machine. "
    "Also return each head's existing Person A summaries and success denominator."
), params=["head_a", "head_b", "start", "end", "machine_id"],
      required=["head_a", "head_b"], owner="A")
def registered_head_correlation(events, *, head_a, head_b,
                                start=None, end=None, machine_id=None):
    _validate_head_ids(head_a, head_b)
    scoped, applied = filter_events(
        events, start=start, end=end, machine_id=machine_id,
    )
    if "machine_id" not in scoped.collect_schema():
        raise ValueError("head_correlation requires machine_id to prevent cross-machine pairing")
    selected = scoped.filter(pl.col("head_id").is_in([head_a, head_b])).collect()
    if selected.height:
        if selected["machine_id"].null_count() or selected["ts"].null_count():
            raise ValueError("Correlation requires non-null machine identifiers and timestamps")
        if selected["machine_id"].n_unique() != 1:
            raise ValueError("Select one machine_id before comparing heads")
        if selected.select("head_id", "ts").is_duplicated().any():
            raise ValueError("Duplicate head/timestamp observations make correlation pairing ambiguous")
    # Audit finding: two heads idling together (No Load, torque ~0) produced
    # r=0.99 on real data although closures alone give r=0.44. Correlation and
    # the per-head summaries therefore use closures only.
    #
    # The population is the cap-present closures, so Person A's success
    # denominator inside head_correlation equals the team contract's (and the
    # KPI tools'): status 0 / cap_present. Unknown cap presence is not counted.
    if "cap_present" in selected.columns:
        selected = selected.filter(pl.col("cap_present").fill_null(False))
    elif "error_class" in selected.columns:
        selected = selected.filter(pl.col("error_class").fill_null("") != NO_LOAD_CLASS)
    result = head_correlation(selected, head_a, head_b)
    matched = _matched_torque_pairs(selected, head_a, head_b)
    response = envelope(
        result, n=result["matched_torque_samples"],
        filters_applied=applied + [f"head pair: {head_a}, {head_b}",
                                   f"population: {CAP_PRESENT_LABEL}"],
        units={"mean_torque": "Nm", "torque_correlation": "dimensionless",
               "success_rate_difference": "fraction",
               "success_rate_difference_percentage_points": "percentage points"},
        window=data_window(selected),
        notes=(OBSERVED_NOTE + "n counts matched finite torque pairs. The main data window "
               "covers both heads' summary observations. Success rates use the team "
               "contract's denominator, the same as the KPI tools: successful (status 0) "
               "cap-present closures / cap-present closures. "
               "Correlation does not establish causation. Ambiguous pairings are rejected, "
               "not deduplicated or paired across machines."),
    )
    response["meta"]["correlation_data_window"] = data_window(matched)
    response["meta"]["population"] = CAP_PRESENT_LABEL
    return response
