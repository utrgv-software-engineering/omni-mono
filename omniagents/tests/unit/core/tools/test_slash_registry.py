"""The process-wide default slash-command registry behind the builtin
`dispatch` tool: registration, execution, replacement, async handlers."""

from __future__ import annotations

import asyncio

import pytest

from omniagents.core.tools import default_slash_registry, register_slash_command
from omniagents.core.tools.slash_commands import _default_registry


@pytest.fixture(autouse=True)
def _isolate_default_registry():
    """The default registry is process-global; snapshot/restore around each
    test so registrations don't leak."""
    prior = dict(_default_registry._commands)
    try:
        yield
    finally:
        _default_registry._commands.clear()
        _default_registry._commands.update(prior)


def test_register_and_execute_sync_handler():
    register_slash_command("hello", lambda _arg=None: "hi there")
    assert default_slash_registry().has("hello")
    result = asyncio.run(default_slash_registry().execute("hello"))
    assert result == "hi there"


@pytest.mark.asyncio
async def test_async_handler_receives_arg():
    seen: list[str | None] = []

    async def _handler(arg=None):
        seen.append(arg)
        return f"got {arg}"

    register_slash_command("echo", _handler)
    result = await default_slash_registry().execute("echo", "some args")
    assert result == "got some args"
    assert seen == ["some args"]


@pytest.mark.asyncio
async def test_reregistering_replaces_handler():
    register_slash_command("cmd", lambda _arg=None: "first")
    register_slash_command("cmd", lambda _arg=None: "second")
    assert await default_slash_registry().execute("cmd") == "second"


@pytest.mark.asyncio
async def test_unknown_command():
    result = await default_slash_registry().execute("nope")
    assert result.startswith("Unknown command:")
