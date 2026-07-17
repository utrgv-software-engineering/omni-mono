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
from typing import Any, Optional


def session_workspace_root(session: Any) -> Optional[str]:
    """The workspace_root stored on a session, or ``None``.

    Single source of the "check ``session.context`` (dict or attr), then
    ``session.variables``" read that server functions need — the framework's
    ``workspace_root_from_ctx`` covers the RunContextWrapper shape, not the
    Session shape.
    """
    ctx = getattr(session, "context", None)
    if isinstance(ctx, dict):
        value = ctx.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    elif ctx is not None:
        value = getattr(ctx, "workspace_root", None)
        if isinstance(value, str) and value.strip():
            return value
    variables = getattr(session, "variables", None)
    if isinstance(variables, dict):
        value = variables.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    return None


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
    from omni_code.models import litellm_api_key_env

    return {litellm_api_key_env(model): api_key}


def _build_model_provider(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    """Turn a resolved model-runtime dict into an SDK model provider.

    Construction is delegated to the framework's
    ``run_options.build_model_provider`` (which also owns the registered
    ``openai-oauth`` codex builder). What stays here is product policy:
    the runtime-dict key shape (``provider`` vs the framework's ``type``),
    litellm env overrides, and credential resolution — including the
    "no credential → no provider" and dummy-key-for-local-endpoints
    fallbacks the framework builder does not apply.
    """
    provider = (runtime.get("provider") or "").strip().lower()
    if not provider:
        return None, {}

    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    env_overrides: dict[str, str | None] = {}
    if provider == "litellm":
        env_overrides = _litellm_env_overrides(model=model, api_key=api_key)

    if provider == "openai-oauth":
        # Bind the codex token store to omni-code's config dir before the
        # framework builder constructs the refreshing client.
        import omni_code.codex_auth  # noqa: F401

    if provider in {"openai", "openai-compatible", "azure"}:
        base_url = base_url or "https://api.openai.com/v1"
        if not api_key:
            if provider == "azure":
                api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            is_azure = (
                "openai.azure.com" in base_url or "services.ai.azure.com" in base_url
            )
            if is_azure or base_url == "https://api.openai.com/v1":
                # No usable credential — run without an explicit provider.
                return None, {}
            # Local / openai-compatible endpoints don't check keys.
            api_key = "dummy"

    config: dict[str, Any] = {"type": provider}
    if api_key is not None:
        config["api_key"] = api_key
    if base_url is not None:
        config["base_url"] = base_url
    for key in ("use_responses", "use_responses_websocket"):
        if runtime.get(key) is not None:
            config[key] = bool(runtime[key])

    from omniagents.core.agents.run_options import build_model_provider

    provider_obj = build_model_provider(config)
    return provider_obj, (env_overrides if provider_obj is not None else {})
