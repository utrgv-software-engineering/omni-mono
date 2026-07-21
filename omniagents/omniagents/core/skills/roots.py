"""Process-global skill-roots resolution.

The skills subsystem is deliberately split in two: the framework owns
discovery, parsing, validation, and prompt rendering; *where skills
live* is derived from product identity and serve-session state. The
default resolution for a workspace is, in order (earlier wins):

1. ``<workspace_root>/.{product slug}/skills`` — the per-workspace
   dot-dir, named after :func:`omniagents.core.product.product_info`'s
   slug (``.omniagents/skills`` when no product is installed).
2. Each published workspace source's ``skills/`` directory
   (:func:`source_skill_roots`) — resolved against the serve session's
   manifest root by mount name, falling back to the source's host path
   when no sandbox session is live.
3. ``<product config dir>/skills`` — the per-user global root
   (``~/.config/<config_dir>/skills`` on Linux).

so a product with a ``product:`` section in project.yml needs no code at
all to get its conventional layout. A product with a genuinely different
layout can still install a provider via :func:`set_skill_roots_provider`;
an installed provider fully replaces the default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional

SkillRootsProvider = Callable[[Optional[str]], List[Path]]

_provider: Optional[SkillRootsProvider] = None


def set_skill_roots_provider(provider: Optional[SkillRootsProvider]) -> None:
    """Install a custom skill-roots provider.

    ``provider(workspace_root)`` returns the ordered list of directories
    to search for ``<name>/SKILL.md``; earlier entries win. Pass ``None``
    to clear (restores the derived default).
    """
    global _provider
    _provider = provider


def skill_roots(workspace_root: str | None = None) -> List[Path]:
    """Ordered skill directories for ``workspace_root``.

    Uses the installed provider when present; otherwise
    :func:`default_skill_roots`. Always returns a list (a failing
    provider yields the default rather than raising — skill resolution
    should degrade, not crash the tool call).
    """
    if _provider is not None:
        try:
            return [Path(p) for p in _provider(workspace_root)]
        except Exception:
            pass
    return default_skill_roots(workspace_root)


def dedupe_skill_roots(roots: Iterable[Path]) -> List[Path]:
    """Order-preserving dedupe of skill-root paths (keyed by string path)."""
    unique: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _resolve_invocation_dir() -> Path:
    """The user's directory: ``$PWD`` (symlink-honoring) over ``cwd``."""
    env_pwd = os.environ.get("PWD")
    if isinstance(env_pwd, str) and env_pwd.strip():
        try:
            return Path(env_pwd).expanduser().resolve()
        except Exception:
            return Path(env_pwd).expanduser()
    try:
        return Path.cwd().resolve()
    except Exception:
        return Path.cwd()


def default_skill_roots(workspace_root: str | None = None) -> List[Path]:
    """The derived default skill roots for ``workspace_root``.

    Workspace dot-dir first, then published-source roots, then the
    per-user global root — so workspace-local skills shadow global ones
    under first-wins merging. Falls back to the invocation directory when
    no workspace root is given (direct CLI runs).
    """
    from omniagents.core.config.dirs import product_config_dir
    from omniagents.core.product import product_info

    if isinstance(workspace_root, str) and workspace_root.strip():
        base = Path(workspace_root).expanduser()
    else:
        base = _resolve_invocation_dir()

    slug = product_info().slug
    unique = dedupe_skill_roots(
        [base / f".{slug}" / "skills", *source_skill_roots(str(base))]
    )
    unique.append(product_config_dir() / "skills")
    return unique


# --------------------------------------------------------------------------
# Published-source roots (serve-session state → skill directories).
# --------------------------------------------------------------------------


def _published_manifest_root() -> Optional[Path]:
    from omniagents.core.runtime import serve_state

    try:
        root = serve_state.get_manifest_root()
    except Exception:
        return None
    if not isinstance(root, str) or not root.strip():
        return None
    return Path(root).expanduser()


def _inferred_manifest_root(workspace_root: str | None) -> Optional[Path]:
    """When serve published no manifest root, infer it: a workspace root
    that *is* one of the source mounts sits one level below the manifest
    root; otherwise the workspace root is the manifest root itself."""
    from omniagents.core.runtime import serve_state

    if not workspace_root:
        return None
    workspace = Path(workspace_root).expanduser()
    try:
        sources = serve_state.get_source_descriptors()
    except Exception:
        return workspace
    source_mounts = {s.mount_name for s in sources if s.mount_name}
    if workspace.name in source_mounts:
        return workspace.parent
    return workspace


def source_skill_roots(workspace_root: str | None = None) -> List[Path]:
    """Map the serve session's published sources to skill directories.

    Each source contributes ``<manifest_root>/<mountName>/skills``; when
    no sandbox session is live (host mode — sources are not relocated),
    a local source additionally contributes ``<host path>/skills``.
    Empty outside a serve session.
    """
    from omniagents.core.runtime import serve_state

    try:
        sources = serve_state.get_source_descriptors()
    except Exception:
        return []
    try:
        sandbox_session_active = serve_state.get_sandbox_session() is not None
    except Exception:
        sandbox_session_active = False

    roots: List[Path] = []
    manifest_root = _published_manifest_root() or _inferred_manifest_root(
        workspace_root
    )
    for source in sources:
        if manifest_root is not None and source.mount_name:
            roots.append(manifest_root / source.mount_name / "skills")
            if sandbox_session_active:
                continue
        local_path = source.local_path()
        if local_path is not None:
            roots.append(Path(local_path).expanduser() / "skills")

    return dedupe_skill_roots(roots)
