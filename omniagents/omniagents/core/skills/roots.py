"""Process-global skill-roots resolution.

The skills subsystem is deliberately split in two: the framework owns
discovery, parsing, validation, and prompt rendering; the *product*
owns where skills live. This module is the seam — a product installs a
provider that maps a workspace root to its skill directories (workspace
dot-dirs, per-user config dirs, mounted sources, …), and framework
consumers (the ``load_skill`` builtin tool, prompt builders) resolve
through :func:`skill_roots` without knowing any product layout.

Without a provider, the default is the single framework-conventional
location ``<workspace_root>/.omniagents/skills``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

SkillRootsProvider = Callable[[Optional[str]], List[Path]]

_provider: Optional[SkillRootsProvider] = None


def set_skill_roots_provider(provider: Optional[SkillRootsProvider]) -> None:
    """Install the product's skill-roots provider.

    ``provider(workspace_root)`` returns the ordered list of directories
    to search for ``<name>/SKILL.md``; earlier entries win. Pass ``None``
    to clear (restores the framework default).
    """
    global _provider
    _provider = provider


def skill_roots(workspace_root: str | None = None) -> List[Path]:
    """Ordered skill directories for ``workspace_root``.

    Uses the installed provider when present; otherwise the framework
    default of ``<workspace_root>/.omniagents/skills``. Always returns a
    list (a failing provider yields the default rather than raising —
    skill resolution should degrade, not crash the tool call).
    """
    if _provider is not None:
        try:
            return [Path(p) for p in _provider(workspace_root)]
        except Exception:
            pass
    if workspace_root:
        return [Path(workspace_root).expanduser() / ".omniagents" / "skills"]
    return []
