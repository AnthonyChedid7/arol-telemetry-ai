"""Per-day head results: each calendar day is analysed on its own, then ranked."""
from datetime import datetime, timedelta
import json

import polars as pl
import pytest

from src.agent.orchestrator import Orchestrator
from src.agent.planner import RulePlanner
from src.common.runtime import dispatch_tool
from src.ingestion.event_pool import build_event_pool
from src.ingestion.event_table_polars import build_event_table

CFG = {'analytics': {'min_n': 30}, 'agent': {'planner': 'rules'}}
START = datetime(2026, 2, 1, 12)  # data starts at noon: the first day is partial


def _raw():
    """10-minute rows from Feb 1 12:00 to Feb 3 12:00; one event per head per row.

    H01 rejects 10 times on Feb 1, H02 rejects 5 times on Feb 2 and once on
    Feb 1, nobody rejects on Feb 3.
    """
    times = [START + timedelta(minutes=10 * i) for i in range(289)]
    columns = {'timestamp': times}
    for head in ('H01', 'H02', 'H03'):
        status = []
        for i, ts in enumerate(times):
            reject = ((head == 'H01' and ts.day == 1 and i % 7 == 0 and i < 70)
                      or (head == 'H02' and ts.day == 2 and i % 20 == 0 and i < 200)
                      or (head == 'H02' and i == 3))
            status.append(65 if reject else 0)
        columns[f'{head} Count'] = list(range(1, len(times) + 1))
        columns[f'{head} AppTorque'] = [2.0] * len(times)
        columns[f'{head} Status'] = status
    return pl.DataFrame(columns)


@pytest.fixture
def events():
    return build_event_table(_raw(), 'M1')


@pytest.fixture
def pool(tmp_path):
    frame = _raw()
    paths = []
    for index, part in enumerate((frame[:100], frame[100:200], frame[200:])):
        path = tmp_path / f'telemetry_M1_2026-02-0{index + 1}.csv'
        part.write_csv(path)
        paths.append(path)
    return build_event_pool(paths, tmp_path / 'pool', machine_id='M1')


def test_each_day_matches_a_one_day_ranking(events):
    reply = dispatch_tool('head_success_by_day', events, config=CFG, arguments={})
    assert reply['ok'], reply['error']
    days = reply['result']['days']
    assert [d['date'] for d in days] == ['2026-02-01', '2026-02-02', '2026-02-03']
    for day in days:
        # An independent one-window ranking of the same day agrees.
        single = dispatch_tool('rank_heads_by_success', events, config=CFG, arguments={
            'machine_id': 'M1', 'start': day['start'], 'end': day['end']})['result']['ranked_heads']
        by_head = {r['head_id']: r for r in single}
        for head in day['heads']:
            assert head['n_cap_present'] == by_head[head['head_id']]['n_cap_present']
            assert head['rank_lowest_success_first'] == by_head[head['head_id']]['rank_lowest_success_first']
    first, second, third = days
    assert first['lowest_success_head']['head_id'] == 'H01' and first['partial_day']
    assert first['highest_failure_head']['head_id'] == 'H01'
    assert second['lowest_success_head']['head_id'] == 'H02' and not second['partial_day']
    assert third['n_tied_lowest_success'] == third['n_eligible_heads'] == 3  # nobody rejected
    assert reply['meta']['n'] == sum(d['n_cap_present'] for d in days)
    json.dumps(reply, allow_nan=False)


def test_scope_and_one_machine(events):
    reply = dispatch_tool('head_success_by_day', events, config=CFG, arguments={
        'start': '2026-02-02T00:00:00', 'end': '2026-02-03T00:00:00'})
    assert [d['date'] for d in reply['result']['days']] == ['2026-02-02']
    two = pl.concat([events, events.with_columns(pl.lit('M2').alias('machine_id'))])
    refused = dispatch_tool('head_success_by_day', two, config=CFG, arguments={})
    assert not refused['ok'] and 'one machine' in refused['error']
    limited = dispatch_tool('head_success_by_day', events,
                            config={**CFG, 'analytics': {'min_n': 30, 'max_time_buckets': 2}}, arguments={})
    assert not limited['ok'] and 'limit is 2' in limited['error']


@pytest.mark.parametrize('question, focus', [
    ('which head has the lowest success rate for each day', ''),
    ('show the worst head per day', ''),
    ('daily head ranking by success', ''),
    ('highest failure rate per head for each day', 'failure'),
    ('which heads rejected the most every day?', 'failure'),
])
def test_per_day_wording_routes_to_the_daily_tool(question, focus):
    plan = RulePlanner().plan(question, {})
    assert plan.calls == [('head_success_by_day', {})] and plan.focus == focus


def test_per_day_wording_keeps_scope_and_leaves_other_questions_alone():
    plan = RulePlanner().plan('lowest success rate per head for each day for machine M1 '
                              'from 2026-02-01T00:00:00 until 2026-02-03T00:00:00', {})
    assert plan.calls == [('head_success_by_day', {'machine_id': 'M1', 'start': '2026-02-01T00:00:00',
                                                   'end': '2026-02-03T00:00:00'})]
    assert RulePlanner().plan('lowest success per head each day from tomorrow until later', {}).ambiguous
    for other in ('success rate for head 5 each day', 'success rate per head',
                  'worst head per hour each day'):
        assert all(name != 'head_success_by_day' for name, _ in RulePlanner().plan(other, {}).calls)


def test_whole_pool_runs_day_by_day_without_loading_events(pool, tmp_path):
    from src.interface import plots
    from src.interface.dashboard import key_figures
    # A limit below the pool size: row-level tools would refuse this scope.
    cfg = {**CFG, 'data': {'source': 'person_a_pool', 'person_a': {'p': str(pool)},
                           'max_loaded_events': 100}}
    answer = Orchestrator(cfg=cfg).answer('which head has the lowest success rate for each day')
    assert answer['status'] == 'ok', answer.get('message')
    assert answer['pool_meta']['materialization'].startswith('aggregated lazily')
    findings = answer['markdown'].split('## 4. Findings', 1)[1].split('## 5.', 1)[0]
    # The pool's first reading is the counter baseline, so the first event is 12:10.
    assert '| 2026-02-01 | 12:10-23:50 (partial) | 213 | H01 | **87.3239%** |' in findings
    assert '| 2026-02-02 | 00:00-23:50 | 432 | H02 | **95.8333%** |' in findings
    assert '| 2026-02-03 | 00:00-12:00 (partial) | 219 | all 3 heads tied |' in findings
    assert 'Lowest success head | Success rate | Highest failure head' in findings
    figures = key_figures(answer['results'])
    assert figures[0] == dict(label='Days analysed', value='3', note='2 partial', tone='neutral')
    assert [p.name for _, p in plots.render(answer['results'], tmp_path / 'a')] == ['head_success_by_day.png']
    failure = Orchestrator(cfg=cfg).answer('highest failure rate per head for each day')
    assert 'Highest failure head | Failure rate | Lowest success head' in failure['markdown']
    assert [p.name for _, p in plots.render(failure['results'], tmp_path / 'b', focus='failure')] == [
        'head_failure_by_day.png']
