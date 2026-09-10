"""Builtin ``/help``: the slash command list.

Renders the slash catalog (``AgentService.list_slash_commands``): only
server functions that carry a slash declaration, with their usage and
description, in presentation order. RPC-only functions never appear, by
construction rather than by naming heuristic. Products with curated help
define their own ``help`` server function; a project definition takes
over the name entirely.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.core.server_functions.slash import slash_command
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@slash_command(description="List the commands you can type", order=0)
@server_function(
    description="List available commands",
    strict=True,
    name_override="help",
    resource_scope=ResourceScope.AGENT_HOST,
    execution_requirements=ExecutionRequirement.NONE,
)
async def help_command(service: Any, session: Any = None) -> dict:
    lister = getattr(service, "list_slash_commands", None)
    commands = list(lister()) if callable(lister) else []
    lines = ["**Available commands:**"]
    for entry in commands:
        usage = f" {entry['usage']}" if entry.get("usage") else ""
        desc = entry.get("description") or ""
        lines.append(f"  `/{entry['name']}{usage}`" + (f" — {desc}" if desc else ""))
    return {"commands": commands, "message": "\n".join(lines)}


async def _help_on_invoke(service, session, args=None):
    return await help_command(service, session)


setattr(help_command, "_server_function_on_invoke", _help_on_invoke)
