"""Run the integrated AROL agent against an existing event-pool manifest.

From the repository root: python -m scripts.demo_agent --examples
Then pass --question '...' or use the interactive prompt.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from src.agent.orchestrator import Orchestrator
from src.common import config as config_mod

ROOT = Path(__file__).resolve().parents[1]


def resolve_manifest(requested: str | None, root: Path = ROOT) -> tuple[Path, dict]:
    """Select one existing manifest; never silently pick among multiple pools."""
    if requested is None:
        matches = sorted((root / 'data/event_pools').glob('*/manifest.json'))
        if len(matches) != 1:
            raise ValueError(f'Expected one local event pool, found {len(matches)}. '
                             'Pass --manifest PATH to select one explicitly.')
        path = matches[0]
    else:
        path = Path(requested)
        if not path.is_absolute():
            path = root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f'Event-pool manifest does not exist: {path}')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f'Invalid event-pool manifest: {path}') from exc
    if (not isinstance(record, dict) or record.get('format') != 'arol-event-pool-v1'
            or record.get('complete') is not True or not isinstance(record.get('partitions'), list)
            or not record['partitions'] or not isinstance(record.get('summary'), dict)
            or not isinstance(record['summary'].get('machine_id'), str)
            or not record['summary']['machine_id'].strip()):
        raise ValueError('Expected a complete AROL v1 event-pool manifest with one machine.')
    return path, record


def examples(manifest: dict) -> list[str]:
    machine = manifest['summary']['machine_id']
    first = next((p.get('ts_min') for p in manifest['partitions'] if p.get('ts_min')), None)
    if first is None:
        raise ValueError('The event pool has no timestamped input rows.')
    start = datetime.fromisoformat(first)
    end = start + timedelta(hours=1)
    bounds = f'for machine {machine} from {start.isoformat()} until {end.isoformat()}'
    return [f'Average torque {bounds}', f'Success rate by hour {bounds}',
            f'Observed throughput by hour {bounds}']


def answer_one(question: str, cfg: dict, output: Path, sequence: int,
               *, json_output: bool = False, plots: bool = False,
               selected_machine: str | None = None) -> tuple[str, str]:
    """Give each question separate report and trace directories to avoid collisions."""
    settings = deepcopy(cfg)
    folder = output / f'question-{sequence:03d}'
    settings['agent']['report_dir'] = str(folder / 'reports')
    settings['agent']['trace_dir'] = str(folder / 'traces')
    engine = Orchestrator(cfg=settings)
    result = engine.answer(question, pool='demo', selected_machine=selected_machine)
    artifacts = engine.deliver(result, formats=['markdown', 'plots'] if plots else ['markdown'])
    delivered = Path(artifacts['report']).read_text(encoding='utf-8')
    trace = result['trace'].to_dict()
    if json_output:
        return json.dumps({'status': result['status'], 'query': question,
                           'planner': trace['planner'],
                           'planned_calls': result['plan'].calls if result['plan'] else [],
                           'focus': getattr(result['plan'], 'focus', '') if result['plan'] else '',
                           'results': result['results'], 'message': result['message'],
                           'markdown': delivered, 'trace': trace,
                           'artifacts': artifacts}, allow_nan=False), result['status']
    return (delivered.rstrip() + '\n\n'
            + f"Status: {result['status']} | Planner: {trace['planner']} | "
            + f"Tool calls: {trace['n_tool_calls']}\n"
            + '\n'.join(f'{key}: {value}' for key, value in artifacts.items())), result['status']


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', help='Existing partitioned event-pool manifest; optional if exactly one exists')
    parser.add_argument('--demo', action='store_true',
                        help='Use the synthetic demo pool (built on first use); no private data needed')
    parser.add_argument('--question', action='append', help='Ask one question (repeat for multiple reports)')
    parser.add_argument('--examples', action='store_true', help='Print three scoped questions for this pool')
    parser.add_argument('--planner', choices=('rules', 'diagnostic', 'llm', 'claude'), default='rules',
                        help='Rules by default; diagnostic explains scoped head evidence; llm is experimental '
                             'single-torque planning; claude understands free-form questions (needs ANTHROPIC_API_KEY)')
    parser.add_argument('--json', action='store_true', help='Output one JSON object per question')
    parser.add_argument('--plots', action='store_true', help='Save PNG figures and embed links in reports where tool outputs permit')
    parser.add_argument('--output', type=Path, help='Parent directory for a new isolated report session')
    args = parser.parse_args(argv)
    if Path.cwd().resolve() != ROOT:
        parser.error('Run from the repository root.')
    if args.json and not args.question and not args.examples:
        parser.error('--json requires at least one --question')
    if args.demo and args.manifest:
        parser.error('Use either --demo or --manifest, not both')
    if args.demo:
        from scripts.build_demo_pool import build
        args.manifest = str(build())
    try:
        path, record = resolve_manifest(args.manifest)
    except ValueError as exc:
        parser.error(f'{exc} (or pass --demo to use the synthetic demo data)')
    if args.examples:
        try:
            for item in examples(record):
                print(item)
        except ValueError as exc:
            parser.error(str(exc))
        return 0

    cfg = config_mod.load()
    cfg.setdefault('data', {}).update(source='person_a_pool', person_a={'demo': str(path)})
    cfg.setdefault('agent', {})['planner'] = args.planner
    parent = (args.output or (ROOT / 'data' / 'demo_runs')).resolve()
    output = parent / ('session-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
                       + '-' + uuid4().hex[:8])
    errors = 0
    questions = args.question or []
    index = 0
    if not questions:
        print(f"Machine: {record['summary']['machine_id']} | Planner: {args.planner}")
        print('Enter a scoped question, or type exit. Use --examples for sample questions.')
    while True:
        if not questions:
            try:
                query = input('AROL> ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if query.lower() in {'quit', 'exit'}:
                break
            if not query:
                continue
        else:
            if index == len(questions):
                break
            query = questions[index].strip()
            if not query:
                parser.error('--question cannot be empty')
        index += 1
        response, status = answer_one(query, cfg, output, index, json_output=args.json, plots=args.plots)
        print(response, flush=True)
        if status != 'ok':
            errors += 1
    return 0 if not errors else 2


if __name__ == '__main__':
    sys.exit(main())
