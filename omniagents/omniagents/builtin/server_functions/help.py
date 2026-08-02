"""Builtin ``/help``: auto-generated command list.

Renders every server function registered on the running AgentService —
name plus its ``@server_function`` description — so the list can never
drift from what's actually installed. Dotted names (``terminal.create``,
``session.ensure``) are RPC endpoints rather than user-facing slash
commands; they're included in the payload but omitted from the rendered
message. Products with curated help simply define their own ``help``
server function — a project definition takes over the name entirely.

Activated by the ``slash`` capability pack.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@server_function(
    description="List available commands",
    strict=True,
    name_override="help",
    resource_scope=ResourceScope.AGENT_HOST,
    execution_requirements=ExecutionRequirement.NONE,
)
async def help_command(service: Any, session: Any = None) -> dict:
    registered = getattr(service, "_server_functions", {}) or {}

    entries = []
    for name in sorted(registered):
        meta = registered.get(name) or {}
        entries.append(
            {"command": f"/{name}", "description": meta.get("description") or ""}
        )

    lines = ["**Available commands:**"]
    for entry in entries:
        name = entry["command"]
        if "." in name:
            continue  # RPC endpoint, not a user-facing slash command
        desc = entry["description"]
        lines.append(f"  `{name}`" + (f" — {desc}" if desc else ""))

    return {"commands": entries, "message": "\n".join(lines)}


async def _help_on_invoke(service, session, args=None):
    return await help_command(service, session)


setattr(help_command, "_server_function_on_invoke", _help_on_invoke)
