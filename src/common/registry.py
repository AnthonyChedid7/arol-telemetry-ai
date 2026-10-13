"""The shared tool registry.

Audit finding F3: the orchestrator has to hand the planner a list of callable
tools with typed parameters. A bare `def f(events, **params)` signature gives
us nothing to generate that from, and a hand-maintained parallel list of
schemas drifts from the code within a week.

Both sides register here. Person A registers ingestion/statistical tools,
Person B registers KPI tools; `get_tool_specs()` produces the planner's tool
list from the same declarations the functions are actually called with, so the
two cannot disagree.

Audit finding F4: parameter names are frozen in PARAM_VOCABULARY. Every tool
that takes a time window calls it `start`/`end`, every tool that takes a head
calls it `head_id`. ALIASES absorbs the obvious near-misses an LLM will emit.
"""

from __future__ import annotations

from typing import Callable

from . import envelope as env

# --- the frozen parameter vocabulary (F4) ---------------------------------

# The histogram does one pass over the events per bin, so the count is bounded:
# an unbounded value let a single question hold the demo for minutes.
MAX_BINS = 200

PARAM_VOCABULARY: dict[str, dict] = {
    "bins": {"type": "integer", "minimum": 1, "maximum": MAX_BINS, "default": 10,
             "description": f"Number of equal-width histogram bins (1 to {MAX_BINS})."},
    "head_a": {"type": "string", "minLength": 1,
               "description": "First head identifier, for example H01."},
    "head_b": {"type": "string", "minLength": 1,
               "description": "Second, distinct head identifier, for example H05."},
    "status_filter": {
        "oneOf": [
            {"type": "null"},
            {"type": "string", "enum": ["successful"]},
            {"type": "integer"},
        ],
        "description": (
            "Omit or use null for all statuses; successful means status 0; "
            "an integer selects that exact status code."
        ),
    },
    "start": {"type": "string",
              "description": "Window start, ISO-8601, plant-local. Inclusive."},
    "end": {"type": "string",
            "description": "Window end, ISO-8601, plant-local. Exclusive."},
    "head_id": {"type": ["string", "array"],
                "description": "Head, e.g. 'H05', or a list of heads. Omit for all heads."},
    "machine_id": {"type": "string",
                   "description": "Machine identifier, e.g. 'MCC777'. Omit for all machines."},
    "pool": {"type": "string",
             "description": "Which data pool to analyse, e.g. 'feb'."},
    "bucket": {"type": "string", "enum": ["hour", "day"],
               "description": "Time granularity to group by."},
    "cap_present_only": {"type": "boolean",
                         "description": "Restrict to closures where a cap was actually applied."},
    "min_n": {"type": "integer",
              "description": "Suppress groups with fewer than this many events."},
    "sigma": {"type": "number",
              "description": "Standard deviations from the mean before flagging an anomaly."},
    "window_seconds": {"type": "integer",
                       "description": "Rolling window length in seconds."},
}

# What a planner is likely to say -> what the tools actually accept.
ALIASES: dict[str, str] = {
    "head": "head_id", "head_no": "head_id", "heads": "head_id",
    "head_ids": "head_id", "machine": "machine_id", "from": "start",
    "to": "end", "since": "start", "until": "end", "from_date": "start",
    "to_date": "end", "start_time": "start", "end_time": "end",
    "granularity": "bucket", "group_by": "bucket", "freq": "bucket",
    "interval": "bucket", "threshold_sigma": "sigma", "n_sigma": "sigma",
    "dataset": "pool", "pool_id": "pool",
}

# Which named agent owns a tool (F10 - the MAS framing).
AGENTS = ("ingestion", "cleaning", "analytics", "kpi")

_REGISTRY: dict[str, "ToolSpec"] = {}


class ToolSpec:
    def __init__(self, fn: Callable, name: str, description: str,
                 params: list[str], agent: str, owner: str,
                 required: list[str]):
        self.fn = fn
        self.name = name
        self.description = description
        self.params = params
        self.agent = agent
        self.owner = owner
        self.required = required

    def json_schema(self) -> dict:
        """The tool definition handed to an LLM for function calling."""
        props = {p: dict(PARAM_VOCABULARY[p]) for p in self.params}
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


def tool(*, name: str, description: str, params: list[str],
         agent: str = "analytics", owner: str = "B",
         required: list[str] | None = None):
    """Register an analysis function as an agent-callable tool.

    Every parameter must already exist in PARAM_VOCABULARY - that is what
    stops the two halves of the project inventing divergent names.
    """
    def decorate(fn: Callable) -> Callable:
        unknown = [p for p in params if p not in PARAM_VOCABULARY]
        if unknown:
            raise KeyError(
                f"{name}: parameters {unknown} are not in PARAM_VOCABULARY. "
                "Add them there first so both sides agree on the name."
            )
        if agent not in AGENTS:
            raise ValueError(f"{name}: agent must be one of {AGENTS}")
        if name in _REGISTRY:
            raise KeyError(f"tool {name!r} is already registered")
        _REGISTRY[name] = ToolSpec(fn, name, description, params, agent,
                                   owner, required or [])
        fn.tool_name = name
        return fn
    return decorate


def normalise_params(params: dict) -> tuple[dict, list[str]]:
    """Map aliases onto canonical names. Returns (params, renames_applied)."""
    out, renames = {}, []
    for key, value in params.items():
        canonical = ALIASES.get(key, key)
        if canonical != key:
            renames.append(f"{key}->{canonical}")
        out[canonical] = value
    return out, renames


# Enum values a small model is likely to produce for `bucket`.
_BUCKET_SYNONYMS = {"hourly": "hour", "hours": "hour", "daily": "day",
                    "days": "day", "weekly": "week", "weeks": "week",
                    "shifts": "shift", "per_shift": "shift"}

_TRUTHY = {"true", "yes", "y", "1", "on"}
_FALSY = {"false", "no", "n", "0", "off"}

# A model asked for an optional argument it does not want to set will often
# fill the slot with a placeholder rather than omit the key. llama3.2:3b sends
# the STRING "null" for min_n and sigma. These mean "not supplied".
_NULLISH = {"null", "none", "nil", "undefined", "n/a", "na", "nan", "-", ""}


def _coerce_one(key: str, value, declared):
    """Coerce one value to its declared JSON-Schema type.

    Returns (value, note); raises ValueError with a readable message when the
    value cannot be honoured at all.
    """
    types = declared if isinstance(declared, list) else [declared]

    if "array" in types and isinstance(value, (list, tuple)):
        return list(value), None

    if "integer" in types or "number" in types:
        if isinstance(value, bool):
            raise ValueError(f"{key}={value!r} is a boolean, not a number")
        want_int = "integer" in types
        try:
            number = float(value)
        except (TypeError, ValueError):
            kind = "an integer" if want_int else "a number"
            raise ValueError(f"{key}={value!r} is not {kind}") from None
        if want_int:
            if not number.is_integer():
                raise ValueError(f"{key}={value!r} is not a whole number")
            number = int(number)
        if type(number) is type(value) and number == value:
            return number, None
        return number, f"{key} {value!r} coerced to {number!r}"

    if "boolean" in types:
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in _TRUTHY:
            return True, f"{key} {value!r} coerced to True"
        if text in _FALSY:
            return False, f"{key} {value!r} coerced to False"
        raise ValueError(f"{key}={value!r} is not a boolean")

    if "string" in types:
        text = value if isinstance(value, str) else str(value)
        enum = PARAM_VOCABULARY.get(key, {}).get("enum")
        if enum:
            lowered = text.strip().lower()
            mapped = _BUCKET_SYNONYMS.get(lowered, lowered)
            if mapped not in enum:
                raise ValueError(f"{key}={value!r} is not one of {enum}")
            if mapped == value:
                return mapped, None
            return mapped, f"{key} {value!r} coerced to {mapped!r}"
        if text == value:
            return text, None
        return text, f"{key} {value!r} coerced to a string"

    return value, None


def coerce_params(params: dict) -> tuple[dict, list[str], list[str]]:
    """Enforce the types the tool schemas advertise to the planner.

    The registry hands the model a JSON Schema saying `min_n` is an integer,
    and then never checked what came back. The first live run of a 3B model
    answered with the STRING "10", which sailed through dispatch and died
    inside the tool as `TypeError: not supported between int and str` - a
    stack trace about the tool's internals for what is really a bad argument.

    A declared type that is never enforced is a comment. Returns
    (params, notes, errors).
    """
    out: dict = {}
    notes: list[str] = []
    errors: list[str] = []
    for key, value in params.items():
        if key == "bins":
            if type(value) is not int or not 0 < value <= MAX_BINS:
                errors.append(f"bins={value!r} must be an integer from 1 to {MAX_BINS}")
                continue
            out[key] = value
            continue
        if key in {"start", "end", "head_id", "machine_id", "head_a", "head_b"}:
            values = (
                value if key == "head_id" and isinstance(value, list)
                else [value]
            )
            if not values or any(
                not isinstance(item, str)
                or item.strip().lower() in _NULLISH
                for item in values
            ):
                errors.append(
                    f"{key}={value!r} is not a valid explicit scope; "
                    "omit the argument if no filter is intended"
                )
                continue
            out[key] = value
            continue
        if isinstance(value, str) and value.strip().lower() in _NULLISH:
            notes.append(f"{key}={value!r} treated as not supplied")
            continue
        declared = PARAM_VOCABULARY.get(key, {}).get("type")
        if declared is None:
            out[key] = value
            continue
        try:
            coerced, note = _coerce_one(key, value, declared)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        out[key] = coerced
        if note:
            notes.append(note)
    return out, notes, errors


def get_tool_specs(agent: str | None = None) -> list[dict]:
    """Tool definitions for the planner, optionally filtered to one agent."""
    return [s.json_schema() for s in _REGISTRY.values()
            if agent is None or s.agent == agent]


def list_tools() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str):
    return _REGISTRY.get(name)


def call_tool(name: str, events, **params) -> dict:
    """Invoke a registered tool and guarantee a well-formed envelope.

    Never raises. A missing tool, a bad parameter, or a bug inside the tool
    all come back as a failure envelope, because the orchestrator has to be
    able to degrade and still produce a report.
    """
    spec = _REGISTRY.get(name)
    if spec is None:
        return env.failure(
            f"no such tool {name!r}; available: {', '.join(list_tools())}",
            tool=name, params=params,
        )

    params, renames = normalise_params(params)
    unknown = [p for p in params if p not in spec.params]
    if unknown:
        return env.failure(
            f"{name} does not accept {unknown}; it accepts {spec.params}",
            tool=name, params=params,
        )
    params, coercions, type_errors = coerce_params(params)
    if type_errors:
        return env.failure(f"{name}: " + "; ".join(type_errors),
                           tool=name, params=params)
    missing = [p for p in spec.required if p not in params]
    if missing:
        return env.failure(f"{name} requires {missing}", tool=name,
                           params=params)

    try:
        with env.Timer() as timer:
            result = spec.fn(events, **params)
    except Exception as exc:  # a tool bug must not kill the whole report
        return env.failure(f"{type(exc).__name__}: {exc}", tool=name,
                           params=params)

    if not isinstance(result, dict) or "meta" not in result:
        return env.failure(
            f"{name} returned {type(result).__name__}, not an envelope",
            tool=name, params=params,
        )

    # Fill in what the tool did not have to bother with itself.
    meta = result["meta"]
    meta["tool"] = meta.get("tool") or name
    meta["agent"] = spec.agent
    meta["params"] = env.jsonable(params)
    meta["elapsed_ms"] = round(timer.ms, 2)
    if renames or coercions:
        applied = list(meta.get("filters_applied", []))
        applied += [f"param alias {r}" for r in renames]
        applied += [f"param type: {c}" for c in coercions]
        meta["filters_applied"] = applied
    return result
