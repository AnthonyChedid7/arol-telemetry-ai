"""Generate 50 days of made-up telemetry CSVs for testing multi-day questions.

Same format as the real AROL daily exports (timestamp plus "Hnn Count /
AppTorque / Status" for 36 heads, one reading per second), but each file holds
only one production hour (08:00-09:00 UTC) so 50 days stay small (about 2 MB
each). Nothing here is AROL data. Machine TEST50, dates 2026-03-01 to
2026-04-20 with 2026-04-02 left out (50 files, one missing day).

Planted signals to find:
  * H09 wears out: its reject rate rises from about 0.1% on day 1 to about 5% on day 50.
  * H21 has three bad days: 2026-03-10, 2026-03-25 and 2026-04-10 (about 8% rejects).
  * H14 drifts upward by about 0.3 Nm per hour on 2026-03-30.
  * H03 has two over-limit closures (2.8 Nm) on 2026-03-05.
  * 2026-03-12, 08:20-08:35: the whole machine runs without caps (idle).
  * 2026-04-02 has no file (a gap in the data).

From the repository root:
    python -m scripts.build_test_days                 # writes data/test_days/
    python -m scripts.build_test_days --pool          # also builds data/test_days_pool/
Analyse them on the website under "My own CSV files", or with
AROL_MANIFEST=data/test_days_pool/manifest.json.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import shutil

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "test_days"
MACHINE_ID = "TEST50"
HEADS = [f"H{n:02d}" for n in range(1, 37)]
FIRST_DAY = date(2026, 3, 1)
SKIPPED = date(2026, 4, 2)
BAD_H21_DAYS = {date(2026, 3, 10), date(2026, 3, 25), date(2026, 4, 10)}
DRIFT_DAY, OVER_LIMIT_DAY, IDLE_DAY = date(2026, 3, 30), date(2026, 3, 5), date(2026, 3, 12)
SECONDS = 3600
START_HOUR = 8


def days_to_build():
    days, day = [], FIRST_DAY
    while len(days) < 50:
        if day != SKIPPED:
            days.append(day)
        day += timedelta(days=1)
    return days


def _day(day, index, counts, rng):
    t = np.arange(SECONDS)
    minute = t / 60.0
    period = np.where(rng.random(SECONDS) < 0.76, 3, 4)
    ticks = np.cumsum(period)
    ticks = ticks[ticks < SECONDS - 1]
    idle = (day == IDLE_DAY) & (minute >= 20) & (minute < 35)
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=START_HOUR)
    columns = {"timestamp": [(start + timedelta(seconds=int(s))).strftime("%Y-%m-%dT%H:%M:%S.000") for s in t]}
    count_cols, torque_cols, status_cols = {}, {}, {}
    for head in HEADS:
        n = len(ticks)
        no_load = (rng.random(n) < 0.40) | idle[ticks]
        reject_rate = 0.0002
        if head == "H09":
            reject_rate = 0.001 + 0.049 * index / 49
        if head == "H21" and day in BAD_H21_DAYS:
            reject_rate = 0.08
        rejects = ~no_load & (rng.random(n) < reject_rate)
        status = np.where(no_load, 2, np.where(rejects, 65, 0))
        torque = np.where(no_load, np.abs(rng.normal(0.0008, 0.002, n)),
                          np.where(rejects, rng.normal(1.75, 0.08, n), rng.normal(2.0, 0.03, n)))
        if head == "H14" and day == DRIFT_DAY:
            torque = np.where(status != 2, torque + 0.3 * ticks / 3600.0, torque)
        if head == "H03" and day == OVER_LIMIT_DAY:
            closures = np.flatnonzero(status == 0)
            torque[closures[[len(closures) // 3, 2 * len(closures) // 3]]] = 2.8
        torque = np.round(torque, 3)
        step = np.zeros(SECONDS, dtype=np.int64)
        step[ticks] = 1
        count_cols[head] = counts[head] + np.cumsum(step)
        counts[head] = int(count_cols[head][-1])
        held = np.searchsorted(ticks, t, side="right") - 1
        torque_cols[head] = np.where(held >= 0, torque[np.clip(held, 0, None)], 0.0)
        status_cols[head] = np.where(held >= 0, status[np.clip(held, 0, None)], 2)
    for head in HEADS:
        columns[f"{head} Count"] = count_cols[head]
    for head in HEADS:
        columns[f"{head} AppTorque"] = torque_cols[head]
    for head in HEADS:
        columns[f"{head} Status"] = status_cols[head]
    return pl.DataFrame(columns)


def build(out=OUT, *, pool=False, seed=50):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rng = np.random.default_rng(seed)
    counts = {head: 100_000 for head in HEADS}
    paths = []
    for index, day in enumerate(days_to_build()):
        path = out / f"telemetry_{MACHINE_ID}_{day.isoformat()}.csv"
        _day(day, index, counts, rng).write_csv(path)
        paths.append(path)
    manifest = None
    if pool:
        from src.ingestion.event_pool import build_event_pool
        pool_dir = out.parent / "test_days_pool"
        if pool_dir.exists():
            shutil.rmtree(pool_dir)
        manifest = build_event_pool(paths, pool_dir, machine_id=MACHINE_ID)
    return paths, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=OUT, help="Folder for the CSV files (default data/test_days)")
    parser.add_argument("--pool", action="store_true", help="Also build an event pool next to the folder")
    args = parser.parse_args(argv)
    paths, manifest = build(args.output, pool=args.pool)
    size = sum(p.stat().st_size for p in paths) / 1e6
    print(f"Wrote {len(paths)} files ({size:.0f} MB) to {args.output}")
    if manifest:
        print(f"Event pool: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
