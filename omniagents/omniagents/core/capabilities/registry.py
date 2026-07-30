"""Capability class registry.

Maps a YAML ``type:`` string to a :class:`agents.sandbox.capabilities.Capability`
subclass. Two registration paths:

* :func:`register_capability` — runtime call, used by projects that aren't
  installed as packages (or by tests).
* ``omniagents.capabilities`` entry point group — discovered automatically on
  first lookup, so installed packages can ship capability classes.

The registry is a process-wide singleton. Phase 0 ships it empty; Phase 1
adds builtin entries (e.g. ``bash_jobs`` → ``BashJobs``). Pure-YAML
capabilities (no ``type:`` field) bypass this registry — they're synthesized
into :class:`omniagents.core.capabilities.yaml_capability._YamlCapability`
directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Type

logger = logging.getLogger(__name__)

_CLASS_REGISTRY: Dict[str, Type[Any]] = {}
_ENTRY_POINTS_LOADED = False
_SDK_BUILTINS_LOADED = False


def register_capability(name: str, cls: Type[Any]) -> None:
    """Register a Capability subclass under *name*.

    Subsequent ``type: <name>`` references in capability YAML resolve to *cls*.
    Idempotent: registering the same (name, cls) pair twice is a no-op;
    re-registering the same name with a different class raises ``ValueError``
    so duplicate registrations surface immediately.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Capability name must be a non-empty string")
    existing = _CLASS_REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Capability {name!r} is already registered to {existing!r}; "
            f"refusing to overwrite with {cls!r}"
        )
    _CLASS_REGISTRY[name] = cls


def _load_entry_points_once() -> None:
    """Discover capability classes shipped via the ``omniagents.capabilities``
    entry point group. Called lazily on first :func:`resolve_capability_class`
    so package import time stays cheap.
    """
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points(group="omniagents.capabilities")
    except TypeError:
        # Older importlib.metadata — entry_points() returns a dict
        legacy_eps = entry_points()
        eps = legacy_eps.get("omniagents.capabilities", [])  # type: ignore[attr-defined, arg-type]
    except Exception:
        return
    for ep in eps:
        try:
            cls = ep.load()
            register_capability(ep.name, cls)
        except Exception as e:
            logger.warning(
                "Failed to load capability %r from entry point %r: %s",
                ep.name,
                ep,
                e,
            )


def _load_sdk_builtins_once() -> None:
    """Pre-register the SDK's ``agents.sandbox.capabilities.*`` classes under
    ``sdk_*`` names so YAML can reference them via ``type: sdk_filesystem``,
    etc. Names are prefixed to avoid colliding with user-defined caps named
    plain ``filesystem`` (which usually bundle host-side tools).

    Uses ``sdk_*`` rather than the SDK's class names directly so that:
    - The provenance is obvious in YAML.
    - Users can name their own dual-mode capability ``filesystem`` without
      shadowing the SDK class registration.
    - Future SDK additions (e.g. a refined ``Filesystem``) won't clobber an
      existing user-defined registration.
    """
    global _SDK_BUILTINS_LOADED
    if _SDK_BUILTINS_LOADED:
        return
    _SDK_BUILTINS_LOADED = True
    try:
        from agents.sandbox.capabilities import (
            Compaction,
            Filesystem,
            Memory,
            Shell,
            Skills,
        )
    except Exception as e:  # pragma: no cover - SDK should always be present
        logger.warning("SDK sandbox capabilities not importable: %s", e)
        return
    for name, cls in (
        ("sdk_filesystem", Filesystem),
        ("sdk_shell", Shell),
        ("sdk_skills", Skills),
        ("sdk_memory", Memory),
        ("sdk_compaction", Compaction),
    ):
        # register_capability raises if the same name is already mapped to a
        # different class — that's the correct loud failure if a project
        # somehow pre-registered something under the same name.
        if _CLASS_REGISTRY.get(name) is None:
            _CLASS_REGISTRY[name] = cls


def resolve_capability_class(name: str) -> Type[Any] | None:
    """Look up a registered capability class by *name*. Returns ``None`` if
    no class is registered (caller decides whether that's an error)."""
    _load_sdk_builtins_once()
    _load_entry_points_once()
    return _CLASS_REGISTRY.get(name)


def list_registered_capabilities() -> List[str]:
    """Return the sorted list of currently-registered capability type names.
    Primarily for diagnostics — printing in error messages when a YAML
    ``type:`` doesn't resolve."""
    _load_sdk_builtins_once()
    _load_entry_points_once()
    return sorted(_CLASS_REGISTRY.keys())
