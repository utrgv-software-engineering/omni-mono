"""OmniAgents sandbox surface.

Thin facade over the SDK 0.14+ sandbox subsystem (``agents.sandbox``). The
factory in :mod:`omniagents.core.sandbox.factory` translates a YAML ``sandbox:``
block into the concrete SDK objects (clients, manifests, capabilities,
:class:`~agents.run_config.SandboxRunConfig`) without duplicating SDK logic.

Direct re-exports of common SDK names are provided here so callers can write
``from omniagents.core.sandbox import SandboxAgent, Manifest`` without reaching
into ``agents.sandbox``. Provider-specific clients live behind import-error
guards because each requires its own pip extra (e.g. ``omniagents[sandbox-e2b]``).
"""

from __future__ import annotations

from typing import Any, Optional

from .factory import (
    build_manifest,
    build_sandbox_client,
    build_sandbox_run_config,
)
# Importing these entry classes registers them with BaseEntry's
# subclass registry so manifests using their ``type`` strings resolve
# correctly. Must happen before any manifest is parsed.
from .local_git_archive import LocalGitArchive, LocalGitArchiveError
from .permissive_local_dir import PermissiveLocalDir, PermissiveLocalDirError

__all__ = [
    "LocalGitArchive",
    "LocalGitArchiveError",
    "PermissiveLocalDir",
    "PermissiveLocalDirError",
    "build_manifest",
    "build_sandbox_client",
    "build_sandbox_run_config",
]


# --- Optional re-exports of the core SDK sandbox API ---
# We import lazily-friendly names so editors and downstream callers can do
# ``from omniagents.core.sandbox import SandboxAgent, Manifest`` without
# always paying the cost of importing the SDK at package import time. We
# still import them eagerly here because the SDK is a hard dependency.
try:  # pragma: no cover - exercised indirectly
    from agents.sandbox import (  # type: ignore
        Capability,
        Manifest,
        SandboxAgent,
        SandboxRunConfig,
    )

    __all__.extend(
        [
            "Capability",
            "Manifest",
            "SandboxAgent",
            "SandboxRunConfig",
        ]
    )
except Exception:  # pragma: no cover - SDK should always be present
    Capability = Any  # type: ignore[assignment]
    Manifest = Any  # type: ignore[assignment]
    SandboxAgent = Any  # type: ignore[assignment]
    SandboxRunConfig = Any  # type: ignore[assignment]


def has_sandbox_run_config(value: Optional[Any]) -> bool:
    """Return True when *value* looks like a populated ``SandboxRunConfig``.

    Used by the bridge/builder to keep behavior unchanged when no sandbox
    block is configured.
    """
    return value is not None
