"""Strict validation of model tool proposals; never delete or repair arguments."""
import json
import re
from datetime import datetime

from ..common.registry import MAX_BINS

VERIFIED_TOOLS = frozenset({
    "torque_stats", "torque_distribution", "torque_trend",
    "detect_torque_anomalies", "head_correlation",
})


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _proposal_message(message):
    """Accept one complete JSON proposal only when native calls are absent.

    Native tool calls remain authoritative. Ordinary prose is never searched
    for embedded JSON, and no parameter is removed, coerced or supplied.
    """
    if not isinstance(message, dict):
        raise ValueError("Model message must be an object")
    native = message.get("tool_calls")
    if native is not None and native != []:
        return message
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return message
    text = content.strip()
    if text.startswith("```"):
        import re
        fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", text, re.I)
        if fenced is None:
            raise ValueError("Expected one complete JSON fence without surrounding prose")
        text = fenced.group(1)
    try:
        proposal = json.loads(text, object_pairs_hook=_json_object)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid JSON tool proposal: {exc}") from exc
    if not isinstance(proposal, dict) or set(proposal) != {"name", "arguments"}:
        raise ValueError("JSON proposal must contain exactly name and arguments")
    return {"tool_calls": [{"function": proposal}]}


def strict_calls(message, lookup):
    """Require one registered, verified tool with canonical, typed parameters."""
    if not isinstance(message, dict):
        raise ValueError("Model message must be an object")
    message = _proposal_message(message)
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or len(raw_calls) != 1:
        raise ValueError("Exactly one tool call is required")
    raw = raw_calls[0]
    if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
        raise ValueError("Tool call must contain a function object")
    function = raw["function"]
    name = function.get("name")
    if not isinstance(name, str) or name not in VERIFIED_TOOLS:
        raise ValueError("Model selected an unsupported tool")
    spec = lookup(name)
    if spec is None:
        raise ValueError("Model selected an unregistered tool")
    if "arguments" not in function:
        raise ValueError("Missing tool arguments object")
    args = function["arguments"]
    if isinstance(args, str):
        args = json.loads(args, object_pairs_hook=_json_object)
    if not isinstance(args, dict) or any(not isinstance(key, str) for key in args):
        raise ValueError("Tool arguments must be an object with string keys")
    unknown = set(args) - set(spec.params)
    if unknown:
        raise ValueError("Unsupported argument names: " + ", ".join(sorted(unknown)))
    missing = set(spec.required) - set(args)
    if missing:
        raise ValueError("Missing required arguments: " + ", ".join(sorted(missing)))
    bounds = {}
    for key, value in args.items():
        if key == "bins":
            if type(value) is not int or not 0 < value <= MAX_BINS:
                raise ValueError(f"bins must be an integer from 1 to {MAX_BINS}")
        elif key == "status_filter":
            if not (type(value) is int or value == "successful"):
                raise ValueError("status_filter must be an integer or 'successful'; omit unused filters")
        elif key in {"head_id", "head_a", "head_b", "machine_id", "start", "end"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
            if key in {"start", "end"}:
                bound = datetime.fromisoformat(value)
                if bound.tzinfo is not None:
                    raise ValueError("Timezone-aware bounds are not supported")
                bounds[key] = bound
        else:
            raise ValueError(f"Argument has no verified validation rule: {key}")
    if "start" in bounds and "end" in bounds and bounds["start"] >= bounds["end"]:
        raise ValueError("start must be earlier than end")
    if name == "head_correlation" and args["head_a"] == args["head_b"]:
        raise ValueError("Head comparison requires distinct heads")
    return [(name, dict(args))]


_ISO_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
MAX_STATUS_CODE = 0xFFFF  # AROL status words are 16-bit fields


def _canonical_head(key, value):
    if (not isinstance(value, str) or not value.startswith("H") or not value[1:].isdigit()
            or int(value[1:]) < 1 or value != f"H{int(value[1:]):02d}"):
        raise ValueError(f"{key} must be a canonical head identifier such as H05, got {value!r}")


def validate_tool_call(name, args, lookup):
    """Check one proposed call against any registered tool. Never repairs values.

    The single exception is status_filter=None: the schema documents null as
    "all statuses", which is exactly what omitting the argument means.
    """
    if not isinstance(name, str) or lookup(name) is None:
        raise ValueError(f"Model selected an unregistered tool: {name!r}")
    spec = lookup(name)
    if not isinstance(args, dict) or any(not isinstance(key, str) for key in args):
        raise ValueError("Tool arguments must be an object with string keys")
    args = dict(args)
    if "status_filter" in args and args["status_filter"] is None:
        del args["status_filter"]
    unknown = set(args) - set(spec.params)
    if unknown:
        raise ValueError(f"{name} does not accept: " + ", ".join(sorted(unknown)))
    missing = set(spec.required) - set(args)
    if missing:
        raise ValueError(f"{name} requires: " + ", ".join(sorted(missing)))
    bounds = {}
    for key, value in args.items():
        if key == "bins":
            if type(value) is not int or not 0 < value <= MAX_BINS:
                raise ValueError(f"bins must be an integer from 1 to {MAX_BINS}")
        elif key == "status_filter":
            if not ((type(value) is int and 0 <= value <= MAX_STATUS_CODE) or value == "successful"):
                raise ValueError("status_filter must be a status code from 0 to 65535 or 'successful'")
        elif key == "bucket":
            if value not in ("hour", "day"):
                raise ValueError("bucket must be 'hour' or 'day'")
        elif key in {"head_a", "head_b"}:
            _canonical_head(key, value)
        elif key == "head_id":
            heads = value if isinstance(value, list) else [value]
            if not heads:
                raise ValueError("head_id list must not be empty")
            for head in heads:
                _canonical_head(key, head)
            if len(set(heads)) != len(heads):
                raise ValueError("head_id list must not repeat a head")
        elif key == "machine_id":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("machine_id must be a non-empty string")
        elif key in {"start", "end"}:
            if not isinstance(value, str) or not _ISO_SECONDS.fullmatch(value):
                raise ValueError(f"{key} must be a timestamp like 2026-02-01T06:00:00, got {value!r}")
            bound = datetime.fromisoformat(value)
            if bound.tzinfo is not None:
                raise ValueError("Timezone-aware bounds are not supported")
            bounds[key] = bound
        else:
            raise ValueError(f"Argument has no verified validation rule: {key}")
    if "start" in bounds and "end" in bounds and bounds["start"] >= bounds["end"]:
        raise ValueError("start must be earlier than end")
    if name == "head_correlation" and args["head_a"] == args["head_b"]:
        raise ValueError("Head comparison requires distinct heads")
    return name, args
