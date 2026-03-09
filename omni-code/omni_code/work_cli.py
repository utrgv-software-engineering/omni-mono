import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import websockets

from omni_code import sandbox_cli
from omni_code.config import get_config_dir
from omni_code.projects_runtime import ProjectsRuntime
from omniagents.core.session.history_db import load_history as oa_load_history


WORK_PERSIST_VOLUMES = [
    ("omni-gh", "/home/user/.config/gh"),
    ("omni-azure", "/home/user/.azure"),
    ("omni-gitconfig", "/home/user/.gitconfig.d"),
    ("omni-ssh", "/home/user/.ssh"),
    ("omni-npm", "/home/user/.npmrc"),
]

SANDBOX_CONTAINER_PORT = 7681
CODE_SERVER_CONTAINER_PORT = 8080
VNC_CONTAINER_PORT = 6080


def _resolve_workspace(project_ref: Optional[str]) -> Path:
    if project_ref:
        candidate = Path(project_ref).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()

        project = ProjectsRuntime().resolve_project(project_ref)
        if project is not None:
            return Path(project.workspace_dir)

        raise RuntimeError(f"Project or workspace not found: {project_ref}")

    return Path.cwd().resolve()


def _resolve_ticket_workspace(ticket_ref: str, project_ref: Optional[str] = None) -> Path:
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")
    worktree_path = getattr(ticket, "worktree_path", None)
    if worktree_path:
        worktree = Path(worktree_path).expanduser()
        if worktree.exists() and worktree.is_dir():
            return worktree.resolve()
    if getattr(ticket, "use_worktree", False) and getattr(ticket, "branch", None):
        project = runtime.resolve_project(ticket.project_id)
        if project is None:
            raise RuntimeError(f"Project not found for ticket: {ticket_ref}")
        worktree_name = _generate_worktree_name()
        worktree = _create_worktree(Path(project.workspace_dir), str(ticket.branch), worktree_name)
        getattr(runtime, "run_hook", lambda *_args, **_kwargs: True)(project.id, "after_create", worktree)
        runtime.update_ticket_fields(
            ticket.id,
            {
                "worktreePath": str(worktree),
                "worktreeName": worktree_name,
            },
            project.id,
        )
        return worktree
    project = runtime.resolve_project(ticket.project_id)
    if project is None:
        raise RuntimeError(f"Project not found for ticket: {ticket_ref}")
    return Path(project.workspace_dir)


def _container_name_for_workspace(workspace: Path) -> str:
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"omni-work-{digest}"


def _get_worktrees_dir() -> Path:
    return Path.home() / "Omni" / "Worktrees"


def _generate_worktree_name() -> str:
    digest = hashlib.sha256(str(time.time()).encode("utf-8")).hexdigest()[:10]
    return f"work-{digest}"


def _create_worktree(workspace: Path, branch: str, name: str) -> Path:
    worktrees_dir = _get_worktrees_dir()
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    worktree_path = worktrees_dir / name
    fleet_branch = f"fleet/{name}"
    subprocess.run(
        ["git", "-C", str(workspace), "worktree", "add", "-b", fleet_branch, str(worktree_path), branch],
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path.resolve()


def _remove_worktree(workspace: Path, worktree_path: Path, worktree_name: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), "worktree", "remove", "--force", str(worktree_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "branch", "-D", f"fleet/{worktree_name}"],
        check=False,
        capture_output=True,
        text=True,
    )


def _ticket_artifacts_dir(ticket_id: str) -> Path:
    return get_config_dir() / "fleet" / "tickets" / ticket_id / "artifacts"


CONTAINER_CONFIG_ROOT = "/home/user/.config/omni_code"
FLEET_TICKETS_DIR = "fleet/tickets"


def _container_ticket_file_path(ticket_id: str) -> str:
    return f"{CONTAINER_CONFIG_ROOT}/{FLEET_TICKETS_DIR}/{ticket_id}/TICKET.yaml"


def _container_artifacts_dir(ticket_id: str) -> str:
    return f"{CONTAINER_CONFIG_ROOT}/{FLEET_TICKETS_DIR}/{ticket_id}/artifacts"


def _build_supervisor_prompt(ticket, project, runtime) -> str:
    pipeline = runtime.get_pipeline(project.id)
    current_column = next((c for c in pipeline.columns if c.id == ticket.column_id), None)
    column_label = current_column.label if current_column else ticket.column_id
    column_names = " → ".join(c.label for c in pipeline.columns)
    ticket_file = _container_ticket_file_path(ticket.id)
    artifacts_dir = _container_artifacts_dir(ticket.id)

    base_prompt = f"""You are a supervisor agent orchestrating work on a ticket. You manage the full lifecycle from planning through implementation, review, and PR creation.

## Ticket
Title: {ticket.title}
Description: {ticket.description or '(no description)'}
Priority: {ticket.priority}
Current Column: {column_label}

## Pipeline
{column_names}

To move this ticket to a different column, edit the `column` field in:
  {ticket_file}

The orchestrator watches this file and will update the ticket's column automatically.
Only use column labels from the pipeline above.

## Escalation

If you are blocked and need human help, add an `escalation` field to TICKET.yaml:

```yaml
column: "{column_label}"
escalation: "Brief description of what you need help with"
```

This will pause your run and notify the human with a toast notification, regardless of what screen they are on. Only escalate when you are truly stuck — do not use this for status updates.

## Artifacts Directory
You have a persistent artifacts directory at: {artifacts_dir}
Use this for progress notes, research, scratch work, or any files that should persist.

## Your Responsibilities

1. **Plan** — Analyze the ticket and create a detailed plan with concrete, testable steps organized by milestone.
2. **Dispatch workers** — Use `spawn_worker` to delegate implementation tasks. Give each worker:
   - A clear goal and scope
   - Context about what files to work with
   - Boundaries (what NOT to change)
   - Acceptance criteria
3. **Track progress** — Keep track of completed steps and remaining work.
4. **Verify quality** — Run tests, check linting, review changes before marking things complete.
5. **Move the ticket** — When work for the current column is complete, update TICKET.yaml to advance the ticket.
6. **Ask when stuck** — If you need human input, end your message with a clear question. The user can respond and you'll continue.

## Worker Dispatch Guidelines

When spawning workers, structure your requests like:
- **Goal**: What the worker should accomplish
- **Scope**: Which files/modules to work in
- **Context**: Relevant background from the plan
- **Boundaries**: What NOT to touch
- **Acceptance**: How to verify the work is done"""

    # Append project-specific instructions from FLEET.md prompt_template if present
    workflow = runtime.get_workflow(project.id)
    if workflow.prompt_template:
        rendered = _render_fleet_template(workflow.prompt_template, ticket, project, pipeline)
        return base_prompt + f"\n\n## Project-Specific Instructions (from FLEET.md)\n\n{rendered}"

    return base_prompt


def _render_fleet_template(template: str, ticket, project, pipeline) -> str:
    """Render a FLEET.md prompt template with {{variable}} expressions."""
    import re
    column_names = " → ".join(c.label for c in pipeline.columns)
    variables = {
        "ticket.id": ticket.id,
        "ticket.title": ticket.title,
        "ticket.description": ticket.description or "(no description)",
        "ticket.priority": ticket.priority,
        "ticket.columnId": ticket.column_id,
        "ticket.branch": getattr(ticket, "branch", "") or "",
        "pipeline.columns": column_names,
        "project.label": project.label,
        "project.workspaceDir": project.workspace_dir,
    }

    def replace_var(match):
        var_path = match.group(1).strip()
        value = variables.get(var_path)
        if value is None:
            return match.group(0)  # leave unresolved variables as-is
        return str(value)

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", replace_var, template)


def _resolve_session_id(ticket_ref: str | None = None, project_ref: str | None = None) -> str:
    runtime = ProjectsRuntime()
    if ticket_ref:
        ticket = runtime.resolve_ticket(ticket_ref, project_ref)
        if ticket is None:
            raise RuntimeError(f"Ticket not found: {ticket_ref}")
        if ticket.supervisor_session_id:
            return ticket.supervisor_session_id
        task = runtime.resolve_task_for_ticket(ticket.id, ticket.project_id)
        if task and task.session_id:
            return task.session_id
        raise RuntimeError("No session is recorded for this ticket")
    raise RuntimeError("History currently requires --ticket")


def _format_history_messages(messages: list[dict]) -> list[dict[str, object]]:
    formatted: list[dict[str, object]] = []
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or message.get("type") or "unknown")
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content) if content is not None else ""
        formatted.append(
            {
                "index": index,
                "role": role,
                "content": content,
            }
        )
    return formatted


def _work_dockerfile_path() -> str:
    return str((Path(sandbox_cli.__file__).resolve().parent / "sandbox" / "Dockerfile.work").resolve())


def _network_allowlist_from_config() -> Optional[str]:
    config_path = get_config_dir() / "network.json"
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    allowlist = raw.get("allowlist") or raw.get("allowedHosts") or []
    if not isinstance(allowlist, list):
        return None
    hosts = [str(item).strip() for item in allowlist if str(item).strip()]
    return ",".join(hosts) if hosts else None


def _inspect_container(container_name: str) -> Optional[dict]:
    result = sandbox_cli._run_process(
        ["docker", "inspect", container_name],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    return first if isinstance(first, dict) else None


def _container_host_port(details: Optional[dict], container_port: int) -> Optional[int]:
    if not details:
        return None
    ports = ((details.get("NetworkSettings") or {}).get("Ports") or {})
    bindings = ports.get(f"{container_port}/tcp")
    if not isinstance(bindings, list) or not bindings:
        return None
    first = bindings[0]
    if not isinstance(first, dict):
        return None
    host_port = first.get("HostPort")
    if host_port is None:
        return None
    try:
        return int(str(host_port))
    except ValueError:
        return None



def _work_urls(details: Optional[dict]) -> dict[str, Optional[str]]:
    sandbox_port = _container_host_port(details, SANDBOX_CONTAINER_PORT)
    code_port = _container_host_port(details, CODE_SERVER_CONTAINER_PORT)
    vnc_port = _container_host_port(details, VNC_CONTAINER_PORT)
    return {
        "sandbox_url": f"http://localhost:{sandbox_port}" if sandbox_port is not None else None,
        "ws_url": f"ws://localhost:{sandbox_port}/ws" if sandbox_port is not None else None,
        "code_server_url": f"http://localhost:{code_port}" if code_port is not None else None,
        "desktop_url": f"http://localhost:{vnc_port}/vnc.html?autoconnect=true&resize=scale" if vnc_port is not None else None,
    }


def _task_status_payload(running: bool, details: Optional[dict]) -> tuple[dict[str, object], dict[str, str] | None]:
    urls = _work_urls(details)
    if running:
        status: dict[str, object] = {
            "type": "running",
            "timestamp": int(__import__("time").time() * 1000),
            "data": {
                "sandboxUrl": urls["sandbox_url"],
                "wsUrl": urls["ws_url"],
                "uiUrl": urls["sandbox_url"],
                "codeServerUrl": urls["code_server_url"],
                "noVncUrl": urls["desktop_url"],
                "containerName": (details or {}).get("Name", "").lstrip("/") if details else None,
            },
        }
        last_urls = {
            key: value
            for key, value in {
                "uiUrl": urls["sandbox_url"],
                "codeServerUrl": urls["code_server_url"],
                "noVncUrl": urls["desktop_url"],
            }.items()
            if value
        }
        return status, last_urls or None
    return {"type": "exited", "timestamp": int(__import__("time").time() * 1000)}, None


def _record_task_state(workspace: Path, details: Optional[dict], *, ticket_ref: str | None = None) -> None:
    runtime = ProjectsRuntime()
    project = runtime.resolve_project(str(workspace))
    if project is None:
        return
    ticket = runtime.resolve_ticket(ticket_ref, project.id) if ticket_ref else None
    task_description = f"Work sandbox for: {ticket.title}" if ticket is not None else f"Work sandbox for: {project.label}"
    running = bool(details and bool((details.get("State") or {}).get("Running")))
    status, last_urls = _task_status_payload(running, details)
    existing = runtime.resolve_task_for_ticket(ticket.id, project.id) if ticket is not None else None
    runtime.upsert_task(
        project_id=project.id,
        task_description=task_description,
        status=status,
        ticket_id=ticket.id if ticket is not None else None,
        last_urls=last_urls,
        task_id=existing.id if existing is not None else None,
    )


async def _rpc_call(ws_url: str, method: str, params: dict[str, object]) -> dict[str, object]:
    async with websockets.connect(ws_url, max_size=None) as websocket:
        request_id = "1"
        await websocket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        while True:
            raw = await websocket.recv()
            payload = json.loads(raw)
            if payload.get("id") != request_id:
                continue
            return payload


def _ensure_ticket_rpc_context(ticket_ref: str, project_ref: str | None = None) -> tuple[ProjectsRuntime, object, object, object | None, str]:
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")
    project = runtime.resolve_project(ticket.project_id)
    if project is None:
        raise RuntimeError(f"Project not found for ticket: {ticket_ref}")
    workspace = _resolve_ticket_workspace(ticket_ref, project_ref)
    container_name = _ensure_started(workspace, rebuild=False)
    details = _inspect_container(container_name)
    _record_task_state(workspace, details, ticket_ref=ticket_ref)
    task = runtime.resolve_task_for_ticket(ticket.id, project.id)
    ws_url = _work_urls(details).get("ws_url")
    if not ws_url:
        raise RuntimeError("No sandbox WebSocket URL is available")
    return runtime, ticket, project, task, ws_url


def supervisor_start(ticket_ref: str, prompt: str, project_ref: str | None = None) -> dict[str, str]:
    runtime, ticket, project, task, ws_url = _ensure_ticket_rpc_context(ticket_ref, project_ref)
    runtime.write_ticket_file(ticket.id, project.id)
    if not getattr(runtime, "run_hook", lambda *_args, **_kwargs: True)(project.id, "before_run", _resolve_ticket_workspace(ticket_ref, project_ref)):
        raise RuntimeError("before_run hook failed")
    supervisor_prompt = _build_supervisor_prompt(ticket, project, runtime)
    session_id = getattr(ticket, "supervisor_session_id", None) or (task.session_id if task else None)
    if session_id is None:
        response = asyncio.run(_rpc_call(ws_url, "server_call", {
            "function": "session.ensure",
            "args": {"variables": {"additional_instructions": supervisor_prompt}},
        }))
        if response.get("error"):
            raise RuntimeError(str(response["error"].get("message") or "session.ensure failed"))
        session_id = str((response.get("result") or {}).get("session_id") or "")
        if not session_id:
            raise RuntimeError("No session_id returned by session.ensure")
        runtime.update_ticket_fields(ticket.id, {"supervisorSessionId": session_id}, project.id)

    response = asyncio.run(
        _rpc_call(
            ws_url,
            "start_run",
            {
                "prompt": prompt,
                "session_id": session_id,
                "variables": {"additional_instructions": supervisor_prompt},
                "safe_tool_overrides": {"safe_tool_patterns": [".*"]},
            },
        )
    )
    if response.get("error"):
        raise RuntimeError(str(response["error"].get("message") or "start_run failed"))
    result = response.get("result") or {}
    final_session_id = str(result.get("session_id") or session_id)
    run_id = str(result.get("run_id") or final_session_id)
    runtime.update_ticket_fields(ticket.id, {"supervisorSessionId": final_session_id}, project.id)
    runtime.upsert_task(
        project_id=project.id,
        task_description=f"Work sandbox for: {ticket.title}",
        status=(task.status if task else {"type": "running", "timestamp": int(time.time() * 1000)}),
        ticket_id=ticket.id,
        session_id=final_session_id,
        run_id=run_id,
        last_urls=(task.last_urls if task else None),
        task_id=(task.id if task else None),
    )
    return {"session_id": final_session_id, "run_id": run_id}


def supervisor_send(ticket_ref: str, message: str, project_ref: str | None = None) -> None:
    runtime, ticket, project, task, ws_url = _ensure_ticket_rpc_context(ticket_ref, project_ref)
    run_id = task.run_id if task else None

    if run_id:
        # Active run — send message directly into the running session.
        response = asyncio.run(_rpc_call(ws_url, "send_user_message", {"run_id": run_id, "content": message}))
        if response.get("error"):
            raise RuntimeError(str(response["error"].get("message") or "send_user_message failed"))
        return

    # No active run — start a new run with the user's message as prompt,
    # injecting full supervisor context (matching launcher's sendUserRunMessage).
    supervisor_prompt = _build_supervisor_prompt(ticket, project, runtime)
    session_id = getattr(ticket, "supervisor_session_id", None) or (task.session_id if task else None)
    if session_id is None:
        resp = asyncio.run(_rpc_call(ws_url, "server_call", {
            "function": "session.ensure",
            "args": {"variables": {"additional_instructions": supervisor_prompt}},
        }))
        if resp.get("error"):
            raise RuntimeError(str(resp["error"].get("message") or "session.ensure failed"))
        session_id = str((resp.get("result") or {}).get("session_id") or "")
        if not session_id:
            raise RuntimeError("No session_id returned by session.ensure")
        runtime.update_ticket_fields(ticket.id, {"supervisorSessionId": session_id}, project.id)

    response = asyncio.run(
        _rpc_call(
            ws_url,
            "start_run",
            {
                "prompt": message,
                "session_id": session_id,
                "variables": {"additional_instructions": supervisor_prompt},
                "safe_tool_overrides": {"safe_tool_patterns": [".*"]},
            },
        )
    )
    if response.get("error"):
        raise RuntimeError(str(response["error"].get("message") or "start_run failed"))
    result = response.get("result") or {}
    final_session_id = str(result.get("session_id") or session_id)
    run_id = str(result.get("run_id") or final_session_id)
    runtime.update_ticket_fields(ticket.id, {"supervisorSessionId": final_session_id}, project.id)
    runtime.upsert_task(
        project_id=project.id,
        task_description=f"Work sandbox for: {ticket.title}",
        status={"type": "running", "timestamp": int(time.time() * 1000)},
        ticket_id=ticket.id,
        session_id=final_session_id,
        run_id=run_id,
        last_urls=(task.last_urls if task else None),
        task_id=(task.id if task else None),
    )


def supervisor_stop(ticket_ref: str, project_ref: str | None = None) -> None:
    runtime, ticket, project, task, ws_url = _ensure_ticket_rpc_context(ticket_ref, project_ref)
    run_id = task.run_id if task else None
    if not run_id:
        raise RuntimeError("No active run is recorded for this ticket")
    response = asyncio.run(_rpc_call(ws_url, "stop_run", {"run_id": run_id}))
    if response.get("error"):
        raise RuntimeError(str(response["error"].get("message") or "stop_run failed"))
    runtime.upsert_task(
        project_id=project.id,
        task_description=task.task_description if task else f"Work sandbox for: {ticket.title}",
        status={"type": "idle", "timestamp": int(time.time() * 1000)},
        ticket_id=ticket.id,
        session_id=(task.session_id if task else getattr(ticket, "supervisor_session_id", None)),
        run_id="",
        last_urls=(task.last_urls if task else None),
        task_id=(task.id if task else None),
    )
    getattr(runtime, "run_hook", lambda *_args, **_kwargs: True)(project.id, "after_run", _resolve_ticket_workspace(ticket_ref, project_ref))


# ---------------------------------------------------------------------------
# Supervisor auto mode — long-running lifecycle manager for a single ticket
# ---------------------------------------------------------------------------

AUTO_MAX_CONTINUATION_TURNS = 10
AUTO_MAX_RETRY_ATTEMPTS = 3
AUTO_RETRY_BASE_DELAY = 10.0
AUTO_RETRY_MAX_DELAY = 300.0
AUTO_CONTINUATION_DELAY = 3.0
AUTO_STALL_TIMEOUT = 300.0  # 5 minutes
AUTO_STALL_CHECK_INTERVAL = 30.0
AUTO_TICKET_POLL_INTERVAL = 2.0
AUTO_WS_RECONNECT_BASE = 5.0
AUTO_WS_RECONNECT_MAX = 60.0

log = logging.getLogger("omni.supervisor_auto")


def _classify_run_end(reason: str) -> str:
    r = reason.lower()
    if r in ("completed", "done", "finished", "success"):
        return "completed"
    if r in ("cancelled", "canceled", "stopped", "user_stopped"):
        return "stopped"
    if r == "max_turns":
        return "max_turns"
    if r == "stalled":
        return "stalled"
    return "error"


def _decide_run_end_action(reason: str, continuation_turn: int, max_turns: int) -> dict:
    cls = _classify_run_end(reason)
    if cls == "stopped":
        return {"type": "stopped"}
    if cls in ("completed", "max_turns"):
        next_turn = continuation_turn + 1
        if next_turn >= max_turns:
            return {"type": "complete"}
        return {"type": "continue", "next_turn": next_turn}
    return {"type": "retry", "failure_class": cls}


def _build_continuation_prompt(ticket, runtime, turn: int, max_turns: int) -> str:
    workflow = runtime.get_workflow(ticket.project_id)
    custom = None
    if workflow.config.supervisor:
        custom = workflow.config.supervisor.continuation_prompt
    if custom:
        return custom.replace("{{turn}}", str(turn)).replace("{{maxTurns}}", str(max_turns))
    pipeline = runtime.get_pipeline(ticket.project_id)
    column_labels = ", ".join(c.label for c in pipeline.columns)
    current_col = next((c for c in pipeline.columns if c.id == ticket.column_id), None)
    current_label = current_col.label if current_col else ticket.column_id
    ticket_file = _container_ticket_file_path(ticket.id)
    return "\n".join([
        "Continuation guidance:",
        "",
        f"- The previous run completed normally, but the ticket may still have remaining work.",
        f"- This is continuation turn {turn} of {max_turns}.",
        "- Resume from current workspace state — do not restart from scratch or re-read files you already have in context.",
        "- The original task instructions and prior context are already in this session, so do not restate them before acting.",
        "- Use your best judgement to move the work forward. You are working in an isolated sandbox, so it is safe to make changes freely. Do not ask for confirmation or escalate to the user unless you are truly blocked on something that requires human input. The human will review your work at a later stage.",
        f'- Your ticket is currently in column "{current_label}". If you have completed the work for this column, update the column field in {ticket_file} to advance the ticket. Valid columns: {column_labels}.',
        f"- If you are truly blocked and need human help, add an `escalation` field to {ticket_file}. This will pause your run and notify the human. Only use this when you cannot proceed without human input.",
    ])


def _effective_supervisor_config(runtime, project_id: str) -> dict:
    workflow = runtime.get_workflow(project_id)
    sup = workflow.config.supervisor
    return {
        "max_continuation_turns": (sup.max_continuation_turns if sup and sup.max_continuation_turns else AUTO_MAX_CONTINUATION_TURNS),
        "max_retry_attempts": (sup.max_retry_attempts if sup and sup.max_retry_attempts else AUTO_MAX_RETRY_ATTEMPTS),
        "stall_timeout_ms": (sup.stall_timeout_ms if sup and sup.stall_timeout_ms else int(AUTO_STALL_TIMEOUT * 1000)),
    }


def _is_terminal_column(runtime, project_id: str, column_id: str) -> bool:
    pipeline = runtime.get_pipeline(project_id)
    if not pipeline.columns:
        return False
    return pipeline.columns[-1].id == column_id


def _is_gate_column(runtime, project_id: str, column_id: str) -> bool:
    pipeline = runtime.get_pipeline(project_id)
    col = next((c for c in pipeline.columns if c.id == column_id), None)
    return bool(col and col.gate)


@dataclass
class _AutoState:
    phase: str = "idle"
    session_id: str = ""
    run_id: str = ""
    continuation_turn: int = 0
    retry_attempt: int = 0
    last_activity: float = field(default_factory=time.monotonic)
    should_stop: bool = False
    stop_reason: str = ""


def _update_phase(state: _AutoState, phase: str, runtime, ticket, project, task) -> None:
    state.phase = phase
    runtime.update_ticket_fields(ticket.id, {"phase": phase}, project.id)
    log.info("ticket=%s phase=%s cont=%d retry=%d", ticket.id, phase, state.continuation_turn, state.retry_attempt)


async def _ws_send_rpc(ws, method: str, params: dict) -> dict:
    """Send a JSON-RPC request on an already-open WebSocket and return the response."""
    request_id = str(int(time.time() * 1000))
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    while True:
        raw = await ws.recv()
        payload = json.loads(raw)
        if payload.get("id") == request_id:
            return payload


async def _auto_start_run(ws, state: _AutoState, prompt: str, supervisor_prompt: str) -> None:
    """Send start_run over the persistent WebSocket."""
    params: dict = {
        "prompt": prompt,
        "session_id": state.session_id,
        "variables": {"additional_instructions": supervisor_prompt},
        "safe_tool_overrides": {"safe_tool_patterns": [".*"]},
    }
    resp = await _ws_send_rpc(ws, "start_run", params)
    if resp.get("error"):
        raise RuntimeError(str(resp["error"].get("message") or "start_run failed"))
    result = resp.get("result") or {}
    state.session_id = str(result.get("session_id") or state.session_id)
    state.run_id = str(result.get("run_id") or state.session_id)
    state.last_activity = time.monotonic()


async def _auto_ensure_session(ws, state: _AutoState, supervisor_prompt: str) -> None:
    """Ensure a session exists, creating one if needed."""
    if state.session_id:
        return
    resp = await _ws_send_rpc(ws, "server_call", {
        "function": "session.ensure",
        "args": {"variables": {"additional_instructions": supervisor_prompt}},
    })
    if resp.get("error"):
        raise RuntimeError(str(resp["error"].get("message") or "session.ensure failed"))
    state.session_id = str((resp.get("result") or {}).get("session_id") or "")
    if not state.session_id:
        raise RuntimeError("No session_id returned by session.ensure")


async def _supervisor_auto_async(
    ticket_ref: str,
    prompt: str,
    project_ref: str | None,
    stop_event: threading.Event | None,
) -> dict:
    """Core async loop for supervisor auto mode."""
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")
    project = runtime.resolve_project(ticket.project_id)
    if project is None:
        raise RuntimeError(f"Project not found for ticket: {ticket_ref}")

    workspace = _resolve_ticket_workspace(ticket_ref, project_ref)
    container_name = _ensure_started(workspace, rebuild=False)
    details = _inspect_container(container_name)
    _record_task_state(workspace, details, ticket_ref=ticket_ref)
    task = runtime.resolve_task_for_ticket(ticket.id, project.id)
    ws_url = _work_urls(details).get("ws_url")
    if not ws_url:
        raise RuntimeError("No sandbox WebSocket URL is available")

    runtime.write_ticket_file(ticket.id, project.id)
    runtime.run_hook(project.id, "before_run", workspace)

    cfg = _effective_supervisor_config(runtime, project.id)
    max_turns = cfg["max_continuation_turns"]
    max_retries = cfg["max_retry_attempts"]
    stall_timeout = cfg["stall_timeout_ms"] / 1000.0

    supervisor_prompt = _build_supervisor_prompt(ticket, project, runtime)
    state = _AutoState()
    state.session_id = getattr(ticket, "supervisor_session_id", "") or (task.session_id if task else "") or ""

    _update_phase(state, "running", runtime, ticket, project, task)

    ws_reconnect_attempt = 0

    while not state.should_stop:
        if stop_event and stop_event.is_set():
            state.should_stop = True
            state.stop_reason = "user_stopped"
            break

        try:
            async with websockets.connect(ws_url, max_size=None) as ws:
                ws_reconnect_attempt = 0

                # Ensure session
                await _auto_ensure_session(ws, state, supervisor_prompt)
                runtime.update_ticket_fields(ticket.id, {"supervisorSessionId": state.session_id}, project.id)

                # Start first run (or continuation/retry depending on phase)
                if state.phase == "running":
                    await _auto_start_run(ws, state, prompt, supervisor_prompt)
                elif state.phase == "continuing":
                    cont_prompt = _build_continuation_prompt(ticket, runtime, state.continuation_turn + 1, max_turns)
                    await _auto_start_run(ws, state, cont_prompt, supervisor_prompt)
                elif state.phase == "retrying":
                    retry_prompt = "The previous run failed. Please review the current state and continue working on this ticket."
                    await _auto_start_run(ws, state, retry_prompt, supervisor_prompt)

                # Persist task state
                runtime.upsert_task(
                    project_id=project.id,
                    task_description=f"Work sandbox for: {ticket.title}",
                    status={"type": "running", "timestamp": int(time.time() * 1000)},
                    ticket_id=ticket.id,
                    session_id=state.session_id,
                    run_id=state.run_id,
                    last_urls=(task.last_urls if task else None),
                    task_id=(task.id if task else None),
                )

                # --- Event loop: listen for notifications ---
                last_ticket_poll = time.monotonic()
                last_stall_check = time.monotonic()

                while not state.should_stop:
                    if stop_event and stop_event.is_set():
                        state.should_stop = True
                        state.stop_reason = "user_stopped"
                        break

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        raw = None
                    except websockets.exceptions.ConnectionClosed:
                        log.warning("WebSocket closed, will reconnect")
                        break

                    now = time.monotonic()

                    # Process WebSocket message
                    if raw is not None:
                        payload = json.loads(raw)
                        # Skip RPC responses (have id)
                        if payload.get("id"):
                            pass
                        elif payload.get("method") == "run_end":
                            end_reason = str((payload.get("params") or {}).get("end_reason") or "completed")
                            state.run_id = ""
                            log.info("run_end reason=%s", end_reason)

                            runtime.run_hook(project.id, "after_run", workspace)

                            action = _decide_run_end_action(end_reason, state.continuation_turn, max_turns)

                            if action["type"] == "stopped":
                                _update_phase(state, "idle", runtime, ticket, project, task)
                                state.should_stop = True
                                state.stop_reason = "user_stopped"

                            elif action["type"] == "complete":
                                _update_phase(state, "completed", runtime, ticket, project, task)
                                state.should_stop = True
                                state.stop_reason = "complete"

                            elif action["type"] == "continue":
                                # Re-read ticket for column changes
                                ticket = runtime.resolve_ticket(ticket.id, project.id) or ticket
                                if _is_terminal_column(runtime, project.id, ticket.column_id):
                                    _update_phase(state, "completed", runtime, ticket, project, task)
                                    state.should_stop = True
                                    state.stop_reason = "terminal_column"
                                elif _is_gate_column(runtime, project.id, ticket.column_id):
                                    _update_phase(state, "idle", runtime, ticket, project, task)
                                    state.should_stop = True
                                    state.stop_reason = "gate_column"
                                else:
                                    state.continuation_turn = action["next_turn"]
                                    _update_phase(state, "continuing", runtime, ticket, project, task)
                                    await asyncio.sleep(AUTO_CONTINUATION_DELAY)
                                    cont_prompt = _build_continuation_prompt(ticket, runtime, state.continuation_turn + 1, max_turns)
                                    await _auto_start_run(ws, state, cont_prompt, supervisor_prompt)
                                    _update_phase(state, "running", runtime, ticket, project, task)

                            elif action["type"] == "retry":
                                if state.retry_attempt >= max_retries:
                                    _update_phase(state, "error", runtime, ticket, project, task)
                                    state.should_stop = True
                                    state.stop_reason = f"max_retries ({max_retries})"
                                else:
                                    state.retry_attempt += 1
                                    delay = min(AUTO_RETRY_BASE_DELAY * (2 ** (state.retry_attempt - 1)), AUTO_RETRY_MAX_DELAY)
                                    _update_phase(state, "retrying", runtime, ticket, project, task)
                                    log.info("Retrying in %.0fs (attempt %d/%d)", delay, state.retry_attempt, max_retries)
                                    await asyncio.sleep(delay)
                                    retry_prompt = "The previous run failed. Please review the current state and continue working on this ticket."
                                    await _auto_start_run(ws, state, retry_prompt, supervisor_prompt)
                                    _update_phase(state, "running", runtime, ticket, project, task)

                        elif payload.get("method") == "message_output":
                            state.last_activity = time.monotonic()

                    # --- TICKET.yaml polling ---
                    if now - last_ticket_poll >= AUTO_TICKET_POLL_INTERVAL:
                        last_ticket_poll = now
                        try:
                            sync = runtime.sync_ticket_file(ticket.id, project.id)
                            if sync.get("escalation"):
                                log.info("Escalation detected: %s", sync["escalation"])
                                _update_phase(state, "awaiting_input", runtime, ticket, project, task)
                                state.should_stop = True
                                state.stop_reason = f"escalation: {sync['escalation']}"
                            if sync.get("column_id"):
                                ticket = runtime.resolve_ticket(ticket.id, project.id) or ticket
                                if _is_terminal_column(runtime, project.id, ticket.column_id):
                                    _update_phase(state, "completed", runtime, ticket, project, task)
                                    state.should_stop = True
                                    state.stop_reason = "terminal_column"
                        except Exception:
                            pass  # ticket file may not exist yet

                    # --- Stall detection ---
                    if now - last_stall_check >= AUTO_STALL_CHECK_INTERVAL and state.run_id:
                        last_stall_check = now
                        if now - state.last_activity > stall_timeout:
                            log.warning("Stall detected (%.0fs since last activity)", now - state.last_activity)
                            # Treat as error → retry
                            action = _decide_run_end_action("stalled", state.continuation_turn, max_turns)
                            if action["type"] == "retry" and state.retry_attempt < max_retries:
                                state.retry_attempt += 1
                                delay = min(AUTO_RETRY_BASE_DELAY * (2 ** (state.retry_attempt - 1)), AUTO_RETRY_MAX_DELAY)
                                _update_phase(state, "retrying", runtime, ticket, project, task)
                                # Stop current run first
                                try:
                                    await _ws_send_rpc(ws, "stop_run", {"run_id": state.run_id})
                                except Exception:
                                    pass
                                state.run_id = ""
                                await asyncio.sleep(delay)
                                retry_prompt = "The previous run stalled. Please review the current state and continue working on this ticket."
                                await _auto_start_run(ws, state, retry_prompt, supervisor_prompt)
                                _update_phase(state, "running", runtime, ticket, project, task)
                            else:
                                _update_phase(state, "error", runtime, ticket, project, task)
                                state.should_stop = True
                                state.stop_reason = "stall_max_retries"

        except (OSError, websockets.exceptions.WebSocketException) as exc:
            if state.should_stop:
                break
            ws_reconnect_attempt += 1
            delay = min(AUTO_WS_RECONNECT_BASE * (2 ** (ws_reconnect_attempt - 1)), AUTO_WS_RECONNECT_MAX)
            log.warning("WebSocket error (%s), reconnecting in %.0fs", exc, delay)
            await asyncio.sleep(delay)

    # --- Cleanup ---
    final_phase = state.phase
    runtime.upsert_task(
        project_id=project.id,
        task_description=f"Work sandbox for: {ticket.title}",
        status={"type": final_phase, "timestamp": int(time.time() * 1000)},
        ticket_id=ticket.id,
        session_id=state.session_id,
        run_id="",
        last_urls=(task.last_urls if task else None),
        task_id=(task.id if task else None),
    )
    runtime.run_hook(project.id, "after_run", workspace)

    return {
        "phase": final_phase,
        "stop_reason": state.stop_reason,
        "continuation_turns": state.continuation_turn,
        "retry_attempts": state.retry_attempt,
    }


def supervisor_auto(
    ticket_ref: str,
    prompt: str = "Begin working on this ticket.",
    project_ref: str | None = None,
    stop_event: threading.Event | None = None,
) -> dict:
    """Run a supervisor in auto mode — blocking until complete or stopped."""
    return asyncio.run(_supervisor_auto_async(ticket_ref, prompt, project_ref, stop_event))


# Global registry of auto-mode threads keyed by ticket_id.
_auto_threads: dict[str, tuple[threading.Thread, threading.Event]] = {}


def supervisor_auto_start(ticket_ref: str, prompt: str = "Begin working on this ticket.", project_ref: str | None = None) -> dict:
    """Start auto mode in a background thread. Returns immediately."""
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")

    existing = _auto_threads.get(ticket.id)
    if existing and existing[0].is_alive():
        raise RuntimeError("Auto mode is already running for this ticket")

    stop_event = threading.Event()

    def target():
        try:
            supervisor_auto(ticket.id, prompt, project_ref, stop_event)
        except Exception as exc:
            log.error("supervisor_auto crashed: %s", exc)
        finally:
            _auto_threads.pop(ticket.id, None)

    thread = threading.Thread(target=target, daemon=True, name=f"auto-{ticket.id[:8]}")
    _auto_threads[ticket.id] = (thread, stop_event)
    thread.start()
    return {"ticket_id": ticket.id, "started": True}


def supervisor_auto_stop(ticket_ref: str, project_ref: str | None = None) -> dict:
    """Signal a running auto-mode thread to stop."""
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")
    existing = _auto_threads.get(ticket.id)
    if not existing or not existing[0].is_alive():
        # Fall back to stopping the current run directly
        try:
            supervisor_stop(ticket_ref, project_ref)
        except RuntimeError:
            pass
        return {"ticket_id": ticket.id, "stopped": True, "was_auto": False}
    existing[1].set()  # signal stop
    # Also stop the current sandbox run so the agent halts immediately
    try:
        supervisor_stop(ticket_ref, project_ref)
    except RuntimeError:
        pass
    return {"ticket_id": ticket.id, "stopped": True, "was_auto": True}


def is_auto_running(ticket_id: str) -> bool:
    """Check if auto mode is running for a ticket."""
    existing = _auto_threads.get(ticket_id)
    return bool(existing and existing[0].is_alive())


async def _supervisor_log_async(ws_url: str, stop_event: threading.Event | None = None) -> None:
    """Stream message_output notifications from the sandbox WebSocket to stdout."""
    async with websockets.connect(ws_url, max_size=None) as ws:
        while True:
            if stop_event and stop_event.is_set():
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                break
            payload = json.loads(raw)
            if payload.get("id"):
                continue  # skip RPC responses
            if payload.get("method") == "message_output":
                params = payload.get("params") or {}
                content = params.get("content")
                role = params.get("role", "assistant")
                tool_name = params.get("tool_name")
                if content is not None:
                    if tool_name:
                        print(f"\033[36m[tool: {tool_name}]\033[0m {content}", flush=True)
                    elif role == "user":
                        print(f"\033[33m[user]\033[0m {content}", flush=True)
                    else:
                        print(content, end="", flush=True)
            elif payload.get("method") == "run_end":
                reason = str((payload.get("params") or {}).get("end_reason") or "completed")
                print(f"\n\033[90m--- run ended: {reason} ---\033[0m", flush=True)


def supervisor_log(ticket_ref: str, project_ref: str | None = None) -> None:
    """Stream the live supervisor output to stdout. Blocks until Ctrl+C or disconnect."""
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(ticket_ref, project_ref)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_ref}")
    project = runtime.resolve_project(ticket.project_id)
    if project is None:
        raise RuntimeError(f"Project not found for ticket: {ticket_ref}")
    workspace = _resolve_ticket_workspace(ticket_ref, project_ref)
    container_name = _ensure_started(workspace, rebuild=False)
    details = _inspect_container(container_name)
    ws_url = _work_urls(details).get("ws_url")
    if not ws_url:
        raise RuntimeError("No sandbox WebSocket URL is available")
    try:
        asyncio.run(_supervisor_log_async(ws_url))
    except KeyboardInterrupt:
        pass


def _ensure_started(workspace: Path, *, rebuild: bool = False) -> str:
    container_name = _container_name_for_workspace(workspace)
    existing = _inspect_container(container_name)
    if existing and bool((existing.get("State") or {}).get("Running")):
        return container_name

    env_file = sandbox_cli._resolve_env_file(None, workspace)
    sandbox_cli._apply_env_file_to_process(env_file)
    host_port = sandbox_cli._pick_free_port()

    sandbox_cli._run_sandbox(
        mode="server",
        workspace=workspace,
        host_port=host_port,
        enable_code_server=True,
        code_server_port=sandbox_cli._pick_free_port(),
        enable_vnc=True,
        vnc_port=sandbox_cli._pick_free_port(),
        env_file=env_file,
        auth_token=None,
        rebuild=rebuild,
        no_cache=False,
        detach=True,
        container_name=container_name,
        print_url=False,
        persist_volumes=list(WORK_PERSIST_VOLUMES),
        dockerfile=_work_dockerfile_path(),
        output="human",
        network_allowlist=_network_allowlist_from_config(),
    )
    return container_name


def _exec_shell(container_name: str, shell: str) -> int:
    return subprocess.run(["docker", "exec", "-it", container_name, shell], check=False).returncode


def cmd_start(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args.project)
    container_name = _ensure_started(workspace, rebuild=bool(args.rebuild))
    details = _inspect_container(container_name)
    urls = _work_urls(details)
    print(f"Started work sandbox for {workspace}")
    print(f"Container: {container_name}")
    if urls["code_server_url"]:
        print(f"Code: {urls['code_server_url']}")
    if urls["desktop_url"]:
        print(f"Desktop: {urls['desktop_url']}")


def cmd_ssh(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args.project)
    container_name = _ensure_started(workspace, rebuild=bool(args.rebuild))
    exit_code = _exec_shell(container_name, args.shell)
    raise SystemExit(exit_code)


def cmd_stop(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args.project)
    container_name = _container_name_for_workspace(workspace)
    sandbox_cli._stop_container(container_name)
    print(f"Stopped work sandbox for {workspace}")


def cmd_status(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args.project)
    container_name = _container_name_for_workspace(workspace)
    details = _inspect_container(container_name)
    running = bool(details and bool((details.get("State") or {}).get("Running")))
    urls = _work_urls(details)
    payload = {
        "workspace": str(workspace),
        "container_name": container_name,
        "exists": details is not None,
        "running": running,
        "status": ((details.get("State") or {}).get("Status") if details else None),
        **urls,
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
        return
    state = payload["status"] or ("running" if running else "not_found")
    print(f"Workspace: {payload['workspace']}")
    print(f"Container: {payload['container_name']}")
    print(f"Status: {state}")
    if payload["sandbox_url"]:
        print(f"Sandbox: {payload['sandbox_url']}")
    if payload["code_server_url"]:
        print(f"Code: {payload['code_server_url']}")
    if payload["desktop_url"]:
        print(f"Desktop: {payload['desktop_url']}")


def cmd_open(args: argparse.Namespace) -> None:
    import webbrowser

    workspace = _resolve_workspace(args.project)
    container_name = _container_name_for_workspace(workspace)
    details = _inspect_container(container_name)
    if not details or not bool((details.get("State") or {}).get("Running")):
        if args.ensure:
            container_name = _ensure_started(workspace, rebuild=False)
            details = _inspect_container(container_name)
        else:
            raise RuntimeError("Work sandbox is not running")
    urls = _work_urls(details)
    target = args.target
    if target == "code":
        url = urls["code_server_url"]
    elif target == "desktop":
        url = urls["desktop_url"]
    else:
        url = urls["sandbox_url"]
    if not url:
        raise RuntimeError(f"No URL available for target: {target}")
    if args.print_url:
        print(url)
        return
    opened = webbrowser.open(url)
    if not opened:
        print(url)


def cmd_artifacts(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(args.ticket, args.project) if args.ticket else None
    if ticket is None:
        raise RuntimeError("Artifacts require --ticket")
    root = _ticket_artifacts_dir(ticket.id)
    target = root / args.dir if args.dir else root
    if not target.exists() or not target.is_dir():
        if args.json:
            print("[]")
            return
        print("No artifacts.")
        return
    entries = []
    for path in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        entries.append(
            {
                "name": path.name,
                "relative_path": str(path.relative_to(root)),
                "is_directory": path.is_dir(),
                "size": path.stat().st_size,
            }
        )
    if args.json:
        print(json.dumps(entries, sort_keys=True))
        return
    for entry in entries:
        prefix = "/" if entry["is_directory"] else "-"
        print(f"{prefix} {entry['relative_path']}")


def cmd_artifact_read(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(args.ticket, args.project) if args.ticket else None
    if ticket is None:
        raise RuntimeError("Artifact read requires --ticket")
    root = _ticket_artifacts_dir(ticket.id)
    target = (root / args.path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise RuntimeError("Path traversal detected")
    if not target.exists() or not target.is_file():
        raise RuntimeError(f"Artifact not found: {args.path}")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("Artifact is not UTF-8 text")
    print(content)


def cmd_diff(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args.project)
    result = subprocess.run(
        ["git", "-C", str(workspace), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = [{"status": line[:2].strip(), "path": line[3:]} for line in lines]
    if args.json:
        print(json.dumps(payload, sort_keys=True))
        return
    if not payload:
        print("No changes.")
        return
    for item in payload:
        print(f"{item['status'] or '??'} {item['path']}")


def cmd_history(args: argparse.Namespace) -> None:
    session_id = _resolve_session_id(args.ticket, args.project)
    messages = _format_history_messages(oa_load_history(session_id, project_slug="omni_code", agent_slug="omni"))
    if args.limit is not None:
        messages = messages[-args.limit :]
    if args.json:
        print(json.dumps(messages, sort_keys=True))
        return
    if not messages:
        print("No history.")
        return
    for message in messages:
        print(f"[{message['index']}] {message['role']}: {message['content']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omni work", description="Manage work sandboxes")
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="Start or reuse a work sandbox")
    start.add_argument("--project", default=None, help="Workspace path or registered project label")
    start.add_argument("--rebuild", action="store_true", help="Rebuild the work sandbox image before starting")
    start.set_defaults(func=cmd_start)

    ssh = subparsers.add_parser("ssh", help="Open a shell in the work sandbox")
    ssh.add_argument("--project", default=None, help="Workspace path or registered project label")
    ssh.add_argument("--rebuild", action="store_true", help="Rebuild the work sandbox image before starting")
    ssh.add_argument("--shell", default=os.getenv("SHELL") or "/bin/bash", help="Shell to execute inside the container")
    ssh.set_defaults(func=cmd_ssh)

    stop = subparsers.add_parser("stop", help="Stop a work sandbox")
    stop.add_argument("--project", default=None, help="Workspace path or registered project label")
    stop.set_defaults(func=cmd_stop)

    status = subparsers.add_parser("status", help="Show work sandbox status")
    status.add_argument("--project", default=None, help="Workspace path or registered project label")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    status.set_defaults(func=cmd_status)

    open_cmd = subparsers.add_parser("open", help="Open work sandbox URLs")
    open_cmd.add_argument("target", choices=["sandbox", "code", "desktop"], nargs="?", default="code")
    open_cmd.add_argument("--project", default=None, help="Workspace path or registered project label")
    open_cmd.add_argument("--ensure", action="store_true", help="Start the sandbox first if it is not already running")
    open_cmd.add_argument("--print-url", action="store_true", help="Print the URL instead of opening it")
    open_cmd.set_defaults(func=cmd_open)

    diff = subparsers.add_parser("diff", help="Show git status for the work workspace")
    diff.add_argument("--project", default=None, help="Workspace path or registered project label")
    diff.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    diff.set_defaults(func=cmd_diff)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    try:
        func(args)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)
