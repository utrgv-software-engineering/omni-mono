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
