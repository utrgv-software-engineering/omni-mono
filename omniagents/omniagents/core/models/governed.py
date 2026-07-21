"""Governed model config: the ``models.governed.json`` producer/consumer.

A management plane (or any governance source) may approve model
providers and a default model. This module owns the on-disk artifact:
a governed config written next to the user's ``models.json`` that
:class:`~omniagents.core.models.ModelConfigStore` consumes through its
``governed_config`` hook.

Semantics (read back by the store's layered merge):

- ``allow_user_models=True``  → governed providers form the *base* that
  user config merges on top of (user config can add models).
- ``allow_user_models=False`` → governed providers *replace* user config
  entirely (user can't use unapproved providers/models).

This module also owns the standard policy-to-governed-file application
(:func:`apply_platform_model_governance`): the omni-platform policy
document's model-governance keys (top-level ``model_providers``,
``project.allow_user_models``, ``project.default_model``) are a platform
client contract, so their extraction lives here too — a product needs no
model-governance code of its own.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .store import MODELS_CONFIG_VERSION

__all__ = [
    "GOVERNED_CONFIG_FILENAME",
    "governed_config_path",
    "write_governed_config",
    "load_governed_config",
    "get_cached_platform_policy",
    "fetch_and_cache_platform_policy",
    "apply_platform_model_governance",
]

logger = logging.getLogger("omniagents.core.models.governed")

GOVERNED_CONFIG_FILENAME = "models.governed.json"


def governed_config_path(config_dir: Path | str) -> Path:
    """Where the governed config lives inside *config_dir* (next to models.json)."""
    return Path(config_dir) / GOVERNED_CONFIG_FILENAME


def write_governed_config(
    config_dir: Path | str,
    providers: Dict[str, Any],
    default_model: Optional[str] = None,
    *,
    allow_user_models: bool = True,
) -> Path:
    """Write the governed model config that takes precedence over user config.

    Each provider dict is stamped with ``_governed: True`` (copies — the
    caller's dicts are left untouched) so downstream code can tell a
    platform-supplied provider from a user one, and the file carries the
    ``_governed`` / ``_allow_user_models`` markers the store's layered
    merge reads back. Written with owner-only (0600) permissions.

    Returns the path written.
    """
    stamped: Dict[str, Any] = {}
    for name, provider_config in providers.items():
        if isinstance(provider_config, dict):
            stamped[name] = {**provider_config, "_governed": True}
        else:
            stamped[name] = provider_config

    config = {
        "version": MODELS_CONFIG_VERSION,
        "default": default_model,
        "voice_default": None,
        "providers": stamped,
        "_governed": True,
        "_allow_user_models": allow_user_models,
    }

    path = governed_config_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(config, indent=2)

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return path

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return path


def load_governed_config(config_dir: Path | str) -> Optional[Dict[str, Any]]:
    """Load the governed model config if present and marked, else None.

    A file without the ``_governed`` marker (or unreadable/invalid JSON)
    is treated as absent — governance must be explicit.
    """
    path = governed_config_path(config_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("_governed"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Platform policy -> governed file (the standard application)
# ---------------------------------------------------------------------------


def get_cached_platform_policy() -> Optional[Dict[str, Any]]:
    """The cached management-plane policy, or None.

    Checked in order: the ``OMNI_PLATFORM_POLICY`` env var (container
    injection by the launcher), then the ``cached_policy`` stored with
    the platform credentials.
    """
    raw = os.getenv("OMNI_PLATFORM_POLICY")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    from omniagents.core.providers.platform.client import load_credentials

    creds = load_credentials()
    if creds and creds.get("cached_policy"):
        return creds["cached_policy"]
    return None


def fetch_and_cache_platform_policy() -> Optional[Dict[str, Any]]:
    """Fetch the policy from the management plane and cache it with the
    stored platform credentials. Returns the policy, or None."""
    from omniagents.core.providers.platform.client import (
        fetch_policy,
        get_access_token,
        get_platform_url,
        load_credentials,
        save_credentials,
    )

    policy = fetch_policy()
    if policy is None:
        return None

    creds = load_credentials() or {}
    creds["cached_policy"] = policy
    creds["platform_url"] = get_platform_url()
    creds["access_token"] = get_access_token()
    save_credentials(creds)
    return policy


def apply_platform_model_governance(config_dir: Path | str | None = None) -> None:
    """Apply the platform policy's model governance to the governed file.

    Reads the cached policy (fetching fresh when no cache exists),
    extracts the model-governance keys — top-level ``model_providers``
    (falling back to ``project.model_providers``),
    ``project.allow_user_models``, ``project.default_model`` — and
    writes the governed config into *config_dir* (default: the installed
    product identity's config dir). No-op when there is no policy or the
    policy carries no model providers.
    """
    policy = get_cached_platform_policy()
    if not policy:
        policy = fetch_and_cache_platform_policy()
    if not policy:
        return

    project = policy.get("project")
    if not project:
        return

    # model_providers is a top-level policy key (not nested under project)
    platform_providers = policy.get("model_providers") or project.get(
        "model_providers"
    )
    if not platform_providers:
        return

    allow_user_models = project.get("allow_user_models", True)
    default_model = project.get("default_model")

    if config_dir is None:
        from omniagents.core.product import product_info

        config_dir = product_info().config_dir()

    write_governed_config(
        config_dir,
        platform_providers,
        default_model,
        allow_user_models=allow_user_models,
    )
    if not allow_user_models:
        logger.info(
            "Model governance: enforced (providers: %s, default: %s)",
            list(platform_providers.keys()),
            default_model,
        )
    else:
        logger.info("Model governance: advisory (platform base + user overrides)")
