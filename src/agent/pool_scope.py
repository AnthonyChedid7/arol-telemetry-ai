"""Validate proposed scope against manifest metadata before loading events."""

from datetime import datetime


def validate_pool_scope(calls, context):
    summary = context.get("pool_summary") or {}
    known = summary.get("machines") or ([context["selected_machine"]]
                                        if context.get("selected_machine") else [])
    heads = set(summary.get("heads") or [])
    lo = datetime.fromisoformat(summary["ts_min"]) if summary.get("ts_min") else None
    hi = datetime.fromisoformat(summary["ts_max"]) if summary.get("ts_max") else None
    for _, args in calls:
        machine = args.get("machine_id")
        if machine is not None and known and machine not in known:
            raise ValueError(f"machine {machine!r} is not in this data pool "
                             f"(available: {', '.join(known)})")
        requested = [args.get("head_a"), args.get("head_b")]
        requested += args["head_id"] if isinstance(args.get("head_id"), list) else [args.get("head_id")]
        missing = sorted(h for h in requested if h is not None and heads and h not in heads)
        if missing:
            raise ValueError(f"head {', '.join(missing)} is not in this data pool "
                             f"(heads {min(heads)} to {max(heads)})")
        start = datetime.fromisoformat(args["start"]) if "start" in args else None
        end = datetime.fromisoformat(args["end"]) if "end" in args else None
        if ((lo is not None and end is not None and end <= lo)
                or (hi is not None and start is not None and start > hi)):
            raise ValueError("the requested time window is outside the recorded data "
                             f"({summary['ts_min']} to {summary['ts_max']})")
