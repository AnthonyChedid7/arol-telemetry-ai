"""The report assembler.

Enforces the structure the brief requires, in this order and always:

    goal -> data used -> analyses executed -> findings
         -> confidence and limits -> next checks

The confidence/limits section is computed from meta.n and the tool notes, not
written as prose. Almost every team ships that heading with something vague
under it; filling it with real sample sizes and real caveats is cheap here
because every envelope already carries `n`.

Findings are rendered by deterministic templates, one per tool. No model is
involved in producing a number or the sentence around it, so the same plan
produces the same report text every time. (With the Claude planner the plan
itself can vary between runs; the "Interpreted as" line shows which plan ran.)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


def _pct(value, digits=2):
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


FAILURE = "failure"
_FAILURE_WORDS = re.compile(r"\b(?:fail(?:s|ed|ing|ures?)?|reject(?:s|ed|ions?)?|defect(?:s|ive)?)\b", re.I)
_SUCCESS_WORDS = re.compile(r"\bsuccess", re.I)
# Tools whose findings, key figures and chart can lead with the failure rate.
FOCUS_AWARE = frozenset({"success_rate", "success_rate_per_head"})


def rate_focus(query: str) -> str:
    """'failure' when a question asks about failures or rejects and not about success.

    Presentation only: the same tools run and the same numbers are reported;
    the failure (cap-present rejection) rate is just shown first.
    """
    text = query or ""
    return FAILURE if _FAILURE_WORDS.search(text) and not _SUCCESS_WORDS.search(text) else ""


# --- per-tool finding templates -------------------------------------------

def _kpi_row_lines(o, focus=""):
    success = f"- Cap-present success: **{_pct(o['success_rate_cap_present'], 6)}** ({o['n_success_cap_present']:,}/{o['n_cap_present']:,})."
    if focus == FAILURE:
        rates = [f"- **Failure rate** (cap-present rejection flags): **{_pct(o['reject_rate_cap_present'], 6)}** ({o['n_reject_cap_present']:,}/{o['n_cap_present']:,}).",
                 success]
    else:
        rates = [success,
                 f"- Cap-present rejection flags: **{_pct(o['reject_rate_cap_present'], 6)}** ({o['n_reject_cap_present']:,}/{o['n_cap_present']:,})."]
    return [
        f"- Observed exact +1 events: **{o['n_observed']:,}**; confirmed cap-present: **{o['n_cap_present']:,}**; unknown cap presence: **{o['n_cap_unknown']:,}**.",
        *rates,
        f"- Rejection flags outside the cap-present population: **{o['n_reject_outside_cap_present']:,}**; unknown rejection flags: {o['n_reject_unknown']:,}.",
        f"- No Load class: {o['n_no_load_class']:,}/{o['n_observed']:,} observed events ({_pct(o['no_load_fraction_all_observed'], 6)}). Explicit cap absence: {o['n_cap_absent']:,} events.",
        f"- Status-0 fraction of all observed events: {_pct(o['success_fraction_all_observed'], 6)} ({o['n_success_all']:,}/{o['n_observed']:,}).",
        f"- Legacy A non-No-Load success fraction: {_pct(o['legacy_a_success_rate_non_no_load'], 6)} ({o['n_legacy_a_success']:,}/{o['n_legacy_a_non_no_load']:,}); this uses a different denominator.",
    ]


def _finding_success_rate(result, meta, focus=""):
    lines = _kpi_row_lines(result['overall'], focus)
    if result['overall']['n_observed'] == 0:
        lines.append('- No observed events matched; rates with empty denominators are undefined (n/a).')
    return lines


def _failure_table(rows):
    """One line per head: the failure rate the question asked for, with its counts."""
    lines = ["", "| Machine | Head | Failure rate | Rejects / cap-present | Success rate |",
             "|---|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['machine_id']} | {row['head_id']} | **{_pct(row['reject_rate_cap_present'], 4)}** | "
                     f"{row['n_reject_cap_present']:,} / {row['n_cap_present']:,} | "
                     f"{_pct(row['success_rate_cap_present'], 4)} |")
    return lines + [""]


def _finding_success_rate_per_head(result, meta, focus=""):
    rows = result['per_head']
    lines = [f"- {result['n_head_groups']} machine/head groups; sorted by identifiers, without ranking."]
    if focus == FAILURE and rows:
        lines.append("- Failure rate = cap-present rejection flags / confirmed cap-present observed events.")
        lines += _failure_table(rows)
        lines.append("Full counts per head:")
    for row in rows:
        lines.append(f"- **{row['machine_id']} / {row['head_id']}**:")
        lines.extend(_kpi_row_lines(row, focus))
        if row['below_min_cap_present_n']:
            lines.append(f"- This group has {row['n_cap_present']:,} cap-present observations, below min_n={row['min_cap_present_n']}.")
    if not rows:
        lines.append('- No observed events matched; no machine/head groups are available.')
    return lines


def _finding_anomaly_heads(result, meta) -> list[str]:
    if not result["flagged_heads"]:
        return [f"- No head exceeded {result['sigma']} sigma against the fleet. "
                f"Median reject rate {_pct(result['fleet_median_reject_rate'], 3)}."]
    lines = [f"- {result['n_flagged']} head(s) flagged against a fleet median "
             f"reject rate of {_pct(result['fleet_median_reject_rate'], 3)}:"]
    for head in result["flagged_heads"]:
        lines.append(f"  - **{head['head_id']}**: reject rate "
                     f"{_pct(head['reject_rate'], 3)} over "
                     f"{head['n_cap_present']:,} cap-present closures "
                     f"({head['n_reject']:,} rejects vs "
                     f"{head['expected_rejects']} expected, "
                     f"{head['rate_ratio']}x the fleet median, "
                     f"p = {head['p_value']:.2e}).")
    lines.append(f"- Method: {result['method']}.")
    return lines


def _finding_idle_periods(result, meta) -> list[str]:
    if not result["idle_periods"]:
        return [f"- No sustained No Load stretch of at least "
                f"{result['threshold_seconds']:.0f}s was found."]
    lines = [
        f"- {result['n_periods']} idle stretch(es) of at least "
        f"{result['threshold_seconds']:.0f}s across "
        f"{len(result['heads_affected'])} head(s); "
        f"{result['total_idle_seconds'] / 3600:.2f} head-hours in total.",
    ]
    longest = result["idle_periods"][0]
    lines.append(f"- Longest: {longest['head_id']} from {longest['start']} to "
                 f"{longest['end']} ({longest['duration_seconds']:.0f}s, "
                 f"{longest['n_cycles']:,} empty cycles).")
    if len(result["heads_affected"]) > 1:
        lines.append("- The stretch spans multiple heads at the same time; "
                     "review independent process signals before attributing a cause.")
    return lines


def _finding_machine_idle(result, meta) -> list[str]:
    lines = [f"- Raw status readings inspected: **{result['n_raw_readings']:,}**; "
             f"every head reported No Load on {result['n_all_heads_no_load']:,} of them.",
             f"- Continuity breaks: {result['timestamp_gaps']} timestamp gaps and "
             f"{result['duplicate_timestamps']} repeated timestamps.",
             f"- Found **{result['n_periods']}** all-head No Load interval(s) "
             f"with at least {result['threshold_seconds']} consecutive one-second readings."]
    for period in result['idle_periods'][:20]:
        lines.append(f"- [{period['start']}, {period['end']}): "
                     f"{period['duration_seconds']} observed seconds.")
    if result['n_periods'] > 20:
        lines.append(f"- {result['n_periods'] - 20} additional intervals are in the structured tool result.")
    lines.append('- This status pattern is an idle candidate; operating schedule and independent machine signals are needed to establish downtime.')
    return lines


def _finding_throughput(result, meta) -> list[str]:
    lines = [
        f"- {result['total_closures']:,} closures over "
        f"{result['span_hours']:.2f}h, mean "
        f"{result['mean_closures_per_hour']:,.0f} closures/hour.",
    ]
    buckets = result.get("by_bucket") or []
    if buckets:
        best = max(buckets, key=lambda b: b["closures_per_hour"])
        worst = min(buckets, key=lambda b: b["closures_per_hour"])
        lines.append(f"- Peak {best['closures_per_hour']:,.0f}/h at "
                     f"{best['bucket_start']}; trough "
                     f"{worst['closures_per_hour']:,.0f}/h at "
                     f"{worst['bucket_start']}.")
    if result.get("n_inferred_closures"):
        lines.append(f"- {result['n_inferred_closures']} closure(s) were "
                     f"inferred from counter jumps greater than one "
                     f"(a dropped poll), not observed directly.")
    return lines



def _finding_head_detail(result, meta) -> list[str]:
    where = ("the WORST of" if result["is_worst_head"]
             else f"ranked {result['rank_worst_first']} worst of")
    lines = [
        f"- **{result['head_id']}**: success rate "
        f"**{_pct(result['success_rate'])}** over "
        f"{result['n_cap_present']:,} cap-present closures "
        f"({result['n_reject']:,} rejects, No Load {_pct(result['no_load_rate'])}).",
        f"- That is {where} {result['n_heads']} heads.",
    ]
    if result.get("difference_from_fleet_median") is not None:
        delta = result["difference_from_fleet_median"] * 100
        direction = "below" if delta < 0 else "above"
        lines.append(f"- Fleet median is {_pct(result['fleet_median_success_rate'])}, "
                     f"so this head sits {abs(delta):.3f} percentage points "
                     f"{direction} it.")
    return lines



def _finding_torque_stats(result, meta):
    n = result["sample_size"]
    if n == 0:
        return [
            "- No finite torque observations matched the requested filters.",
            "- Torque statistics cannot be estimated from this empty sample.",
        ]

    lines = [
        f"- Finite torque observations: **{n:,}**.",
        f"- Mean: **{result['mean']:.6g} Nm**; "
        f"minimum: **{result['min']:.6g} Nm**; "
        f"maximum: **{result['max']:.6g} Nm**.",
    ]
    if result["std"] is None:
        lines.append(
            "- Sample standard deviation is undefined for fewer than "
            "two observations."
        )
    else:
        lines.append(
            f"- Sample standard deviation (ddof=1): "
            f"**{result['std']:.6g} Nm**."
        )
    lines.append(
        "- These summary statistics do not establish whether torque "
        "meets engineering limits."
    )
    return _population_lines(meta) + lines


def _analytics_number(value):
    return "n/a" if value is None else f"{value:.6g}"


def _population_lines(meta):
    """Which events a torque result describes, and known status/torque disagreements."""
    lines = []
    if meta.get("population"):
        lines.append(f"- Events analysed: {meta['population']}.")
    mismatch = meta.get("status_torque_mismatches") or {}
    ok_low, no_load_high = mismatch.get("ok_with_low_torque"), mismatch.get("no_load_with_torque")
    if ok_low or no_load_high:
        lines.append(
            f"- Status/torque disagreements in the selected scope: {ok_low or 0:,} "
            f"'Closure OK' events below {mismatch['threshold_nm']:g} Nm and "
            f"{no_load_high or 0:,} 'No Load' events at or above it. The controller "
            "updates Status one reading after the count, so these events probably "
            "carry the previous cycle's status. They are flagged, not corrected.")
    return lines


def _finding_torque_distribution(result, meta):
    if result["sample_size"] == 0:
        return ["- No finite torque observations matched; no histogram is available."]
    counts = result["counts"]
    shown = counts[:20]
    label = "Bin counts" if len(counts) <= 20 else "First 20 bin counts"
    lines = [
        f"- Histogram: {result['sample_size']:,} finite observations in {len(counts)} bins.",
        f"- Bin-edge range: {_analytics_number(result['bin_edges'][0])} to "
        f"{_analytics_number(result['bin_edges'][-1])} Nm.",
        f"- {label}: {shown}.",
    ]
    if zoom := result.get("display_zoom"):
        lines.append(
            f"- Plot detail: central 1st–99th percentile range "
            f"{_analytics_number(zoom['bin_edges'][0])} to "
            f"{_analytics_number(zoom['bin_edges'][-1])} Nm contains "
            f"{zoom['sample_size']:,} observations; "
            f"{zoom['outside_count']:,} fall outside this zoom but remain in the "
            "full-range histogram. Both panels use the requested bin count."
        )
    return _population_lines(meta) + lines


def _finding_torque_trend(result, meta):
    if result["sample_size"] == 0:
        return ["- No finite torque observations matched; no trend can be estimated."]
    lines = [
        f"- Moving average uses a {result['window_seconds']} s trailing window over "
        f"{result['sample_size']:,} finite observations.",
    ]
    threshold = result.get("drift_threshold_nm_per_hour")
    if threshold is None:  # direct use of the Person A function
        lines.append(f"- Latest-window slope: {_analytics_number(result['drift_slope_per_second'])} Nm/s; "
                     f"numerical direction: {result['drift_direction']}.")
    elif result["drift_detected"]:
        lines.append(f"- **Drift detected ({result['drift_direction']})**: the latest-window slope is "
                     f"{_analytics_number(result['drift_slope_nm_per_hour'])} Nm per hour, beyond the "
                     f"configured {threshold:g} Nm per hour.")
    else:
        lines.append(f"- No drift beyond the configured {threshold:g} Nm per hour "
                     f"(latest-window slope {_analytics_number(result['drift_slope_nm_per_hour'])} Nm per hour; "
                     f"{result['drift_direction'].replace('_', ' ')}).")
    lines.append("- This signal alone does not establish an engineering problem or its cause.")
    return _population_lines(meta) + lines


def _finding_torque_anomalies(result, meta):
    if result["sample_size"] == 0:
        return ["- No finite torque observations matched; anomaly absence cannot be assessed."]
    lines = [
        f"- {result['anomaly_count']:,} of {result['sample_size']:,} observations flagged "
        f"({_pct(result['anomaly_rate'])}).",
        f"- Configured limits: {result['expected_min']} to {result['expected_max']} Nm; "
        f"statistical threshold: {result['sigma']} sample standard deviations.",
    ]
    for item in result["anomalies"][:3]:
        lines.append(f"- Example: {item['head_id']} at {item['ts']}, "
                     f"{_analytics_number(item['torque'])} Nm; reason: {item['reason']}.")
    lines.append("- Flags describe the configured checks; they do not identify a physical cause.")
    return _population_lines(meta) + lines


def _finding_head_correlation(result, meta):
    lines = [
        f"- Matched finite torque pairs: {result['matched_torque_samples']:,}; "
        f"Pearson correlation: {_analytics_number(result['torque_correlation'])} "
        f"({result['torque_correlation_interpretation']}).",
        "- Success denominator: cap-present closures, the same definition as the "
        "KPI tools (team contract).",
    ]
    if meta.get("population"):
        lines.insert(0, f"- Events analysed: {meta['population']} (pairs where both heads "
                        "closed a cap in the same second).")
    for side in ("head_a", "head_b"):
        head = result[side]
        lines.append(f"- {head['head_id']}: {head['event_count']:,} events; "
                     f"mean torque {_analytics_number(head['mean_torque'])} Nm; "
                     f"success {_pct(head['success_rate'])} "
                     f"({head['success_count']:,}/{head['success_evaluable_count']:,}).")
    lines.append("- Correlation does not establish causation.")
    return lines


def _finding_head_ranking(result, meta):
    lines = [
        f"- Metric: confirmed cap-present success fraction; eligible heads need at least {result['min_cap_present_n']} cap-present observations.",
        f"- {result['n_eligible_heads']} of {result['n_head_groups']} observed heads are eligible. Lowest fractions appear first; exact ties share competition ranks.",
        "- This is descriptive ordering, not a statistical anomaly test or a fault diagnosis.",
    ]
    if not result['ranking_available']:
        lines.append('- Fewer than two heads are eligible; comparative ranks are unavailable.')
    for row in result['ranked_heads']:
        rank = row['rank_lowest_success_first']
        label = f"rank {rank}" if rank is not None else 'unranked'
        lines.append(f"- **{row['head_id']}**: {label}; {_pct(row['success_rate_cap_present'], 6)} "
                     f"({row['n_success_cap_present']:,}/{row['n_cap_present']:,} confirmed cap-present); "
                     f"unknown cap presence {row['n_cap_unknown']:,}."
                     + (' Below the minimum sample size.' if not row['eligible_for_ranking'] else ''))
    return lines


def _finding_head_success_comparison(result, meta):
    lines = [f"- Focus head: **{result['focus_head_id']}**. Peers are other available heads in the same selected machine/time window."]
    if result['comparison_available']:
        focus = result['focus']
        lines += [
            f"- Focus cap-present success: {_pct(focus['success_rate_cap_present'], 6)} ({focus['n_success_cap_present']:,}/{focus['n_cap_present']:,}).",
            f"- Unweighted median across {result['n_eligible_peers']} eligible OTHER heads: {_pct(result['peer_median_success_rate'], 6)}.",
            f"- Focus minus peer median: {result['difference_from_peer_median_pp']:+.6f} percentage points.",
        ]
    else:
        reasons = {
            'focus_head_not_found': 'The requested head has no observations in this scope; no other head was substituted.',
            'focus_below_minimum_sample': 'The focus head has too few confirmed cap-present observations for this comparison.',
            'fewer_than_two_eligible_peers': 'At least two other eligible heads are required for the peer baseline.',
        }
        lines.append('- Comparison unavailable: ' + reasons[result['comparison_reason']])
    return lines + _finding_head_ranking(result, meta)


def _finding_temporal(result, meta, *, throughput=False):
    total = result['overall']
    lines = [
        f"- Requested window: {result['requested_window']['start']} to {result['requested_window']['end']} (end exclusive); {total['duration_seconds']:g} stored-timestamp seconds.",
        f"- Observed exact +1 events: **{total['n_observed']:,}**; cap-present: **{total['n_cap_present']:,}**; successful: **{total['n_success_cap_present']:,}**.",
        f"- Overall observed-event rate: **{total['observed_events_per_hour']:.6g}/hour**, using the full requested duration.",
        (f"- Incremental average of the {result['n_buckets']} bucket rates: "
         f"{result['incremental_mean_bucket_observed_rates'][-1]:.6g}/hour "
         "(unweighted; partial buckets differ in length)."
         if result.get('incremental_mean_bucket_observed_rates') else
         "- No bucket rates are available for an incremental average."),
        f"- Unknown cap presence: {total['n_cap_unknown']:,}; rejection flags outside cap-present rows: {total['n_reject_outside_cap_present']:,}.",
        '- Empty buckets mean no recorded events. They do not establish zero production or machine downtime; telemetry coverage is unverified.',
        '- Buckets use timestamps as stored. Timezone, DST elapsed-time effects and operating-time utilization are not established.',
    ]
    rows = result['by_bucket'][:48]
    if len(rows) < result['n_buckets']:
        lines.append(f"- Showing the first 48 of {result['n_buckets']} intervals; use a shorter window for a complete report table.")
    if throughput:
        lines += ['', '| Interval [start, end) | Seconds | Observed events | Observed/hour | Cap-present/hour | Successful/hour |',
                  '|---|---:|---:|---:|---:|---:|']
        for r in rows:
            lines.append(f"| {r['bucket_start']} → {r['bucket_end']} | {r['duration_seconds']:g} | {r['n_observed']:,} | {r['observed_events_per_hour']:.6g} | {r['cap_present_events_per_hour']:.6g} | {r['successful_events_per_hour']:.6g} |")
    else:
        lines += [f"- Cap-present rates marked 'small n' use fewer than {total['min_cap_present_n']} observations.", '',
                  '| Interval [start, end) | Observed | Cap-present | Success / cap | Reject / cap | No Load / all | Unknown cap |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for r in rows:
            small = ' (small n)' if r['below_min_cap_present_n'] else ''
            lines.append(f"| {r['bucket_start']} → {r['bucket_end']} | {r['n_observed']:,} | {r['n_cap_present']:,}{small} | {_pct(r['success_rate_cap_present'], 6)} | {_pct(r['reject_rate_cap_present'], 6)} | {_pct(r['no_load_fraction_all_observed'], 6)} | {r['n_cap_unknown']:,} |")
    return lines


def _finding_kpi_over_time(result, meta):
    return _finding_temporal(result, meta)


def _finding_observed_throughput(result, meta):
    return _finding_temporal(result, meta, throughput=True)


FINDING_TEMPLATES = {
    "kpi_over_time": _finding_kpi_over_time,
    "observed_throughput": _finding_observed_throughput,
    "rank_heads_by_success": _finding_head_ranking,
    "compare_head_success": _finding_head_success_comparison,
    "torque_distribution": _finding_torque_distribution,
    "torque_trend": _finding_torque_trend,
    "detect_torque_anomalies": _finding_torque_anomalies,
    "head_correlation": _finding_head_correlation,
    "torque_stats": _finding_torque_stats,
    "success_rate": _finding_success_rate,
    "success_rate_per_head": _finding_success_rate_per_head,
    "anomaly_heads": _finding_anomaly_heads,
    "idle_periods": _finding_idle_periods,
    "machine_idle": _finding_machine_idle,
    "throughput": _finding_throughput,
    "head_detail": _finding_head_detail,
}


def assemble(query, plan, results, pool_meta, trace, *, min_n=30) -> str:
    """Render the six mandated sections as Markdown."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok_results = [(name, r) for name, r in results if r.get("ok")]
    failed = [(name, r) for name, r in results if not r.get("ok")]

    out: list[str] = [
        f"# Telemetry report",
        "",
        f"*Query:* {query}",
        f"*Generated:* {now}  |  *Planner:* {trace.planner}  |  "
        f"*Schema:* {pool_meta.get('schema_version', '?')}",
    ]
    if getattr(plan, "interpretation", ""):
        out.append(f"*Interpreted as:* {plan.interpretation}")
    out += [
        "",
        "## 1. Goal",
        "",
        plan.goal,
        "",
        "## 2. Data used",
        "",
        (f"- Pool `{pool_meta.get('pool')}` from the **{pool_meta.get('source', '?')}** source, "
         f"{pool_meta['n_raw_readings']:,} raw status readings across "
         f"{len(pool_meta.get('heads', []))} heads."
         if pool_meta.get('n_raw_readings') is not None else
         f"- Pool `{pool_meta.get('pool')}` from the **{pool_meta.get('source', '?')}** "
         f"source, {pool_meta.get('n_events', 0):,} observed exact +1 events "
         f"across {len(pool_meta.get('heads', []))} heads."),
        f"- Window {pool_meta.get('ts_min')} to {pool_meta.get('ts_max')} "
        f"(clock: "
        f"{pool_meta.get('timezone') or 'as recorded; timezone unconfirmed'}).",
        f"- Machines: {', '.join(pool_meta.get('machines', [])) or 'n/a'}.",
    ]
    if pool_meta.get("pool_total_events") is not None and pool_meta.get('n_raw_readings') is None:
        out.append(
            f"- Events loaded for the requested scope: {pool_meta['loaded_events']:,} "
            f"of {pool_meta['pool_total_events']:,} stored observed events in this pool."
        )
    if pool_meta.get("rows_read") is not None:
        out.append(f"- Cleaning: {pool_meta.get('rows_read', 0):,} rows read, "
                   f"{pool_meta.get('rows_after_cleaning', 0):,} retained, "
                   f"{pool_meta.get('duplicates_removed', 0):,} duplicates removed.")
    for warning in pool_meta.get("warnings", []):
        out.append(f"- **Warning:** {warning}")
    if plan.filters:
        out.append(f"- Filters requested: "
                   f"{', '.join(f'`{k}={v}`' for k, v in plan.filters.items())}.")

    if any(name == 'compare_head_success' for name, _ in plan.calls):
        out.append('- For this peer comparison, head_id identifies the focus. Input data intentionally include other available heads in the requested machine/time window.')

    out += ["", "## 3. Analyses executed", ""]
    for name, result in results:
        meta = result.get("meta", {})
        status = "ok" if result.get("ok") else f"FAILED - {result.get('error')}"
        out.append(f"- `{name}` (agent: {meta.get('agent', '?')}) - "
                   f"n={meta.get('n', 0):,}, {meta.get('elapsed_ms', 0)} ms - {status}")
        if meta.get("filters_applied"):
            out.append(f"  - filters: {', '.join(meta['filters_applied'])}")

    out += ["", "## 4. Findings", ""]
    if not ok_results:
        out.append("No analysis produced a result. See section 5.")
    for name, result in ok_results:
        template = FINDING_TEMPLATES.get(name)
        out.append(f"**{name}**")
        focus = getattr(plan, "focus", "") if name in FOCUS_AWARE else ""
        out += ((template(result["result"], result["meta"], focus) if focus
                 else template(result["result"], result["meta"])) if template
                else [f"- {result['result']}"])
        out.append("")

    out += ["## 5. Confidence and limits", ""]
    limits: list[str] = []
    if len(ok_results) > 1:
        limits.append("- These analyses may reuse the same observations. Their sample sizes "
                      "must not be added to claim a count of distinct events.")
    for name, result in ok_results:
        meta = result["meta"]
        if meta.get("notes"):
            limits.append(f"- `{name}`: {meta['notes']}")
        if meta.get("n", 0) < min_n:
            limits.append(f"- `{name}`: n={meta['n']} is below min_n={min_n}.")
    for name, result in failed:
        limits.append(f"- `{name}` produced no result: {result.get('error')}")
    for warning in pool_meta.get("warnings", []):
        limits.append(f"- {warning}")
    if {"compare_head_success", "detect_torque_anomalies"}.issubset(
        {name for name, _ in plan.calls}
    ):
        limits.append(
            "- This head-health check describes observed events and configured "
            "torque flags. The torque check covers closures and excludes No Load "
            "cycles; a few events can still carry the previous cycle's status "
            "(see the status/torque disagreement count). Confirm engineering torque limits, source rows, "
            "and comparable operating conditions before deciding whether a "
            "head has a fault; these results do not support a yes/no fault verdict."
        )
    if not limits:
        limits.append(f"- All analyses met the minimum sample size "
                      f"(min_n={min_n}); sample sizes are reported per tool.")
    rate_tools = {
        "success_rate", "success_rate_per_head", "head_detail", "anomaly_heads"
    }
    if any(name in rate_tools for name, _ in ok_results):
        limits.append(
            "- Every rate identifies its denominator. Unknown cap presence is not treated as confirmed cap absence or presence."
        )
    out += limits

    out += ["", "## 6. Next checks", ""] + _next_checks(ok_results, failed)
    out += ["", "---", "", "## Trace", "",
            f"{trace.to_dict()['n_tool_calls']} tool call(s), "
            f"{trace.elapsed_ms:.0f} ms total.", "", trace.to_markdown(), ""]
    return "\n".join(out)


def _next_checks(ok_results, failed) -> list[str]:
    """Concrete follow-ups implied by what was actually found."""
    checks: list[str] = []
    for name, result in ok_results:
        r = result["result"]
        if name in {"torque_distribution", "torque_trend", "detect_torque_anomalies", "head_correlation"}:
            if result["meta"].get("n", 0) == 0:
                checks.append("- Verify the requested scope and observation coverage before interpreting an empty result.")
            else:
                checks.append("- Review observation coverage and process context before drawing operational conclusions.")
        if name == "torque_stats":
            if r["sample_size"] == 0:
                checks.append(
                    "- Verify data availability and the requested status "
                    "selection before drawing conclusions."
                )
            else:
                checks.append(
                    "- Compare torque statistics with confirmed engineering "
                    "limits before assessing acceptability."
                )
                checks.append(
                    "- Review counter discontinuities separately; this "
                    "report describes observed exact +1 events."
                )
        if name in {"success_rate", "success_rate_per_head", "rank_heads_by_success", "compare_head_success"}:
            checks.append("- Review unknown status/cap-presence counts, sample sizes and observation coverage before comparing rates. These summaries do not establish machine stability or a fault cause.")
        if name in {'kpi_over_time', 'observed_throughput'}:
            checks.append('- Check raw telemetry coverage and the operating schedule before interpreting empty intervals or changes in observed-event rates.')
        if name == 'machine_idle':
            checks.append('- Compare candidate No Load intervals with the machine operating schedule and independent alarm or sensor logs before calling them downtime.')
        if name == "anomaly_heads" and r.get("flagged_heads"):
            head = r["flagged_heads"][0]["head_id"]
            checks.append(f"- Run a torque distribution and drift check on "
                          f"`{head}` to examine its measured behavior.")
            checks.append(f"- Compare `{head}` against its neighbours to see "
                          f"whether the fault is positional.")
        if name == "idle_periods" and r.get("idle_periods"):
            checks.append("- Cross-check the idle window against upstream "
                          "filler/conveyor logs and the operating schedule "
                          "before attributing a cause.")
        if name == "throughput" and r.get("n_inferred_closures"):
            checks.append("- Investigate the dropped polls: counter jumps "
                          "greater than one mean the 1 Hz sampling missed a "
                          "cycle, which slightly understates every rate.")
    for name, _ in failed:
        checks.append(
            f"- Check the reported error and requested filters for `{name}` "
            "before retrying."
        )
    if not checks:
        checks.append("- Review coverage and select a suitable comparison "
                      "window before interpreting an absence of flags.")
    return checks


def save(markdown: str, report_dir, stem="report") -> Path:
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Microseconds keep two reports delivered in the same second apart, and
    # mode "x" refuses to overwrite one if they still collide.
    path = out / f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(markdown)
    return path
