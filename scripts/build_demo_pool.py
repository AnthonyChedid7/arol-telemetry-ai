"""Build a synthetic DEMO event pool so anyone can try the project.

The real AROL telemetry is private and not in this repository. This script
generates one day of made-up telemetry in the same CSV format (timestamp plus
"Hnn Count / AppTorque / Status" columns, one row per second) and runs it
through the normal pool builder, so every question, tool, report and plot can
be tried without private data. Nothing here is AROL data.

Planted signals a tester can look for (machine DEMO01, 2026-02-01, UTC):
  * H07 is a weak head: about 3% of its closures are rejects (status 65).
  * H12 drifts: its torque rises by about 0.1 Nm per hour from 08:00 to 12:00.
  * H03 has three over-limit closures (2.8 Nm) at 10:15, 14:30 and 18:45.
  * 01:00-01:30 the whole machine runs without caps (all-head No Load).
  * A few events carry the previous cycle's status, like the real controller.

The output is deterministic (fixed seed). From the repository root:
    python -m scripts.build_demo_pool            # builds data/demo_pool/ if missing
    python -m scripts.build_demo_pool --rebuild
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import stat

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "data" / "demo_pool"
MACHINE_ID = "DEMO01"
DAY = datetime(2026, 2, 1)
HEADS = [f"H{n:02d}" for n in range(1, 37)]
SEED = 2026


def _telemetry(seconds: int = 86_400, seed: int = SEED) -> pl.DataFrame:
    """One row per second, one Count/AppTorque/Status triple per head."""
    rng = np.random.default_rng(seed)
    t = np.arange(seconds)
    hour = t / 3600.0
    # The rotary machine closes on every head roughly every 3.24 s, in step:
    # like the real files, all heads increment in the same second.
    period = np.where(rng.random(seconds) < 0.76, 3, 4)
    ticks = np.cumsum(period)
    ticks = ticks[ticks < seconds - 1]
    idle = (hour >= 1.0) & (hour < 1.5)
    columns = {"timestamp": [DAY + timedelta(seconds=int(s)) for s in t]}
    counts, torques, statuses = {}, {}, {}
    for index, head in enumerate(HEADS):
        cycle_rows = ticks
        n = len(cycle_rows)
        cycle_hour = hour[cycle_rows]
        no_load = (rng.random(n) < 0.40) | idle[cycle_rows]
        status = np.where(no_load, 2, 0)
        torque = np.where(no_load, np.abs(rng.normal(0.0008, 0.002, n)),
                          rng.normal(2.0, 0.03, n))
        reject_rate = 0.03 if head == "H07" else 0.0002
        rejects = ~no_load & (rng.random(n) < reject_rate)
        status = np.where(rejects, 65, status)
        torque = np.where(rejects, rng.normal(1.75, 0.08, n), torque)
        if head == "H07":
            torque = np.where(status == 0, torque - 0.05, torque)
        if head == "H12":
            ramp = np.clip(cycle_hour - 8.0, 0, 4) * 0.1
            torque = np.where(status != 2, torque + ramp, torque)
        if head == "H03":
            for at in (10.25, 14.5, 18.75):
                closures = np.flatnonzero((status == 0) & (cycle_hour >= at))
                if len(closures):
                    torque[closures[0]] = 2.8
        # Controller lag: a few No Load cycles still report the previous OK status.
        lag = (status == 2) & ~idle[cycle_rows] & (rng.random(n) < 0.0005)
        status = np.where(lag, 0, status)
        torque = np.round(torque, 3)
        count = np.zeros(seconds, dtype=np.int64)
        count[cycle_rows] = 1
        counts[head] = (100_000 + np.cumsum(count)).astype(np.float64)
        # Torque and status hold their last closure's value between closures.
        value_index = np.searchsorted(cycle_rows, t, side="right") - 1
        held = value_index >= 0
        torques[head] = np.where(held, torque[np.clip(value_index, 0, None)], 0.0)
        statuses[head] = np.where(held, status[np.clip(value_index, 0, None)], 2).astype(np.float64)
    for head in HEADS:
        columns[f"{head} Count"] = counts[head]
    for head in HEADS:
        columns[f"{head} AppTorque"] = torques[head]
    for head in HEADS:
        columns[f"{head} Status"] = statuses[head]
    return pl.DataFrame(columns)


def build(target: Path | None = None, *, seconds: int = 86_400, rebuild: bool = False) -> Path:
    """Return the demo pool's manifest, generating it first if needed."""
    from src.ingestion.event_pool import build_event_pool

    target = Path(target) if target is not None else DEMO_DIR
    manifest = target / "pool" / "manifest.json"
    if manifest.is_file() and not rebuild:
        return manifest
    if target.exists():
        def _writable_retry(function, path, _):
            # Windows can mark folders read-only, which blocks deletion.
            os.chmod(path, stat.S_IWRITE)
            function(path)
        try:
            shutil.rmtree(target, onexc=_writable_retry)
        except PermissionError as exc:
            raise SystemExit(f"Cannot rebuild {target}: a running website or analysis has its "
                             "files open. Close it and try again.") from exc
    raw_dir = target / "raw"
    raw_dir.mkdir(parents=True)
    frame = _telemetry(seconds)
    half = seconds // 2
    paths = []
    for part, chunk in enumerate((frame[:half], frame[half:]), start=1):
        path = raw_dir / f"telemetry_{MACHINE_ID}_{DAY:%Y-%m-%d}_part{part}.csv"
        chunk.with_columns(pl.col("timestamp").dt.strftime("%Y-%m-%dT%H:%M:%S.000")).write_csv(path)
        paths.append(path)
    return build_event_pool(paths, target / "pool", machine_id=MACHINE_ID)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="Regenerate even if the demo pool exists")
    parser.add_argument("--output", type=Path, help="Folder for the demo pool (default data/demo_pool)")
    args = parser.parse_args(argv)
    manifest = build(args.output, rebuild=args.rebuild)
    print(f"Demo pool ready: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
