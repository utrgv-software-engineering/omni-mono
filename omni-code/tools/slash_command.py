from __future__ import annotations

from pathlib import Path
from typing import Optional

from omniagents.core.tools import function_tool


async def run_init_agent(max_turns: int = 1000) -> str:
    """Run the init_agent from the omni_agents project structure."""
    from agents import Runner

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime()

    # Load the init_agent spec directly from YAML
    # Builtin tools are auto-discovered when no tool_registry is provided
    agent_yaml = Path(__file__).resolve().parent.parent / "omni_agents" / "init_agent" / "agent.yml"
    spec = load_agent_spec_from_yaml(str(agent_yaml))

    resolved_model = model_runtime.get("model") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    # Build the agent from the spec
    agent = await _default_build_agent(settings={}, mcp_servers=None, spec=spec)

    # Run the agent
    result = await Runner.run(agent, "begin", max_turns=max_turns)
    try:
        return result.final_output_as(str)
    except Exception:
        return "Failed to update AGENTS.md"


@function_tool(client_status="Running command...")
async def dispatch(command: str, arg: Optional[str] = None) -> str:
    """Invoke a slash command with optional args.

    Called when the user's input starts with "/". Runs inside the agent's
    current run so the TUI can stream progress and avoid separate RPC timeouts.
    """
    norm = (command or "").strip().lower()

    if norm == "init":
        return await run_init_agent()

    return f"Unknown command: {command}"
