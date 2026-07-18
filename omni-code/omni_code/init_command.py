"""The `/init` slash command: generate or update the workspace AGENTS.md.

Runs the product's `init_agent` (omni_code/agents/init_agent) through the
shared utility-run wiring (`omni_code.utility_runs.run_utility_agent`) — a
multi-turn agent run with real tools, outside the session lifecycle.
Registered on the framework's default slash registry from
`omni_code/agents/omni/context.py`, so the builtin `dispatch` tool can
execute it.
"""

from __future__ import annotations

import os
from pathlib import Path


async def run_init_agent(max_turns: int = 1000) -> str:
    """Run the init_agent from the packaged agents tree."""
    from omni_code.utility_runs import resolve_model_runtime, run_utility_agent

    # The init agent takes the runtime's raw model id (no provider-prefix
    # normalization — historical behavior for this path).
    model, model_runtime = resolve_model_runtime(normalize=False)

    model_settings = model_runtime.get("model_settings")

    # Anchor the run to the invoking shell's directory ($PWD, falling back
    # to process cwd) — the init agent's apply_patch / write_file tools
    # resolve relative paths against it, and the resulting AGENTS.md is
    # read back from the same location for the success message.
    workspace_root = os.environ.get("PWD")
    base_dir = Path(workspace_root).expanduser() if workspace_root else Path.cwd()

    try:
        status = await run_utility_agent(
            "init_agent",
            "begin",
            model=model,
            runtime=model_runtime,
            model_settings=model_settings if isinstance(model_settings, dict) else None,
            workspace_root=str(base_dir),
            max_turns=max_turns,
        )
    except Exception:
        return "Failed to update AGENTS.md"

    agents_md_path = base_dir / "AGENTS.md"
    if status in {"Created AGENTS.md", "Updated AGENTS.md"} and agents_md_path.exists():
        try:
            body = agents_md_path.read_text(encoding="utf-8")
            return f"{status}\n\nAGENTS.md:\n\n```markdown\n{body}\n```"
        except Exception:
            return status

    return status
