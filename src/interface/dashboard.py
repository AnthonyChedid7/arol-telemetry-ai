"""Key figures for the dashboard cards and the report summary.

Reads the tool envelopes the report already uses; computes nothing new except
formatting (and picking the lowest head out of a returned list). Each figure
is {label, value, note, tone}; tone is ok / warn / bad / neutral.
"""
from __future__ import annotations


def _pct(value, digits=2):
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _num(value, unit="", digits=3):
    return "n/a" if value is None else f"{value:,.{digits}f}{unit}"


def _count(value):
    return "n/a" if value is None else f"{int(value):,}"


def _success_tone(rate):
    if rate is None:
        return "neutral"
    return "ok" if rate >= 0.995 else "warn" if rate >= 0.98 else "bad"


def _failure_tone(rate):
    if rate is None:
        return "neutral"
    return "ok" if rate <= 0.005 else "warn" if rate <= 0.02 else "bad"


def _failure_figures(name, r):
    """Rate tools seen from the failure side, for failure-rate questions."""
    o = r["overall"]
    figures = [dict(label="Failure rate", value=_pct(o.get("reject_rate_cap_present"), 3),
                    tone=_failure_tone(o.get("reject_rate_cap_present")),
                    note=f"{_count(o.get('n_reject_cap_present'))} rejects / {_count(o.get('n_cap_present'))} cap-present"),
               dict(label="Success rate", value=_pct(o.get("success_rate_cap_present"), 3),
                    note="cap-present closures"),
               dict(label="Cap-present closures", value=_count(o.get("n_cap_present")),
                    note=f"No Load {_pct(o.get('no_load_fraction_all_observed'), 1)} of events")]
    heads = [h for h in r.get("per_head") or [] if h.get("reject_rate_cap_present") is not None]
    if heads:
        worst = max(heads, key=lambda h: h["reject_rate_cap_present"])
        figures.append(dict(label="Highest failure head", value=f"{worst['head_id']} {_pct(worst['reject_rate_cap_present'])}",
                            tone=_failure_tone(worst["reject_rate_cap_present"]), note=f"of {len(heads)} heads"))
    return figures


def _daily_figures(r, focus="", extreme=""):
    from ..analytics.registered_daily_kpi import daily_view

    asked, _ = daily_view(focus, extreme)
    key, rate, label = asked["key"], asked["rate"], asked["label"]
    failure = rate.startswith("reject")
    tone = _failure_tone if failure else _success_tone
    days = r.get("days") or []
    ranked = [d for d in days if d.get(f"{key}_head")]
    figures = [dict(label="Days analysed", value=_count(len(days)),
                    note=f"{sum(d['partial_day'] for d in days)} partial")]
    if not ranked:
        return figures
    # A day where every eligible head ties says nothing about any one head.
    telling = [d for d in ranked if d[f"n_tied_{key}"] < d["n_eligible_heads"]]
    counts = {}
    for day in telling:
        for head in day[f"{key}_tied_heads"]:
            counts[head] = counts.get(head, 0) + 1
    most = max(counts.values(), default=0)
    leaders = sorted(head for head, n in counts.items() if n == most)
    if extreme == "best":
        values = sorted(d[f"{key}_head"][rate] for d in ranked)
        shared = sorted(len(d[f"{key}_tied_heads"]) for d in ranked)
        span = _pct(values[0], 3) if values[0] == values[-1] else f"{_pct(values[0], 3)} - {_pct(values[-1], 3)}"
        figures.append(dict(label=f"{label} each day", value=span,
                            note=(f"shared by {shared[0]} heads" if shared[0] == shared[-1]
                                  else f"shared by {shared[0]}-{shared[-1]} heads"),
                            tone=tone(values[-1] if failure else values[0])))
    else:
        open_days = [d for d in ranked if d[f"n_tied_{key}"] < d["n_eligible_heads"]] or ranked
        pick = (max if failure else min)(open_days, key=lambda d: d[f"{key}_head"][rate])
        figures.append(dict(label=label, value=f"{pick[f'{key}_head']['head_id']} {_pct(pick[f'{key}_head'][rate], 3)}",
                            note=f"on {pick['date']}", tone=tone(pick[f"{key}_head"][rate])))
    if not telling or most < 2:  # no head repeats: nothing to report
        return figures
    figures.append(dict(label=f"Most often {label.lower()}",
                        value=leaders[0] if len(leaders) == 1 else f"{len(leaders)} heads",
                        note=f"{most} of {len(telling)} days" + ("" if len(leaders) == 1 else
                                                               f" ({', '.join(leaders[:3])}...)" if len(leaders) > 3
                                                               else f" ({', '.join(leaders)})"),
                        tone="warn" if extreme != "best" and most > 1 else "neutral"))
    return figures


def _figures_for(name, result, focus="", extreme=""):
    r = result
    if name == "head_success_by_day":
        return _daily_figures(r, focus, extreme)
    if focus == "failure" and name in ("success_rate", "success_rate_per_head"):
        return _failure_figures(name, r)
    if name == "torque_stats":
        return [dict(label="Mean torque", value=_num(r["mean"], " Nm"), note=f"n = {_count(r['sample_size'])}"),
                dict(label="Spread (std)", value=_num(r["std"], " Nm", 4), note="sample standard deviation"),
                dict(label="Range", value=f"{_num(r['min'], '', 2)} - {_num(r['max'], ' Nm', 2)}", note="min - max")]
    if name == "torque_distribution":
        return [dict(label="Observations", value=_count(r["sample_size"]), note=f"{len(r['counts'])} bins"),
                dict(label="Torque range", value=f"{_num(r['bin_edges'][0], '', 2)} - {_num(r['bin_edges'][-1], ' Nm', 2)}"
                     if r["bin_edges"] else "n/a", note="histogram edges")]
    if name == "torque_trend":
        detected = r.get("drift_detected")
        return [dict(label="Drift", value=(f"Detected ({r['drift_direction']})" if detected else "None"),
                     note=f"threshold {r.get('drift_threshold_nm_per_hour', 'n/a')} Nm/h",
                     tone="warn" if detected else "ok"),
                dict(label="Slope", value=_num(r.get("drift_slope_nm_per_hour"), " Nm/h", 4),
                     note="latest window of the moving average"),
                dict(label="Observations", value=_count(r["sample_size"]), note=f"{r['window_seconds']} s window")]
    if name == "detect_torque_anomalies":
        rate = r.get("anomaly_rate")
        return [dict(label="Flagged readings", value=_count(r["anomaly_count"]),
                     note=f"of {_count(r['sample_size'])} ({_pct(rate, 3)})",
                     tone="ok" if not r["anomaly_count"] else "warn"),
                dict(label="Limits", value=f"{r['expected_min']} - {r['expected_max']} Nm", note=f"and {r['sigma']} sigma"),
                dict(label="Mean torque", value=_num(r.get("mean"), " Nm"), note="checked population")]
    if name == "head_correlation":
        a, b = r["head_a"], r["head_b"]
        return [dict(label="Torque correlation", value=_num(r["torque_correlation"], "", 3),
                     note=str(r["torque_correlation_interpretation"]).replace("_", " ")),
                dict(label=f"{a['head_id']} success", value=_pct(a.get("success_rate")), tone=_success_tone(a.get("success_rate"))),
                dict(label=f"{b['head_id']} success", value=_pct(b.get("success_rate")), tone=_success_tone(b.get("success_rate"))),
                dict(label="Matched pairs", value=_count(r["matched_torque_samples"]), note="same-second closures")]
    if name == "compare_head_success":
        focus, diff = r.get("focus") or {}, r.get("difference_from_peer_median_pp")
        tone = "neutral" if diff is None else "ok" if diff >= -0.5 else "warn" if diff >= -2 else "bad"
        return [dict(label=f"{r.get('focus_head_id')} success", value=_pct(focus.get("success_rate_cap_present")),
                     note=f"{_count(focus.get('n_success_cap_present'))} / {_count(focus.get('n_cap_present'))}", tone=tone),
                dict(label="Peer median", value=_pct(r.get("peer_median_success_rate")),
                     note=f"{r.get('n_eligible_peers')} other heads"),
                dict(label="Difference", value="n/a" if diff is None else f"{diff:+.2f} pp", tone=tone,
                     note="focus minus peer median")]
    if name == "rank_heads_by_success":
        ranked = r.get("ranked_heads") or []
        if not ranked:
            return [dict(label="Heads ranked", value="0")]
        worst, best = ranked[0], ranked[-1]
        return [dict(label="Lowest head", value=f"{worst['head_id']} {_pct(worst.get('success_rate_cap_present'))}",
                     tone=_success_tone(worst.get("success_rate_cap_present")), note="lowest success first"),
                dict(label="Highest head", value=f"{best['head_id']} {_pct(best.get('success_rate_cap_present'))}"),
                dict(label="Heads ranked", value=_count(r.get("n_eligible_heads")), note=f"of {r.get('n_head_groups')}")]
    if name in ("success_rate", "success_rate_per_head"):
        o = r["overall"]
        figures = [dict(label="Success rate", value=_pct(o.get("success_rate_cap_present"), 3),
                        tone=_success_tone(o.get("success_rate_cap_present")), note="cap-present closures"),
                   dict(label="Reject rate", value=_pct(o.get("reject_rate_cap_present"), 3),
                        note=f"{_count(o.get('n_reject_cap_present'))} rejects"),
                   dict(label="Cap-present closures", value=_count(o.get("n_cap_present")),
                        note=f"No Load {_pct(o.get('no_load_fraction_all_observed'), 1)} of events")]
        heads = [h for h in r.get("per_head") or [] if h.get("success_rate_cap_present") is not None]
        if heads:
            worst = min(heads, key=lambda h: h["success_rate_cap_present"])
            figures.append(dict(label="Lowest head", value=f"{worst['head_id']} {_pct(worst['success_rate_cap_present'])}",
                                tone=_success_tone(worst["success_rate_cap_present"]), note=f"of {len(heads)} heads"))
        return figures
    if name in ("kpi_over_time", "observed_throughput"):
        o = r["overall"]
        return [dict(label="Events per hour", value=_num(o.get("observed_events_per_hour"), "", 0),
                     note="observed, whole window"),
                dict(label="Success rate", value=_pct(o.get("success_rate_cap_present"), 3),
                     tone=_success_tone(o.get("success_rate_cap_present"))),
                dict(label="Buckets", value=_count(r.get("n_buckets")), note=f"per {r.get('bucket')}")]
    if name == "machine_idle":
        periods = r.get("idle_periods") or []
        longest = max((p["duration_seconds"] for p in periods), default=0)
        return [dict(label="Idle periods", value=_count(r.get("n_periods")),
                     tone="warn" if periods else "ok", note=f"at least {r.get('threshold_seconds')} s"),
                dict(label="Total idle", value=f"{(r.get('total_idle_seconds') or 0) / 60:,.1f} min"),
                dict(label="Longest", value=f"{longest / 60:,.1f} min",
                     note=periods[0]["start"][11:16] + " first" if periods else "")]
    return []


def key_figures(results, limit=6, focus="", extreme=""):
    """results: [(tool name, envelope)] as returned by the orchestrator.

    focus="failure" leads the rate tools with their failure (rejection) rate.
    """
    figures, seen = [], set()
    for name, envelope in results:
        if not envelope.get("ok") or name in seen:
            continue
        seen.add(name)
        for figure in _figures_for(name, envelope.get("result") or {}, focus, extreme):
            figure.setdefault("note", "")
            figure.setdefault("tone", "neutral")
            figures.append(figure)
    return figures[:limit]
