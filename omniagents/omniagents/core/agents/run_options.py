"""Resolution helpers for SDK-native run-time toggles surfaced in YAML.

Supports retry policies (`agents.retry`), error handlers
(`agents.run_error_handlers`), tool execution limits, and a few
RunConfig-level booleans. Each helper accepts the raw YAML dict shape
and returns the SDK object (or ``None``) ready to be plugged into
``RunConfig`` / ``ModelSettings`` / ``Runner.run`` kwargs.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, Iterable, Optional


def _resolve_dotted(path: str) -> Callable[..., Any]:
    """Resolve ``module.sub:attr`` or ``module.sub.attr`` to a callable."""
    if ":" in path:
        module_name, attr = path.split(":", 1)
    else:
        module_name, _, attr = path.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"Invalid dotted path: {path!r}")
    module = importlib.import_module(module_name)
    target = getattr(module, attr)
    if not callable(target):
        raise TypeError(f"{path} did not resolve to a callable")
    return target


def resolve_retry_settings(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Coerce a YAML ``retry_policy`` block into ``ModelRetrySettings``.

    Accepted shape::

        retry_policy:
          name: network_error  # or never, provider_suggested, retry_after,
                               #        http_status, all
          statuses: [429, 502, 503]   # for http_status
          policies: [network_error, retry_after]   # for `all`
          custom: pkg.module:my_policy   # alternative to `name`
          max_retries: 3
          backoff:
            initial_delay: 1.0
            max_delay: 30.0
            multiplier: 2.0
            jitter: true
    """
    if not config:
        return None
    try:
        from agents.retry import (
            ModelRetryBackoffSettings,
            ModelRetrySettings,
            retry_policies,
        )
    except Exception:  # pragma: no cover — SDK pin guarantees this
        return None

    backoff_cfg = config.get("backoff")
    backoff: Any = None
    if isinstance(backoff_cfg, dict):
        backoff = ModelRetryBackoffSettings(**backoff_cfg)

    policy: Any = None
    custom = config.get("custom")
    if custom:
        policy = _resolve_dotted(str(custom))
    else:
        name = config.get("name")
        if name:
            factory = getattr(retry_policies, str(name), None)
            if factory is None:
                raise ValueError(
                    f"Unknown retry_policy name {name!r}. Expected one of: "
                    "never, provider_suggested, network_error, retry_after, "
                    "http_status, all"
                )
            if name == "http_status":
                statuses = config.get("statuses") or []
                policy = factory(statuses)
            elif name == "all":
                inner_names = config.get("policies") or []
                inner_policies = [
                    getattr(retry_policies, str(n))() for n in inner_names
                ]
                policy = factory(*inner_policies)
            else:
                policy = factory()

    return ModelRetrySettings(
        max_retries=config.get("max_retries"),
        backoff=backoff,
        policy=policy,
    )


def resolve_error_handlers(
    config: Optional[Dict[str, Any]],
    *,
    registry: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Optional[Dict[str, Callable[..., Any]]]:
    """Map a YAML ``error_handlers`` block to a ``RunErrorHandlers`` dict.

    Accepted shape::

        error_handlers:
          max_turns: pkg.module:on_max_turns
          model_refusal: pkg.module:on_refusal

    Names are resolved against *registry* first (matching the discovery
    pattern used by guardrails), falling back to dotted-path import.
    """
    if not config:
        return None
    handlers: Dict[str, Callable[..., Any]] = {}
    for kind, target in config.items():
        if not target:
            continue
        if registry and isinstance(target, str) and target in registry:
            handlers[kind] = registry[target]
            continue
        if callable(target):
            handlers[kind] = target
            continue
        handlers[kind] = _resolve_dotted(str(target))
    return handlers or None


def resolve_tool_execution_config(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Coerce a YAML ``tool_execution`` block to ``ToolExecutionConfig``."""
    if not config:
        return None
    try:
        from agents.run_config import ToolExecutionConfig
    except Exception:  # pragma: no cover
        return None
    allowed: Iterable[str] = ("max_function_tool_concurrency",)
    return ToolExecutionConfig(**{k: config[k] for k in allowed if k in config})


def _expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` strings against the process environment."""
    import os

    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1])
    return value


def build_model_provider(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Construct an SDK ``ModelProvider`` from a config dict.

    Accepted shapes::

        # Default OpenAI / OpenAI-compatible / Azure with explicit creds:
        {"type": "openai", "base_url": "...", "api_key": "..."}
        {"type": "openai-compatible", "base_url": "...", "api_key": "..."}
        {"type": "azure", "base_url": "...", "api_key": "..."}

        # LiteLLM (routes to any provider via litellm):
        {"type": "litellm"}

        # any-llm:
        {"type": "any_llm", "api_key": "...", "base_url": "...",
         "api": "responses" | "chat_completions"}

        # MultiProvider with prefix routing (the SDK default — usually
        # nothing to configure unless you want non-default OpenAI creds):
        {"type": "multi", "openai_base_url": "...", "openai_api_key": "..."}

    Returns ``None`` when ``config`` is empty so callers fall back to the
    SDK's default ``MultiProvider``.
    """
    import os

    if not config:
        return None
    provider_type = (config.get("type") or "openai").lower()

    if provider_type == "litellm":
        try:
            from agents.extensions.models.litellm_provider import LitellmProvider
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "LiteLLM provider not installed. Install with: pip install 'openai-agents[litellm]'"
            ) from e
        return LitellmProvider()

    if provider_type in ("any-llm", "any_llm"):
        try:
            from agents.extensions.models.any_llm_provider import AnyLLMProvider
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "any-llm provider not installed. Install the relevant openai-agents extra."
            ) from e
        return AnyLLMProvider(
            api_key=_expand_env(config.get("api_key")),
            base_url=_expand_env(config.get("base_url")),
            api=config.get("api"),
        )

    if provider_type == "multi":
        from agents.models.multi_provider import MultiProvider

        return MultiProvider(
            openai_api_key=_expand_env(config.get("openai_api_key")),
            openai_base_url=_expand_env(config.get("openai_base_url")),
            openai_organization=_expand_env(config.get("openai_organization")),
            openai_project=_expand_env(config.get("openai_project")),
            openai_use_responses=config.get("openai_use_responses"),
            openai_prefix_mode=config.get("openai_prefix_mode", "alias"),
            unknown_prefix_mode=config.get("unknown_prefix_mode", "error"),
        )

    # Default branch: OpenAI / OpenAI-compatible / Azure
    from openai import AsyncOpenAI
    from agents.models.openai_provider import OpenAIProvider

    base_url = _expand_env(config.get("base_url"))
    api_key = _expand_env(config.get("api_key"))
    if not api_key:
        if provider_type == "azure":
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
        else:
            api_key = os.getenv("OPENAI_API_KEY")
    if not base_url:
        base_url = "https://api.openai.com/v1"

    is_azure = "openai.azure.com" in base_url or "services.ai.azure.com" in base_url
    if api_key and is_azure:
        client = AsyncOpenAI(
            api_key="dummy",
            base_url=base_url,
            default_headers={"api-key": api_key},
        )
        return OpenAIProvider(openai_client=client)
    if api_key:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return OpenAIProvider(openai_client=client)
    return None
