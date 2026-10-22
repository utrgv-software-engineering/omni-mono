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


def describe_agent_config(agent_cfg: Dict[str, Any], agent_path: Any) -> Dict[str, Any]:
    """The manifest's agent fields for a project agent (its ``agent.yml``)."""
    agent_cfg = dict(agent_cfg or {})
    path = Path(agent_path or "")
    instructions_sha: Optional[str] = None
    instructions_file = agent_cfg.get("instructions_file")
    if instructions_file and path.name:
        instructions_sha = _file_sha(path.parent / str(instructions_file))
    elif isinstance(agent_cfg.get("instructions"), str):
        instructions_sha = _sha(agent_cfg["instructions"].encode("utf-8"))
    return {
        "agent": agent_cfg.get("name") or path.parent.name or None,
        "agent_yml": str(path) if path.name else None,
        "agent_yml_sha": _file_sha(path) if path.name else None,
        "instructions_sha": instructions_sha,
        "model": agent_cfg.get("model"),
        "tools": _tool_names(agent_cfg.get("tools")),
        "capabilities": _tool_names(agent_cfg.get("capabilities")),
        "max_turns": agent_cfg.get("max_turns"),
    }


def describe_sdk_agent(agent: Any, max_turns: Optional[int]) -> Dict[str, Any]:
    """The manifest's agent fields for an ``agents.Agent`` passed in directly."""
    instructions = getattr(agent, "instructions", None)
    model = getattr(agent, "model", None)
    return {
        "agent": getattr(agent, "name", None),
        "agent_yml": None,
        "agent_yml_sha": None,
        "instructions_sha": (
            _sha(instructions.encode("utf-8"))
            if isinstance(instructions, str)
            else None
        ),
        "model": (
            model if isinstance(model, str) or model is None else type(model).__name__
        ),
        "tools": _tool_names(
            getattr(t, "name", None) or str(t)
            for t in (getattr(agent, "tools", None) or [])
        ),
        "capabilities": [],
        "max_turns": max_turns,
    }


def build_run_manifest(
    agent: Dict[str, Any],
    *,
    defaults: Optional[Dict[str, Any]] = None,
    select: Optional[str] = None,
    exclude: Optional[str] = None,
    split: Optional[str] = None,
    holdout: Optional[Dict[str, Any]] = None,
    repeats: int = 1,
    scenarios: int = 0,
) -> Dict[str, Any]:
    """Describe the agent and selection an evaluation run used.

    ``agent`` is :func:`describe_agent_config` or :func:`describe_sdk_agent`.
    ``evaluation.defaults.max_turns`` overrides the agent's own limit at run
    time, so the manifest records the limit the run actually used.
    """
    try:
        from omniagents._version import __version__ as version
    except Exception:  # pragma: no cover - version module always present in a checkout
        version = "unknown"
    fields = dict(agent)
    if (defaults or {}).get("max_turns") is not None:
        fields["max_turns"] = int(defaults["max_turns"])  # type: ignore[index]
    return {
        **fields,
        "omniagents_version": version,
        "select": select or None,
        "exclude": exclude or None,
        "split": split or None,
        "holdout": dict(holdout or {}) or None,
        "repeats": int(repeats),
        "scenarios": int(scenarios),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
