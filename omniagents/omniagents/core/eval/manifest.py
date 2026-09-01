"""What was run: a manifest that makes two evaluation runs comparable.

The results index used to record the agent as a bare name. Two runs of
"derm_web" a week apart could differ in model, tools, instructions and
framework version and nothing in the record would say so. The manifest
carries hashes of the things that change an agent's behaviour, so a
comparison can refuse cells that differ in anything but the variable under
study.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _file_sha(path: Path) -> Optional[str]:
    try:
        return _sha(path.read_bytes())
    except Exception:
        return None


def _tool_names(tools: Any) -> List[str]:
    names: List[str] = []
    for t in tools or []:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            names.append(str(t.get("name") or t.get("id") or t))
        else:
            names.append(str(t))
    return sorted(names)


def build_run_manifest(
    eval_config: Any,
    *,
    select: Optional[str] = None,
    exclude: Optional[str] = None,
    split: Optional[str] = None,
    repeats: int = 1,
) -> Dict[str, Any]:
    """Describe the agent and selection an evaluation run used."""
    agent_cfg: Dict[str, Any] = dict(getattr(eval_config, "agent_config", None) or {})
    agent_path = Path(getattr(eval_config, "agent_path", "") or "")
    instructions_sha: Optional[str] = None
    instructions_file = agent_cfg.get("instructions_file")
    if instructions_file and agent_path.name:
        instructions_sha = _file_sha(agent_path.parent / str(instructions_file))
    elif isinstance(agent_cfg.get("instructions"), str):
        instructions_sha = _sha(agent_cfg["instructions"].encode("utf-8"))
    try:
        from omniagents._version import __version__ as version
    except Exception:  # pragma: no cover - version module always present in a checkout
        version = "unknown"
    cfg = getattr(eval_config, "config", None) or {}
    return {
        "agent": agent_cfg.get("name") or agent_path.parent.name or None,
        "agent_yml": str(agent_path) if agent_path.name else None,
        "agent_yml_sha": _file_sha(agent_path) if agent_path.name else None,
        "instructions_sha": instructions_sha,
        "model": agent_cfg.get("model"),
        "tools": _tool_names(agent_cfg.get("tools")),
        "capabilities": _tool_names(agent_cfg.get("capabilities")),
        "max_turns": agent_cfg.get("max_turns"),
        "omniagents_version": version,
        "select": select or None,
        "exclude": exclude or None,
        "split": split or getattr(eval_config, "split", None),
        "holdout": dict(getattr(eval_config, "holdout_manifest", None) or {}) or None,
        "repeats": int(repeats),
        "scenarios": len(cfg.get("scenarios") or []),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
