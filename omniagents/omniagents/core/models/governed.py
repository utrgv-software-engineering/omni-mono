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

What stays downstream is only how the governed provider map and flags
are extracted from the product's policy document (e.g. omni-code's
``project.model_providers``); the write/load and marker contract live
here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .store import MODELS_CONFIG_VERSION

__all__ = [
    "GOVERNED_CONFIG_FILENAME",
    "governed_config_path",
    "write_governed_config",
    "load_governed_config",
]

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
