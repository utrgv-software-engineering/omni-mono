"""Allowed services: the models, integrations and MCP servers a deployment permits.

Whoever runs a factory (Workplace, under its strict policy) sets these where the
factory's own project cannot change them:

* ``OMNIAGENTS_ALLOWED_MODEL_ENDPOINTS``: JSON list of ``{"provider": ...,
  "base_url": ...}``. Every model the runtime resolves must use one (the model
  store refuses others). ``base_url`` is a prefix; omitted, the provider's own
  endpoint (OpenAI's API, ChatGPT sign-in) is meant.
* ``OMNIAGENTS_ALLOWED_INTEGRATIONS``: JSON list of ``{"type": ..., "url": ...}``.
  Only these ``integrations:`` start; ``url``, when given, prefixes the
  integration's API address.
* MCP servers: ``security.mcp_allowed_servers`` entries are a server name, or
  ``name=<identity>``: the server's URL prefix, or ``command:<executable>``. A
  server that takes an allowed name with another address is refused.

Unset, nothing is restricted.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, List, Mapping, Optional
from urllib.parse import urlsplit

MODELS_ENV = "OMNIAGENTS_ALLOWED_MODEL_ENDPOINTS"
INTEGRATIONS_ENV = "OMNIAGENTS_ALLOWED_INTEGRATIONS"

#: Where a provider goes when its configuration names no endpoint.
DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "openai-oauth": "https://chatgpt.com",
}

__all__ = [
    "ServiceNotAllowed",
    "integration_allowed",
    "mcp_server_allowed",
    "model_allowed",
    "require_model",
]


class ServiceNotAllowed(ValueError):
    """A model, integration or MCP server the deployment does not allow."""


def _entries(
    name: str, environ: Optional[Mapping[str, str]] = None
) -> Optional[List[dict]]:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
        raise ServiceNotAllowed(f"{name} must be a JSON list of objects")
    return value


def _prefix(url: Optional[str], allowed: Optional[str]) -> bool:
    """Whether ``url`` is ``allowed`` or below it: same scheme and host, path inside."""
    if not allowed:
        return True
    if not url:
        return False
    a, b = urlsplit(url.rstrip("/")), urlsplit(allowed.rstrip("/"))
    return (
        a.scheme.lower() == b.scheme.lower()
        and a.netloc.lower() == b.netloc.lower()
        and (a.path == b.path or a.path.startswith(b.path.rstrip("/") + "/"))
    )


def model_allowed(runtime: Mapping[str, Any], environ=None) -> bool:
    entries = _entries(MODELS_ENV, environ)
    if entries is None:
        return True
    provider = str(runtime.get("provider") or "openai").lower()
    base_url = runtime.get("base_url") or DEFAULT_ENDPOINTS.get(provider)
    return any(
        str(e.get("provider") or "").lower() == provider
        and _prefix(base_url, e.get("base_url") or DEFAULT_ENDPOINTS.get(provider))
        for e in entries
    )


def require_model(runtime: Mapping[str, Any], environ=None) -> None:
    if not model_allowed(runtime, environ):
        provider = runtime.get("provider") or "openai"
        where = runtime.get("base_url") or DEFAULT_ENDPOINTS.get(str(provider), "")
        raise ServiceNotAllowed(
            f"The model endpoint {provider} {where} is not one of the allowed services"
        )


def integration_allowed(entry: Mapping[str, Any], environ=None) -> bool:
    entries = _entries(INTEGRATIONS_ENV, environ)
    if entries is None:
        return True
    kind = str(entry.get("type") or "")
    url = entry.get("api_url") or entry.get("url")
    return any(
        e.get("type") == kind and (not e.get("url") or _prefix(url, e.get("url")))
        for e in entries
    )


def _identity(config: Any) -> tuple[Optional[str], Optional[str]]:
    params = getattr(config, "params", None) or {}
    return params.get("url"), params.get("command")


def mcp_server_allowed(config: Any, allowed: Iterable[str]) -> bool:
    """Whether an MCP server config is on ``security.mcp_allowed_servers``."""
    url, command = _identity(config)
    for entry in allowed:
        name, _, identity = str(entry).partition("=")
        if name.strip() != getattr(config, "name", None):
            continue
        identity = identity.strip()
        if not identity:
            return True
        if identity.startswith("command:"):
            if command and command == identity[len("command:") :].strip():
                return True
        elif _prefix(url, identity):
            return True
    return False
