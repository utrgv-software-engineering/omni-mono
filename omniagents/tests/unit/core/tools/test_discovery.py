import asyncio
import importlib
import inspect
import json
import sys
import textwrap
import uuid

import pytest
from pathlib import Path

from omniagents.core.runtime.bridge import stream_event_payload
from omniagents.core.tools import discovery
from omniagents.core.tools.discovery import RichToolOutput
from omniagents.core.debug import Debug


@pytest.fixture(autouse=True)
def reset_approval_registry():
    registry = discovery._approval_metadata_registry
    snapshot = dict(registry)
    registry.clear()
    try:
        yield
    finally:
        registry.clear()
        registry.update(snapshot)


def test_function_tool_marks_sync_function():
    tool = discovery.function_tool(running_summary="Running")(lambda name: f"hi {name}")
    assert getattr(tool, "_is_omniagents_core_tool", False) is True
    assert getattr(tool, "_running_summary", None) == "Running"
    # SDK 0.16+ handles sync->async natively via asyncio.to_thread; the
    # original function is captured for direct invocation (used by training).
    assert getattr(tool, "_original_func", None) is not None
    assert tool._original_func("Ada") == "hi Ada"


def test_function_tool_running_summary_str_error():
    class FailingStr:
        def __str__(self):
            raise RuntimeError("no string")

    tool = discovery.function_tool(running_summary=FailingStr())(lambda: "ok")
    assert getattr(tool, "_is_omniagents_core_tool", False) is True
    assert not hasattr(tool, "_running_summary")


def test_rich_function_tool_sets_rich_marker():
    @discovery.rich_function_tool
    async def rich_tool():
        return RichToolOutput("done", {"key": "value"})

    assert getattr(rich_tool, "_is_omniagents_core_tool", False) is True
    assert getattr(rich_tool, "_is_rich_tool", False) is True


def test_rich_function_tool_short_form_registers():
    @discovery.rich_function_tool()
    async def rich_tool():
        return RichToolOutput("ok", {})

    assert getattr(rich_tool, "_is_omniagents_core_tool", False) is True
    assert getattr(rich_tool, "_is_rich_tool", False) is True


def test_rich_tool_output_str_and_repr():
    output = RichToolOutput("summary", {"rows": 2, "extra": "value"})
    assert str(output) == "summary"
    assert "RichToolOutput" in repr(output)
    assert "metadata_keys=['rows', 'extra']" in repr(output)


def test_discover_functions_in_dir_detects_marked_tools(tmp_path, capsys):
    package_name = f"tools_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def duplicate():
                return "first"


            def helper():
                return "ignored"
            """))
    (base_dir / "zzz_second.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import (
                function_tool,
                rich_function_tool,
                RichToolOutput,
            )


            @rich_function_tool
            async def duplicate():
                return RichToolOutput("second", {"x": 1})


            @function_tool
            def other_tool():
                return "other"


            @rich_function_tool
            async def rich_only():
                return RichToolOutput("rich", {"y": 2})
            """))
    try:
        discovered = discovery.discover_functions_in_dir(str(base_dir))
    finally:
        for mod in list(sys.modules):
            if mod.startswith(package_name):
                sys.modules.pop(mod, None)

    assert set(discovered) == {"duplicate", "other_tool", "rich_only"}
    assert getattr(discovered["rich_only"], "_is_rich_tool", False) is True
    assert getattr(discovered["other_tool"], "_is_omniagents_core_tool", False) is True
    assert getattr(discovered["duplicate"], "_is_omniagents_core_tool", False) is True

    captured = capsys.readouterr()
    assert "Warning: Duplicate tool name 'duplicate'" in captured.out


def test_discover_functions_in_dir_handles_missing_dir(tmp_path):
    missing = tmp_path / "not_there"
    assert discovery.discover_functions_in_dir(str(missing)) == {}


def test_discover_functions_in_dir_isolates_same_named_dirs(tmp_path):
    """Two tool directories named the same thing must not collide on
    sys.modules. Regression for the case where a host app imports
    `tools._bash_jobs` (binding sys.modules['tools'] to its local
    `tools/` package) and then discovery is invoked on a separate
    `tools/` directory whose files differ — the second discovery used to
    fail because Python's already-cached `tools` package didn't contain
    the second dir's submodules.
    """
    # First "tools" package — looks like the host app's tools dir.
    host = tmp_path / "host" / "tools"
    host.mkdir(parents=True)
    (host / "__init__.py").write_text("")
    (host / "host_only.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def host_only_tool():
                return "host"
            """))

    # Second "tools" package — same dirname, different content.
    other = tmp_path / "other" / "tools"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    (other / "other_only.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def other_only_tool():
                return "other"
            """))

    # Pre-bind `tools` in sys.modules pointing at the first package, the
    # way a host app would after a real `from tools.host_only import ...`.
    # Drop any 'tools' package another test left behind first — the premise
    # is that THIS test controls what sys.modules['tools'] points at.
    for mod in list(sys.modules):
        if mod == "tools" or mod.startswith("tools."):
            sys.modules.pop(mod, None)
    sys.path.insert(0, str(tmp_path / "host"))
    try:
        host_pkg = importlib.import_module("tools")
        assert host_pkg.__file__ == str(host / "__init__.py")

        # Now discover on the OTHER `tools/` dir. Before the fix this
        # failed: discovery tried `tools.other_only` and Python resolved
        # `tools` to the already-cached host package, finding no
        # `other_only` submodule → ImportError on every file.
        discovered = discovery.discover_functions_in_dir(str(other))
    finally:
        try:
            sys.path.remove(str(tmp_path / "host"))
        except ValueError:
            pass
        for mod in list(sys.modules):
            if mod == "tools" or mod.startswith("tools."):
                sys.modules.pop(mod, None)

    assert "other_only_tool" in discovered
    assert "host_only_tool" not in discovered


def test_discover_functions_in_dir_cleans_synthetic_modules(tmp_path):
    """Synthetic-prefix modules (dir is not a real package) are removed
    from sys.modules after discovery — their names are discovery-internal
    and holding them would only leak."""
    pkg = tmp_path / "no_init_dir"
    pkg.mkdir()
    (pkg / "tool_a.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def tool_a():
                return "a"
            """))
    original_modules = set(sys.modules)
    try:
        discovered = discovery.discover_functions_in_dir(str(pkg))
        assert "tool_a" in discovered
        leaked = [
            m
            for m in sys.modules
            if m not in original_modules and m.startswith("_omniagents_disco_")
        ]
        assert not leaked
    finally:
        for mod in list(sys.modules):
            if mod.startswith("_omniagents_disco_"):
                sys.modules.pop(mod, None)


def test_discover_functions_in_dir_preserves_canonical_singletons(tmp_path):
    """Canonically-named modules persist in sys.modules after discovery,
    so module-level state is a process singleton: a later import of the
    same module must see mutations made through the discovered tool.
    Regression for host apps that kept job registries alive by stashing
    them on sys.modules['sys'] because discovery evicted their module."""
    pkg_name = f"singleton_pkg_{uuid.uuid4().hex}"
    pkg = tmp_path / pkg_name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "_state.py").write_text("REGISTRY = {}\n")
    (pkg / "reg_tool.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool

            from ._state import REGISTRY


            @function_tool
            def add_item():
                REGISTRY["k"] = "v"
                return "ok"
            """))
    sys.path.insert(0, str(tmp_path))
    try:
        discovered = discovery.discover_functions_in_dir(str(pkg))
        assert "add_item" in discovered
        # Mutate module-level state through the discovered tool...
        discovered["add_item"]._original_func()
        # ...then import the state module the ordinary way. Before the
        # fix this re-executed _state.py (discovery had evicted it) and
        # the mutation was lost.
        state = importlib.import_module(f"{pkg_name}._state")
        assert state.REGISTRY == {"k": "v"}
    finally:
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass
        for mod in list(sys.modules):
            if mod == pkg_name or mod.startswith(f"{pkg_name}."):
                sys.modules.pop(mod, None)


def test_discover_functions_in_dir_reports_unexpected_errors(tmp_path, capsys):
    pkg = tmp_path / "broken"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bad.py").write_text("raise RuntimeError('boom')\n")
    discovery.discover_functions_in_dir(str(pkg))
    captured = capsys.readouterr()
    assert "ERROR: Unexpected error processing file" in captured.out
    assert "RuntimeError: boom" in captured.out


def test_discover_functions_in_dir_reports_import_errors(tmp_path, capsys):
    pkg = tmp_path / "missing"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bad_import.py").write_text("import definitely_missing_module\n")
    discovery.discover_functions_in_dir(str(pkg))
    captured = capsys.readouterr()
    assert "ERROR: Could not import module" in captured.out


def test_discover_functions_in_dir_reports_syntax_errors(tmp_path, capsys):
    pkg = tmp_path / "syntax"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bad_syntax.py").write_text("def broken(:\n")
    discovery.discover_functions_in_dir(str(pkg))
    captured = capsys.readouterr()
    assert "ERROR: Syntax error in file" in captured.out


def test_discover_functions_in_dir_skips_outside_files(tmp_path, capsys, monkeypatch):
    pkg_name = f"pkg_{uuid.uuid4().hex}"
    base = tmp_path / pkg_name
    base.mkdir()
    (base / "__init__.py").write_text("")
    outside = tmp_path.parent / f"outside_{uuid.uuid4().hex}.py"
    outside.write_text("print('ignore')\n")

    original_rglob = Path.rglob

    def fake_rglob(self, pattern):
        if self == base:
            yield outside
        yield from original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)
    try:
        discovery.discover_functions_in_dir(str(base))
        captured = capsys.readouterr()
        assert "Warning: Skipping file" in captured.out
    finally:
        outside.unlink(missing_ok=True)


def test_discover_functions_in_dir_debug_logging(tmp_path, capsys):
    pkg_name = f"debug_pkg_{uuid.uuid4().hex}"
    base = tmp_path / pkg_name
    base.mkdir()
    (base / "__init__.py").write_text("")
    (base / "tool.py").write_text(textwrap.dedent("""
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def sample():
                return "value"
            """))
    prev = Debug.enabled()
    Debug.set_enabled(True)
    capsys.readouterr()
    try:
        try:
            discovery.discover_functions_in_dir(str(base))
        finally:
            for mod in list(sys.modules):
                if mod.startswith(pkg_name):
                    sys.modules.pop(mod, None)
        err = capsys.readouterr().err
        assert "Starting tool discovery" in err
        assert "Discovered marked tool: sample" in err
        assert "Completed tool discovery" in err
    finally:
        Debug.set_enabled(prev)


def test_approval_metadata_registration_and_binding():
    @discovery.approval_metadata()
    def sample_tool_approval(path, new_text, optional="default"):
        return {"value": (path, new_text, optional)}

    metadata = discovery.get_approval_metadata(
        "sample_tool", {"path": "p", "new_text": "n"}
    )
    assert metadata == {"value": ("p", "n", "default")}


def test_approval_metadata_requires_suffix():
    with pytest.raises(ValueError):

        @discovery.approval_metadata()
        def invalid():
            return {}


def test_get_approval_metadata_handles_missing_and_errors():
    assert discovery.get_approval_metadata("unregistered", {}) is None

    @discovery.approval_metadata(tool="failing_tool")
    def failing_tool_metadata(required):
        return {"value": required}

    assert discovery.get_approval_metadata("failing_tool", {"required": "ok"}) == {
        "value": "ok"
    }
    assert discovery.get_approval_metadata("failing_tool", {}) is None


def test_approval_metadata_debug_logging(capsys):
    prev = Debug.enabled()
    Debug.set_enabled(True)
    capsys.readouterr()
    try:

        @discovery.approval_metadata(tool="debug_tool")
        def debug_tool():
            return {"ok": True}

        assert discovery.get_approval_metadata("debug_tool", {}) == {"ok": True}
        err = capsys.readouterr().err
        assert "Registered approval metadata for tool 'debug_tool'" in err
        assert "Generated approval metadata for tool 'debug_tool'" in err
    finally:
        Debug.set_enabled(prev)


def test_get_approval_metadata_debug_error(capsys):
    prev = Debug.enabled()
    Debug.set_enabled(True)
    capsys.readouterr()
    try:

        @discovery.approval_metadata(tool="debug_error_tool")
        def debug_error_tool(required):
            raise RuntimeError("fail")

        assert (
            discovery.get_approval_metadata("debug_error_tool", {"required": "x"})
            is None
        )
        captured = capsys.readouterr()
        assert (
            "Error generating approval metadata for tool 'debug_error_tool'"
            in captured.err
        )
        assert "RuntimeError: fail" in captured.err
    finally:
        Debug.set_enabled(prev)


def test_stream_event_payload_rich_tool_output_includes_metadata():
    class DummyItem:
        type = "tool_call_output_item"
        name = "demo"

        def __init__(self, output):
            self.output = output

        def to_input_item(self):
            return {"call_id": "call-1"}

    item = DummyItem(RichToolOutput("summary", {"rows": 3}))
    method, payload = stream_event_payload(item, "run-1", session_id="sess-1")
    assert method == "tool_result"
    assert payload["metadata"] == {"rows": 3}
    assert payload["output"] == "summary"
    assert payload["call_id"] == "call-1"


def test_stream_event_payload_plain_output_omits_metadata():
    class DummyItem:
        type = "tool_call_output_item"
        name = "demo"

        def __init__(self, output):
            self.output = output

        def to_input_item(self):
            return {"call_id": "call-2"}

    item = DummyItem("plain")
    method, payload = stream_event_payload(item, "run-2", session_id="sess-2")
    assert method == "tool_result"
    assert "metadata" not in payload
    assert payload["output"] == "plain"
    assert payload["call_id"] == "call-2"
