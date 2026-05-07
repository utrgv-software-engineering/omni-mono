"""Run-level option helpers (model_provider construction, etc.).

This module centralizes how OmniAgents builds the ``model_provider`` that gets
passed to the SDK ``Runner``. It supports:

* Plain ``OpenAIProvider`` (default)
* ``MultiProvider`` for prefix-based routing
* Responses-over-WebSocket transport (SDK 0.16+)
* Reasoning content replay hook for chat-completions-style models

The session model_config path in ``service.py`` still constructs an
``OpenAIProvider`` for explicit api_key/base_url overrides; this builder is
used when the YAML opts in via ``model_provider:``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from omniagents.core.utils.logging import get_logger

logger = get_logger(__name__)


def _expand_env(val: Any) -> Any:
    if val and isinstance(val, str) and val.startswith("${") and val.endswith("}"):
        return os.getenv(val[2:-1])
    return val


def build_model_provider(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Construct a ``ModelProvider`` from a YAML ``model_provider:`` block.

    Returns ``None`` when ``config`` is empty/missing, signaling that callers
    should fall back to existing behavior (session.model_config or SDK default).

    Recognized keys::

        type: "openai" | "multi"
        api_key: str
        base_url: str
        websocket_base_url: str
        organization: str
        project: str
        use_responses: bool
        use_responses_websocket: bool
        websocket_options:
          ping_interval: float
          ping_timeout: float
        # multi-provider only
        openai_prefix_mode: "alias" | "model_id"
        unknown_prefix_mode: "error" | "model_id"
        # OmniAgents extension
        reasoning_content_replay:
          enabled: bool
          # plus optional ReasoningContentReplayContext-derived knobs
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

    # Build provider
    if provider_type in ("openai", "openai-compatible", "azure"):
        try:
            from agents.models.openai_provider import OpenAIProvider
        except Exception as e:
            logger.error(f"Could not import OpenAIProvider: {e}")
            return None

        kwargs: Dict[str, Any] = {}
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

        try:
            provider = OpenAIProvider(**kwargs)
        except Exception as e:
            logger.error(f"Failed to create OpenAIProvider: {e}")
            return None

    elif provider_type == "multi":
        try:
            from agents.models.multi_provider import MultiProvider
        except Exception as e:
            logger.error(f"Could not import MultiProvider: {e}")
            return None

        kwargs = {}
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

        try:
            provider = MultiProvider(**kwargs)
        except Exception as e:
            logger.error(f"Failed to create MultiProvider: {e}")
            return None

    elif provider_type == "litellm":
        try:
            from agents.extensions.models.litellm_provider import LitellmProvider

            provider = LitellmProvider()
        except Exception as e:
            logger.error(f"Could not create LitellmProvider: {e}")
            return None
    else:
        logger.warning(f"Unknown model_provider.type '{provider_type}'")
        return None

    # Reasoning content replay opt-in.
    # The SDK exposes this as a per-model constructor arg on chat-completions
    # and litellm models, NOT on ModelProvider. Without subclassing the
    # provider we can't mutate constructed models. We log the intent so
    # callers know it's accepted but not yet hard-wired into provider.get_model().
    rcr = config.get("reasoning_content_replay")
    if isinstance(rcr, dict) and rcr.get("enabled"):
        try:
            provider = _wrap_provider_with_reasoning_replay(provider, rcr)
        except Exception as e:
            logger.warning(f"reasoning_content_replay wrap failed: {e}")

    return provider


def _wrap_provider_with_reasoning_replay(
    provider: Any, rcr_config: Dict[str, Any]
) -> Any:
    """Return a ModelProvider wrapper that applies reasoning-content replay.

    The wrapper delegates ``get_model()`` to the inner provider and attaches
    ``should_replay_reasoning_content`` to the returned model when supported
    (``OpenAIChatCompletionsModel`` and litellm extension model).
    """
    from agents.models.interface import Model, ModelProvider
    from agents.models.reasoning_content_replay import (
        default_should_replay_reasoning_content,
    )

    # Resolve the predicate. If a user provides a callable directly, use it.
    # Otherwise, default to the SDK's DeepSeek-aware default.
    user_pred = rcr_config.get("should_replay")
    if callable(user_pred):
        predicate = user_pred
    else:
        predicate = default_should_replay_reasoning_content

    class _ReasoningReplayProvider(ModelProvider):
        def __init__(self, inner: ModelProvider) -> None:
            self._inner = inner
            # Pass through `openai_provider` for ResponsesWebSocketSession alignment.
            for attr in ("openai_provider", "agent_registration"):
                if hasattr(inner, attr):
                    try:
                        setattr(self, attr, getattr(inner, attr))
                    except Exception:
                        pass

        def get_model(self, model_name: Optional[str]) -> Model:
            model = self._inner.get_model(model_name)
            try:
                # Only chat-completions / litellm models accept this.
                if hasattr(model, "should_replay_reasoning_content"):
                    setattr(model, "should_replay_reasoning_content", predicate)
            except Exception:
                pass
            return model

        def __getattr__(self, item):
            return getattr(self._inner, item)

    return _ReasoningReplayProvider(provider)
