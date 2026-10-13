"""Exercise the public demo command against the production scoped source."""
from datetime import datetime, timedelta
import json
from pathlib import Path

import polars as pl
import pytest

from scripts import demo_agent
from src.ingestion.event_pool import build_event_pool


@pytest.fixture
def setup(tmp_path, monkeypatch):
    begin = datetime(2026, 2, 1)
    raw = pl.DataFrame({
        'timestamp': [begin + timedelta(seconds=n) for n in range(6)],
        'H01 Count': list(range(10, 16)), 'H02 Count': list(range(20, 26)),
        'H01 AppTorque': [2., 2., 2.1, 2.2, 2.3, 2.4],
        'H02 AppTorque': [2.] * 6,
        'H01 Status': [0, 0, 65, 0, 2, 0], 'H02 Status': [0] * 6,
    })
    paths = []
    for number, frame in enumerate((raw[:3], raw[3:])):
        path = tmp_path / f'raw-{number}.csv'
        frame.write_csv(path)
        paths.append(path)
    manifest = build_event_pool(paths, tmp_path / 'pool', machine_id='M1')
    monkeypatch.chdir(demo_agent.ROOT)
    monkeypatch.setattr(demo_agent.config_mod, 'load', lambda: {
        'data': {'pools': {}},
        'agent': {'planner': 'rules', 'llm': {'fallback_to_rules': False}},
        'analytics': {'min_n': 2},
    })
    return manifest, tmp_path / 'out'


def _query():
    return ('Average torque for head 1 for machine M1 from '
            '2026-02-01T00:00:01 until 2026-02-01T00:00:05')


def test_command_saves_real_report_and_trace_with_exact_scope(setup, capsys):
    manifest, output = setup
    assert demo_agent.main(['--manifest', str(manifest), '--output', str(output),
                            '--question', _query(), '--json']) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply['status'] == 'ok' and reply['planner'] == 'rules'
    assert reply['planned_calls'] == [['torque_stats', {
        'head_id': 'H01', 'machine_id': 'M1',
        'start': '2026-02-01T00:00:01', 'end': '2026-02-01T00:00:05'}]]
    # Five H01 events in the window; the status-2 No Load cycle is excluded by default.
    assert reply['results'][0][1]['result']['sample_size'] == 3
    assert reply['trace']['n_tool_calls'] == 1
    assert Path(reply['artifacts']['report']).read_text() == reply['markdown']
    saved_trace = json.loads(Path(reply['artifacts']['trace']).read_text())
    assert saved_trace['n_tool_calls'] == 1
    assert reply['results'][0][1]['meta']['params'] == reply['planned_calls'][0][1]


def test_two_questions_use_separate_report_and_trace_paths(setup, capsys):
    manifest, output = setup
    temporal = ('Success rate by hour for machine M1 from '
                '2026-02-01T00:00:00 until 2026-02-01T00:00:06')
    assert demo_agent.main(['--manifest', str(manifest), '--output', str(output),
                            '--question', _query(), '--question', temporal, '--json']) == 0
    first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first['artifacts']['report'] != second['artifacts']['report']
    assert first['artifacts']['trace'] != second['artifacts']['trace']
    assert all(Path(item['artifacts']['trace']).is_file() for item in (first, second))
    assert second['results'][0][0] == 'kpi_over_time'
    assert second['results'][0][1]['result']['overall']['n_observed'] == 10


def test_undefined_cause_question_requests_clarification_without_loading(setup, monkeypatch, capsys):
    manifest, output = setup
    from src.common import event_pool_source

    def forbidden(*args, **kwargs):
        raise AssertionError('Unspecified cause must not open event partitions')

    monkeypatch.setattr(event_pool_source, 'scan_event_pool', forbidden)
    assert demo_agent.main(['--manifest', str(manifest), '--output', str(output),
                            '--question', 'Why is H04 failing more?', '--json']) == 2
    reply = json.loads(capsys.readouterr().out)
    assert reply['status'] == 'needs_clarification'
    assert reply['trace']['n_tool_calls'] == 0
    assert Path(reply['artifacts']['trace']).is_file()


def test_unverified_llm_kpi_is_blocked_before_inference(setup, monkeypatch, capsys):
    manifest, output = setup
    from src.agent import planner

    def forbidden(*args, **kwargs):
        raise AssertionError('KPI requests are not enabled for live model routing')

    monkeypatch.setattr(planner.LLMPlanner, '_chat', forbidden)
    question = ('Success rate for machine M1 from 2026-02-01T00:00:00 '
                'until 2026-02-01T00:00:06')
    assert demo_agent.main(['--manifest', str(manifest), '--output', str(output),
                            '--planner', 'llm', '--question', question, '--json']) == 2
    reply = json.loads(capsys.readouterr().out)
    assert reply['status'] == 'needs_clarification'
    assert reply['trace']['n_tool_calls'] == 0


def test_examples_derive_machine_and_time_from_manifest_without_writing(setup, capsys):
    manifest, output = setup
    assert demo_agent.main(['--manifest', str(manifest), '--output', str(output), '--examples']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all('machine M1 from 2026-02-01T00:00:00 until 2026-02-01T01:00:00'
               in line for line in lines)
    assert not output.exists()


def test_multiple_local_manifests_require_explicit_selection(tmp_path):
    for number in (1, 2):
        folder = tmp_path / 'data/event_pools' / str(number)
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps({
            'format': 'arol-event-pool-v1', 'complete': True,
            'summary': {'machine_id': 'M1'},
            'partitions': [{'ts_min': '2026-02-01T00:00:00'}],
        }))
    with pytest.raises(ValueError, match='found 2'):
        demo_agent.resolve_manifest(None, root=tmp_path)


def test_invalid_manifest_is_rejected_before_orchestrator(tmp_path):
    path = tmp_path / 'not-a-pool.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='complete AROL v1'):
        demo_agent.resolve_manifest(str(path), root=tmp_path)
