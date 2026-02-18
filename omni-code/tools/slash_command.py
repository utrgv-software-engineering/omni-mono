from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Optional

from omniagents.core.tools import function_tool


@contextlib.contextmanager
def _temporary_environ(overrides: dict[str, str | None]):
    prior: dict[str, str | None] = {}
    for key, value in (overrides or {}).items():
        prior[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _litellm_env_overrides(*, model: str | None, api_key: str | None) -> dict[str, str | None]:
    if not api_key:
        return {}
    model_name = (model or "").lower()
    if model_name.startswith("anthropic/") or model_name.startswith("claude"):
        return {"ANTHROPIC_API_KEY": api_key}
    if model_name.startswith("gemini/") or model_name.startswith("google/"):
        return {"GOOGLE_API_KEY": api_key}
    return {"OPENAI_API_KEY": api_key}


def _build_model_provider(runtime: dict) -> tuple[object | None, dict[str, str | None]]:
    provider = (runtime.get("provider") or "").strip().lower()
    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    if provider == "litellm":
        from agents.extensions.models.litellm_provider import LitellmProvider

        return LitellmProvider(), _litellm_env_overrides(model=model, api_key=api_key)

    if provider in {"openai", "openai-compatible", "azure"}:
        from openai import AsyncOpenAI
        from agents.models.openai_provider import OpenAIProvider

        resolved_base_url = base_url or "https://api.openai.com/v1"
        resolved_api_key = api_key
        if not resolved_api_key:
            if provider == "azure":
                resolved_api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                resolved_api_key = os.getenv("OPENAI_API_KEY")

        is_azure = resolved_base_url and (
            "openai.azure.com" in resolved_base_url
            or "services.ai.azure.com" in resolved_base_url
        )

        if is_azure:
            if not resolved_api_key:
                return None, {}
            client = AsyncOpenAI(
                api_key="dummy",
                base_url=resolved_base_url,
                default_headers={"api-key": resolved_api_key},
            )
            return OpenAIProvider(openai_client=client), {}

        if not resolved_api_key and resolved_base_url != "https://api.openai.com/v1":
            resolved_api_key = "dummy"

        if not resolved_api_key:
            return None, {}

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        return OpenAIProvider(openai_client=client), {}

    return None, {}


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
    agent_yaml = Path(__file__).resolve().parent.parent / "omni_agents" / "init_agent" / "agent.yml"
    spec = load_agent_spec_from_yaml(str(agent_yaml))

    resolved_model = model_runtime.get("model") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    # Build the agent from the spec
    agent = await _default_build_agent(settings={}, mcp_servers=None, spec=spec)

    model_provider, env_overrides = _build_model_provider(model_runtime or {})
    run_config = RunConfig(model_provider=model_provider) if model_provider is not None else None

    # Run the agent
    with _temporary_environ(env_overrides):
        result = await Runner.run(agent, "begin", max_turns=max_turns, run_config=run_config)

    try:
        status = result.final_output_as(str)
    except Exception:
        return "Failed to update AGENTS.md"

    workspace_root = os.environ.get("PWD")
    base_dir = Path(workspace_root).expanduser() if workspace_root else Path.cwd()
    agents_md_path = base_dir / "AGENTS.md"
    if status in {"Created AGENTS.md", "Updated AGENTS.md"} and agents_md_path.exists():
        try:
            body = agents_md_path.read_text(encoding="utf-8")
            return f"{status}\n\nAGENTS.md:\n\n```markdown\n{body}\n```"
        except Exception:
            return status

    return status


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
