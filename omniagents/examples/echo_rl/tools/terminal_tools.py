"""The terminal agent's tool(s).

`bash` is written ONCE against the omniagents.workspace seam, so the exact same
code path runs (a) under the SDK Runner at eval/inference time and (b) inside
the per-rollout training sandbox. `run_bash` is the shared body; `bash` is the
agent-facing @function_tool; the training environment's method reuses `run_bash`.
"""

from __future__ import annotations

import omniagents.workspace as ws
from omniagents import function_tool


async def run_bash(command: str, timeout: int = 30) -> str:
    """Execute a shell command via the bound workspace; format the result.

    Errors (timeouts, transport failures) are reported back to the agent as
    output rather than raised — a terminal tool should never crash the rollout.
    """
    try:
        r = await ws.exec(command, timeout=float(timeout))
    except Exception as exc:  # e.g. ExecTimeoutError on a hanging/interactive command
        return f"{type(exc).__name__}: {exc}\n(exit_code=-1)"
    out = (r.stdout or "") + (r.stderr or "")
    return f"{out}\n(exit_code={r.exit_code})"


@function_tool
async def bash(command: str, timeout: int = 30) -> str:
    """Run a shell command in the terminal and return its output.

    Args:
        command: The shell command to execute.
        timeout: Maximum number of seconds to wait for the command.
    """
    return await run_bash(command, timeout)
