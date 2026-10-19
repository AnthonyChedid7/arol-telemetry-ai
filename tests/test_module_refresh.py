"""After a redeploy the website loads changed project code without a restart."""
import importlib.util
import os
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("module_refresh", ROOT / "app" / "module_refresh.py")
module_refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module_refresh)


def _module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    return module


def test_changed_project_file_drops_all_project_modules(tmp_path):
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    first.write_text("x = 1"), second.write_text("y = 1")
    modules = {"src.common.a": _module("src.common.a", first),
               "scripts.b": _module("scripts.b", second),
               "polars": _module("polars", tmp_path / "polars.py")}
    seen = {}
    module_refresh.record_project_modules(seen, modules)
    assert set(seen) == {"src.common.a", "scripts.b"}  # third-party modules are left alone
    assert module_refresh.drop_changed_project_modules(seen, modules) == []
    stamp = os.stat(first).st_mtime_ns + 1_000_000_000
    os.utime(first, ns=(stamp, stamp))  # a redeploy rewrote one file
    assert module_refresh.drop_changed_project_modules(seen, modules) == ["scripts.b", "src.common.a"]
    assert set(modules) == {"polars"} and seen == {}


def test_modules_first_seen_now_are_recorded_not_dropped(tmp_path):
    path = tmp_path / "c.py"
    path.write_text("z = 1")
    modules = {"src.c": _module("src.c", path)}
    seen = {}
    assert module_refresh.drop_changed_project_modules(seen, modules) == []
    module_refresh.record_project_modules(seen, modules)
    assert "src.c" in seen and "src.c" in modules


def test_file_changed_during_a_run_is_still_detected(tmp_path, monkeypatch):
    """The race seen on the host: a module is imported, then its file changes
    before the run records stamps. The bytecode stamp from import time still
    shows that the loaded module is old."""
    import importlib
    import sys
    package = tmp_path / "pkgrace"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "mod.py"
    source.write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    module_refresh.install_import_stamps(packages=("pkgrace",))
    module_refresh.install_import_stamps(packages=("pkgrace",))  # every run; still one hook
    assert sum(getattr(f, "marker", False) for f in sys.meta_path) == 1
    module = importlib.import_module("pkgrace.mod")
    assert getattr(module, module_refresh.STAMP) is not None
    modules = {name: m for name, m in sys.modules.items() if name.startswith("pkgrace")}
    seen = {}
    # The deploy rewrites the file mid-run, then the run records its stamps.
    source.write_text("VALUE = 2\nNEW = 'added by the deploy'\n")
    stamp = os.stat(source).st_mtime_ns + 2_000_000_000
    os.utime(source, ns=(stamp, stamp))
    module_refresh.record_project_modules(seen, modules, packages=("pkgrace",))
    dropped = module_refresh.drop_changed_project_modules(seen, modules, packages=("pkgrace",))
    assert dropped == ["pkgrace", "pkgrace.mod"]
    for name in dropped:
        sys.modules.pop(name, None)
    assert importlib.import_module("pkgrace.mod").NEW == "added by the deploy"
    fresh = {name: m for name, m in sys.modules.items() if name.startswith("pkgrace")}
    assert module_refresh.drop_changed_project_modules({}, fresh, packages=("pkgrace",)) == []
    for name in fresh:
        sys.modules.pop(name, None)


def test_namespace_packages_are_dropped_with_their_submodules(tmp_path, monkeypatch):
    """A folder without __init__.py keeps 'report' as an attribute; if it
    survived, 'from pkgns.agent import report' would return the old module."""
    import importlib
    import sys
    agent = tmp_path / "pkgns" / "agent"
    agent.mkdir(parents=True)  # no __init__.py anywhere: namespace packages
    source = agent / "report.py"
    source.write_text("OLD = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    module_refresh.install_import_stamps(packages=("pkgns",))
    from pkgns.agent import report  # noqa: F401
    source.write_text("OLD = False\nNEW = 1\n")
    stamp = os.stat(source).st_mtime_ns + 2_000_000_000
    os.utime(source, ns=(stamp, stamp))
    dropped = module_refresh.drop_changed_project_modules({}, sys.modules, packages=("pkgns",))
    assert dropped == ["pkgns", "pkgns.agent", "pkgns.agent.report"]
    from pkgns.agent import report as fresh
    assert fresh.NEW == 1
    for name in [n for n in sys.modules if n.startswith("pkgns")]:
        sys.modules.pop(name)


def test_stale_submodule_kept_only_as_a_package_attribute_is_found(tmp_path):
    package = types.ModuleType("src.agent")  # namespace package: no __file__
    old = tmp_path / "report.py"
    old.write_text("x = 1")
    report = _module("src.agent.report", old)
    package.report = report  # left over; no longer listed in sys.modules
    modules = {"src.agent": package}
    seen = {"src.agent.report": os.stat(old).st_mtime_ns}
    stamp = os.stat(old).st_mtime_ns + 1_000_000_000
    os.utime(old, ns=(stamp, stamp))
    assert module_refresh.drop_changed_project_modules(seen, modules) == ["src.agent"]
    assert modules == {}
