"""Load changed project code without restarting the website.

A redeploy (e.g. a push to the branch Streamlit Community Cloud follows) pulls
new code into the running server, but Streamlit reruns only the main script.
Modules imported earlier would stay old, so a new function could be missing.
Before each run, if any project module is older than its file, every project
module is forgotten and the run imports one consistent, current version.

"Older than its file" compares the file with its version at import time,
recorded by an import hook (install_import_stamps) just before the module's
code is read. Files rewritten while a run is in progress (a deploy pulls them
one by one) are therefore still detected on the next run; a stamp recorded
after the run would wrongly mark them current. Modules imported before the
hook fall back to stamps remembered in `seen` the first time they were observed.
"""
from __future__ import annotations

import importlib.machinery
import os
import sys

PROJECT_PACKAGES = ("src", "scripts")
STAMP = "__source_version_at_import__"


def _file_version(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF


class _ImportStamps:
    """Meta-path finder: project modules remember their file version when loaded."""

    marker = True

    def __init__(self, packages):
        self.packages = tuple(packages)

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in self.packages:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        loader = getattr(spec, "loader", None)
        if spec is None or not spec.origin or not hasattr(loader, "exec_module"):
            return spec
        original, origin = loader.exec_module, spec.origin

        def exec_module(module):
            setattr(module, STAMP, _file_version(origin))  # before the code is read
            original(module)

        loader.exec_module = exec_module
        return spec


def install_import_stamps(packages=PROJECT_PACKAGES, meta_path=None) -> None:
    """Put one current stamping finder first on sys.meta_path (safe to call every run)."""
    meta_path = sys.meta_path if meta_path is None else meta_path
    meta_path[:] = [finder for finder in meta_path
                    if not getattr(finder, "marker", False) or not hasattr(finder, "packages")]
    meta_path.insert(0, _ImportStamps(packages))


def _project_modules(modules, packages=PROJECT_PACKAGES) -> dict:
    """Project modules with a file: those in `modules`, plus submodules held only as
    attributes of a project package (an earlier partial reload can leave those)."""
    found = {}
    for name, module in list(modules.items()):
        if name.split(".")[0] not in packages:
            continue
        candidates = [module] + [value for value in list(vars(module).values())
                                 if type(value) is type(module)]
        for candidate in candidates:
            label = getattr(candidate, "__name__", "")
            if label.split(".")[0] in packages and getattr(candidate, "__file__", None):
                found.setdefault(label, candidate)
    return found


def _stamp(module):
    try:
        return os.stat(module.__file__).st_mtime_ns
    except OSError:
        return None


def _imported_version(module):
    """(mtime, size) of the source when this module was imported, if the hook saw it.

    Bytecode-cache headers are deliberately not used: when bytecode writing is
    off, an outdated .pyc stays on disk although the module loaded fresh from
    source, which would look stale and reload every module on every run.
    """
    return getattr(module, STAMP, None)


def _current_version(module):
    return _file_version(module.__file__)


def _is_stale(name, module, seen) -> bool:
    imported = _imported_version(module)
    if imported is not None:
        current = _current_version(module)
        return current is not None and current != imported
    return name in seen and seen[name] != _stamp(module)


def record_project_modules(seen: dict, modules=sys.modules, packages=PROJECT_PACKAGES) -> None:
    """Remember file stamps for modules without a bytecode stamp (the fallback)."""
    for name, module in _project_modules(modules, packages).items():
        seen.setdefault(name, _stamp(module))


def drop_changed_project_modules(seen: dict, modules=sys.modules,
                                 packages=PROJECT_PACKAGES) -> list[str]:
    """Forget all project modules if any of them is older than its file; return their names.

    Namespace packages (folders without __init__.py, e.g. src.agent) have no
    file to check but are dropped too: they keep attributes pointing at their
    old submodules, and "from src.agent import report" would return those.
    """
    project = _project_modules(modules, packages)
    if not any(_is_stale(name, module, seen) for name, module in project.items()):
        return []
    dropped = sorted(name for name in list(modules) if name.split(".")[0] in packages)
    for name in dropped:
        modules.pop(name, None)
    seen.clear()
    return dropped
