"""Tests for the builtin /help: it renders the slash catalog, nothing else."""

import asyncio


class _FakeService:
    def __init__(self):
        # The RPC surface is wider than the typeable one; /help must only
        # ever read the catalog.
        self._server_functions = {
            "compact": {"description": "Compact context"},
            "terminal.create": {"description": "Open a terminal"},
        }

    def list_slash_commands(self):
        return [
            {"name": "compact", "usage": "", "description": "Compact context"},
            {"name": "goal", "usage": "<goal text>", "description": "Start a goal"},
            {"name": "recap", "usage": "", "description": ""},
        ]


def test_help_renders_only_the_slash_catalog():
    from omniagents.builtin.server_functions.help import help_command

    result = asyncio.run(help_command(_FakeService()))

    assert [c["name"] for c in result["commands"]] == ["compact", "goal", "recap"]
    assert "`/compact` — Compact context" in result["message"]
    assert "`/goal <goal text>` — Start a goal" in result["message"]
    assert "`/recap`" in result["message"]
    assert "terminal.create" not in result["message"]


def test_help_tolerates_a_service_without_a_catalog():
    from omniagents.builtin.server_functions.help import help_command

    result = asyncio.run(help_command(object()))
    assert result["commands"] == []
