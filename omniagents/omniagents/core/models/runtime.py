"""Runtime-side plumbing over the model store: ref → runtime dict → provider.

The store's :meth:`~omniagents.core.models.ModelConfigStore.resolve_model_for_runtime`
produces a runtime dict (keys ``provider``/``model``/``api_key``/``base_url``/…);
the SDK-facing :func:`omniagents.core.agents.run_options.build_model_provider`
consumes a different shape (keyed by ``type``). This module owns the seams
between them, lifted from omni-code so every product stops re-implementing
the same dance:

- :func:`resolve_model_runtime` — one entry point for the CLI-side
  resolution dance (ref → ``(model_name, runtime)``).
- :func:`resolve_service_model_runtime` — the utility-run preference
  order (session active model → service spec model → raw session
  ``model_config``).
- :func:`provider_from_runtime` — runtime dict → SDK model provider +
  env overrides, including the "no credential → no provider" and
  dummy-key-for-local-endpoints fallbacks the plain builder does not
  apply.

All three take an explicit ``store=`` or fall back to the process-wide
registry installed via :func:`omniagents.core.models.set_model_store`.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .store import ModelConfigStore, litellm_api_key_env, normalize_model_name

__all__ = [
    "resolve_model_runtime",
    "resolve_service_model_runtime",
    "provider_from_runtime",
]


def _active_store(store: Optional[ModelConfigStore]) -> Optional[ModelConfigStore]:
    if store is not None:
        return store
    from omniagents.core.models import model_store

    return model_store()


def resolve_model_runtime(
    model_ref: Optional[str] = None,
    *,
    store: Optional[ModelConfigStore] = None,
    normalize: bool = True,
) -> tuple[Optional[str], dict[str, Any]]:
    """Resolve *model_ref* (or the configured default) into a
    ``(model_name, runtime)`` pair.

    With ``normalize`` (the default) the name has non-openai/litellm
    provider prefixes stripped (see :func:`normalize_model_name`);
    without, the runtime's raw model id is kept. Returns ``(None, {})``
    when no model is configured or no store is installed.
    """
    target = _active_store(store)
    if target is None:
        return None, {}
    runtime = target.resolve_model_for_runtime(model_ref)
    if not runtime:
        return None, {}
    if normalize:
        return normalize_model_name(model_ref, runtime), runtime
    model = runtime.get("model")
    model_name = model.strip() if isinstance(model, str) and model.strip() else None
    return model_name, runtime


def resolve_service_model_runtime(
    service: Any,
    session: Any,
    *,
    store: Optional[ModelConfigStore] = None,
) -> tuple[Optional[str], dict[str, Any]]:
    """Resolve the model runtime a utility (ephemeral) agent should run on.

    Preference order: the session's active model, then the service spec's
    model, resolved through the model store; as a last resort the
    session's raw ``model_config`` is adapted into a runtime dict.
    """
    target = _active_store(store)
    if target is not None and target.single_provider:
        ref = getattr(target, "_run_model_ref", None)
        if ref is None:
            target.reconcile_session_model(session)
            ref = session.active_model
        return ref, target.resolve_model_for_runtime(ref)
    candidates: list[str] = []
    active_model = getattr(session, "active_model", None)
    if isinstance(active_model, str) and active_model.strip():
        candidates.append(active_model.strip())
    spec_model = getattr(getattr(service, "spec", None), "model_name", None)
    if isinstance(spec_model, str) and spec_model.strip():
        candidates.append(spec_model.strip())

    target = _active_store(store)
    if target is not None:
        for ref in candidates:
            runtime = target.resolve_model_for_runtime(ref)
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


def provider_from_runtime(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    """Turn a resolved model-runtime dict into an SDK model provider.

    Adapts the store's runtime-dict shape (keyed by ``provider``) into
    the config shape of
    :func:`omniagents.core.agents.run_options.build_model_provider`
    (keyed by ``type``, which also owns registered custom builders such
    as ``openai-oauth``), and layers on the credential policy the plain
    builder does not apply:

    - litellm runtimes yield the provider-appropriate API-key env
      override (see :func:`litellm_api_key_env`);
    - openai/openai-compatible/azure runtimes fall back to the
      conventional env vars, and with no usable credential against a
      real OpenAI/Azure endpoint return ``(None, {})`` so callers fall
      through to the SDK default provider;
    - local / openai-compatible endpoints that don't check keys get a
      ``"dummy"`` key so the client constructs.

    Returns ``(provider, env_overrides)``; env overrides are only
    returned when a provider was actually built.
    """
    provider = (runtime.get("provider") or "").strip().lower()
    if not provider:
        return None, {}

    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    if provider == "litellm" and runtime.get("routing_prefix"):
        from agents.models.interface import ModelProvider
        from agents.extensions.models.litellm_model import LitellmModel

        prefix = runtime["routing_prefix"]
        if not api_key:
            raise ValueError("Configure credentials on the selected LiteLLM provider.")

        class ScopedLiteLLMProvider(ModelProvider):
            def get_model(self, model_name):
                selected = model_name or model
                if not isinstance(selected, str) or not selected.startswith(
                    prefix + "/"
                ):
                    raise ValueError(
                        "The model must use its provider's routing prefix."
                    )
                return LitellmModel(selected, api_key=api_key, base_url=base_url)

        return ScopedLiteLLMProvider(), {}

    env_overrides: dict[str, str | None] = {}
    if provider == "litellm" and api_key:
        env_overrides = {litellm_api_key_env(model): api_key}

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

    from omniagents.core.agents import run_options

    provider_obj = run_options.build_model_provider(config)
    return provider_obj, (env_overrides if provider_obj is not None else {})
