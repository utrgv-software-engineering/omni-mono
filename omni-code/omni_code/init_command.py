"""The `/init` slash command: generate or update the workspace AGENTS.md.

Runs the product's `init_agent` (omni_agents/init_agent) via a direct
Runner.run — a multi-turn agent run with real tools, outside the session
lifecycle. Registered on the framework's default slash registry from
`omni_agents/omni/context.py`, so the builtin `dispatch` tool can execute
it.
"""

from __future__ import annotations

import os
from pathlib import Path

from omniagents.core.runtime import temporary_environ

from omni_code.utility_runs import _build_model_provider


async def run_init_agent(max_turns: int = 1000) -> str:
    """Run the init_agent from the omni_agents project structure."""
    from agents import RunConfig, Runner

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime()

    # Load the init_agent spec directly from YAML
    # Builtin tools are auto-discovered when no tool_registry is provided
    agent_yaml = (
        Path(__file__).resolve().parent.parent
        / "omni_agents"
        / "init_agent"
        / "agent.yml"
    )
    spec = load_agent_spec_from_yaml(str(agent_yaml))

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    # Build the agent from the spec
    agent = await _default_build_agent(settings={}, mcp_servers=None, spec=spec)

    model_provider, env_overrides = _build_model_provider(model_runtime or {})
    run_config = (
        RunConfig(model_provider=model_provider) if model_provider is not None else None
    )

    # Resolve the workspace BEFORE the run so we can both (a) pass it as
    # the agent's run context — the init agent's apply_patch / write_file
    # tools resolve relative paths against ctx.context["workspace_root"]
    # and would raise WorkspaceRootRequiredError without it under strict
    # mode — and (b) read the resulting AGENTS.md back from the same
    # location for the success message.
    workspace_root = os.environ.get("PWD")
    base_dir = Path(workspace_root).expanduser() if workspace_root else Path.cwd()

    # Run the agent
    with temporary_environ(env_overrides):
        result = await Runner.run(
            agent,
            "begin",
            context={"workspace_root": str(base_dir)},
            max_turns=max_turns,
            run_config=run_config,
        )

    try:
        status = result.final_output_as(str)
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
