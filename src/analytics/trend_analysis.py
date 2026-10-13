from datetime import timedelta

import polars as pl

from src.analytics.torque_stats import (
    _apply_status_filter,
    _as_lazy,
    _validate_event_schema,
)


DRIFT_EPSILON = 1e-12


def _validate_trend_schema(
    events: pl.LazyFrame,
) -> None:
    """
    Validate the columns required for trend analysis.

    Trend analysis requires:
        ts
        torque
        status

    torque/status are validated by the shared Step 10
    event-schema validator.
    """

    _validate_event_schema(
        events
    )

    schema = events.collect_schema()

    if "ts" not in schema:
        raise ValueError(
            "Missing required event column: ts"
        )

    if (
        schema["ts"].base_type()
        != pl.Datetime
    ):
        raise TypeError(
            "ts must be a Polars Datetime column"
        )


def _get_drift_window_seconds(
    config: dict,
) -> int:
    """
    Read and validate the configured drift window.
    """

    try:
        window_seconds = config[
            "analytics"
        ][
            "drift_window_seconds"
        ]
    except (
        KeyError,
        TypeError,
    ) as exc:
        raise ValueError(
            "Missing config value: "
            "analytics.drift_window_seconds"
        ) from exc

    if isinstance(
        window_seconds,
        bool,
    ):
        raise TypeError(
            "analytics.drift_window_seconds "
            "must be a positive integer"
        )

    if not isinstance(
        window_seconds,
        int,
    ):
        raise TypeError(
            "analytics.drift_window_seconds "
            "must be a positive integer"
        )

    if window_seconds <= 0:
        raise ValueError(
            "analytics.drift_window_seconds "
            "must be greater than zero"
        )

    return window_seconds


def _prepare_trend_events(
    events: pl.DataFrame | pl.LazyFrame,
    status_filter=None,
) -> pl.LazyFrame:
    """
    Prepare valid event-level torque observations.

    The event table is sorted chronologically because
    trend analysis is explicitly time dependent.

    Null, NaN, and infinite torque observations are
    excluded.
    """

    lazy_events = _as_lazy(
        events
    )

    _validate_trend_schema(
        lazy_events
    )

    filtered = _apply_status_filter(
        lazy_events,
        status_filter,
    )

    return (
        filtered
        .filter(
            pl.col("torque")
            .is_not_null()
            & pl.col("torque")
            .is_finite()
        )
        .sort(
            "ts"
        )
    )


def _calculate_drift_slope(
    trend_frame: pl.DataFrame,
    window_seconds: int,
) -> float | None:
    """
    Calculate the linear slope of the moving average
    inside the most recent configured time window.

    The independent variable is elapsed event time
    in seconds.

    Returns:
        torque-units per second

    If fewer than two distinct timestamps are available,
    no slope can be calculated.
    """

    if trend_frame.height < 2:
        return None

    latest_timestamp = (
        trend_frame[
            "ts"
        ][-1]
    )

    cutoff_timestamp = (
        latest_timestamp
        - timedelta(
            seconds=window_seconds
        )
    )

    recent = (
        trend_frame
        .lazy()
        .filter(
            pl.col("ts")
            > pl.lit(
                cutoff_timestamp
            )
        )
        .with_columns(
            pl.col("ts")
            .dt.epoch(
                time_unit="ms"
            )
            .cast(
                pl.Float64
            )
            .truediv(
                1000.0
            )
            .alias(
                "__time_seconds"
            )
        )
        .collect()
    )

    if recent.height < 2:
        return None

    if (
        recent[
            "__time_seconds"
        ].n_unique()
        < 2
    ):
        return None

    slope_result = (
        recent
        .lazy()
        .select(
            (
                (
                    (
                        pl.col(
                            "__time_seconds"
                        )
                        - pl.col(
                            "__time_seconds"
                        ).mean()
                    )
                    * (
                        pl.col(
                            "moving_average"
                        )
                        - pl.col(
                            "moving_average"
                        ).mean()
                    )
                )
                .sum()
                /
                (
                    (
                        pl.col(
                            "__time_seconds"
                        )
                        - pl.col(
                            "__time_seconds"
                        ).mean()
                    )
                    .pow(2)
                )
                .sum()
            )
            .alias(
                "slope"
            )
        )
        .collect()
    )

    slope = slope_result[
        "slope"
    ][0]

    if slope is None:
        return None

    return float(
        slope
    )


def _classify_drift(
    slope: float | None,
    threshold_per_second: float = DRIFT_EPSILON,
) -> tuple[bool, str]:
    """
    Convert the numerical trend slope into an
    interpretable drift signal.

    By default a tiny epsilon is used only to avoid
    classifying floating-point numerical noise as real
    movement. The registered tool passes the configured
    practical threshold instead (audit finding: with the
    epsilon, any non-zero slope counted as drift).
    """

    if slope is None:
        return (
            False,
            "insufficient_data",
        )

    if slope > threshold_per_second:
        return (
            True,
            "upward",
        )

    if slope < -threshold_per_second:
        return (
            True,
            "downward",
        )

    return (
        False,
        "stable",
    )


def torque_trend(
    events: pl.DataFrame | pl.LazyFrame,
    config: dict,
    status_filter=None,
) -> dict:
    """
    Calculate a time-based moving average and drift
    signal from the clean event table.

    Parameters
    ----------
    events:
        Clean Polars event DataFrame or LazyFrame.

    config:
        Project configuration dictionary.

        Required key:

            analytics:
                drift_window_seconds: <positive int>

    status_filter:
        Optional status filter using the same semantics
        as the other torque analytics.

        None
            all events

        "successful"
            status == 0

        integer
            exact AROL status code

    Returns
    -------
    dict
        JSON-safe dictionary containing:

            window_seconds
            sample_size
            timestamps
            moving_average
            drift_detected
            drift_direction
            drift_slope_per_second

    Notes
    -----
    The moving average uses an actual timestamp-based
    trailing window rather than a fixed number of rows.

    Drift is calculated from the slope of the moving
    average during the most recent configured window.
    """

    window_seconds = (
        _get_drift_window_seconds(
            config
        )
    )

    prepared = (
        _prepare_trend_events(
            events,
            status_filter,
        )
    )

    trend_frame = (
        prepared
        .with_columns(
            pl.col("torque")
            .rolling_mean_by(
                by="ts",
                window_size=(
                    f"{window_seconds}s"
                ),
                min_samples=1,
                closed="right",
            )
            .alias(
                "moving_average"
            )
        )
        .select(
            "ts",
            "moving_average",
        )
        .collect()
    )

    sample_size = int(
        trend_frame.height
    )

    if sample_size == 0:
        return {
            "window_seconds": (
                window_seconds
            ),
            "sample_size": 0,
            "timestamps": [],
            "moving_average": [],
            "drift_detected": False,
            "drift_direction": (
                "insufficient_data"
            ),
            "drift_slope_per_second": None,
        }

    slope = (
        _calculate_drift_slope(
            trend_frame,
            window_seconds,
        )
    )

    (
        drift_detected,
        drift_direction,
    ) = _classify_drift(
        slope
    )

    timestamps = [
        timestamp.isoformat()
        for timestamp
        in trend_frame[
            "ts"
        ].to_list()
    ]

    moving_average = [
        float(value)
        for value
        in trend_frame[
            "moving_average"
        ].to_list()
    ]

    return {
        "window_seconds": (
            window_seconds
        ),
        "sample_size": (
            sample_size
        ),
        "timestamps": (
            timestamps
        ),
        "moving_average": (
            moving_average
        ),
        "drift_detected": (
            drift_detected
        ),
        "drift_direction": (
            drift_direction
        ),
        "drift_slope_per_second": (
            slope
        ),
    }