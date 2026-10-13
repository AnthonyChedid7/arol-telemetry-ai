"""Which events a registered torque analysis describes.

Audit finding: with no status filter the torque tools pooled "No Load" cycles
(the head cycled with no cap, torque ~0 Nm) with real closures (~2 Nm). On a
real day that made a third of all events "anomalies", put the mean at 1.34 Nm
and turned the standard deviation into a measure of the No Load share.

The registered tools therefore describe closures by default. The underlying
Person A functions keep their documented semantics (None = every event); this
module only decides which events reach them:

    omitted / None   every status except the No Load class (default)
    "all_statuses"   every observed event, including No Load cycles
    "successful"     status 0 (unchanged)
    integer          that exact status code (unchanged)
"""

import polars as pl

ALL_STATUSES = "all_statuses"
NO_LOAD_CLASS = "No Load"
DEFAULT_MISMATCH_NM = 0.5


def select_population(events, status_filter):
    """Return (events, status_filter for the Person A function, label)."""
    if status_filter == ALL_STATUSES:
        return events, None, "every status, including No Load cycles"
    if status_filter is None:
        if "error_class" not in events.collect_schema():
            return events, None, "every status (No Load class unavailable in this data)"
        # Unknown (null) classes are kept: they are not known to be No Load.
        closures = events.filter(pl.col("error_class").fill_null("") != NO_LOAD_CLASS)
        return closures, None, "closures, excluding No Load cycles (default)"
    if status_filter == "successful":
        return events, status_filter, "successful closures only (status 0)"
    return events, status_filter, f"status {status_filter} only"


def optional_config() -> dict:
    """Runtime configuration when a dispatch set one, else {} (defaults apply).

    torque_stats never required configuration; it must keep working when
    called through the registry without it.
    """
    from ..common.runtime import current_config
    try:
        return current_config()
    except ValueError:
        return {}


def describe(response, label, scoped, config):
    """Record the population and status/torque disagreements in the envelope meta."""
    response["meta"]["population"] = label
    response["meta"]["status_torque_mismatches"] = status_torque_mismatches(scoped, config)
    return response


def status_torque_mismatches(events, config) -> dict:
    """Count events whose status contradicts their torque.

    The controller updates Status one poll after Count/AppTorque, so a few
    events carry the previous cycle's status: "Closure OK" with ~0 Nm, or
    "No Load" with closing torque. They are flagged, not corrected.
    """
    threshold = float(((config or {}).get("analytics") or {}).get(
        "status_torque_mismatch_nm", DEFAULT_MISMATCH_NM))
    schema = events.collect_schema()
    if not {"status", "torque", "error_class"} <= set(schema.names()):
        return {"threshold_nm": threshold, "ok_with_low_torque": None,
                "no_load_with_torque": None}
    finite = pl.col("torque").is_not_null() & pl.col("torque").is_finite()
    counts = events.lazy().select(
        (finite & (pl.col("status") == 0) & (pl.col("torque") < threshold))
        .sum().alias("ok_with_low_torque"),
        (finite & (pl.col("error_class") == NO_LOAD_CLASS) & (pl.col("torque") >= threshold))
        .sum().alias("no_load_with_torque"),
    ).collect().row(0, named=True)
    return {"threshold_nm": threshold,
            "ok_with_low_torque": int(counts["ok_with_low_torque"] or 0),
            "no_load_with_torque": int(counts["no_load_with_torque"] or 0)}
