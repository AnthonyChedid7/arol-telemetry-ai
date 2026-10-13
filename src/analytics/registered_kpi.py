"""Explicit-denominator KPIs on supplied observed exact +1 events.

Extends Person B's cap-present proposal; A's analytics stay unchanged.
Reject numerators use the same population as their denominators.
"""
import polars as pl

from .event_filters import filter_events
from ..common.registry import tool
from ..common.envelope import envelope, data_window
from ..common.runtime import current_config

SCOPE = ['start', 'end', 'head_id', 'machine_id']
DEFINITIONS = {
    'success_rate_cap_present': 'status 0 AND cap_present=True / cap_present=True',
    'reject_rate_cap_present': 'reject_signal=True AND cap_present=True / cap_present=True',
    'success_fraction_all_observed': 'status 0 / all selected observed exact +1 events',
    'no_load_fraction_all_observed': "error_class='No Load' / all selected observed exact +1 events",
    'legacy_a_success_rate_non_no_load': "status 0 among error_class != 'No Load' / error_class != 'No Load'; null classes excluded",
}


def _sum(expr, name):
    return expr.fill_null(False).cast(pl.Int64).sum().alias(name)


def _aggregations():
    cap = pl.col('cap_present') == True
    success = pl.col('status') == 0
    reject = pl.col('reject_signal') == True
    a = pl.col('error_class') != 'No Load'
    return [
        pl.len().cast(pl.Int64).alias('n_observed'),
        _sum(cap, 'n_cap_present'),
        _sum(pl.col('cap_present') == False, 'n_cap_absent'),
        _sum(pl.col('cap_present').is_null(), 'n_cap_unknown'),
        _sum(success, 'n_success_all'),
        _sum(success & cap, 'n_success_cap_present'),
        _sum(success & ~cap.fill_null(False), 'n_success_outside_cap_present'),
        _sum(reject, 'n_reject_all'),
        _sum(reject & cap, 'n_reject_cap_present'),
        _sum(reject & ~cap.fill_null(False), 'n_reject_outside_cap_present'),
        _sum(pl.col('reject_signal').is_null(), 'n_reject_unknown'),
        _sum(pl.col('error_class') == 'No Load', 'n_no_load_class'),
        _sum(a, 'n_legacy_a_non_no_load'),
        _sum(success & a, 'n_legacy_a_success'),
    ]


def finish_counts(counts, min_n):
    """Rates remain null for an empty denominator; counts always remain visible."""
    out = dict(counts)
    for key, value in out.items():
        if key.startswith('n_'):
            out[key] = int(value)
    if out['n_success_outside_cap_present']:
        raise ValueError('Inconsistent events: status 0 must have cap_present=True')
    for key, numerator, denominator in [
        ('success_rate_cap_present', 'n_success_cap_present', 'n_cap_present'),
        ('reject_rate_cap_present', 'n_reject_cap_present', 'n_cap_present'),
        ('success_fraction_all_observed', 'n_success_all', 'n_observed'),
        ('no_load_fraction_all_observed', 'n_no_load_class', 'n_observed'),
        ('legacy_a_success_rate_non_no_load', 'n_legacy_a_success', 'n_legacy_a_non_no_load'),
    ]:
        out[key] = out[numerator] / out[denominator] if out[denominator] else None
    out['min_cap_present_n'] = min_n
    out['below_min_cap_present_n'] = out['n_cap_present'] < min_n
    return out


def _prepare(events, *, start=None, end=None, head_id=None, machine_id=None):
    scoped, applied = filter_events(events, start=start, end=end, head_id=head_id, machine_id=machine_id)
    schema = scoped.collect_schema()
    required = {'ts', 'machine_id', 'head_id', 'status', 'error_class', 'cap_present', 'reject_signal'}
    if missing := required - set(schema.names()):
        raise ValueError(f'Missing required KPI columns: {sorted(missing)}')
    if (not schema['status'].is_integer() or schema['cap_present'] != pl.Boolean
            or schema['reject_signal'] != pl.Boolean or schema['error_class'] != pl.String
            or schema['machine_id'] != pl.String or schema['head_id'] != pl.String
            or schema['ts'].base_type() != pl.Datetime or schema['ts'].time_zone is not None):
        raise ValueError('KPI inputs require integer status, nullable Boolean flags, string identifiers/classes and naive Datetime ts')
    # The checks and aggregations run lazily, so a whole-pool scope is never
    # materialized (brief: scalability); eager inputs give identical results.
    frame = scoped.lazy()
    checks = frame.select(
        pl.any_horizontal(*(pl.col(c).is_null() for c in ('status', 'machine_id', 'head_id', 'ts')))
        .sum().alias('nulls'),
        ((pl.col('machine_id').str.strip_chars() == '') | (pl.col('head_id').str.strip_chars() == ''))
        .sum().alias('blank'),
    ).collect().row(0, named=True)
    if checks['nulls']:
        raise ValueError('KPI events require non-null status, identifiers and timestamps')
    if checks['blank']:
        raise ValueError('KPI identifiers must not be empty')
    min_n = current_config().get('analytics', {}).get('min_n', 30)
    if type(min_n) is not int or min_n < 1:
        raise ValueError('analytics.min_n must be a positive integer')
    return frame, applied, min_n


def _calculate(events, *, per_head, start=None, end=None, head_id=None, machine_id=None):
    frame, applied, min_n = _prepare(events, start=start, end=end, head_id=head_id, machine_id=machine_id)
    overall = finish_counts(frame.select(_aggregations()).collect().row(0, named=True), min_n)
    result = {'overall': overall, 'rate_definitions': dict(DEFINITIONS)}
    if per_head:
        groups = (frame.group_by('machine_id', 'head_id').agg(_aggregations())
                  .sort(['machine_id', 'head_id']).collect())
        result['per_head'] = [finish_counts(row, min_n) for row in groups.to_dicts()]
        result['n_head_groups'] = len(result['per_head'])
        result['n_groups_below_min_cap_present_n'] = sum(r['below_min_cap_present_n'] for r in result['per_head'])
    notes = ('Rates describe selected observed exact +1 events, not total production. '
             'Cap-present rates exclude unknown cap presence; unknowns and rejects outside this population are reported separately. '
             'meta.n is the confirmed cap-present denominator. Torque finiteness does not filter KPI events. '
             'The legacy A non-No-Load fraction is separately labelled; A calculations are unchanged. '
             'These descriptive rates do not establish fault causes or engineering compliance.')
    if per_head:
        notes += f" {result['n_groups_below_min_cap_present_n']} machine/head groups have fewer than {min_n} cap-present observations; no head ranking or statistical significance claim is made."
    return envelope(result, n=overall['n_cap_present'], filters_applied=applied,
                    window=data_window(frame.select('ts').collect()),
                    units={name: 'fraction' for name in DEFINITIONS}, notes=notes)


@tool(name='success_rate', description='Observed-event KPI counts and explicitly labelled cap-present success and rejection fractions. Unknown cap presence is reported separately.', params=SCOPE, agent='kpi', owner='integration')
def success_rate(events, *, start=None, end=None, head_id=None, machine_id=None):
    return _calculate(events, per_head=False, start=start, end=end, head_id=head_id, machine_id=machine_id)


@tool(name='success_rate_per_head', description='The same explicit-denominator observed-event KPIs grouped by machine and head, without ranking or causal claims.', params=SCOPE, agent='kpi', owner='integration')
def success_rate_per_head(events, *, start=None, end=None, head_id=None, machine_id=None):
    return _calculate(events, per_head=True, start=start, end=end, head_id=head_id, machine_id=machine_id)
