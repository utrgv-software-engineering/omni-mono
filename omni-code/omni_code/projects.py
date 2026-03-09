import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from omni_code.config import get_config_dir


PROJECTS_CONFIG_VERSION = 1


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    label: str
    workspace_dir: str
    created_at: int
    updated_at: int


def get_projects_config_path() -> Path:
    return get_config_dir() / "projects.json"


def _default_config() -> dict[str, Any]:
    return {
        "version": PROJECTS_CONFIG_VERSION,
        "current_project": None,
        "projects": [],
    }


def load_projects_config() -> dict[str, Any]:
    path = get_projects_config_path()
    if not path.exists():
        return _default_config()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()

    if raw.get("version") != PROJECTS_CONFIG_VERSION:
        return _default_config()

    projects = raw.get("projects")
    if not isinstance(projects, list):
        projects = []

    current_project = raw.get("current_project")
    if current_project is not None and not isinstance(current_project, str):
        current_project = None

    return {
        "version": PROJECTS_CONFIG_VERSION,
        "current_project": current_project,
        "projects": projects,
    }


def save_projects_config(config: dict[str, Any]) -> None:
    path = get_projects_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        {
            "version": PROJECTS_CONFIG_VERSION,
            "current_project": config.get("current_project"),
            "projects": config.get("projects") or [],
        },
        indent=2,
        sort_keys=True,
    )

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def list_projects() -> list[RegisteredProject]:
    config = load_projects_config()
    projects: list[RegisteredProject] = []
    for raw in config.get("projects", []):
        if not isinstance(raw, dict):
            continue
        try:
            projects.append(
                RegisteredProject(
                    id=str(raw["id"]),
                    label=str(raw["label"]),
                    workspace_dir=str(raw["workspace_dir"]),
                    created_at=int(raw["created_at"]),
                    updated_at=int(raw.get("updated_at", raw["created_at"])),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    projects.sort(key=lambda item: (item.label.lower(), item.workspace_dir.lower()))
    return projects


def _normalize_workspace_dir(workspace_dir: str | Path) -> str:
    return str(Path(workspace_dir).expanduser().resolve())


def _project_to_dict(project: RegisteredProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "label": project.label,
        "workspace_dir": project.workspace_dir,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def add_project(*, label: str, workspace_dir: str | Path, now_ms: int, project_id: str) -> RegisteredProject:
    normalized_workspace_dir = _normalize_workspace_dir(workspace_dir)
    projects = list_projects()

    for project in projects:
        if project.workspace_dir == normalized_workspace_dir:
            return project
        if project.label == label:
            raise ValueError(f'Project label already exists: {label}')

    project = RegisteredProject(
        id=project_id,
        label=label,
        workspace_dir=normalized_workspace_dir,
        created_at=now_ms,
        updated_at=now_ms,
    )
    config = load_projects_config()
    config["projects"] = [_project_to_dict(item) for item in [*projects, project]]
    if not config.get("current_project"):
        config["current_project"] = project.id
    save_projects_config(config)
    return project


def remove_project(project_ref: str) -> RegisteredProject:
    projects = list_projects()
    target = resolve_project(project_ref)
    if target is None:
        raise ValueError(f'Project not found: {project_ref}')

    remaining = [item for item in projects if item.id != target.id]
    config = load_projects_config()
    config["projects"] = [_project_to_dict(item) for item in remaining]
    if config.get("current_project") == target.id:
        config["current_project"] = remaining[0].id if remaining else None
    save_projects_config(config)
    return target


def resolve_project(project_ref: Optional[str]) -> Optional[RegisteredProject]:
    projects = list_projects()
    config = load_projects_config()

    if not project_ref:
        current_project = config.get("current_project")
        if isinstance(current_project, str):
            for project in projects:
                if project.id == current_project:
                    return project
        return None

    normalized: Optional[str] = None
    candidate = Path(project_ref).expanduser()
    if candidate.exists():
        normalized = str(candidate.resolve())

    for project in projects:
        if project.id == project_ref or project.label == project_ref:
            return project
        if normalized is not None and project.workspace_dir == normalized:
            return project

    return None


def set_current_project(project_ref: str) -> RegisteredProject:
    project = resolve_project(project_ref)
    if project is None:
        raise ValueError(f'Project not found: {project_ref}')
    config = load_projects_config()
    config["current_project"] = project.id
    save_projects_config(config)
    return project
