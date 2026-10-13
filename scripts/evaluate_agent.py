"""Evaluate fixed new phrasings and planted telemetry signals through production routing.

The questions were authored with the known grammar; this is a controlled
integration evaluation, not an unbiased estimate of language accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

import polars as pl

from src.agent.orchestrator import Orchestrator
from src.common import datasource, registry
from src.ingestion.event_pool import build_event_pool
from src.ingestion.event_table_polars import EVENT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'benchmarks/evaluation/prompts_v1.json'
HEADS = ('H05', 'H06', 'H07', 'H08')
WINDOW = {'machine_id':'M1', 'start':'2026-02-01T00:00:00', 'end':'2026-02-01T00:00:13'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def path_for_evidence(path):
    path=Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def plant_input(directory: Path) -> list[Path]:
    """12 observed closures per head, two torque breaches, four H05 rejects."""
    start = datetime(2026, 2, 1)
    rows = {'timestamp':[start + timedelta(seconds=i) for i in range(13)]}
    for head in HEADS:
        rows[head+' Count'] = list(range(100, 113))
        rows[head+' Status'] = [65 if head == 'H05' and 1 <= i <= 4 else 0
                               for i in range(13)]
        rows[head+' AppTorque'] = [3.1 if head == 'H05' and i == 6 else
                                  0.5 if head == 'H05' and i == 7 else 2.0
                                  for i in range(13)]
    raw = pl.DataFrame(rows)
    directory.mkdir(parents=True, exist_ok=False)
    paths = []
    for index, part in enumerate((raw[:7], raw[7:]), 1):
        path = directory / f'input-{index}.csv'
        part.write_csv(path)
        paths.append(path)
    return paths


def scalar_events(paths: list[Path]) -> tuple[list[dict], int]:
    """Independent counter scan, explicit status truth for this two-code fixture."""
    previous = {}
    result = []
    boundary = 0
    for file_index, path in enumerate(paths):
        with path.open(newline='', encoding='utf-8') as stream:
            for row_number, row in enumerate(csv.DictReader(stream)):
                for head in HEADS:
                    count = int(row[head+' Count'])
                    before = previous.get(head)
                    if before is not None and count - before == 1:
                        status = int(row[head+' Status'])
                        if status not in (0, 65):
                            raise AssertionError(f'Unexpected planted status: {status}')
                        result.append(dict(ts=datetime.fromisoformat(row['timestamp']).isoformat(),
                                           machine_id='M1', head_id=head,
                                           torque=float(row[head+' AppTorque']), status=status,
                                           error_class='Closure OK' if status == 0 else 'Bad Closure',
                                           reject_signal=status == 65, cap_present=True))
                        boundary += int(file_index > 0 and row_number == 0)
                    previous[head] = count
    return result, boundary


def compare_event_fields(events: pl.DataFrame, expected: list[dict]):
    actual = events.select(list(EVENT_SCHEMA)).to_dicts()
    for row in actual:
        row['ts'] = row['ts'].isoformat()
    order = lambda row: (row['ts'], row['head_id'])
    if sorted(actual, key=order) != sorted(expected, key=order):
        raise AssertionError('Event source differs from independent counter/status/torque oracle')


def planted_checks(case_id: str, result: dict, meta: dict, markdown: str):
    """Check a predeclared effect without inferring a physical fault cause."""
    if case_id == 'planted_torque_thresholds':
        flags = result['anomalies']
        found = {(r['ts'],r['torque'],r['reason']) for r in flags}
        expected = {('2026-02-01 00:00:06',3.1,'above_expected_max'),
                    ('2026-02-01 00:00:07',0.5,'below_expected_min')}
        # Polars' Datetime-to-String display can use either ISO or a space.
        found = {(datetime.fromisoformat(t).isoformat(timespec='seconds').replace('T',' '),v,why)
                 for t,v,why in found}
        assert result['sample_size'] == 12 and result['anomaly_count'] == 2
        assert found == expected, (found, expected)
        assert 'do not establish a cause' in meta['notes']
        return {'flags':sorted([list(row) for row in found])}
    if case_id in {'planted_rank','planted_peers'}:
        ranked = result['ranked_heads']
        by_head = {row['head_id']:row for row in ranked}
        assert set(by_head) == set(HEADS) and result['n_eligible_heads'] == 4
        assert result['overall']['n_observed'] == 48
        assert by_head['H05']['n_cap_present'] == 12
        assert by_head['H05']['n_success_cap_present'] == 8
        assert by_head['H05']['rank_lowest_success_first'] == 1
        assert all(by_head[h]['rank_lowest_success_first'] == 2 for h in HEADS[1:])
        assert 'not a statistical anomaly test or a fault diagnosis' in markdown
        if case_id == 'planted_peers':
            assert result['comparison_available'] and result['n_eligible_peers'] == 3
            assert result['peer_median_success_rate'] == 1.0
            assert math.isclose(result['difference_from_peer_median_pp'], -100/3, abs_tol=1e-12)
        return {'ranking':{h:by_head[h]['rank_lowest_success_first'] for h in HEADS}}
    if case_id == 'new_observed_hourly':
        overall = result['overall']
        assert overall['n_observed'] == 48
        assert overall['duration_seconds'] == 13
        assert math.isclose(overall['observed_events_per_hour'], 48*3600/13, rel_tol=1e-12)
        return {'observed_events':48,'requested_seconds':13}
    return {}


def evaluate(catalogue: dict, cfg: dict, run_dir: Path) -> list[dict]:
    records = []
    for case in catalogue['cases']:
        settings = deepcopy(cfg)
        settings['agent']['report_dir'] = str(run_dir/'cases'/case['id']/'reports')
        settings['agent']['trace_dir'] = str(run_dir/'cases'/case['id']/'traces')
        engine = Orchestrator(cfg=settings)
        requested = [(item['name'],item['arguments']) for item in case['calls']]
        attempted = []
        if case['expected'] == 'needs_clarification':
            def forbidden(*args, **kwargs):
                attempted.append(True)
                raise AssertionError('Clarification case attempted to load data')
            engine.source.load_for_plan = forbidden
        problem = None
        try:
            answer = engine.answer(case['question'], pool='evaluation')
            trace = answer['trace'].to_dict()
            calls = answer['plan'].calls if answer['plan'] else []
            routes_exact = calls == requested
            if case['expected'] == 'routed':
                passed = (answer['status'] == 'ok' and routes_exact
                          and trace['n_tool_calls'] == len(requested)
                          and [name for name,_ in answer['results']] == [name for name,_ in requested]
                          and all(r['ok'] and r['meta']['params'] == params
                                  for (_,r),(_,params) in zip(answer['results'],requested)))
                if passed:
                    for name,response in answer['results']:
                        planted_checks(case['id'], response['result'], response['meta'], answer['markdown'])
            else:
                # Legacy keyword plans may name tools that have never been
                # registered; the orchestrator rejects them before loading.
                passed = (answer['status'] == 'needs_clarification'
                          and (not calls or any(registry.get(name) is None for name,_ in calls))
                          and not attempted and trace['n_tool_calls'] == 0)
            artifacts = engine.deliver(answer, formats=['markdown'])
            snapshot = run_dir / 'cases' / case['id'] / 'result.json'
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps({'status':answer['status'],
                                            'calls':calls,'results':answer['results'],
                                            'trace':trace,'artifacts':artifacts},
                                           indent=2, allow_nan=False) + '\n')
            recorded = {k:path_for_evidence(v) for k,v in artifacts.items()}
            recorded['snapshot'] = path_for_evidence(snapshot)
            row = {'id':case['id'], 'expected':case['expected'], 'status':answer['status'],
                   'exact_plan':routes_exact, 'tool_calls':trace['n_tool_calls'],
                   'unavailable_legacy_planned_tools':[name for name,_ in calls if registry.get(name) is None],
                   'data_load_attempted_for_clarification':bool(attempted),
                   'passed':bool(passed),'artifacts':recorded,
                   'result_sha256':sha256(snapshot)}
        except Exception as exc:
            row = {'id':case['id'], 'expected':case['expected'], 'passed':False,
                   'exception':f'{type(exc).__name__}: {exc}'}
        records.append(row)
        print(f"{len(records):02d}/{len(catalogue['cases'])}: {case['id']}: "
              f"{'PASS' if row['passed'] else 'FAIL'}", flush=True)
    return records


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New ignored run directory; default under data/integration_smoke/')
    parser.add_argument('--record', action='store_true',
                        help='Also save the evidence under benchmarks/integration/ and append a checkpoint '
                             'to docs/PROJECT-AUDIT.md and docs/INTEGRATION-TRACKER.md (changes tracked files)')
    args=parser.parse_args(argv)
    if Path.cwd().resolve() != ROOT:
        parser.error('Run from repository root')
    if not __debug__:
        parser.error('Run without -O: the evaluation checks are assert statements')
    catalogue=json.loads(CATALOGUE.read_text(encoding='utf-8'))
    assert catalogue['version'] == 1 and len(catalogue['cases']) == 28
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    run_dir=args.output or ROOT/'data/integration_smoke'/f'agent-evaluation-{stamp}'
    run_dir=run_dir.resolve()
    print('Creating a 2-file controlled telemetry pool; no original CSV is modified.',flush=True)
    raw_paths=plant_input(run_dir/'raw')
    oracle,boundary=scalar_events(raw_paths)
    assert len(oracle)==48 and boundary==4
    manifest=build_event_pool(raw_paths,run_dir/'pool',machine_id='M1')
    cfg={'data':{'source':'person_a_pool','person_a':{'evaluation':str(manifest)},
                 'max_loaded_events':1000,'verify_pool_hashes':True},
         'agent':{'planner':'rules','report_dir':str(run_dir/'reports'),
                  'trace_dir':str(run_dir/'traces'),'max_steps':8},
         'analytics':{'min_n':3,'torque_expected_min':1.5,'torque_expected_max':2.5,
                      'anomaly_sigma':3.0,'drift_window_seconds':3}}
    source=datasource.get_source(cfg)
    events,meta=source.load_for_plan('evaluation',[('success_rate_per_head',dict(WINDOW))])
    compare_event_fields(events,oracle)
    assert meta['pool_total_events']==48 and meta['loaded_events']==48
    stored=json.loads(manifest.read_text(encoding='utf-8'))
    assert stored['summary']['boundary_exact_plus_one']==4
    records=evaluate(catalogue,cfg,run_dir)
    passed=sum(r['passed'] for r in records)
    supported=[r for r in records if r['expected']=='routed']
    clarification=[r for r in records if r['expected']=='needs_clarification']
    state={'git_commit_before_run':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           'git_status_before_run':subprocess.check_output(['git','status','--short'],text=True).strip()}
    evidence={**state,'version':1,'catalogue_sha256':sha256(CATALOGUE),
              'script_sha256':sha256(Path(__file__)), 'manifest_sha256':sha256(manifest),
              'synthetic_csv_sha256':[sha256(p) for p in raw_paths],
              'independent_scalar_field_parity':True,'expected_observed_events':48,
              'expected_cross_file_events':4,'planted_truth':{
                  'H05_cap_present':12,'H05_success':8,'peer_cap_present':12,
                  'peer_success':12,'H05_threshold_torque':{'second_6':3.1,'second_7':0.5}},
              'requested':len(records),'passed':passed,
              'supported_routing_exact':sum(r['passed'] for r in supported),
              'supported_routing_total':len(supported),
              'clarifications_without_load':sum(r['passed'] for r in clarification),
              'clarification_total':len(clarification),
              'cases':records,'config':cfg['analytics'],
              'limits':['Fixed phrasings were authored with knowledge of the supported grammar, not randomly sampled language.',
                        'Synthetic threshold violations and status-65 observations are planted signals, not evidence of a physical failure cause.',
                        'This deterministic evaluation does not make live model requests or estimate model language accuracy.',
                        'One short synthetic pool with four heads; no full-pool scalability claim.'],
              'run_dir':path_for_evidence(run_dir)}
    # By default nothing tracked changes; --record promotes the run to evidence.
    target=(ROOT/'benchmarks/integration'/f'agent_evaluation_{stamp}.json' if args.record
            else run_dir/'agent_evaluation.json')
    target.write_text(json.dumps(evidence,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    heading=f'## Integration measurement — fixed new questions and planted signals {stamp}'
    entry=(heading+'\n\n'
           f'- Fixed new question catalogue: {passed}/{len(records)} cases passed; '
           f'{evidence["supported_routing_exact"]}/{len(supported)} supported plans and '
           f'{evidence["clarifications_without_load"]}/{len(clarification)} clarification cases passed.\n'
           '- Planted counter events matched an independent scalar field oracle, including four cross-file events.\n'
           '- H05 had four status-65 records and two planted torque limit breaches; describe these as signals, not causes.\n'
           f'- Evidence: {path_for_evidence(target)}. No live LLM requests.\n'
           '- Remaining: clean checkout, presentation review, and any explicitly defined causal/operating-time inputs.\n')
    for name in ('PROJECT-AUDIT.md','INTEGRATION-TRACKER.md') if args.record else ():
        path=ROOT/'docs'/name
        path.write_text(path.read_text(encoding='utf-8').rstrip()+'\n\n'+entry,encoding='utf-8')
    print('EVALUATION RESULT')
    print(json.dumps({'passed':passed,'total':len(records),
                      'supported_exact':evidence['supported_routing_exact'],
                      'supported_total':len(supported),
                      'clarified_without_load':evidence['clarifications_without_load'],
                      'clarification_total':len(clarification),
                      'scalar_event_field_parity':True,'boundary_events':4,
                      'evidence':path_for_evidence(target)},indent=2))
    raise SystemExit(0 if passed==len(records) else 1)


if __name__=='__main__':
    main()
