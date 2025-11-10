from __future__ import annotations

from typing import Optional

from omniagents.core.tools import function_tool


@function_tool(client_status="Running command...")
async def dispatch(command: str, arg: Optional[str] = None) -> str:
    """Invoke a slash command with optional args.

    Called when the user's input starts with "/". Runs inside the agent's
    current run so the TUI can stream progress and avoid separate RPC timeouts.
    """
    norm = (command or "").strip().lower()

    if norm == "init":
        from oagents.init_agent import run_init_agent

        return await run_init_agent()

    return f"Unknown command: {command}"
