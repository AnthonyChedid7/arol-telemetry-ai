"""Build a partitioned event pool and optionally compare a prior counter audit.

Run from the repository root with python -m scripts.build_event_pool --help.
"""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.event_pool import build_event_pool, scan_event_pool, sha256_file


ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def _display(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def _append_checkpoint(heading, entry):
    for name in ("PROJECT-AUDIT.md", "INTEGRATION-TRACKER.md"):
        path = ROOT / "docs" / name
        text = path.read_text(encoding="utf-8")
        if heading not in text:
            path.write_text(text.rstrip() + "\n\n" + heading + "\n\n" + entry + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--pattern", required=True, help="Repository-relative CSV glob, in chronological filename order")
    parser.add_argument("--audit", type=Path, help="Existing full counter audit JSON")
    parser.add_argument("--output", type=Path, help="New output directory; existing directories are never overwritten")
    parser.add_argument("--record", action="store_true",
                        help="Also save evidence under benchmarks/integration/ and append a checkpoint to "
                             "docs/PROJECT-AUDIT.md and docs/INTEGRATION-TRACKER.md (changes tracked files)")
    args = parser.parse_args(argv)
    if Path.cwd().resolve() != ROOT:
        parser.error("Run this command from the repository root")
    paths = sorted(ROOT.glob(args.pattern))
    if not paths:
        parser.error("No input CSV files matched")
    audit = None
    if args.audit:
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        summary = audit["summary"]
        if summary["machine_id"] != args.machine_id or summary["files"] != len(paths):
            parser.error("Audit machine or file count does not match the selected inputs")
        audited_names = [entry["file"] for entry in audit["per_file"]]
        if sorted(audited_names) != [path.name for path in paths]:
            parser.error("Selected input filenames differ from the counter audit")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output = args.output or ROOT / "data" / "event_pools" / f"continuous-{stamp}"
    source_paths = [
        "src/ingestion/event_pool.py", "src/ingestion/conversion.py",
        "src/ingestion/event_table_polars.py", "src/ingestion/closure_detection_polars.py",
        "src/ingestion/event_assembly_polars.py", "scripts/build_event_pool.py",
    ]
    code_hashes = {name: sha256_file(ROOT / name) for name in source_paths}
    before = {"git_commit": _git("rev-parse", "HEAD"), "git_status_before_run": _git("status", "--short")}
    print(f"Building {len(paths)} files for {args.machine_id}", flush=True)
    manifest_path = build_event_pool(paths, output, machine_id=args.machine_id,
                                     progress=lambda message: print(message, flush=True))
    if any(sha256_file(ROOT / name) != value for name, value in code_hashes.items()):
        raise RuntimeError("Source code changed during the build; verification stopped")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Schema validation is lazy and does not collect the full event pool.
    scan_event_pool(manifest_path).collect_schema()
    checks = {}
    if audit is not None:
        keys = ["machine_id", "files", "heads", "input_rows", "exact_plus_one",
                "holds", "jump_rows", "decreases", "boundary_exact_plus_one",
                "boundary_jump_rows", "boundary_decreases"]
        for key in keys:
            actual, expected = manifest["summary"][key], audit["summary"][key]
            checks[key] = {"expected": expected, "actual": actual, "match": actual == expected}
    verified = all(item["match"] for item in checks.values()) if checks else None
    try:
        displayed_manifest = manifest_path.relative_to(ROOT).as_posix()
    except ValueError:
        displayed_manifest = str(manifest_path)
    evidence = {
        **before, "source_code_sha256": code_hashes,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": displayed_manifest, "manifest_sha256": sha256_file(manifest_path),
        "summary": manifest["summary"], "counter_audit_verified": verified,
        "audit_checks": checks,
        "prior_audit_sha256": sha256_file(args.audit) if args.audit else None,
        "partitions": [{key: entry[key] for key in (
            "input_file", "input_sha256", "event_parquet", "event_sha256",
            "input_rows", "observed_events", "boundary_counts",
        )} for entry in manifest["partitions"]],
        "limits": [
            "Count agreement with the prior audit is not independent verification of every event value.",
            "Continuous-reference field parity is checked by controlled-data tests.",
            "Only observed exact +1 events; total physical production is not established.",
            "This build does not run the agent or an LLM.",
            "One raw file plus event-building workspace; peak memory has not been measured.",
            "Raw and event Parquet are retained. This batch builds a fresh pool; no resume/cache reuse yet.",
        ],
    }
    # Without --record the evidence stays next to the built pool (ignored data/).
    evidence_dir = ROOT / "benchmarks" / "integration" if args.record else output
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"continuous_event_pool_{stamp}.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    heading = f"## Integration measurement — continuous event pool {stamp}"
    s = manifest["summary"]
    entry = (
        f"- Source commit: {before['git_commit']}; working-tree state recorded in evidence.\n"
        f"- Files: {s['files']}; raw rows: {s['input_rows']}; heads: {s['heads']}.\n"
        f"- Observed exact +1 events: {s['observed_events']}.\n"
        f"- Cross-file exact +1 events retained: {s['boundary_exact_plus_one']}.\n"
        f"- Agreement with prior full counter audit: {verified}.\n"
        f"- Evidence: {_display(evidence_path)}.\n"
        f"- Pool: {displayed_manifest}.\n\n"
        "The first observation is the pool baseline. Later partitions carry the preceding "
        "observation. Empty files preserve it. Discontinuities are measured, not reconstructed. "
        "No source event or decoding function was changed. Timestamps remain as stored.\n\n"
        "Limits: this is a data-build verification, not agent execution or a measured memory "
        "benchmark. The current PersonASource still reads a single event file eagerly.\n\n"
        "Next action: inspect audit comparison results, then connect manifest-backed, "
        "scope-filtered event loading to the orchestrator."
    )
    if args.record:
        _append_checkpoint(heading, entry)
    print("\nCONTINUOUS EVENT POOL SUMMARY")
    print(json.dumps(s, indent=2))
    print(f"\nCounter-audit agreement: {verified}")
    print(f"Manifest: {displayed_manifest}")
    print(f"Evidence: {_display(evidence_path)}")
    print(("Updated both audit documents. " if args.record else "Tracked files unchanged (use --record to save evidence). ")
          + "No agent or live model request was run.")
    if verified is False:
        print(json.dumps({key: value for key, value in checks.items() if not value['match']}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
