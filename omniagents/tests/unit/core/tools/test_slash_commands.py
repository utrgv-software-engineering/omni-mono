import pytest

from omniagents.core.tools import SlashCommandRegistry


@pytest.mark.asyncio
async def test_registry_executes_sync_and_async_handlers():
    registry = SlashCommandRegistry()
    registry.register("ping", lambda arg=None: f"pong:{arg}")

    async def async_handler(arg=None):
        return f"async:{arg}"

    registry.register("async", async_handler)

    assert await registry.execute("ping", "x") == "pong:x"
    assert await registry.execute("async", "y") == "async:y"


@pytest.mark.asyncio
async def test_registry_reports_unknown_and_failures():
    registry = SlashCommandRegistry()

    def boom(_arg=None):
        raise RuntimeError("nope")

    registry.register("boom", boom)

    assert await registry.execute("missing") == "Unknown command: missing"
    assert await registry.execute("boom") == "Failed to run /boom: nope"


def test_registry_creates_rich_output():
    registry = SlashCommandRegistry()
    output = registry.create_rich_output(command="ping", arg="x", output="pong")

    assert str(output) == "pong"
    assert output.ui_metadata["display_type"] == "command"
    assert output.ui_metadata["summary"] == "/ping x"
