"""Load changed project code without restarting the website.

A redeploy (e.g. a push to the branch Streamlit Community Cloud follows) pulls
new code into the running server, but Streamlit reruns only the main script.
Modules imported earlier would stay old, so a new function could be missing.
Before each run, if any project file changed since it was loaded, every
project module is forgotten and the run imports one consistent, current version.
"""
from __future__ import annotations

import os
import sys

PROJECT_PACKAGES = ("src", "scripts")


def _project_modules(modules) -> dict:
    return {name: module for name, module in list(modules.items())
            if name.split(".")[0] in PROJECT_PACKAGES and getattr(module, "__file__", None)}


def _stamp(module):
    try:
        return os.stat(module.__file__).st_mtime_ns
    except OSError:
        return None


def record_project_modules(seen: dict, modules=sys.modules) -> None:
    """Remember the file version of each project module loaded so far."""
    for name, module in _project_modules(modules).items():
        seen.setdefault(name, _stamp(module))


def drop_changed_project_modules(seen: dict, modules=sys.modules) -> list[str]:
    """Forget all project modules if any of their files changed; return their names."""
    project = _project_modules(modules)
    if not any(name in seen and seen[name] != _stamp(module) for name, module in project.items()):
        return []
    for name in project:
        modules.pop(name, None)
    seen.clear()
    return sorted(project)
