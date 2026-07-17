"""Per-user config directory resolution for products built on omniagents.

A product (a project shipping an agent) keeps its user-level state —
model config, skills, credentials — in a per-user config directory. The
platform conventions live here so every product resolves them the same
way instead of re-implementing XDG/Windows logic.
"""

from __future__ import annotations

import os
from pathlib import Path


def product_config_dir(name: str, *, windows_name: str | None = None) -> Path:
    """Per-user config directory for the product called ``name``.

    * Windows: ``%APPDATA%/<windows_name or name>``
    * elsewhere: ``$XDG_CONFIG_HOME/<name>`` when set, otherwise
      ``~/.config/<name>``

    The directory is not created; callers create it on first write.
    """
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / (windows_name or name)
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override) / name
    return Path.home() / ".config" / name
