import importlib
import sys
import textwrap

from omniagents.core.server_functions.discovery import (
    discover_server_functions_in_dir,
)

_SF_SRC = textwrap.dedent(
    """
    from omniagents.core.server_functions.discovery import server_function


    @server_function(description="d", name_override="%(name)s")
    async def fn():
        return {"ok": True}
    """
)


def test_same_named_dirs_do_not_collide(tmp_path):
    """A host app's imported ``server_functions`` package must not shadow
    discovery over a different directory with the same name (and vice
    versa) — regression for the bare-dirname module prefix."""
    host = tmp_path / "host" / "server_functions"
    host.mkdir(parents=True)
    (host / "__init__.py").write_text("")
    (host / "host_fn.py").write_text(_SF_SRC % {"name": "host.fn"})

    other = tmp_path / "other" / "server_functions"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    (other / "other_fn.py").write_text(_SF_SRC % {"name": "other.fn"})

    sys.path.insert(0, str(tmp_path / "host"))
    try:
        importlib.import_module("server_functions")
        first = discover_server_functions_in_dir(str(host))
        second = discover_server_functions_in_dir(str(other))
    finally:
        sys.path.remove(str(tmp_path / "host"))
        for mod in list(sys.modules):
            if mod == "server_functions" or mod.startswith("server_functions."):
                sys.modules.pop(mod, None)
            if mod.startswith("_omniagents_sf_disco_"):
                sys.modules.pop(mod, None)

    assert "host.fn" in first
    assert "other.fn" in second
    assert "host.fn" not in second


def test_modules_persist_after_discovery(tmp_path):
    """Server-function modules hold live state (tick loops, provider
    installs) — discovery must leave them cached in sys.modules."""
    pkg = tmp_path / "server_functions"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "stateful.py").write_text(
        _SF_SRC % {"name": "stateful.fn"} + "\nSTATE = {'installed': True}\n"
    )

    sys.path.insert(0, str(tmp_path))
    try:
        discovered = discover_server_functions_in_dir(str(pkg))
        assert "stateful.fn" in discovered
        mod = importlib.import_module("server_functions.stateful")
        assert mod.STATE == {"installed": True}
        assert discovered["stateful.fn"]["func"].__module__ == mod.__name__
    finally:
        sys.path.remove(str(tmp_path))
        for mod_name in list(sys.modules):
            if mod_name == "server_functions" or mod_name.startswith(
                "server_functions."
            ):
                sys.modules.pop(mod_name, None)
