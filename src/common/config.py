"""Configuration loading.

All paths, thresholds and parameters live in config.yaml - the brief requires
that nothing be hard-coded. config.local.yaml, if present, is merged on top so
each of us can point at our own data without touching the shared file.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
LOCAL_CONFIG = REPO_ROOT / "config.local.yaml"


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DOTENV = REPO_ROOT / ".env"
# Only these settings are read from .env, so it cannot change anything else.
_DOTENV_KEYS = ("ANTHROPIC_API_KEY", "AROL_MANIFEST", "AROL_PLANNER", "AROL_DEMO",
                "SUPABASE_URL", "SUPABASE_KEY", "AROL_STORAGE_BUCKET", "AROL_STORE_DIR",
                "AROL_UPLOAD_PASSWORD", "AROL_CACHE_DIR")


def load_dotenv(path: str | Path | None = None) -> list[str]:
    """Copy allowed KEY=value lines from the project's .env into os.environ.

    Lets anyone keep their own Anthropic key in one ignored file instead of a
    system-wide variable. Values already set in the environment win. Returns
    the names that were set; values are never printed or logged.
    """
    import os

    path = Path(path) if path else DOTENV
    if not path.is_file():
        return []
    applied = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key in _DOTENV_KEYS and value and not os.environ.get(key):
            os.environ[key] = value
            applied.append(key)
    return applied


def _refuse_keys_in_files(cfg: dict, source: Path) -> None:
    claude = ((cfg.get("agent") or {}).get("claude") or {})
    if any(name in claude for name in ("api_key", "session_api_key")):
        raise ConfigError(f"{source.name} contains an API key. Never store keys in config files; "
                          "use ANTHROPIC_API_KEY, the ignored .env file, or the website's key field.")


def load(path: str | Path | None = None) -> dict:
    """Load config.yaml, merge config.local.yaml over it, resolve paths."""
    load_dotenv()
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(f"no config at {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _refuse_keys_in_files(cfg, path)
    if LOCAL_CONFIG.exists() and path == DEFAULT_CONFIG:
        local = yaml.safe_load(LOCAL_CONFIG.read_text(encoding="utf-8")) or {}
        _refuse_keys_in_files(local, LOCAL_CONFIG)
        cfg = _deep_merge(cfg, local)

    for section, key in (("data", "cache_dir"), ("agent", "report_dir"),
                         ("agent", "trace_dir")):
        value = cfg.get(section, {}).get(key)
        if value and not Path(value).is_absolute():
            cfg[section][key] = str(REPO_ROOT / value)

    cfg["data"]["pools"] = {
        name: [str(REPO_ROOT / p) if not Path(p).is_absolute() else p
               for p in (files or [])]
        for name, files in (cfg.get("data", {}).get("pools") or {}).items()
    }
    return cfg


def get(cfg: dict, dotted: str, default=None):
    """cfg lookup by dotted path: get(cfg, 'analytics.anomaly_sigma')."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
