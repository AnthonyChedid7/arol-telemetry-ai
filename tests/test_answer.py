"""The direct answer: the form the question asked for, and only checked numbers."""
from types import SimpleNamespace

import pytest

from src.agent import answer as answer_mod
from src.agent.orchestrator import Orchestrator
from src.agent.planner import Plan
from src.agent.trace import Trace

from test_daily_head_kpi import CFG, pool  # noqa: F401  (fixture)


def _orchestrator(pool):
    return Orchestrator(cfg={**CFG, 'data': {'source': 'person_a_pool', 'person_a': {'p': str(pool)}}})


@pytest.mark.parametrize('question, artifact, metric', [
    ('can you now give me the performance of each head please on each day?', 'head_day_table', 'success_rate'),
    ('give me the error rate of all heads for each day', 'head_day_table', 'failure_rate'),
    ('give me the lowest error rates on each day', 'day_table', 'failure_rate'),
    ('plot the success rate of each head for each day', 'chart', 'success_rate'),
])
def test_the_form_follows_the_wording(pool, question, artifact, metric):  # noqa: F811
    answer = _orchestrator(pool).answer(question)['answer']
    assert (answer['artifact'], answer['metric']) == (artifact, metric)


def test_every_head_on_every_day_is_a_full_table(pool):  # noqa: F811
    result = _orchestrator(pool).answer('performance of each head for each day')
    answer = result['answer']
    assert answer['sentence'] == 'Here is the success rate of each of the 3 heads on each of the 3 days.'
    rows = answer['table'].splitlines()
    assert rows[0] == '| Head | 2026-02-01 (partial) | 2026-02-02 | 2026-02-03 (partial) |'
    assert rows[2].startswith('| H01 | 87.3239% |') and rows[-1].startswith('| **All heads** | 95.3052% |')
    assert len(rows) == 2 + 3 + 1  # header, separator, one row per head, all heads
    assert result['markdown'].index('## Answer') < result['markdown'].index('## 1. Goal')


def test_lowest_error_each_day_lists_the_tied_heads(pool):  # noqa: F811
    answer = _orchestrator(pool).answer('give me the lowest error rates on each day')['answer']
    assert answer['sentence'].startswith('On every one of the 3 days the lowest failure rate was 0.0000%')
    assert '| 2026-02-02 | 0.0000% | 2 heads: H01, H03 | 1.3889% |' in answer['table']


def _results(**result):
    return [('success_rate', {'ok': True, 'result': result, 'meta': {}})]


def test_number_check_accepts_rounding_and_rejects_invented_numbers():
    results = _results(overall={'n_cap_present': 6038, 'n_reject_cap_present': 3,
                                'reject_rate_cap_present': 3 / 6038})
    good = 'H22 had 3 rejects on 2026-02-03, out of 6,038 closures (0.0497%; Feb 3, head 22).'
    assert answer_mod.unmatched_numbers(good, results) == []
    assert answer_mod.unmatched_numbers('H22 had 4 rejects out of 6,100 closures.', results) == ['4', '6,100']


class _Writer:
    def __init__(self, proposal):
        self.proposal = proposal

    def compose_answer(self, query, plan, results, meta, draft):
        return self.proposal


def test_claude_answer_is_used_only_when_every_number_checks_out():
    results = _results(overall={'n_cap_present': 6038, 'n_reject_cap_present': 3, 'n_success_cap_present': 6035,
                                'reject_rate_cap_present': 3 / 6038, 'success_rate_cap_present': 6035 / 6038,
                                'n_observed': 12553})
    plan = Plan(goal='g', calls=[('success_rate', {'head_id': 'H22'})], focus='failure')
    trace = Trace('q', planner='claude')
    used = answer_mod.compose('how many rejects did head 22 have?', plan, results, {},
                              planner=_Writer({'sentence': 'H22 had 3 rejects.', 'artifact': 'head_day_table',
                                               'metric': 'rejects'}), trace=trace)
    assert used['source'] == 'claude' and used['sentence'] == 'H22 had 3 rejects.'
    assert used['artifact'] == 'none'  # a form this tool cannot produce is ignored
    trace = Trace('q', planner='claude')
    refused = answer_mod.compose('how many rejects did head 22 have?', plan, results, {},
                                 planner=_Writer({'sentence': 'H22 had 5 rejects.'}), trace=trace)
    assert refused['source'] == 'built-in'
    assert refused['sentence'] == ('Head H22 had 3 rejects over all the data out of 6,038 closures '
                                   'with a cap (failure rate 0.0497%).')
    assert any('not in the results (5)' in note for note in trace.to_dict()['notes'])


def test_claude_planner_answer_request_and_switch(monkeypatch):
    from src.agent.claude_planner import ClaudePlanner
    planner = ClaudePlanner({'agent': {'claude': {}}})
    planner.last_usage = {}
    sent = {}

    def fake_call(content):
        sent['content'] = content
        block = SimpleNamespace(type='tool_use', name='give_answer',
                                input={'answer': 'H22 had 3 rejects.', 'form': 'none', 'metric': 'rejects'})
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    monkeypatch.setattr(planner, '_call_answer', fake_call)
    results = _results(overall={'n_reject_cap_present': 3})
    out = planner.compose_answer('how many rejects?', Plan(goal='g'), results, {}, draft={'sentence': 'x'})
    assert out == {'sentence': 'H22 had 3 rejects.', 'artifact': 'none', 'metric': 'rejects'}
    assert '<question>\nhow many rejects?\n</question>' in sent['content'] and '"n_reject_cap_present":3' in sent['content']
    assert planner.last_usage == {'answer_input_tokens': 10, 'answer_output_tokens': 5}
    off = ClaudePlanner({'agent': {'claude': {'answer_from_results': False}}})
    assert off.compose_answer('q', Plan(goal='g'), results, {}, draft={}) is None


def test_compact_results_keep_summaries_and_shorten_series():
    results = [('torque_trend', {'ok': True, 'result': {'sample_size': 4764, 'moving_average': [2.0] * 5000,
                                                         'rate_definitions': {'x': 'y'}}, 'meta': {}})]
    text = answer_mod.compact_results(results)
    assert '"sample_size":4764' in text and '"n_values":5000' in text and 'rate_definitions' not in text
