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


# Session-shape workspace-root reading and the ensure+persist dance are
# framework helpers now; re-exported here for omni-code's server functions.
from omniagents.core.server_functions.helpers import (  # noqa: E402,F401
    ensure_session_with_workspace,
    session_workspace_root,
)


def resolve_service_model_runtime(
    service: Any, session: Any
) -> tuple[Optional[str], dict[str, Any]]:
    """Resolve the model runtime a utility agent should run on.

    Preference order: the session's active model, then the service spec's
    model, resolved through omni-code's model store; as a last resort the
    session's raw ``model_config`` is adapted into a runtime dict.
    """
    candidates: list[str] = []
    active_model = getattr(session, "active_model", None)
    if isinstance(active_model, str) and active_model.strip():
        candidates.append(active_model.strip())
    spec_model = getattr(getattr(service, "spec", None), "model_name", None)
    if isinstance(spec_model, str) and spec_model.strip():
        candidates.append(spec_model.strip())

    from omni_code.models import resolve_model_for_runtime

    for ref in candidates:
        runtime = resolve_model_for_runtime(ref)
        if runtime:
            return ref, runtime

    model_config = getattr(session, "model_config", None)
    if isinstance(model_config, dict) and model_config.get("model"):
        runtime = {
            "provider": (model_config.get("provider") or "").strip().lower(),
            "model": model_config.get("model"),
            "api_key": model_config.get("api_key"),
            "base_url": model_config.get("base_url"),
            "api_version": model_config.get("api_version"),
            "use_responses": model_config.get("use_responses"),
            "use_responses_websocket": model_config.get("use_responses_websocket"),
        }
        return candidates[0] if candidates else None, runtime

    return candidates[0] if candidates else None, {}


def normalize_model_name(
    model_ref: Optional[str], runtime: dict[str, Any]
) -> Optional[str]:
    """Strip a non-openai/litellm provider prefix so the bare model id
    reaches the provider."""
    model_name = runtime.get("model") if isinstance(runtime, dict) else None
    if (not isinstance(model_name, str) or not model_name.strip()) and isinstance(
        model_ref, str
    ):
        model_name = model_ref.strip()
    if (
        isinstance(model_name, str)
        and "/" in model_name
        and runtime.get("provider") != "litellm"
    ):
        prefix = model_name.split("/", 1)[0]
        if prefix not in {"openai", "litellm"}:
            model_name = model_name.split("/", 1)[1]
    return model_name


async def run_utility_agent(
    agent_name: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    runtime: Optional[dict[str, Any]] = None,
    session: Any = None,
    workspace_root: Optional[str] = None,
) -> str:
    """Run one of omni-code's headless utility agents (``omni_agents/<name>``)
    through the framework's ephemeral-run harness."""
    from pathlib import Path

    from omniagents.core.runtime import run_agent_once

    agent_yaml = (
        Path(__file__).resolve().parent.parent / "omni_agents" / agent_name / "agent.yml"
    )
    model_provider, env_overrides = _build_model_provider(runtime or {})
    return await run_agent_once(
        agent_yaml,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=workspace_root or ephemeral_workspace_root(session),
    )


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
