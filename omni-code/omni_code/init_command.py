"""The `/init` slash command: generate or update the workspace AGENTS.md.

Runs the product's `init_agent` (omni_code/agents/init_agent) through the
framework's ephemeral-run harness (`run_agent_once`) — a multi-turn agent
run with real tools, outside the session lifecycle. Registered on the
framework's default slash registry from `omni_code/agents/omni/context.py`, so
the builtin `dispatch` tool can execute it.
"""

from __future__ import annotations

import os
from pathlib import Path

from omni_code.utility_runs import _build_model_provider


async def run_init_agent(max_turns: int = 1000) -> str:
    """Run the init_agent from the packaged agents tree."""
    from omniagents.core.config.loader import load_agent_spec_from_yaml
    from omniagents.core.runtime import run_agent_once

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime()

    agent_yaml = (
        Path(__file__).resolve().parent / "agents" / "init_agent" / "agent.yml"
    )
    spec = load_agent_spec_from_yaml(str(agent_yaml))

    # run_agent_once applies the model-name override itself; model_settings
    # is the one spec field it doesn't touch, so stamp it on the loaded spec.
    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    model = (
        resolved_model.strip()
        if isinstance(resolved_model, str) and resolved_model.strip()
        else None
    )

    model_provider, env_overrides = _build_model_provider(model_runtime or {})

    # Anchor the run to the invoking shell's directory ($PWD, falling back
    # to process cwd) — the init agent's apply_patch / write_file tools
    # resolve relative paths against it, and the resulting AGENTS.md is
    # read back from the same location for the success message.
    workspace_root = os.environ.get("PWD")
    base_dir = Path(workspace_root).expanduser() if workspace_root else Path.cwd()

    try:
        status = await run_agent_once(
            spec,
            "begin",
            model=model,
            model_provider=model_provider,
            env_overrides=env_overrides,
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
