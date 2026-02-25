from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from omniagents.core.tools import rich_function_tool, RichToolOutput


def _build_model_provider(runtime: dict):
    """Build a model provider and env overrides from a runtime config dict.

    Reuses the same logic as slash_command._build_model_provider so the
    explorer inherits the user's configured provider/credentials.
    """
    provider = (runtime.get("provider") or "").strip().lower()
    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    if provider == "litellm":
        from agents.extensions.models.litellm_provider import LitellmProvider

        env_overrides: dict[str, str | None] = {}
        if api_key:
            model_name = (model or "").lower()
            if model_name.startswith("anthropic/") or model_name.startswith("claude"):
                env_overrides = {"ANTHROPIC_API_KEY": api_key}
            elif model_name.startswith("gemini/") or model_name.startswith("google/"):
                env_overrides = {"GOOGLE_API_KEY": api_key}
            else:
                env_overrides = {"OPENAI_API_KEY": api_key}
        return LitellmProvider(), env_overrides

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


@rich_function_tool(client_status="Exploring codebase...")
async def explore(question: str, model: Optional[str] = None) -> RichToolOutput:
    """Ask a read-only sub-agent to explore the codebase and answer a question.

    Use this for broad codebase exploration when you need to search across
    multiple files or don't know where to look (e.g. "how is authentication
    implemented?", "find all API route definitions", "what files handle
    database migrations?").

    For targeted lookups of a specific file or symbol you already know about,
    use read_file or grep_files directly — they are faster for simple queries.

    You can run multiple explore calls in parallel when investigating different
    aspects of the codebase.

    Args:
        question: A precise question about the codebase to investigate.
        model: Optional model override. Defaults to the session's current model.
    """
    import contextlib

    from agents import RunConfig, Runner

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime(model) if model else resolve_model_for_runtime()

    agent_yaml = Path(__file__).resolve().parent.parent / "omni_agents" / "explorer" / "agent.yml"
    spec = load_agent_spec_from_yaml(str(agent_yaml))

    resolved_model = model_runtime.get("model") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    agent = await _default_build_agent(settings={}, mcp_servers=None, spec=spec)

    model_provider, env_overrides = _build_model_provider(model_runtime or {})
    run_config = RunConfig(model_provider=model_provider) if model_provider is not None else None

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

    with _temporary_environ(env_overrides):
        result = await Runner.run(agent, question, max_turns=50, run_config=run_config)

    try:
        output = result.final_output_as(str)
    except Exception:
        output = "Explorer agent failed to produce a result."

    preview = output[:1500] if output else ""
    truncated = len(output) > 1500 if output else False
    question_summary = question[:60] + "..." if len(question) > 60 else question

    return RichToolOutput(output, {
        "display_type": "search_results",
        "summary": f"Explored: {question_summary}",
        "preview": preview,
        "truncated": truncated,
        "metadata": {
            "success": output != "Explorer agent failed to produce a result.",
            "question": question,
            "content_length": len(output) if output else 0,
        },
    })
