"""Create privacy-safe sample reports and figures from a synthetic two-file pool."""

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

import polars as pl

from src.agent.orchestrator import Orchestrator
from src.ingestion.event_pool import build_event_pool

ROOT = Path(__file__).resolve().parents[1]


def generate(target=None):
    output = Path(target) if target is not None else ROOT / "docs" / "samples"
    output.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 2, 1)
    rows = 400
    data = {"timestamp": [start + timedelta(seconds=i) for i in range(rows)]}
    for head, base in (("H01", 100), ("H02", 200)):
        data[f"{head} Count"] = [base + i for i in range(rows)]
        data[f"{head} AppTorque"] = [round(1.96 + (i % 15) * 0.008, 3) for i in range(rows)]
        data[f"{head} Status"] = [2 if i < 310 else (65 if i in (320, 330) and head == "H02" else 0)
                                   for i in range(rows)]
    data["H01 AppTorque"][355] = 2.8
    with TemporaryDirectory(prefix="arol-samples-") as directory:
        temp = Path(directory)
        frame = pl.DataFrame(data)
        paths = []
        for index, part in enumerate((frame[:160], frame[160:])):
            path = temp / f"synthetic-{index}.csv"
            part.write_csv(path)
            paths.append(path)
        manifest = build_event_pool(paths, temp / "pool", machine_id="DEMO")
        cfg = {"data": {"source": "person_a_pool", "person_a": {"sample": str(manifest)}},
               "agent": {"planner": "rules", "report_dir": str(temp / "reports"),
                         "trace_dir": str(temp / "traces")},
               "analytics": {"idle_window_seconds": 300, "drift_window_seconds": 60,
                             "torque_expected_min": 1.5, "torque_expected_max": 2.5,
                             "anomaly_sigma": 3.0}}
        agent = Orchestrator(cfg=cfg)
        bounds = "for machine DEMO from 2026-02-01T00:00:00 until 2026-02-01T00:06:40"
        questions = {
            "torque-and-distribution": f"Summarize torque and show its distribution with 10 bins {bounds} for head 1 for successful closures",
            "head-kpis": f"Success rate per head {bounds}",
            "all-head-no-load": f"Machine idle {bounds}",
        }
        for stem, query in questions.items():
            answer = agent.answer(query, pool="sample")
            if answer["status"] != "ok":
                raise RuntimeError(f"{stem}: {answer['status']}: {answer.get('message')}")
            artifacts = agent.deliver(answer, formats=["markdown", "plots"])
            text = Path(artifacts["report"]).read_text(encoding="utf-8")
            for figure in artifacts.get("figures", []):
                source = Path(figure)
                destination = output / f"{stem}-{source.name}"
                shutil.copyfile(source, destination)
                relative = source.relative_to(Path(artifacts["report"]).parent).as_posix()
                text = text.replace(f"]({relative})", f"]({destination.name})")
            (output / f"{stem}.md").write_text(text, encoding="utf-8")
    return sorted(output.glob("*.md"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__ + " Overwrites docs/samples unless --output is given.")
    parser.add_argument("--output", type=Path, help="Write the samples here instead of docs/samples")
    for report in generate(parser.parse_args().output):
        print(report)
