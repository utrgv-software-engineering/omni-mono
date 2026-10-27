"""Skill-root resolution for the current AgentHost Run.

The skills subsystem is deliberately split in two: the framework owns
discovery, parsing, validation, and prompt rendering; *where skills
live* is derived from product identity and the routed Run. The
default resolution for a workspace is, in order (earlier wins):

1. ``<workspace_root>/.{product slug}/skills`` — the per-workspace
   dot-dir, named after :func:`omniagents.core.product.product_info`'s
   slug (``.omniagents/skills`` when no product is installed).
2. Each Workspace source's ``skills/`` directory
   (:func:`source_skill_roots`) — resolved through the Run's immutable
   environment lease. Host environments use declared local paths; other
   environments use source mounts beneath the leased workspace root.
3. ``<product config dir>/skills`` — the per-user global root
   (``~/.config/<config_dir>/skills`` on Linux).

so a product with a ``product:`` section in project.yml needs no code at
all to get its conventional layout. A product with a genuinely different
layout can still install a provider via :func:`set_skill_roots_provider`;
an installed provider fully replaces the default.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence

SkillRootsProvider = Callable[[Optional[str]], List[Path]]

_provider: Optional[SkillRootsProvider] = None

# The skill folders of the agent whose instructions are rendering or whose
# load_skill call is running: its own `skills/`, any definition it extends,
# and the project's `skills/` (see AgentSpec.skill_roots). Set by the agent
# builder around exactly those two moments, so a sub-agent called as a tool
# sees its own skills, not its caller's.
_agent_skill_roots: ContextVar[tuple] = ContextVar("agent_skill_roots", default=())


@contextmanager
def agent_skill_roots(roots: Sequence[Path | str]) -> Iterator[None]:
    """Make ``roots`` the current agent's skill folders for the block's duration."""
    token = _agent_skill_roots.set(tuple(Path(r) for r in roots))
    try:
        yield
    finally:
        _agent_skill_roots.reset(token)


def _with_agent_roots(roots: List[Path]) -> List[Path]:
    """Insert the current agent's folders after the workspace's own folder.

    The repository's dot-dir skills stay first (a workspace can override),
    then the agent's, then shared and global roots.
    """
    extra = list(_agent_skill_roots.get())
    if not extra:
        return roots
    return dedupe_skill_roots([*roots[:1], *extra, *roots[1:]])


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
            return _with_agent_roots([Path(p) for p in _provider(workspace_root)])
        except Exception:
            pass
    return _with_agent_roots(default_skill_roots(workspace_root))


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

    Workspace dot-dir first, then routed source roots, then the
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
# Workspace-source roots (routed RequestContext → skill directories).
# --------------------------------------------------------------------------


def source_skill_roots(workspace_root: str | None = None) -> List[Path]:
    """Map the current Run's Workspace sources to skill directories.

    The active :class:`~omniagents.execution.RequestContext` is the only
    source of placement. A host environment uses a local source's declared
    path. Every other source/environment pair resolves to
    ``<lease.workspace.root>/<mountName>/skills``. Empty outside a routed Run.

    ``workspace_root`` remains accepted for the public provider API but does
    not select an execution substrate.
    """
    from omniagents.execution import EnvironmentKind, current_request_context

    del workspace_root
    context = current_request_context()
    if (
        context is None
        or context.workspace is None
        or context.environment_lease is None
    ):
        return []

    roots: List[Path] = []
    lease = context.environment_lease
    host_environment = lease.descriptor.kind is EnvironmentKind.HOST
    for source in context.workspace.sources:
        mount_name = source.get("mountName")
        if not isinstance(mount_name, str) or not mount_name.strip():
            continue
        kind = source.get("kind")
        local_path = source.get("path")
        if (
            host_environment
            and kind in {"local", "local-git"}
            and isinstance(local_path, str)
            and local_path.strip()
        ):
            roots.append(Path(local_path).expanduser() / "skills")
        else:
            roots.append(Path(lease.workspace.root) / mount_name / "skills")

    return dedupe_skill_roots(roots)
