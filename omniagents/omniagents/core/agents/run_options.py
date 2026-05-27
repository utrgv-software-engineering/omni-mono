"""Resolution helpers for SDK-native run-time toggles surfaced in YAML.

Supports retry policies (`agents.retry`), error handlers
(`agents.run_error_handlers`), tool execution limits, RunConfig-level
booleans, and ``model_provider`` construction (OpenAI / MultiProvider /
LiteLLM / AnyLLM, with optional Responses-over-WebSocket transport and
reasoning-content replay).
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable, Dict, Iterable, Optional

from omniagents.core.utils.logging import get_logger

logger = get_logger(__name__)


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
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1])
    return value


# Custom provider-type builders, registered by downstream packages that need a
# provider type omniagents can't construct itself (e.g. an OAuth-authenticated
# client whose token store/refresh lives outside this layer). Keyed by the
# lowercased ``model_provider.type``; the builder receives the config dict and
# returns an SDK ``ModelProvider`` (or ``None`` to fall through).
_CUSTOM_PROVIDER_BUILDERS: Dict[str, Callable[[Dict[str, Any]], Optional[Any]]] = {}


def register_model_provider_builder(
    provider_type: str, builder: Callable[[Dict[str, Any]], Optional[Any]]
) -> None:
    """Register a builder for a non-built-in ``model_provider.type``.

    Lets a dependent package (which may import SDK/provider modules omniagents
    doesn't) supply a provider without omniagents taking a dependency on it.
    Idempotent per type — the latest registration wins.
    """
    _CUSTOM_PROVIDER_BUILDERS[provider_type.lower().strip()] = builder


def build_model_provider(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Construct an SDK ``ModelProvider`` from a config dict.

    Recognized keys::

        type: "openai" | "openai-compatible" | "azure" | "multi" | "litellm" | "any-llm"
        api_key: str
        base_url: str
        websocket_base_url: str
        organization: str
        project: str
        use_responses: bool
        use_responses_websocket: bool
        websocket_options: { ping_interval, ping_timeout, ... }
        # multi-provider only
        openai_prefix_mode: "alias" | "model_id"
        unknown_prefix_mode: "error" | "model_id"
        # any-llm only
        api: "responses" | "chat_completions"
        # OmniAgents-side wrapper
        reasoning_content_replay:
          enabled: bool
          should_replay: pkg.mod:fn   # optional override predicate

    Returns ``None`` when ``config`` is empty so callers fall back to the
    SDK's default ``MultiProvider`` (which itself handles prefix routing
    for ``litellm/...`` / ``any-llm/...`` / ``openai/...``).
    """
    if not config or not isinstance(config, dict):
        return None

    provider_type = (config.get("type") or "openai").lower().strip()
    api_key = _expand_env(config.get("api_key"))
    base_url = _expand_env(config.get("base_url"))
    websocket_base_url = _expand_env(config.get("websocket_base_url"))
    organization = _expand_env(config.get("organization"))
    project = _expand_env(config.get("project"))
    use_responses = config.get("use_responses")
    use_responses_websocket = config.get("use_responses_websocket")

    websocket_options = config.get("websocket_options") or None
    if websocket_options is not None and not isinstance(websocket_options, dict):
        logger.warning("model_provider.websocket_options must be a dict; ignoring")
        websocket_options = None

    provider: Any = None

    if provider_type == "litellm":
        try:
            from agents.extensions.models.litellm_provider import LitellmProvider
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "LiteLLM provider not installed. Install with: pip install 'openai-agents[litellm]'"
            ) from e
        provider = LitellmProvider()

    elif provider_type in ("any-llm", "any_llm"):
        try:
            from agents.extensions.models.any_llm_provider import AnyLLMProvider
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "any-llm provider not installed. Install the relevant openai-agents extra."
            ) from e
        provider = AnyLLMProvider(
            api_key=api_key,
            base_url=base_url,
            api=config.get("api"),
        )

    elif provider_type == "multi":
        from agents.models.multi_provider import MultiProvider

        kwargs: Dict[str, Any] = {}
        if api_key is not None:
            kwargs["openai_api_key"] = api_key
        if base_url is not None:
            kwargs["openai_base_url"] = base_url
        if websocket_base_url is not None:
            kwargs["openai_websocket_base_url"] = websocket_base_url
        if organization is not None:
            kwargs["openai_organization"] = organization
        if project is not None:
            kwargs["openai_project"] = project
        if use_responses is not None:
            kwargs["openai_use_responses"] = bool(use_responses)
        if use_responses_websocket is not None:
            kwargs["openai_use_responses_websocket"] = bool(use_responses_websocket)
        if websocket_options is not None:
            kwargs["openai_responses_websocket_options"] = websocket_options
        if "openai_prefix_mode" in config:
            kwargs["openai_prefix_mode"] = config["openai_prefix_mode"]
        if "unknown_prefix_mode" in config:
            kwargs["unknown_prefix_mode"] = config["unknown_prefix_mode"]
        provider = MultiProvider(**kwargs)

    elif provider_type in ("openai", "openai-compatible", "azure"):
        from agents.models.openai_provider import OpenAIProvider

        # Default OpenAI / OpenAI-compatible / Azure with explicit creds.
        if not api_key:
            if provider_type == "azure":
                api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                api_key = os.getenv("OPENAI_API_KEY")
        if not base_url:
            base_url = "https://api.openai.com/v1"

        is_azure = "openai.azure.com" in base_url or "services.ai.azure.com" in base_url
        if api_key and is_azure:
            # Azure needs api-key header instead of Bearer.
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key="dummy",
                base_url=base_url,
                default_headers={"api-key": api_key},
            )
            provider = OpenAIProvider(openai_client=client)
        else:
            kwargs = {}
            if api_key is not None:
                kwargs["api_key"] = api_key
            if base_url is not None:
                kwargs["base_url"] = base_url
            if websocket_base_url is not None:
                kwargs["websocket_base_url"] = websocket_base_url
            if organization is not None:
                kwargs["organization"] = organization
            if project is not None:
                kwargs["project"] = project
            if use_responses is not None:
                kwargs["use_responses"] = bool(use_responses)
            if use_responses_websocket is not None:
                kwargs["use_responses_websocket"] = bool(use_responses_websocket)
            if websocket_options is not None:
                kwargs["responses_websocket_options"] = websocket_options
            provider = OpenAIProvider(**kwargs) if kwargs else None
    elif provider_type in _CUSTOM_PROVIDER_BUILDERS:
        provider = _CUSTOM_PROVIDER_BUILDERS[provider_type](config)
        if provider is None:
            logger.warning(f"Registered builder for {provider_type!r} returned no provider")
            return None
    else:
        logger.warning(f"Unknown model_provider.type {provider_type!r}")
        return None

    # Reasoning content replay opt-in. The SDK exposes this as a per-model
    # attribute on chat-completions / litellm models, not on ModelProvider,
    # so we wrap the provider's get_model() to attach the predicate.
    rcr = config.get("reasoning_content_replay")
    if isinstance(rcr, dict) and rcr.get("enabled") and provider is not None:
        try:
            provider = _wrap_provider_with_reasoning_replay(provider, rcr)
        except Exception as e:
            logger.warning(f"reasoning_content_replay wrap failed: {e}")

    return provider


def _wrap_provider_with_reasoning_replay(
    provider: Any, rcr_config: Dict[str, Any]
) -> Any:
    """Return a ``ModelProvider`` wrapper that applies reasoning-content replay.

    The wrapper delegates ``get_model()`` to *provider* and attaches
    ``should_replay_reasoning_content`` to the returned model when the model
    type supports it (``OpenAIChatCompletionsModel`` and the litellm
    extension model). Responses-API models silently ignore it.
    """
    from agents.models.interface import Model, ModelProvider
    from agents.models.reasoning_content_replay import (
        default_should_replay_reasoning_content,
    )

    user_pred = rcr_config.get("should_replay")
    if isinstance(user_pred, str):
        predicate = _resolve_dotted(user_pred)
    elif callable(user_pred):
        predicate = user_pred
    else:
        predicate = default_should_replay_reasoning_content

    class _ReasoningReplayProvider(ModelProvider):
        def __init__(self, inner: ModelProvider) -> None:
            self._inner = inner
            # Pass through select attrs for ResponsesWebSocketSession alignment.
            for attr in ("openai_provider", "agent_registration"):
                if hasattr(inner, attr):
                    try:
                        setattr(self, attr, getattr(inner, attr))
                    except Exception:
                        pass

        def get_model(self, model_name: Optional[str]) -> Model:
            model = self._inner.get_model(model_name)
            try:
                if hasattr(model, "should_replay_reasoning_content"):
                    setattr(model, "should_replay_reasoning_content", predicate)
            except Exception:
                pass
            return model

        def __getattr__(self, item):
            return getattr(self._inner, item)

    return _ReasoningReplayProvider(provider)
