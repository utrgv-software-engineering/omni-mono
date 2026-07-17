"""Tests for the builtin auto-generated /help."""

import asyncio


class _FakeService:
    def __init__(self):
        self._server_functions = {
            "compact": {"description": "Compact context"},
            "recap": {"description": None},
            "terminal.create": {"description": "Open a terminal"},
        }


def test_help_renders_registered_functions():
    from omniagents.builtin.server_functions.help import help_command

    result = asyncio.run(help_command(_FakeService()))

    assert {"command": "/compact", "description": "Compact context"} in result["commands"]
    assert any(e["command"] == "/terminal.create" for e in result["commands"])
    # Dotted RPC endpoints stay out of the rendered message.
    assert "/terminal.create" not in result["message"]
    assert "`/compact` — Compact context" in result["message"]
    assert "`/recap`" in result["message"]
