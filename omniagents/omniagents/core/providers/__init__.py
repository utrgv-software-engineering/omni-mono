"""Pluggable provider system for enterprise compliance controls.

Providers follow a simple pattern::

    security:
      providers:
        auth:
          type: none        # default — no authentication
        secrets:
          type: env         # default — os.getenv()
        content_scanner:
          type: none        # default — no scanning
        session_encryption:
          type: none        # default — plaintext SQLite

Each domain has a base ABC, a default implementation that ships with
omniagents (zero-config for open-source users), and an extension point
for enterprise implementations.

Third-party packages can register providers via the ``omniagents.providers``
entry-point group::

    [project.entry-points."omniagents.providers"]
    auth_oidc = "mypackage.auth:OIDCAuthProvider"
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

# Registry: domain -> type_name -> provider_class
_REGISTRY: Dict[str, Dict[str, Type]] = {}


def register_provider(domain: str, type_name: str, cls: Type) -> None:
    """Register a provider class for *domain* under *type_name*."""
    _REGISTRY.setdefault(domain, {})[type_name] = cls


def resolve_provider(domain: str, config: Optional[Dict[str, Any]] = None) -> Any:
    """Instantiate and return a provider for *domain*.

    *config* is the dict from ``security.providers.<domain>`` in
    ``project.yml``.  If absent or ``type`` is missing, the ``"none"``
    provider for that domain is used.

    Raises ``KeyError`` if no provider is registered for the requested type.
    """
    config = config or {}
    type_name = str(config.get("type", "none")).lower()
    domain_providers = _REGISTRY.get(domain, {})
    if type_name not in domain_providers:
        # Try loading via entry-points (lazy discovery)
        _load_entry_points(domain, type_name)
        domain_providers = _REGISTRY.get(domain, {})
    if type_name not in domain_providers:
        raise KeyError(
            f"No provider registered for domain='{domain}', type='{type_name}'. "
            f"Available: {sorted(domain_providers.keys())}"
        )
    cls = domain_providers[type_name]
    # Convention: providers accept a config dict in __init__
    return cls(config)


def available_providers(domain: str) -> list[str]:
    """Return registered type names for *domain*."""
    return sorted(_REGISTRY.get(domain, {}).keys())


def _load_entry_points(domain: str, type_name: str) -> None:
    """Attempt to discover a provider via ``importlib.metadata`` entry-points."""
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        # Python 3.12+ returns a SelectableGroups; 3.9-3.11 returns a dict
        group = "omniagents.providers"
        candidates: Any
        if isinstance(eps, dict):
            candidates = eps.get(group, [])
        else:
            candidates = eps.select(group=group)
        for ep in candidates:
            # Entry-point name convention: "{domain}_{type}" e.g. "auth_oidc"
            if ep.name == f"{domain}_{type_name}":
                cls = ep.load()
                register_provider(domain, type_name, cls)
                return
    except Exception:
        pass
