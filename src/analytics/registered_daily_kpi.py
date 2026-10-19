"""Per-day head success: each calendar day is its own scoped query.

A whole real AROL day holds more events than the row-level head ranking may
load, so asking "which head was worst on each day" cannot be one window. Here
every day in the requested scope is aggregated separately (one lazy Polars
query per day, so memory stays bounded however many days there are), then
ranked with the same exact-ratio rules as rank_heads_by_success.
"""
from datetime import datetime, time, timedelta
from fractions import Fraction

import polars as pl

from .registered_head_kpi import rank_counts
from .registered_kpi import DEFINITIONS, _aggregations, _prepare, finish_counts
from ..common.envelope import envelope
from ..common.registry import tool
from ..common.runtime import current_config

PARAMS = ['machine_id', 'start', 'end']
HEAD_FIELDS = ('head_id', 'n_cap_present', 'n_success_cap_present', 'n_reject_cap_present',
               'success_rate_cap_present', 'reject_rate_cap_present',
               'eligible_for_ranking', 'rank_lowest_success_first')


def _head(row):
    return {key: row[key] for key in HEAD_FIELDS}


# The four per-day picks: (result key, count field, exact rate, pick the maximum?).
PICKS = (
    ('lowest_success', 'n_success_cap_present', False),
    ('highest_success', 'n_success_cap_present', True),
    ('lowest_failure', 'n_reject_cap_present', False),
    ('highest_failure', 'n_reject_cap_present', True),
)


def _pick(key, rate, count, label):
    return dict(key=key, rate=rate, count=count, label=label)


_SUCCESS = ('success_rate_cap_present', 'n_success_cap_present')
_FAILURE = ('reject_rate_cap_present', 'n_reject_cap_present')
# (focus, extreme) -> the pick the question asked for, then its counterpart.
VIEWS = {
    ('', 'worst'): (_pick('lowest_success', *_SUCCESS, 'Lowest success'),
                    _pick('highest_failure', *_FAILURE, 'Highest failure')),
    ('', 'best'): (_pick('highest_success', *_SUCCESS, 'Highest success'),
                   _pick('lowest_failure', *_FAILURE, 'Lowest failure')),
    ('failure', 'worst'): (_pick('highest_failure', *_FAILURE, 'Highest failure'),
                           _pick('lowest_success', *_SUCCESS, 'Lowest success')),
    ('failure', 'best'): (_pick('lowest_failure', *_FAILURE, 'Lowest failure'),
                          _pick('highest_success', *_SUCCESS, 'Highest success')),
}


def daily_view(focus='', extreme=''):
    """(asked pick, counterpart) for presenting head_success_by_day."""
    return VIEWS[('failure' if focus == 'failure' else '', 'best' if extreme == 'best' else 'worst')]


def _extreme(eligible, count, highest):
    """The head with the lowest/highest exact rate and every head sharing that rate.

    Exact ratios, like the success ranking; the reported head is the lowest
    head id among the tied ones.
    """
    rate = {r['head_id']: Fraction(r[count], r['n_cap_present']) for r in eligible}
    target = (max if highest else min)(rate.values())
    tied = sorted(head for head, value in rate.items() if value == target)
    return next(r for r in eligible if r['head_id'] == tied[0]), tied


def _run(events, *, machine_id=None, start=None, end=None):
    frame, applied, min_n = _prepare(events, start=start, end=end, machine_id=machine_id)
    machines = frame.select(pl.col('machine_id').unique()).collect().to_series().to_list()
    if len(machines) > 1:
        raise ValueError('Per-day head results need one machine; add machine_id '
                         f"(found {', '.join(sorted(machines))})")
    days = sorted(frame.select(pl.col('ts').dt.date().unique()).collect().to_series().to_list())
    limit = current_config().get('analytics', {}).get('max_time_buckets', 1000)
    if type(limit) is not int or limit < 1:
        raise ValueError('analytics.max_time_buckets must be a positive integer')
    if len(days) > limit:
        raise ValueError(f'The scope covers {len(days)} days; the limit is {limit}. Select a shorter period.')
    out, n_cap_total = [], 0
    for day in days:
        lo = datetime.combine(day, time())
        hi = lo + timedelta(days=1)
        groups = (frame.filter((pl.col('ts') >= lo) & (pl.col('ts') < hi))
                  .group_by('machine_id', 'head_id')
                  .agg(*_aggregations(), pl.col('ts').min().alias('first'), pl.col('ts').max().alias('last'))
                  .sort(['machine_id', 'head_id']).collect())
        rows = groups.to_dicts()
        first = min(row.pop('first') for row in rows)
        last = max(row.pop('last') for row in rows)
        ranked = rank_counts([finish_counts(row, min_n) for row in rows], min_n)
        eligible = [r for r in ranked if r['eligible_for_ranking']]
        n_cap = sum(r['n_cap_present'] for r in ranked)
        n_cap_total += n_cap
        covered = (last - first).total_seconds() / 3600
        picks = {}
        for key, count, highest in PICKS:
            head, tied = _extreme(eligible, count, highest) if len(eligible) >= 2 else (None, [])
            # How many eligible heads share that value (all of them = no difference that day).
            picks.update({f'{key}_head': _head(head) if head else None,
                          f'n_tied_{key}': len(tied), f'{key}_tied_heads': tied})
        n_success = sum(r['n_success_cap_present'] for r in ranked)
        n_reject = sum(r['n_reject_cap_present'] for r in ranked)
        out.append(dict(
            date=day.isoformat(), start=lo.isoformat(), end=hi.isoformat(),
            first_event=first.isoformat(), last_event=last.isoformat(),
            covered_hours=round(covered, 2), partial_day=covered < 23,
            n_observed=sum(r['n_observed'] for r in ranked), n_cap_present=n_cap,
            # The whole machine that day (all heads together), same definitions.
            n_success_cap_present=n_success, n_reject_cap_present=n_reject,
            success_rate_cap_present=n_success / n_cap if n_cap else None,
            reject_rate_cap_present=n_reject / n_cap if n_cap else None,
            n_heads=len(ranked), n_eligible_heads=len(eligible),
            ranking_available=len(eligible) >= 2,
            **picks,
            heads=[_head(r) for r in sorted(ranked, key=lambda r: r['head_id'])],
        ))
    result = dict(
        machine_id=machines[0] if machines else machine_id, days=out, n_days=len(out),
        metric='success_rate_cap_present', min_cap_present_n=min_n,
        day_definition='calendar days of the recorded clock (UTC as recorded), 00:00 to 24:00',
        method='one scoped aggregation per day; ranking per day by exact cap-present ratios',
        rate_definitions={k: DEFINITIONS[k] for k in ('success_rate_cap_present', 'reject_rate_cap_present')},
    )
    window = dict(ts_min=out[0]['first_event'] if out else None,
                  ts_max=out[-1]['last_event'] if out else None)
    notes = ('Each day is aggregated separately; days the data only partly covers are marked partial. '
             f'Heads with fewer than {min_n} cap-present observations on a day are not ranked that day. '
             'Descriptive ordering only; no statistical significance or fault cause is claimed.')
    return envelope(result, n=n_cap_total, filters_applied=applied, window=window,
                    units={'success_rate_cap_present': 'fraction', 'reject_rate_cap_present': 'fraction'},
                    notes=notes)


@tool(name='head_success_by_day',
      description=('For EACH calendar day in the selected scope (the whole data if no start/end): every '
                   "head's cap-present success and failure (rejection/error) rate, and that day's lowest- and "
                   'highest-success and lowest- and highest-failure heads with ties. Use for "worst/best head '
                   'each day", "lowest success rate per head for each day", "lowest error rate each day", '
                   '"daily head ranking". One machine; days are computed one at a time.'),
      params=PARAMS, agent='kpi', owner='integration')
def head_success_by_day(events, *, machine_id=None, start=None, end=None):
    return _run(events, machine_id=machine_id, start=start, end=end)
