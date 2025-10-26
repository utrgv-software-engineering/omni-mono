import asyncio
import inspect
import json
import sys
import textwrap
import uuid

import pytest

from omniagents.core.runtime.bridge import _stream_event_payload
from omniagents.core.tools import discovery
from omniagents.core.tools.discovery import RichToolOutput


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


def test_wrap_sync_function_creates_async_wrapper():
    def add(a, b=1):
        return a + b

    wrapped, was_wrapped = discovery._wrap_sync_function(add)
    assert was_wrapped is True
    assert inspect.iscoroutinefunction(wrapped)
    assert inspect.signature(wrapped) == inspect.signature(add)
    assert asyncio.run(wrapped(2, b=3)) == 5


def test_wrap_sync_function_leaves_coroutine_unchanged():
    async def add(a):
        return a * 2

    wrapped, was_wrapped = discovery._wrap_sync_function(add)
    assert wrapped is add
    assert was_wrapped is False


def test_function_tool_marks_and_invokes_sync_function():
    tool = discovery.function_tool(client_status="Running")(lambda name: f"hi {name}")
    assert getattr(tool, "_is_omniagents_core_tool", False) is True
    assert getattr(tool, "_client_status_text", None) == "Running"
    result = asyncio.run(tool.on_invoke_tool(None, json.dumps({"name": "Ada"})))
    assert result == "hi Ada"


def test_rich_function_tool_sets_rich_marker():
    @discovery.rich_function_tool
    async def rich_tool():
        return RichToolOutput("done", {"key": "value"})

    assert getattr(rich_tool, "_is_omniagents_core_tool", False) is True
    assert getattr(rich_tool, "_is_rich_tool", False) is True


def test_discover_functions_in_dir_detects_marked_tools(tmp_path, capsys):
    package_name = f"tools_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.tools.discovery import function_tool


            @function_tool
            def duplicate():
                return "first"


            def helper():
                return "ignored"
            """
        )
    )
    (base_dir / "zzz_second.py").write_text(
        textwrap.dedent(
            """
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
            """
        )
    )
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


def test_approval_metadata_registration_and_binding():
    @discovery.approval_metadata()
    def sample_tool_approval(path, new_text, optional="default"):
        return {"value": (path, new_text, optional)}

    metadata = discovery.get_approval_metadata(
        "sample_tool", {"path": "p", "new_text": "n"}
    )
    assert metadata == {"value": ("p", "n", "default")}


def test_get_approval_metadata_handles_missing_and_errors():
    assert discovery.get_approval_metadata("unregistered", {}) is None

    @discovery.approval_metadata(tool="failing_tool")
    def failing_tool_metadata(required):
        return {"value": required}

    assert discovery.get_approval_metadata("failing_tool", {"required": "ok"}) == {
        "value": "ok"
    }
    assert discovery.get_approval_metadata("failing_tool", {}) is None


def test_stream_event_payload_rich_tool_output_includes_metadata():
    class DummyItem:
        type = "tool_call_output_item"
        name = "demo"

        def __init__(self, output):
            self.output = output

        def to_input_item(self):
            return {"call_id": "call-1"}

    item = DummyItem(RichToolOutput("summary", {"rows": 3}))
    method, payload = _stream_event_payload(item, "run-1")
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
    method, payload = _stream_event_payload(item, "run-2")
    assert method == "tool_result"
    assert "metadata" not in payload
    assert payload["output"] == "plain"
    assert payload["call_id"] == "call-2"
