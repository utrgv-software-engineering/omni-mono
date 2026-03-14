from dataclasses import asdict, dataclass
from copy import deepcopy
from subprocess import run
from pathlib import Path
from time import time
from typing import Any, Optional

from omni_code.config import get_config_dir
from omni_code.fleet_workflow import FleetWorkflow, FleetWorkflowConfig, parse_fleet_workflow


@dataclass(frozen=True)
class SandboxConfig:
    """Global sandbox configuration.

    When enabled, supervisor commands run inside a Docker container.
    When disabled (default), everything runs locally — no Docker needed.
    """
    enabled: bool = False
    image: str | None = None       # Pre-built Docker image (e.g. "ubuntu:24.04")
    dockerfile: str | None = None  # Path to a Dockerfile (absolute)


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    label: str
    workspace_dir: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class TicketRecord:
    id: str
    project_id: str
    title: str
    description: str
    priority: str
    blocked_by: list[str]
    column_id: str
    created_at: int
    updated_at: int
    branch: str | None = None
    use_worktree: bool = False
    worktree_path: str | None = None
    worktree_name: str | None = None
    supervisor_session_id: str | None = None
    phase: str | None = None
    token_usage: dict[str, int] | None = None


@dataclass(frozen=True)
class ColumnRecord:
    id: str
    label: str
    max_concurrent: int | None = None
    gate: bool = False


@dataclass(frozen=True)
class PipelineRecord:
    columns: list[ColumnRecord]


@dataclass(frozen=True)
class TaskRecord:
    id: str
    project_id: str
    task_description: str
    status: dict[str, Any]
    created_at: int
    ticket_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    last_urls: dict[str, str] | None = None


DEFAULT_PIPELINE = PipelineRecord(
    columns=[
        ColumnRecord(id="backlog", label="Backlog"),
        ColumnRecord(id="implementation", label="Implementation"),
        ColumnRecord(id="review", label="Review", gate=True),
        ColumnRecord(id="completed", label="Completed"),
    ]
)

FLEET_SCHEMA_VERSION = 4


def get_fleet_store_path() -> Path:
    canonical = get_config_dir() / "config.json"
    server_mode = get_config_dir().parent / "Omni Code" / "config.json"
    launcher_mode = Path.home() / ".config" / "omni-code-launcher" / "config.json"
    if canonical.exists():
        return canonical
    return canonical


def get_ticket_dir(ticket_id: str) -> Path:
    return get_config_dir() / "fleet" / "tickets" / ticket_id


def get_ticket_file_path(ticket_id: str) -> Path:
    return get_ticket_dir(ticket_id) / "TICKET.yaml"


class ProjectsRuntime:
    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or get_fleet_store_path()
        if config_path is None:
            self._migrate_legacy_store_if_needed()

    def _load(self) -> dict[str, Any]:
        config = self._load_from_path(self._config_path)
        if "fleetSandbox" not in config:
            config = self._migrate_per_project_sandbox(config)
            self._save(config)
        return config

    def _save(self, config: dict[str, Any]) -> None:
        self._save_to_path(self._config_path, config)

    @staticmethod
    def _load_from_path(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {
                "fleetSchemaVersion": FLEET_SCHEMA_VERSION,
                "fleetProjects": [],
                "fleetTickets": [],
                "fleetTasks": [],
            }
        import json

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "fleetSchemaVersion": FLEET_SCHEMA_VERSION,
                "fleetProjects": [],
                "fleetTickets": [],
                "fleetTasks": [],
            }
        return {
            **raw,
            "fleetSchemaVersion": raw.get("fleetSchemaVersion", FLEET_SCHEMA_VERSION),
            "fleetProjects": raw.get("fleetProjects") or [],
            "fleetTickets": raw.get("fleetTickets") or [],
            "fleetTasks": raw.get("fleetTasks") or [],
        }

    @staticmethod
    def _save_to_path(path: Path, config: dict[str, Any]) -> None:
        import json
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(config, indent=2, sort_keys=True)
        if os.name == "nt":
            path.write_text(content, encoding="utf-8")
            return
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _migrate_legacy_store_if_needed(self) -> None:
        if self._config_path.exists():
            return
        legacy_candidates = [
            get_config_dir().parent / "Omni Code" / "config.json",
            Path.home() / ".config" / "omni-code-launcher" / "config.json",
        ]
        chosen: Optional[Path] = None
        chosen_size = -1
        for candidate in legacy_candidates:
            if not candidate.exists():
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if size > chosen_size:
                chosen = candidate
                chosen_size = size
        if chosen is None:
            return
        data = self._load_from_path(chosen)
        self._save_to_path(self._config_path, data)

    @staticmethod
    def _now_ms() -> int:
        return int(time() * 1000)

    @staticmethod
    def _normalize_workspace_dir(workspace_dir: str | Path) -> str:
        return str(Path(workspace_dir).expanduser().resolve())

    def get_sandbox(self) -> SandboxConfig:
        """Get the global sandbox configuration."""
        config = self._load()
        raw = config.get("fleetSandbox")
        if not isinstance(raw, dict):
            return SandboxConfig()
        return SandboxConfig(
            enabled=bool(raw.get("enabled", False)),
            image=raw.get("image"),
            dockerfile=raw.get("dockerfile"),
        )

    def set_sandbox(self, sandbox: SandboxConfig) -> None:
        """Set the global sandbox configuration."""
        config = self._load()
        config["fleetSandbox"] = asdict(sandbox)
        self._save(config)

    def _migrate_per_project_sandbox(self, config: dict[str, Any]) -> dict[str, Any]:
        """One-time migration: promote per-project sandbox to global fleetSandbox."""
        if "fleetSandbox" in config:
            return config
        global_sandbox: dict[str, Any] = {"enabled": False, "image": None, "dockerfile": None}
        for raw in config.get("fleetProjects", []):
            sb = raw.pop("sandbox", None)
            if sb and not global_sandbox["enabled"]:
                global_sandbox = {"enabled": True, **sb}
        config["fleetSandbox"] = global_sandbox
        return config

    def list_projects(self) -> list[ProjectRecord]:
        config = self._load()
        projects: list[ProjectRecord] = []
        for raw in config.get("fleetProjects", []):
            if not isinstance(raw, dict):
                continue
            try:
                projects.append(
                    ProjectRecord(
                        id=str(raw["id"]),
                        label=str(raw["label"]),
                        workspace_dir=str(raw.get("workspaceDir", raw.get("workspace_dir", ""))),
                        created_at=int(raw.get("createdAt", raw.get("created_at", 0))),
                        updated_at=int(raw.get("updatedAt", raw.get("updated_at", raw.get("createdAt", raw.get("created_at", 0))))),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        projects.sort(key=lambda item: (item.label.lower(), item.workspace_dir.lower()))
        return projects

    def _serialize_ticket_yaml(self, ticket: TicketRecord) -> str:
        pipeline = self.get_pipeline(ticket.project_id)
        column = next((item for item in pipeline.columns if item.id == ticket.column_id), None)
        column_label = column.label if column is not None else ticket.column_id
        pipeline_labels = " → ".join(item.label for item in pipeline.columns)
        lines = [
            "# This file is managed by omni-code.",
            "# The agent may change \"column\" to move the ticket through the pipeline.",
            "# Set \"escalation\" to a message to pause and notify the human.",
            "#",
            f"# Pipeline: {pipeline_labels}",
            "",
            f'column: "{column_label}"',
        ]
        return "\n".join(lines) + "\n"

    def parse_ticket_file(self, ticket_ref: str, project_ref: Optional[str] = None) -> dict[str, str | None]:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise ValueError(f"Ticket not found: {ticket_ref}")
        path = self._ticket_file_path(ticket.id)
        if not path.exists():
            return {"column": None, "escalation": None}
        content = path.read_text(encoding="utf-8")
        import re

        column_match = re.search(r'^column:\s*"?([^"\n]+)"?\s*$', content, flags=re.MULTILINE)
        escalation_match = re.search(r'^escalation:\s*"?([^"\n]+)"?\s*$', content, flags=re.MULTILINE)
        return {
            "column": column_match.group(1).strip() if column_match else None,
            "escalation": escalation_match.group(1).strip() if escalation_match else None,
        }

    def sync_ticket_file(self, ticket_ref: str, project_ref: Optional[str] = None) -> dict[str, str | None]:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise ValueError(f"Ticket not found: {ticket_ref}")
        parsed = self.parse_ticket_file(ticket.id, ticket.project_id)
        updated_column_id = None
        if parsed["column"]:
            pipeline = self.get_pipeline(ticket.project_id)
            column = next((item for item in pipeline.columns if item.label.lower() == str(parsed["column"]).lower()), None)
            if column is not None and column.id != ticket.column_id:
                self.move_ticket(ticket.id, column.id, ticket.project_id)
                updated_column_id = column.id
        return {"column_id": updated_column_id, "escalation": parsed["escalation"]}

    def get_workflow(self, project_ref: str) -> FleetWorkflow:
        project = self.resolve_project(project_ref)
        if project is None:
            return FleetWorkflow(config=FleetWorkflowConfig(), prompt_template="")
        workflow_path = Path(project.workspace_dir) / "FLEET.md"
        if not workflow_path.exists():
            return FleetWorkflow(config=FleetWorkflowConfig(), prompt_template="")
        try:
            content = workflow_path.read_text(encoding="utf-8")
        except OSError:
            return FleetWorkflow(config=FleetWorkflowConfig(), prompt_template="")
        return parse_fleet_workflow(content)

    def run_hook(self, project_ref: str, hook_name: str, workspace_dir: str | Path) -> bool:
        workflow = self.get_workflow(project_ref)
        hooks = workflow.config.hooks
        script = getattr(hooks, hook_name, None) if hooks else None
        if not script:
            return True
        timeout_ms = hooks.timeout_ms if hooks and hooks.timeout_ms else 60_000
        result = run(
            script,
            cwd=str(Path(workspace_dir)),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
        return result.returncode == 0

    def _ticket_file_path(self, ticket_id: str) -> Path:
        return self._config_path.parent / "fleet" / "tickets" / ticket_id / "TICKET.yaml"

    def write_ticket_file(self, ticket_ref: str, project_ref: Optional[str] = None) -> Path:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise ValueError(f"Ticket not found: {ticket_ref}")
        path = self._ticket_file_path(ticket.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._serialize_ticket_yaml(ticket), encoding="utf-8")
        return path

    def get_current_project(self) -> Optional[ProjectRecord]:
        projects = self.list_projects()
        return projects[0] if projects else None

    def resolve_project(self, project_ref: Optional[str]) -> Optional[ProjectRecord]:
        projects = self.list_projects()
        if not project_ref:
            return self.get_current_project()

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

    def add_project(self, *, label: str, workspace_dir: str | Path, project_id: Optional[str] = None) -> ProjectRecord:
        normalized_workspace_dir = self._normalize_workspace_dir(workspace_dir)
        projects = self.list_projects()

        for project in projects:
            if project.workspace_dir == normalized_workspace_dir:
                return project
            if project.label == label:
                raise ValueError(f"Project label already exists: {label}")

        now_ms = self._now_ms()
        created = ProjectRecord(
            id=project_id or f"project-{abs(hash(normalized_workspace_dir)) & 0xffffffff:x}",
            label=label,
            workspace_dir=normalized_workspace_dir,
            created_at=now_ms,
            updated_at=now_ms,
        )
        config = self._load()
        project_payload: dict[str, Any] = {
            "id": created.id,
            "label": created.label,
            "workspaceDir": created.workspace_dir,
            "createdAt": created.created_at,
        }
        config["fleetProjects"] = [
            *config.get("fleetProjects", []),
            project_payload,
        ]
        config.setdefault("fleetTickets", [])
        config.setdefault("fleetTasks", [])
        config["fleetSchemaVersion"] = config.get("fleetSchemaVersion", FLEET_SCHEMA_VERSION)
        self._save(config)
        return created

    def remove_project(self, project_ref: str) -> ProjectRecord:
        project = self.resolve_project(project_ref)
        if project is None:
            raise ValueError(f"Project not found: {project_ref}")
        config = self._load()
        config["fleetProjects"] = [raw for raw in config.get("fleetProjects", []) if raw.get("id") != project.id]
        config["fleetTickets"] = [raw for raw in config.get("fleetTickets", []) if raw.get("projectId") != project.id]
        config["fleetTasks"] = [raw for raw in config.get("fleetTasks", []) if raw.get("projectId") != project.id]
        self._save(config)
        return project

    def set_current_project(self, project_ref: str) -> ProjectRecord:
        project = self.resolve_project(project_ref)
        if project is None:
            raise ValueError(f"Project not found: {project_ref}")
        return project

    def list_tickets(self, project_ref: Optional[str] = None) -> list[TicketRecord]:
        project = self.resolve_project(project_ref) if project_ref else None
        config = self._load()
        tickets: list[TicketRecord] = []
        for raw in config.get("fleetTickets", []):
            if not isinstance(raw, dict):
                continue
            if project is not None and raw.get("projectId") != project.id:
                continue
            try:
                tickets.append(
                    TicketRecord(
                        id=str(raw["id"]),
                        project_id=str(raw["projectId"]),
                        title=str(raw["title"]),
                        description=str(raw.get("description", "")),
                        priority=str(raw.get("priority", "medium")),
                        blocked_by=list(raw.get("blockedBy") or raw.get("blocked_by") or []),
                        column_id=str(raw.get("columnId", raw.get("column_id", self.get_pipeline(project.id if project else str(raw["projectId"])).columns[0].id))),
                        created_at=int(raw.get("createdAt", raw.get("created_at", 0))),
                        updated_at=int(raw.get("updatedAt", raw.get("updated_at", raw.get("createdAt", raw.get("created_at", 0))))),
                        branch=str(raw["branch"]) if raw.get("branch") is not None else None,
                        use_worktree=bool(raw.get("useWorktree", raw.get("use_worktree", False))),
                        worktree_path=str(raw["worktreePath"]) if raw.get("worktreePath") is not None else (str(raw["worktree_path"]) if raw.get("worktree_path") is not None else None),
                        worktree_name=str(raw["worktreeName"]) if raw.get("worktreeName") is not None else (str(raw["worktree_name"]) if raw.get("worktree_name") is not None else None),
                        supervisor_session_id=str(raw["supervisorSessionId"]) if raw.get("supervisorSessionId") is not None else (str(raw["supervisor_session_id"]) if raw.get("supervisor_session_id") is not None else None),
                        phase=str(raw["phase"]) if raw.get("phase") is not None else None,
                        token_usage=raw.get("tokenUsage") or raw.get("token_usage"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        tickets.sort(key=lambda item: item.updated_at, reverse=True)
        return tickets

    def add_ticket(
        self,
        *,
        project_ref: str,
        title: str,
        description: str = "",
        priority: str = "medium",
        blocked_by: Optional[list[str]] = None,
        ticket_id: Optional[str] = None,
    ) -> TicketRecord:
        project = self.resolve_project(project_ref)
        if project is None:
            raise ValueError(f"Project not found: {project_ref}")
        pipeline = self.get_pipeline(project.id)
        now_ms = self._now_ms()
        ticket = TicketRecord(
            id=ticket_id or f"ticket-{abs(hash((project.id, title, now_ms))) & 0xffffffff:x}",
            project_id=project.id,
            title=title,
            description=description,
            priority=priority,
            blocked_by=blocked_by or [],
            column_id=pipeline.columns[0].id,
            created_at=now_ms,
            updated_at=now_ms,
        )
        config = self._load()
        config.setdefault("fleetTickets", [])
        config["fleetTickets"] = [
            *config["fleetTickets"],
            {
                "id": ticket.id,
                "projectId": ticket.project_id,
                "title": ticket.title,
                "description": ticket.description,
                "priority": ticket.priority,
                "status": "open",
                "blockedBy": ticket.blocked_by,
                "createdAt": ticket.created_at,
                "updatedAt": ticket.updated_at,
                "columnId": ticket.column_id,
            },
        ]
        self._save(config)
        self.write_ticket_file(ticket.id, project.id)
        return ticket

    def resolve_ticket(self, ticket_ref: str, project_ref: Optional[str] = None) -> Optional[TicketRecord]:
        tickets = self.list_tickets(project_ref)
        for ticket in tickets:
            if ticket.id == ticket_ref or ticket.title == ticket_ref:
                return ticket
        return None

    def move_ticket(self, ticket_ref: str, column_id: str, project_ref: Optional[str] = None) -> TicketRecord:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise ValueError(f"Ticket not found: {ticket_ref}")
        pipeline = self.get_pipeline(ticket.project_id)
        if not any(column.id == column_id for column in pipeline.columns):
            raise ValueError(f"Unknown column: {column_id}")
        config = self._load()
        updated = TicketRecord(
            id=ticket.id,
            project_id=ticket.project_id,
            title=ticket.title,
            description=ticket.description,
            priority=ticket.priority,
            blocked_by=ticket.blocked_by,
            column_id=column_id,
            created_at=ticket.created_at,
            updated_at=self._now_ms(),
            branch=ticket.branch,
            use_worktree=ticket.use_worktree,
            worktree_path=ticket.worktree_path,
            worktree_name=ticket.worktree_name,
            supervisor_session_id=ticket.supervisor_session_id,
            phase=ticket.phase,
            token_usage=ticket.token_usage,
        )
        config["fleetTickets"] = [
            {
                **raw,
                "columnId": updated.column_id,
                "updatedAt": updated.updated_at,
            }
            if raw.get("id") == ticket.id
            else raw
            for raw in config.get("fleetTickets", [])
        ]
        self._save(config)
        self.write_ticket_file(updated.id, updated.project_id)
        return updated

    def update_ticket_fields(self, ticket_ref: str, patch: dict[str, Any], project_ref: Optional[str] = None) -> TicketRecord:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise ValueError(f"Ticket not found: {ticket_ref}")
        config = self._load()
        updated_raw = None
        fleet_tickets = []
        for raw in config.get("fleetTickets", []):
            if raw.get("id") == ticket.id:
                updated_raw = {**deepcopy(raw), **patch}
                fleet_tickets.append(updated_raw)
            else:
                fleet_tickets.append(raw)
        config["fleetTickets"] = fleet_tickets
        self._save(config)
        resolved = self.resolve_ticket(ticket.id, project_ref) or ticket
        self.write_ticket_file(resolved.id, resolved.project_id)
        return resolved

    def list_tasks(self, project_ref: Optional[str] = None) -> list[TaskRecord]:
        project = self.resolve_project(project_ref) if project_ref else None
        config = self._load()
        tasks: list[TaskRecord] = []
        for raw in config.get("fleetTasks", []):
            if not isinstance(raw, dict):
                continue
            if project is not None and raw.get("projectId") != project.id:
                continue
            try:
                tasks.append(
                    TaskRecord(
                        id=str(raw["id"]),
                        project_id=str(raw["projectId"]),
                        task_description=str(raw.get("taskDescription", "")),
                        status=dict(raw.get("status") or {}),
                        created_at=int(raw.get("createdAt", 0)),
                        ticket_id=str(raw["ticketId"]) if raw.get("ticketId") is not None else None,
                        session_id=str(raw["sessionId"]) if raw.get("sessionId") is not None else None,
                        run_id=str(raw["runId"]) if raw.get("runId") is not None else None,
                        last_urls=dict(raw.get("lastUrls") or {}) if raw.get("lastUrls") else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        tasks.sort(key=lambda item: item.created_at, reverse=True)
        return tasks

    def resolve_task_for_ticket(self, ticket_ref: str, project_ref: Optional[str] = None) -> Optional[TaskRecord]:
        ticket = self.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            return None
        for task in self.list_tasks(ticket.project_id):
            if task.ticket_id == ticket.id:
                return task
        return None

    def upsert_task(
        self,
        *,
        project_id: str,
        task_description: str,
        status: dict[str, Any],
        ticket_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        last_urls: dict[str, str] | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        config = self._load()
        tasks = list(config.get("fleetTasks", []))
        existing_index = -1
        if task_id:
            existing_index = next((index for index, raw in enumerate(tasks) if raw.get("id") == task_id), -1)
        elif ticket_id:
            existing_index = next((index for index, raw in enumerate(tasks) if raw.get("ticketId") == ticket_id), -1)
        else:
            existing_index = next((index for index, raw in enumerate(tasks) if raw.get("projectId") == project_id and raw.get("ticketId") is None), -1)

        created_at = self._now_ms()
        resolved_task_id = task_id or f"task-{abs(hash((project_id, ticket_id, task_description))) & 0xffffffff:x}"
        if existing_index >= 0:
            previous = tasks[existing_index]
            created_at = int(previous.get("createdAt", created_at))
            resolved_task_id = str(previous.get("id", resolved_task_id))

        payload = {
            "id": resolved_task_id,
            "projectId": project_id,
            "taskDescription": task_description,
            "status": status,
            "createdAt": created_at,
        }
        if ticket_id is not None:
            payload["ticketId"] = ticket_id
        if session_id is not None:
            payload["sessionId"] = session_id
        if run_id is not None:
            payload["runId"] = run_id
        if last_urls is not None:
            payload["lastUrls"] = last_urls

        if existing_index >= 0:
            tasks[existing_index] = {**tasks[existing_index], **payload}
        else:
            tasks.append(payload)

        config["fleetTasks"] = tasks
        self._save(config)
        return TaskRecord(
            id=resolved_task_id,
            project_id=project_id,
            task_description=task_description,
            status=status,
            created_at=created_at,
            ticket_id=ticket_id,
            session_id=session_id,
            run_id=run_id,
            last_urls=last_urls,
        )

    def get_pipeline(self, project_ref: str) -> PipelineRecord:
        project = self.resolve_project(project_ref)
        if project is None:
            return DEFAULT_PIPELINE
        workflow = self.get_workflow(project.id)
        if workflow.config.pipeline:
            return PipelineRecord(
                columns=[
                    ColumnRecord(
                        id=column.id,
                        label=column.label,
                        gate=column.gate,
                    )
                    for column in workflow.config.pipeline
                ]
            )
        return DEFAULT_PIPELINE

    def export_snapshot(self) -> dict[str, Any]:
        projects = self.list_projects()
        current_project = self.get_current_project()
        project_payload = [asdict(project) for project in projects]
        tickets_payload: list[dict[str, Any]] = []
        pipelines_payload: dict[str, dict[str, Any]] = {}

        for project in projects:
            pipeline = self.get_pipeline(project.id)
            pipelines_payload[project.id] = {
                "columns": [
                    {
                        "id": column.id,
                        "label": column.label,
                        "max_concurrent": column.max_concurrent,
                        "gate": column.gate,
                    }
                    for column in pipeline.columns
                ]
            }
            tickets_payload.extend(asdict(ticket) for ticket in self.list_tickets(project.id))

        return {
            "current_project": current_project.id if current_project else None,
            "projects": project_payload,
            "tickets": tickets_payload,
            "pipelines": pipelines_payload,
        }


__all__ = [
    "ColumnRecord",
    "DEFAULT_PIPELINE",
    "PipelineRecord",
    "ProjectRecord",
    "ProjectsRuntime",
    "SandboxConfig",
    "TaskRecord",
    "TicketRecord",
]
