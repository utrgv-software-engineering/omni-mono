from __future__ import annotations

import os
from pathlib import Path


def _expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def get_home_dir() -> Path:
    base = os.getenv("OMNIAGENTS_HOME")
    if base:
        return _expand_path(base)
    return (Path.home() / ".omniagents").resolve()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_traces_dir() -> Path:
    home = _ensure_dir(get_home_dir())
    return _ensure_dir(home / "traces")


def get_sessions_dir() -> Path:
    home = _ensure_dir(get_home_dir())
    return _ensure_dir(home / "sessions")


def _normalise_slug(value: str | None, fallback: str) -> str:
    if value is None or not value.strip():
        value = fallback
    value = value.strip().lower()
    out = []
    for ch in value:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or fallback


def get_sessions_db_path(
    project_slug: str | None = None, agent_slug: str | None = None
) -> Path:
    override = os.getenv("OMNIAGENTS_HISTORY_DB")
    if override:
        return _expand_path(override)
    sessions_dir = get_sessions_dir()
    project_key = _normalise_slug(project_slug, "default")
    agent_key = _normalise_slug(agent_slug, "default")
    target_dir = sessions_dir / project_key / agent_key
    return target_dir / "sessions.db"
