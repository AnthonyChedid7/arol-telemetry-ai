"""Read the requested event scope from an existing partitioned pool."""
from copy import deepcopy
from pathlib import Path
import json

import polars as pl

from . import registry, schema
from ..analytics.event_filters import filter_events
from ..analytics.torque_stats import _apply_status_filter
from ..ingestion.adapter import PERSON_A_COLUMNS, adapt, describe
from ..ingestion.event_pool import scan_event_pool

SUPPORTED_TOOLS = frozenset(['compare_head_success', 'detect_torque_anomalies', 'head_correlation', 'kpi_over_time', 'machine_idle', 'observed_throughput', 'rank_heads_by_success', 'success_rate', 'success_rate_per_head', 'torque_distribution', 'torque_stats', 'torque_trend'])


class ScopeTooLarge(ValueError):
    """The requested scope exceeds the configured materialization limit."""


def _read_parameters(calls):
    # Validate one call at a time; _read_plan checks the complete combination
    # and rejects different scopes before opening any event data.
    if not isinstance(calls, (list, tuple)) or len(calls) != 1:
        raise ValueError("Partitioned event loading currently requires exactly one analysis")
    call = calls[0]
    if not isinstance(call, (list, tuple)) or len(call) != 2:
        raise ValueError("Invalid planned tool call")
    name, arguments = call
    if not isinstance(name, str) or name not in SUPPORTED_TOOLS:
        raise ValueError("This event source supports the verified torque and explicit-denominator KPI tools only")
    spec = registry.get(name)
    if spec is None:
        raise ValueError(f"Tool is not registered: {name}")
    if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
        raise ValueError("Tool arguments must be a dictionary with string keys")
    args = deepcopy(arguments)
    unknown = set(args) - set(spec.params)
    missing = set(spec.required) - set(args)
    if unknown or missing:
        raise ValueError(f"Invalid tool arguments: unsupported={sorted(unknown)}, missing={sorted(missing)}")
    checked, notes, errors = registry.coerce_params(args)
    if errors or notes or checked != args:
        raise ValueError("Read scope must use valid canonical parameters without coercion: "
                         + "; ".join(errors + notes))
    status = args.get("status_filter")
    if status is not None and not (type(status) is int or status == "successful"):
        raise ValueError("status_filter must be an integer or 'successful'")
    scope = {key: args[key] for key in ("start", "end", "head_id", "machine_id") if key in args}
    if name == "head_correlation":
        if args["head_a"] == args["head_b"]:
            raise ValueError("Head comparison requires distinct heads")
        scope["head_id"] = [args["head_a"], args["head_b"]]
    if name in {'rank_heads_by_success', 'compare_head_success'}:
        from ..analytics.registered_head_kpi import validate_population
        validate_population(args['machine_id'], args['start'], args['end'], args.get('head_id'))
        if name == 'compare_head_success':
            # This tool explicitly requests peer data; head_id denotes its
            # focus, while machine/time bound the shared comparison population.
            scope.pop('head_id')
    if name == 'machine_idle':
        from ..analytics.event_filters import _bound
        lo, hi = _bound(args['start']), _bound(args['end'])
        if lo is None or hi is None or lo >= hi or not args['machine_id'].strip():
            raise ValueError('Idle analysis requires one machine and a bounded start before end')
    return name, args, scope, status


COMBINED_TOOLS = {
    frozenset({"success_rate", "success_rate_per_head"}),
    frozenset({"torque_stats", "torque_distribution"}),
    frozenset({"torque_trend", "detect_torque_anomalies"}),
    frozenset({"compare_head_success", "detect_torque_anomalies"}),
}


def _read_plan(calls):
    if not isinstance(calls, (list, tuple)) or len(calls) not in (1, 2):
        raise ValueError("Use exactly one analysis or one supported two-tool combination")
    parsed = [_read_parameters([call]) for call in calls]
    if len(parsed) == 2:
        tools = frozenset(item[0] for item in parsed)
        if tools not in COMBINED_TOOLS:
            raise ValueError("Use exactly one analysis or a supported statistics/distribution, trend/anomalies, KPI or head-health pair")
        if tools == {"compare_head_success", "detect_torque_anomalies"}:
            comparison = next(item for item in parsed if item[0] == "compare_head_success")
            anomalies = next(item for item in parsed if item[0] == "detect_torque_anomalies")
            required = {"machine_id", "start", "end", "head_id"}
            if (set(comparison[1]) != required or comparison[1] != anomalies[1]
                    or comparison[3] is not None or anomalies[3] is not None):
                raise ValueError("Head-health checks require the same head, machine and time window without status filters")
            # The comparison needs all peer heads; the anomaly tool applies
            # head_id itself after the shared machine/time population is read.
        elif parsed[0][2:] != parsed[1][2:]:
            raise ValueError("Combined analyses must use exactly the same head, machine, time and status scope")
    return parsed


def _stat_signature(paths):
    return [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]


def _read_raw_idle(manifest_path, manifest, args, *, limit, verify_hashes):
    """Select validated per-second status rows from raw pool partitions."""
    from ..analytics.event_filters import _bound
    lo, hi = _bound(args['start']), _bound(args['end'])
    if manifest['summary']['machine_id'] != args['machine_id']:
        raise ValueError('Requested machine differs from the raw telemetry pool')
    # Also validate event partitions, manifest structure and optional hashes.
    scan_event_pool(manifest_path, verify_hashes=verify_hashes)
    paths, frames, heads = [], [], None
    root = manifest_path.parent
    for entry in manifest['partitions']:
        relative = Path(entry['raw_parquet'])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or path in paths or not path.is_file():
            raise ValueError('Invalid or duplicate raw partition in manifest')
        paths.append(path)
        frame = pl.scan_parquet(path)
        cols = frame.collect_schema()
        present = sorted(name[:-7] for name in cols if name.endswith(' Status'))
        if (not present or (heads is not None and heads != present)
                or len(present) != manifest['summary']['heads']
                or cols.get('timestamp') != pl.Datetime('us')
                or any(not (cols[f'{head} Status'].is_integer()
                            or cols[f'{head} Status'].is_float()) for head in present)):
            raise ValueError('Invalid raw status partition schema')
        heads = present
        status_valid = []
        for head in heads:
            name = f'{head} Status'
            column = pl.col(name)
            dtype = cols[name]
            valid = column.is_not_null()
            if dtype.is_float():
                # Raw CSV inference can store codes such as 2.0 as floats.
                # Mirror the builder's whole-number and exact-range checks
                # before using those values as categorical status codes.
                representable_limit = 2 ** (24 if dtype == pl.Float32 else 53) - 1
                valid = (valid & column.is_finite() & ((column % 1) == 0)
                         & (column.abs() <= representable_limit))
            elif dtype == pl.UInt64:
                valid = valid & (column <= 2**63 - 1)
            status_valid.append(valid)
        is_all_no_load = pl.all_horizontal(
            [pl.col(f'{head} Status').is_in([2, 3]) for head in heads]
        )
        frames.append(frame.filter((pl.col('timestamp') >= lo) & (pl.col('timestamp') < hi))
                      .select(pl.col('timestamp').alias('ts'),
                              pl.lit(args['machine_id']).alias('machine_id'),
                              is_all_no_load.alias('all_heads_no_load'),
                              pl.all_horizontal(status_valid).alias('_status_valid')))
    signature = _stat_signature(paths)
    selected = pl.concat(frames, how='vertical')
    n = selected.select(pl.len()).collect().item()
    if n > limit:
        raise ScopeTooLarge(f'Idle window contains {n:,} raw readings; the limit is {limit:,}. Select a shorter time window.')
    rows = selected.limit(limit + 1).collect()
    if (_stat_signature(paths) != signature or len(rows) != n):
        raise ValueError('Raw telemetry pool changed during idle analysis')
    if not rows['_status_valid'].fill_null(False).all():
        raise ValueError('Raw status must be finite, exactly representable whole numbers')
    rows = rows.drop('_status_valid')
    # No timestamp sort: source order is the temporal evidence. A backwards
    # transition fails, while duplicate/gapped timestamps break a run.
    if rows.height > 1 and (rows['ts'].diff().dt.total_microseconds() < 0).any():
        raise ValueError('Raw timestamps go backwards within the selected scope')
    meta = {
        'pool_total_events': manifest['summary']['observed_events'],
        'loaded_events': 0, 'n_events': 0, 'pool': None,
        'source': 'person_a_pool', 'n_raw_readings': n, 'heads': heads,
        'machines': [args['machine_id']], 'schema_version': '1.0',
        'ts_min': rows['ts'].min().isoformat() if n else None,
        'ts_max': rows['ts'].max().isoformat() if n else None,
        'timezone': None,
        'warnings': ['Idle candidates use raw status readings, not closure events. A missing poll, '
                     'duplicate timestamp or other status breaks continuity. These candidates '
                     'do not prove machine downtime or a physical cause.'],
    }
    return rows, meta


class EventPoolSource:
    """No whole-pool frame cache; each plan loads only its requested scope.

    All partition schemas are checked. Parquet predicate pushdown controls
    physical reads; no partition is skipped using unverified manifest dates.
    Hash verification is optional because it reads all partition bytes.
    """
    name = "person_a_pool"

    def __init__(self, cfg, *, repo_root):
        self.cfg = deepcopy(cfg)
        self.paths = {}
        for pool, value in cfg.get("data", {}).get("person_a", {}).items():
            path = Path(value)
            self.paths[pool] = (path if path.is_absolute() else Path(repo_root) / path).resolve()
        self.max_events = cfg.get("data", {}).get("max_loaded_events", 1_000_000)
        if type(self.max_events) is not int or self.max_events < 1:
            raise ValueError("data.max_loaded_events must be a positive integer")
        self.verify_hashes = cfg.get("data", {}).get("verify_pool_hashes", False)
        if type(self.verify_hashes) is not bool:
            raise ValueError("data.verify_pool_hashes must be a boolean")

    def list_pools(self):
        return sorted(self.paths)

    def describe_pool(self, pool):
        """Machine and time range from the manifest only; no event data is read.

        Used as planning context (resolving "the morning" to timestamps),
        never as report evidence.
        """
        if pool not in self.paths:
            raise KeyError(f"Unknown Person A event pool: {pool!r}")
        manifest = json.loads(self.paths[pool].read_bytes())
        partitions = manifest.get("partitions") or []
        starts = [p["ts_min"] for p in partitions if p.get("ts_min")]
        ends = [p["ts_max"] for p in partitions if p.get("ts_max")]
        machine = (manifest.get("summary") or {}).get("machine_id")
        return {
            "machines": [machine] if machine else [],
            "ts_min": min(starts) if starts else None,
            "ts_max": max(ends) if ends else None,
        }

    def load_for_plan(self, pool, calls):
        parsed = _read_plan(calls)
        population_call = next((item for item in parsed if item[0] == 'compare_head_success'), parsed[0])
        name, args, scope, status = population_call
        if name in {'kpi_over_time', 'observed_throughput'}:
            from ..analytics.registered_temporal_kpi import validate_request, TooManyTimeBuckets
            try:
                validate_request(args['machine_id'], args['start'], args['end'], args['bucket'],
                                 self.cfg.get('analytics', {}).get('max_time_buckets', 1000))
            except TooManyTimeBuckets as exc:
                raise ScopeTooLarge(str(exc)) from exc
        # Validate the complete filter on an empty schema before opening data.
        filter_events(pl.DataFrame(schema=PERSON_A_COLUMNS).lazy(), **scope)
        if pool not in self.paths:
            raise KeyError(f"Unknown Person A event pool: {pool!r}")
        manifest_path = self.paths[pool]
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if name == 'machine_idle':
            readings, meta = _read_raw_idle(manifest_path, manifest, args,
                                            limit=self.max_events, verify_hashes=self.verify_hashes)
            if manifest_path.read_bytes() != manifest_bytes:
                raise ValueError('Event-pool manifest changed during idle analysis')
            meta.update(pool=pool, event_manifest=str(manifest_path),
                        timezone=self.cfg.get('data', {}).get('timezone'),
                        selection_parameters=dict(args), loaded_raw_readings=len(readings),
                        materialization='selected raw status readings only')
            return readings, meta
        lazy = scan_event_pool(manifest_path, verify_hashes=self.verify_hashes)
        total = manifest["summary"]["observed_events"]
        partitions = manifest["partitions"]
        counts = [entry["observed_events"] for entry in partitions]
        if (type(total) is not int or total < 0 or any(type(n) is not int or n < 0 for n in counts)
                or sum(counts) != total):
            raise ValueError("Manifest event totals are inconsistent")
        paths = [(manifest_path.parent / entry["event_parquet"]).resolve() for entry in partitions]
        signatures = _stat_signature(paths)
        selected, _ = filter_events(lazy, **scope)
        selected = _apply_status_filter(selected, status)
        n_selected = selected.select(pl.len()).collect().item()
        if n_selected > self.max_events:
            raise ScopeTooLarge(
                f"Requested scope contains {n_selected:,} observed events; the configured "
                f"limit is {self.max_events:,}. Specify a narrower time window or another supported scope."
            )
        # The extra limit also bounds the collected result if underlying files
        # change between the count and the read. Such changes are rejected below.
        original = selected.limit(self.max_events + 1).collect()
        if (manifest_path.read_bytes() != manifest_bytes or _stat_signature(paths) != signatures
                or len(original) != n_selected):
            raise ValueError("Event pool changed during the scoped read; no analysis was run")
        events, notes = adapt(original, pool_id=pool, redecode=False)
        schema.validate_events(events)
        if events.is_empty():
            notes.append("No observed events matched the requested scope. Counter discontinuities "
                         "are not reconstructed; total production completeness is not measured.")
        meta = describe(events, pool_id=pool, notes=notes, source=self.name,
                        timezone=self.cfg.get("data", {}).get("timezone"))
        selection = dict(scope)
        if "status_filter" in args:
            selection["status_filter"] = status
        meta.update({
            "n_files": len(partitions), "event_manifest": str(manifest_path),
            "pool_total_events": total, "loaded_events": len(events),
            "selection_parameters": selection, "planned_tool": name if len(parsed) == 1 else None,
            "planned_arguments": args if len(parsed) == 1 else None,
            "planned_calls": [(item[0], item[1]) for item in parsed], "max_loaded_events": self.max_events,
            "materialization": "requested scope only", "verified_partition_hashes": self.verify_hashes,
        })
        if name in {'rank_heads_by_success', 'compare_head_success'}:
            meta['comparison_population'] = dict(scope)
            meta['comparison_focus_head'] = args.get('head_id')
        return events, meta
