from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def _published_sources() -> List[Dict[str, Any]]:
    try:
        from omni_code.agent_workspace_state import get_sources
    except Exception:
        return []
    try:
        return get_sources()
    except Exception:
        return []


def _manifest_root() -> Optional[Path]:
    try:
        from omni_code.agent_workspace_state import get_manifest_root
    except Exception:
        return None
    try:
        root = get_manifest_root()
    except Exception:
        return None
    if not isinstance(root, str) or not root.strip():
        return None
    return Path(root).expanduser()


def _source_local_path(source: Dict[str, Any]) -> Optional[Path]:
    if source.get("kind") not in ("local", "local-git"):
        return None
    path = source.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    return Path(path).expanduser()


def _inferred_manifest_root(workspace_root: str | None) -> Optional[Path]:
    if not workspace_root:
        return None
    workspace = Path(workspace_root).expanduser()
    source_mounts = {
        source.get("mountName")
        for source in _published_sources()
        if isinstance(source.get("mountName"), str)
    }
    if workspace.name in source_mounts:
        return workspace.parent
    return workspace


def source_skill_roots(workspace_root: str | None = None) -> List[Path]:
    roots: List[Path] = []
    manifest_root = _manifest_root() or _inferred_manifest_root(workspace_root)
    for source in _published_sources():
        mount_name = source.get("mountName")
        if (
            manifest_root is not None
            and isinstance(mount_name, str)
            and mount_name.strip()
        ):
            roots.append(manifest_root / mount_name / "skills")
        local_path = _source_local_path(source)
        if local_path is not None:
            roots.append(local_path / "skills")

    unique: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique
