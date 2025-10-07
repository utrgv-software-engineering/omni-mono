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


def get_sessions_db_path() -> Path:
    override = os.getenv("OMNIAGENTS_HISTORY_DB")
    if override:
        return _expand_path(override)
    sessions_dir = get_sessions_dir()
    return sessions_dir / "sessions.db"
