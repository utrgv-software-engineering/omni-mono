"""Shared wiring for omni-code's ephemeral (utility) agent runs.

The compact, recap, and session-summarize flows all run a one-shot
utility agent via ``omniagents.core.runtime.run_agent_once``. What is
product-specific — and therefore lives here, once, instead of copied
into each server function — is how omni-code turns a resolved model
runtime dict (``omni_code.models.resolve_model_for_runtime``) into an
SDK model provider + env overrides, and how it anchors workspace_root
for runs that bypass the AgentService resolver.
"""

from __future__ import annotations

import os
from typing import Any


def ephemeral_workspace_root(session: Any = None) -> str:
    """workspace_root for a run that bypasses the AgentService resolver.

    Prefer the caller's session workspace, then the sandbox manifest root
    over the host cwd — they differ for container backends, and an
    unclamped host path would be wrong there. Matches
    fs_get_workspace_root's fallback chain.
    """
    if session is not None:
        sess_ctx = getattr(session, "context", None)
        if isinstance(sess_ctx, dict):
            wr = sess_ctx.get("workspace_root")
            if isinstance(wr, str) and wr.strip():
                return wr
    from omni_code.agent_workspace_state import get_manifest_root

    return get_manifest_root() or os.getcwd()


def _litellm_env_overrides(
    *, model: str | None, api_key: str | None
) -> dict[str, str | None]:
    if not api_key:
        return {}

    model_name = (model or "").lower()
    if model_name.startswith("anthropic/") or model_name.startswith("claude"):
        return {"ANTHROPIC_API_KEY": api_key}
    if model_name.startswith("gemini/") or model_name.startswith("google/"):
        return {"GOOGLE_API_KEY": api_key}
    return {"OPENAI_API_KEY": api_key}


def _build_model_provider(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    provider = (runtime.get("provider") or "").strip().lower()
    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")
    use_responses = runtime.get("use_responses")
    use_responses_websocket = runtime.get("use_responses_websocket")

    provider_kwargs: dict[str, Any] = {}
    if use_responses is not None:
        provider_kwargs["use_responses"] = bool(use_responses)
    if use_responses_websocket is not None:
        provider_kwargs["use_responses_websocket"] = bool(use_responses_websocket)

    if provider == "openai-oauth":
        from agents.models.openai_provider import OpenAIProvider
        from omni_code.codex_auth import build_codex_openai_client

        client = build_codex_openai_client()
        if client is None:
            return None, {}
        return OpenAIProvider(openai_client=client, **provider_kwargs), {}

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
            return OpenAIProvider(openai_client=client, **provider_kwargs), {}

        if not resolved_api_key and resolved_base_url != "https://api.openai.com/v1":
            resolved_api_key = "dummy"

        if not resolved_api_key:
            return None, {}

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        return OpenAIProvider(openai_client=client, **provider_kwargs), {}

    return None, {}
