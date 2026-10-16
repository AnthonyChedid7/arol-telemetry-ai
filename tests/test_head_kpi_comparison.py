"""Descriptive ranks, explicit peer populations and conservative missing-data behavior."""
from datetime import datetime, timedelta
from copy import deepcopy
import pytest
import polars as pl
from polars.testing import assert_frame_equal

from src.analytics.registered_head_kpi import rank_counts, compare_with_peers
from src.common.runtime import dispatch_tool
from src.common import datasource
from src.agent.planner import RulePlanner, LLMPlanner
from src.agent.orchestrator import Orchestrator
from src.ingestion.event_table_polars import build_event_table, write_event_table_parquet, EVENT_SCHEMA
from src.ingestion.event_pool import build_event_pool

WINDOW = dict(machine_id='M1', start='2026-02-01T00:00:00', end='2026-02-01T01:00:00')
CFG = {'analytics': {'min_n':3}, 'agent': {'planner':'rules'}}
SUFFIX = ' for machine M1 from 2026-02-01T00:00:00 until 2026-02-01T01:00:00'
RANK_QUERY = 'rank heads by cap-present success rate'
COMPARE_QUERY = 'compare head 1 with peers by cap-present success rate'


def counts(head, ok, n, machine='M1'):
    return dict(head_id=head,machine_id=machine,n_success_cap_present=ok,n_cap_present=n)


def test_exact_ratio_ties_have_competition_ranks():
    rows=[counts('H03',4,4),counts('H02',6,8),counts('H01',3,4),counts('H04',1,2),counts('H05',0,0)]
    original=deepcopy(rows)
    ranked=rank_counts(rows,3)
    assert rows==original
    assert [(r['head_id'],r['rank_lowest_success_first']) for r in ranked]==[('H01',1),('H02',1),('H03',3),('H04',None),('H05',None)]
    result=compare_with_peers(ranked,'H01')
    assert result['eligible_peer_heads']==['H02','H03']
    assert result['peer_median_success_rate']==0.875
    assert result['difference_from_peer_median_pp']==-12.5
    assert result['comparison_available']


def test_ranks_do_not_use_rounded_float_rates():
    n=2**54
    rows=[counts('H01',n-1,n),counts('H02',n,n)]
    ranked=rank_counts(rows,3)
    assert [r['rank_lowest_success_first'] for r in ranked]==[1,2]


@pytest.mark.parametrize('rows,focus,reason', [
    ([], 'H01', 'focus_head_not_found'),
    ([counts('H01',1,2),counts('H02',3,3),counts('H03',3,3)], 'H01','focus_below_minimum_sample'),
    ([counts('H01',3,3),counts('H02',3,3)], 'H01','fewer_than_two_eligible_peers'),
    ([counts('H01',3,3)], 'H99','focus_head_not_found'),
])
def test_unavailable_comparisons_do_not_substitute_a_baseline(rows,focus,reason):
    result=compare_with_peers(rank_counts(rows,3),focus)
    assert result['comparison_reason']==reason
    assert not result['comparison_available']
    assert result['peer_median_success_rate'] is None
    assert result['difference_from_peer_median_pp'] is None


def test_single_eligible_head_has_no_comparative_rank():
    ranked=rank_counts([counts('H01',3,3),counts('H02',1,2)],3)
    assert all(r['rank_lowest_success_first'] is None for r in ranked)


@pytest.mark.parametrize('rows', [
    [counts('H01',3,3),counts('H01',3,3)],
    [counts('H01',3,3),counts('H02',3,3,'M2')],
    [counts('H01',4,3)], [counts('H01',0,-1)], [counts('H01',True,3)],
])
def test_invalid_rank_summaries_fail(rows):
    with pytest.raises(ValueError):
        rank_counts(rows,3)


@pytest.fixture
def raw():
    statuses={'H01':[0,0,0,65,9], 'H02':[0]*6+[65]*2, 'H03':[0]*4, 'H04':[0,65], 'H05':[2]*3}
    values={'timestamp':[datetime(2026,2,1)+timedelta(seconds=i) for i in range(9)]}
    for head,states in statuses.items():
        values[head+' Count']=[0]+[min(i,len(states)) for i in range(1,9)]
        values[head+' Status']=[0]+[states[min(i,len(states)-1)] for i in range(8)]
        values[head+' AppTorque']=[2.]*9
    return pl.DataFrame(values)


@pytest.fixture
def events(raw):
    return build_event_table(raw,'M1')


@pytest.mark.parametrize('tool,args', [
    ('rank_heads_by_success',dict(WINDOW)),
    ('compare_head_success',dict(WINDOW,head_id='H01')),
])
def test_tools_retain_unknowns_and_apply_population_scope(events,tool,args):
    distractors=events.with_columns(pl.lit('M2').alias('machine_id'))
    reply=dispatch_tool(tool,pl.concat([events,distractors]),config=CFG,arguments=args)
    assert reply['ok'],reply['error']
    r=reply['result']
    assert r['overall']['n_observed']==22 and r['n_eligible_heads']==3
    assert r['overall']['n_cap_unknown']==1
    assert [(v['head_id'],v['rank_lowest_success_first']) for v in r['ranked_heads']]==[('H01',1),('H02',1),('H03',3),('H04',None),('H05',None)]
    assert r['population_scope']==WINDOW
    if tool=='compare_head_success':
        assert r['difference_from_peer_median_pp']==-12.5
        assert r['focus']['n_cap_unknown']==1


@pytest.mark.parametrize('args', [
    {'machine_id':'M1','start':WINDOW['start']},
    dict(WINDOW,machine_id=None),dict(WINDOW,end=WINDOW['start']),
    dict(WINDOW,start='2026-02-01T00:00:00Z'),
    dict(WINDOW,head_id=['H01']),dict(WINDOW,head_id='head 1'),
])
def test_invalid_population_arguments_fail(events,args):
    tool='compare_head_success' if 'head_id' in args else 'rank_heads_by_success'
    reply=dispatch_tool(tool,events,config=CFG,arguments=args)
    assert not reply['ok']


@pytest.mark.parametrize('base', [RANK_QUERY,COMPARE_QUERY,'Compare H01 with peers by cap present success rate'])
def test_comparison_grammar_requires_and_preserves_full_population(base):
    plan=RulePlanner().plan(base+SUFFIX,{})
    assert not plan.ambiguous
    comparison=base.lower().startswith('compare')
    assert plan.calls==[('compare_head_success' if comparison else 'rank_heads_by_success',dict(WINDOW,**({'head_id':'H01'} if comparison else {})))]


@pytest.mark.parametrize('query', [
    RANK_QUERY,COMPARE_QUERY,RANK_QUERY+' for machine M1',RANK_QUERY+' on 2026-02-01',
    RANK_QUERY+SUFFIX+' for head 1',COMPARE_QUERY+SUFFIX+' for head 2',
    RANK_QUERY+SUFFIX+' for successful closures',COMPARE_QUERY+SUFFIX+' for status 0',
    RANK_QUERY+SUFFIX+' excluding H02',RANK_QUERY+SUFFIX+' with min_n 1',
    'rank heads by success rate'+SUFFIX,COMPARE_QUERY+' yesterday',
])
def test_incomplete_or_unsupported_comparisons_request_clarification(query):
    plan=RulePlanner().plan(query,{})
    assert plan.ambiguous and not plan.calls


@pytest.fixture
def sources(tmp_path,raw,events):
    single=write_event_table_parquet(events,tmp_path/'events.parquet')
    paths=[]
    for i,part in enumerate([raw[:5],raw[5:]]):
        path=tmp_path/f'raw-{i}.csv'
        part.write_csv(path)
        paths.append(path)
    manifest=build_event_pool(paths,tmp_path/'pool',machine_id='M1')
    return {'person_a':single,'person_a_pool':manifest}


@pytest.mark.parametrize('source_name',['person_a','person_a_pool'])
@pytest.mark.parametrize('base',[RANK_QUERY,COMPARE_QUERY])
def test_orchestration_reports_comparison_scope_without_causal_claims(sources,source_name,base):
    cfg={**CFG,'data':{'source':source_name,'person_a':{'fixture':str(sources[source_name])}}}
    answer=Orchestrator(cfg=cfg).answer(base+SUFFIX)
    assert answer['status']=='ok',answer.get('message')
    assert answer['trace'].to_dict()['n_tool_calls']==1
    assert answer['results'][0][1]['result']['overall']['n_observed']==22
    assert 'not a statistical anomaly test or a fault diagnosis' in answer['markdown']
    assert 'Nothing anomalous surfaced' not in answer['markdown']
    if base==COMPARE_QUERY:
        assert '-12.500000 percentage points' in answer['markdown']
        assert 'head_id identifies the focus' in answer['markdown']
    if source_name=='person_a_pool':
        assert answer['pool_meta']['loaded_events']==22
        assert answer['pool_meta']['comparison_population']==WINDOW
        assert 'head_id' not in answer['pool_meta']['selection_parameters']
        if base==COMPARE_QUERY:
            assert answer['pool_meta']['comparison_focus_head']=='H01'
            step=next(s for s in answer['trace'].steps if s['kind']=='load_pool')
            assert step['comparison_focus_head']=='H01'
            assert step['comparison_population']==WINDOW


def test_missing_focus_is_reported_without_substitution(sources):
    cfg={**CFG,'data':{'source':'person_a_pool','person_a':{'fixture':str(sources['person_a_pool'])}}}
    answer=Orchestrator(cfg=cfg).answer('compare head 99 with peers by cap-present success rate'+SUFFIX)
    assert answer['status']=='needs_clarification'
    assert 'H99 is not in this data pool' in answer['message']
    assert not answer['results']
    assert answer['trace'].to_dict()['n_tool_calls']==0


def test_invalid_scope_stops_before_source_load(sources,monkeypatch):
    cfg={**CFG,'data':{'source':'person_a_pool','person_a':{'fixture':str(sources['person_a_pool'])}}}
    engine=Orchestrator(cfg=cfg)
    monkeypatch.setattr(engine.source,'load_for_plan',lambda *a: pytest.fail('must not load'))
    answer=engine.answer(RANK_QUERY)
    assert answer['status']=='needs_clarification' and answer['trace'].to_dict()['n_tool_calls']==0


def test_comparison_does_not_remove_peers_to_fit_memory_limit(sources):
    cfg={**CFG,'data':{'source':'person_a_pool','person_a':{'fixture':str(sources['person_a_pool'])},'max_loaded_events':10}}
    answer=Orchestrator(cfg=cfg).answer(COMPARE_QUERY+SUFFIX)
    assert answer['status']=='needs_clarification'
    assert answer['results']==[] and answer['trace'].to_dict()['n_tool_calls']==0


@pytest.mark.parametrize('query',[RANK_QUERY+SUFFIX,COMPARE_QUERY+SUFFIX])
def test_llm_comparisons_remain_blocked(query,monkeypatch):
    planner=LLMPlanner({'agent':{'llm':{'fallback_to_rules':False}}})
    monkeypatch.setattr(planner,'_chat',lambda *a: pytest.fail('LLM comparison not verified'))
    plan=planner.plan(query,{})
    assert plan.ambiguous and not plan.calls
