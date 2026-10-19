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
