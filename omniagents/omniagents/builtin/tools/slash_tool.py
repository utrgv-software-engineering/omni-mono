"""Builtin `dispatch` tool: model-invokable slash commands.

Executes commands from the process-wide default ``SlashCommandRegistry``
(``omniagents.core.tools.register_slash_command``). Ships with the `slash`
capability pack; products register their commands at import time.
"""

from __future__ import annotations

from typing import Optional

from omniagents.core.tools import (
    RichToolOutput,
    default_slash_registry,
    rich_function_tool,
)


@rich_function_tool(running_summary="Running command...")
async def dispatch(command: str, arg: Optional[str] = None) -> RichToolOutput:
    """Invoke a slash command with optional args.

    Called when the user's input starts with "/". Runs inside the agent's
    current run so the UI can stream progress and avoid separate RPC timeouts.
    """
    registry = default_slash_registry()
    output = await registry.execute(command, arg)
    return registry.create_rich_output(command=command, arg=arg, output=output)
