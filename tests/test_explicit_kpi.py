"""KPI denominator, scope and reporting checks against independent expectations."""
from datetime import datetime, timedelta
import json
import math

import polars as pl
import pytest

from src.analytics import registered_kpi
from src.agent.planner import RulePlanner, LLMPlanner, Plan
from src.agent.orchestrator import Orchestrator
from src.common.runtime import dispatch_tool
from src.common import datasource
from src.ingestion.event_table_polars import build_event_table, write_event_table_parquet
from src.ingestion.event_pool import build_event_pool

CFG = {'analytics': {'min_n': 30}, 'agent': {'planner': 'rules'}}


@pytest.fixture
def raw():
    return pl.DataFrame({
        'timestamp': [datetime(2026, 2, 1) + timedelta(seconds=i) for i in range(9)],
        'H05 Count': list(range(9)), 'H06 Count': list(range(9)),
        'H05 AppTorque': [0., 1., float('nan'), float('inf'), 2., 2., 2., 2., 2.],
        'H06 AppTorque': [2.] * 9,
        'H05 Status': [0, 0, 2, 3, 4, 9, 65, 999, 64],
        'H06 Status': [0] * 9,
    })


@pytest.fixture
def events(raw):
    return build_event_table(raw, 'M1')


@pytest.mark.parametrize('tool', ['success_rate', 'success_rate_per_head'])
def test_explicit_denominators_unknowns_and_nonfinite_torque(events, tool):
    reply = dispatch_tool(tool, events, config=CFG, arguments={'head_id': 'H05'})
    assert reply['ok'], reply['error']
    o = reply['result']['overall']
    assert o['n_observed'] == 8
    assert o['n_cap_present'] == 2
    assert o['n_cap_absent'] == 1
    assert o['n_cap_unknown'] == 5
    assert o['n_no_load_class'] == 2  # code 3 is No Load but cap presence is unknown
    assert o['n_reject_all'] == 3
    assert o['n_reject_cap_present'] == 1
    assert o['n_reject_outside_cap_present'] == 2
    assert o['n_reject_unknown'] == 1  # code 999
    assert o['success_rate_cap_present'] == 0.5
    assert o['reject_rate_cap_present'] == 0.5
    assert o['success_fraction_all_observed'] == 1/8
    assert o['no_load_fraction_all_observed'] == 2/8
    assert o['legacy_a_success_rate_non_no_load'] == 1/6
    assert reply['meta']['n'] == 2
    assert 'torque is non-null and finite' not in reply['meta']['filters_applied']
    json.dumps(reply, allow_nan=False)


@pytest.mark.parametrize('tool', ['success_rate', 'success_rate_per_head'])
def test_empty_scope_is_not_broadened(events, tool):
    reply = dispatch_tool(tool, events, config=CFG, arguments={'head_id': 'H99'})
    assert reply['ok']
    o = reply['result']['overall']
    assert o['n_observed'] == 0 and reply['meta']['n'] == 0
    assert all(o[name] is None for name in registered_kpi.DEFINITIONS)
    if tool.endswith('per_head'):
        assert reply['result']['per_head'] == []


def test_no_cap_population_keeps_observations_and_null_cap_rates(events):
    frame = events.filter(pl.col('status').is_in([2, 3, 4, 9, 999, 64]))
    reply = dispatch_tool('success_rate', frame, config=CFG)
    assert reply['ok']
    o = reply['result']['overall']
    assert o['n_observed'] == 6 and o['n_cap_present'] == 0
    assert o['success_rate_cap_present'] is None and o['reject_rate_cap_present'] is None
    assert o['success_fraction_all_observed'] == 0


def test_per_head_keeps_machines_separate(events):
    both = pl.concat([events, events.with_columns(pl.lit('M2').alias('machine_id'))])
    reply = dispatch_tool('success_rate_per_head', both, config=CFG)
    assert reply['ok']
    result = reply['result']
    assert result['n_head_groups'] == 4
    assert [(r['machine_id'], r['head_id']) for r in result['per_head']] == [('M1','H05'),('M1','H06'),('M2','H05'),('M2','H06')]
    assert result['n_groups_below_min_cap_present_n'] == 4
    assert result['overall']['n_observed'] == 32


@pytest.mark.parametrize('missing', ['status', 'cap_present', 'reject_signal', 'error_class'])
def test_missing_columns_fail(events, missing):
    reply = dispatch_tool('success_rate', events.drop(missing), config=CFG)
    assert not reply['ok'] and 'Missing required KPI columns' in reply['error']


@pytest.mark.parametrize('column', ['status', 'ts', 'head_id', 'machine_id'])
def test_null_required_values_fail(events, column):
    broken = events.with_columns(pl.lit(None).cast(events.schema[column]).alias(column))
    reply = dispatch_tool('success_rate', broken, config=CFG)
    assert not reply['ok'] and 'non-null' in reply['error']


def test_inconsistent_success_cap_presence_fails(events):
    broken = events.with_columns(pl.when(pl.col('status') == 0).then(None).otherwise(pl.col('cap_present')).alias('cap_present'))
    reply = dispatch_tool('success_rate', broken, config=CFG)
    assert not reply['ok'] and 'status 0 must have' in reply['error']


@pytest.mark.parametrize('min_n', [0, -1, True, '30'])
def test_minimum_sample_threshold_must_be_trusted_positive_integer(events, min_n):
    reply = dispatch_tool('success_rate', events, config={'analytics': {'min_n': min_n}})
    assert not reply['ok'] and 'positive integer' in reply['error']


@pytest.mark.parametrize('args', [{'status_filter':'successful'}, {'cap_present_only':True}, {'bucket':'hour'}, {'min_n':1}])
def test_kpi_dispatch_rejects_denominator_filters_and_unintegrated_options(events, args):
    reply = dispatch_tool('success_rate', events, config=CFG, arguments=args)
    assert not reply['ok'] and 'does not accept' in reply['error']


@pytest.mark.parametrize('base', ['success rate', 'Show success rate per head', 'reject rate', 'No Load rate', 'capping KPIs'])
def test_kpi_question_preserves_complete_scope(base):
    plan = RulePlanner().plan(base + ' for head 5 for machine MiXeD_01 from 2026-02-01T00:00:00 until 2026-02-01T12:00:00', {})
    assert not plan.ambiguous
    args = {'head_id':'H05', 'machine_id':'MiXeD_01', 'start':'2026-02-01T00:00:00', 'end':'2026-02-01T12:00:00'}
    assert all(params == args for _,params in plan.calls)
    assert [n for n,_ in plan.calls] == (['success_rate_per_head'] if 'per head' in base else ['success_rate','success_rate_per_head'])


@pytest.mark.parametrize('question', [
    'show me the failure rate for each head',
    'Failure rate per head',
    'What is the failure rate for every head?',
])
def test_per_head_failure_wording_uses_explicit_rejection_denominator(question):
    plan = RulePlanner().plan(question, {})
    assert not plan.ambiguous
    assert plan.calls == [('success_rate_per_head', {})]
    assert 'rejection flags among confirmed cap-present observed events' in plan.interpretation


def test_per_head_failure_question_preserves_scope_and_rejects_status_prefilter():
    scoped = RulePlanner().plan(
        'Show me the failure rate for each head for machine M1 '
        'from 2026-02-01T00:00:00 until 2026-02-01T01:00:00', {})
    assert scoped.calls == [('success_rate_per_head', {
        'machine_id': 'M1', 'start': '2026-02-01T00:00:00',
        'end': '2026-02-01T01:00:00'})]
    invalid = RulePlanner().plan('show me the failure rate for each head for status 0', {})
    assert invalid.ambiguous and invalid.calls == []


@pytest.mark.parametrize('query', [
    'success rate yesterday', 'success rate for successful closures',
    'success rate for status 0', 'success rate for all closures',
    'success rate excluding head 5', 'success rate for head 5 for head 6',
    'success rate by hour', 'success rate for machine null',
    'success rate from 2026-02-02 until 2026-02-01',
    'rank heads by success rate', 'why is the success rate low',
    'success rate from 2026-02-01T00:00:00Z until 2026-02-02T00:00:00Z',
])
def test_unsupported_kpi_question_never_discards_scope(query):
    plan = RulePlanner().plan(query, {})
    assert plan.ambiguous and not plan.calls


@pytest.fixture
def sources(tmp_path, raw, events):
    one = write_event_table_parquet(events, tmp_path/'one.parquet')
    paths=[]
    for i, part in enumerate([raw[:5], raw[5:]]):
        path=tmp_path/f'raw-{i}.csv'
        part.write_csv(path)
        paths.append(path)
    manifest=build_event_pool(paths, tmp_path/'pool', machine_id='M1')
    return {'person_a':one, 'person_a_pool':manifest}


@pytest.mark.parametrize('source_name', ['person_a', 'person_a_pool'])
def test_kpi_orchestration_reports_counts_and_denominators(sources, source_name, events):
    cfg={**CFG, 'data': {'source':source_name, 'person_a': {'fixture':str(sources[source_name])}}}
    answer=Orchestrator(cfg=cfg).answer('success rate for head 5 for machine M1 on 2026-02-01')
    assert answer['status']=='ok', answer.get('message')
    assert answer['trace'].to_dict()['n_tool_calls']==2
    for name, response in answer['results']:
        reference=dispatch_tool(name, events, config=cfg, arguments=answer['plan'].calls[0][1])
        assert response['result']==reference['result']
    assert '50.000000%' in answer['markdown']
    assert 'Rejection flags outside the cap-present population: **2**' in answer['markdown']
    assert 'unknown cap presence: **5**' in answer['markdown']
    assert 'Legacy A non-No-Load' in answer['markdown']
    assert 'must not be added' in answer['markdown']
    assert 'Nothing anomalous surfaced' not in answer['markdown']
    if source_name=='person_a_pool':
        assert answer['pool_meta']['loaded_events']==8
        assert 'status_filter' not in answer['pool_meta']['selection_parameters']


def test_failure_rate_for_each_head_runs_existing_kpi(sources):
    cfg = {**CFG, 'data': {'source': 'person_a_pool',
                         'person_a': {'fixture': str(sources['person_a_pool'])}}}
    answer = Orchestrator(cfg=cfg).answer('show me the failure rate for each head')
    assert answer['status'] == 'ok', answer.get('message')
    assert answer['trace'].to_dict()['n_tool_calls'] == 1
    assert answer['plan'].calls == [('success_rate_per_head', {})]
    rows = answer['results'][0][1]['result']['per_head']
    assert [(r['head_id'], r['n_reject_cap_present'], r['n_cap_present'])
            for r in rows] == [('H05', 1, 2), ('H06', 0, 8)]
    assert [r['reject_rate_cap_present'] for r in rows] == [0.5, 0.0]
    assert 'Failure rate means rejection flags' in answer['markdown']


def test_pool_rejects_kpi_status_prefilter(sources):
    cfg={**CFG,'data':{'source':'person_a_pool','person_a':{'fixture':str(sources['person_a_pool'])}}}
    source=datasource.get_source(cfg)
    with pytest.raises(ValueError, match='unsupported'):
        source.load_for_plan('fixture',[('success_rate',{'status_filter':0})])


def test_kpi_unsupported_request_stops_before_load(sources, monkeypatch):
    cfg={**CFG,'data':{'source':'person_a_pool','person_a':{'fixture':str(sources['person_a_pool'])}}}
    engine=Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.source,'load_for_plan',lambda *a: pytest.fail('must not load'))
    answer=engine.answer('success rate yesterday')
    assert answer['status']=='needs_clarification' and answer['trace'].to_dict()['n_tool_calls']==0


def test_unavailable_tool_in_plan_blocks_partial_report(sources, monkeypatch):
    cfg={**CFG,'data':{'source':'person_a','person_a':{'fixture':str(sources['person_a'])}}}
    engine=Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.planner,'plan',lambda *a: Plan(goal='fixture',calls=[('success_rate',{}),('not_integrated',{})]))
    monkeypatch.setattr(engine.source,'load_pool',lambda *a: pytest.fail('must not load'))
    answer=engine.answer('fixture')
    assert answer['status']=='needs_clarification' and answer['results']==[]


def test_llm_kpi_remains_blocked_before_inference(monkeypatch):
    planner=LLMPlanner({'agent': {'llm': {'fallback_to_rules':False}}})
    monkeypatch.setattr(planner,'_chat',lambda *a: pytest.fail('LLM KPI is not integrated'))
    plan=planner.plan('success rate for head 5',{})
    assert plan.ambiguous and not plan.calls


# --- a failure-rate question is answered with the failure rate first ----------------

@pytest.mark.parametrize('question, focus', [
    ('show me the failure rate for each head', 'failure'),
    ('reject rate per head', 'failure'),
    ('success rate per head', ''),
])
def test_rate_wording_sets_the_presentation_focus(question, focus):
    assert RulePlanner().plan(question, {}).focus == focus


@pytest.mark.parametrize('question, focus', [
    ('which heads fail the most?', 'failure'),
    ('how many rejects did head 7 have', 'failure'),
    ('show defective closures per head', 'failure'),
    ('success and failure per head', ''),  # both asked: keep the default view
    ('average torque for head 5', ''),
])
def test_rate_focus_from_any_planners_wording(question, focus):
    from src.agent.report import rate_focus
    assert rate_focus(question) == focus


def test_failure_question_leads_report_figures_and_chart_with_failure(sources, tmp_path):
    from src.interface import plots
    from src.interface.dashboard import key_figures
    cfg = {**CFG, 'data': {'source': 'person_a_pool',
                         'person_a': {'fixture': str(sources['person_a_pool'])}}}
    answer = Orchestrator(cfg=cfg).answer('show me the failure rate for each head')
    findings = answer['markdown'].split('## 4. Findings', 1)[1]
    assert '| Machine | Head | Failure rate | Rejects / cap-present | Success rate |' in findings
    assert '| M1 | H05 | **50.0000%** | 1 / 2 | 50.0000% |' in findings
    assert findings.index('**Failure rate**') < findings.index('Cap-present success')
    figures = key_figures(answer['results'], focus=answer['plan'].focus)
    assert [f['label'] for f in figures][:1] == ['Failure rate']
    assert figures[-1]['label'] == 'Highest failure head' and figures[-1]['value'].startswith('H05')
    made = plots.render(answer['results'], tmp_path, focus=answer['plan'].focus)
    assert [path.name for _, path in made] == ['failure_rate_per_head.png']


def test_success_question_keeps_the_success_view(sources, tmp_path):
    from src.interface import plots
    from src.interface.dashboard import key_figures
    cfg = {**CFG, 'data': {'source': 'person_a_pool',
                         'person_a': {'fixture': str(sources['person_a_pool'])}}}
    answer = Orchestrator(cfg=cfg).answer('success rate per head')
    assert answer['plan'].focus == ''
    assert '| Failure rate |' not in answer['markdown']
    assert key_figures(answer['results'])[0]['label'] == 'Success rate'
    made = plots.render(answer['results'], tmp_path)
    assert [path.name for _, path in made] == ['success_rate_per_head.png']
