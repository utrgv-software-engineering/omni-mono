from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata


_WORKER_AGENT_YAML = Path(__file__).resolve().parent.parent / "omni_agents" / "worker" / "agent.yml"

# ---------------------------------------------------------------------------
# Worker registry
# ---------------------------------------------------------------------------


@dataclass
class _WorkerEntry:
    agent: object  # LocalAgent
    run_id: str
    session_id: str
    done_event: asyncio.Event
    monitor_task: asyncio.Task
    status: str = "running"
    result: Optional[str] = None
    error: Optional[str] = None


_WORKERS: dict[str, _WorkerEntry] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_and_configure_spec(model: Optional[str]):
    from omniagents.core.config.loader import load_agent_spec_from_yaml
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime(model) if model else resolve_model_for_runtime()

    spec = load_agent_spec_from_yaml(str(_WORKER_AGENT_YAML))

    # Inherit project context so worker sessions/traces land under omni_code
    spec.project_slug = "omni_code"

    if not getattr(spec, "tracing_config", None):
        project_yml = Path(__file__).resolve().parent.parent / "project.yml"
        if project_yml.exists():
            try:
                import yaml

                with open(project_yml) as f:
                    project_raw = yaml.safe_load(f) or {}
                tracing_raw = project_raw.get("tracing")
                if isinstance(tracing_raw, dict):
                    spec.tracing_config = tracing_raw
            except Exception:
                pass

    resolved_model = model_runtime.get("model") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    spec.resolve_default_model_config = lambda: model_runtime if isinstance(model_runtime, dict) else None

    return spec, model_runtime


async def _monitor_worker(entry: _WorkerEntry) -> None:
    agent = entry.agent
    try:
        await agent._ensure_client()
    except Exception:
        entry.status = "error"
        entry.error = "Failed to connect to worker agent."
        entry.done_event.set()
        return

    while True:
        try:
            msg = await agent._event_queue.get()
        except Exception:
            entry.status = "error"
            entry.error = "Event queue closed unexpectedly."
            entry.done_event.set()
            return

        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "client_request":
            # Auto-approve tool calls — workers run autonomously
            request_id = params.get("request_id")
            if request_id:
                try:
                    await agent._client.other.client_response(
                        request_id=request_id, ok=True, result={"approved": True}
                    )
                except Exception:
                    pass
            continue

        if method == "message_output":
            entry.result = params.get("content", "")

        elif method == "run_end":
            if params.get("run_id") != entry.run_id:
                continue
            error_info = params.get("error")
            end_reason = params.get("end_reason", "completed")
            if error_info:
                entry.status = "error"
                msg_text = error_info.get("message", "") if isinstance(error_info, dict) else str(error_info)
                entry.error = msg_text
                if not entry.result:
                    entry.result = f"[error] {msg_text}"
            elif end_reason == "cancelled":
                entry.status = "cancelled"
                if not entry.result:
                    entry.result = "[cancelled]"
            else:
                entry.status = "completed"
                if not entry.result:
                    entry.result = "Worker completed but produced no output."
            entry.done_event.set()
            return


async def _create_and_start_worker(
    task_description: str, model: Optional[str], session_id: Optional[str] = None
) -> _WorkerEntry:
    from omniagents.rpc.agents.local import LocalAgent

    spec, _ = _load_and_configure_spec(model)

    agent = LocalAgent(spec=spec, settings={})
    await agent.initialize()
    await agent._ensure_client()

    resp = await agent._client.other.start_run(
        prompt=task_description,
        session_id=session_id,
    )

    raw = getattr(resp, "result", resp)
    if isinstance(raw, dict):
        run_id = raw.get("run_id", "")
        sess_id = raw.get("session_id", "")
    else:
        run_id = ""
        sess_id = ""

    done_event = asyncio.Event()
    entry = _WorkerEntry(
        agent=agent,
        run_id=run_id,
        session_id=sess_id,
        done_event=done_event,
        monitor_task=None,  # set below
    )
    entry.monitor_task = asyncio.create_task(
        _monitor_worker(entry), name=f"worker-monitor-{run_id}"
    )
    return entry


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


@rich_function_tool(client_status="Spawning worker...")
async def spawn_worker(task: str, model: Optional[str] = None) -> RichToolOutput:
    """Spawn an autonomous worker agent to execute a coding subtask in the background.

    Workers have full read/write access to the workspace (list_directory, glob_files,
    grep_files, read_file, apply_patch, write_file, execute_bash, etc.) and run
    concurrently with the orchestrator and other workers.

    Use this to parallelise independent subtasks — for example, implementing
    separate files, running independent refactors, or writing tests while another
    worker builds the feature.

    Returns a short worker ID you can pass to wait_for_workers, send_worker_message,
    close_worker, or resume_worker.

    Args:
        task: A precise, self-contained description of the subtask. Include all
              context the worker needs (file paths, function names, requirements)
              because the worker has no access to conversation history.
        model: Optional model override. Defaults to the session's current model.
    """
    worker_id = uuid.uuid4().hex[:8]
    entry = await _create_and_start_worker(task, model)
    _WORKERS[worker_id] = entry
    llm_output = json.dumps({"worker_id": worker_id, "status": "spawned"})
    task_summary = task[:80] + "..." if len(task) > 80 else task
    return RichToolOutput(llm_output, {
        "display_type": "command",
        "summary": f"Worker {worker_id} spawned",
        "preview": f"Task: {task_summary}",
        "truncated": False,
        "metadata": {
            "success": True,
            "worker_id": worker_id,
            "status": "spawned",
        },
    })


@rich_function_tool(client_status="Waiting for workers...")
async def wait_for_workers(
    worker_ids: list[str],
    timeout_seconds: Optional[float] = None,
) -> RichToolOutput:
    """Wait for one or more workers to finish and return their results.

    If any listed workers are already done, their results are returned
    immediately. Otherwise blocks until at least one running worker completes
    (or the timeout expires).

    Args:
        worker_ids: List of worker IDs returned by spawn_worker.
        timeout_seconds: Max seconds to wait. None means wait indefinitely.
    """
    completed: dict[str, str | None] = {}
    still_running: list[str] = []
    not_found: list[str] = []
    pending_events: dict[str, asyncio.Event] = {}

    for wid in worker_ids:
        entry = _WORKERS.get(wid)
        if entry is None:
            not_found.append(wid)
        elif entry.status != "running":
            completed[wid] = entry.result if entry.status == "completed" else f"[{entry.status}] {entry.error}"
        else:
            pending_events[wid] = entry.done_event

    if completed or not pending_events:
        for wid in pending_events:
            still_running.append(wid)
    else:
        waiters = {
            wid: asyncio.create_task(event.wait(), name=f"wait-{wid}")
            for wid, event in pending_events.items()
        }

        done, _ = await asyncio.wait(
            waiters.values(),
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )

        done_names = {t.get_name() for t in done}
        for wid, t in waiters.items():
            if t.get_name() in done_names:
                entry = _WORKERS[wid]
                completed[wid] = entry.result if entry.status == "completed" else f"[{entry.status}] {entry.error}"
            else:
                t.cancel()
                still_running.append(wid)

    result_data = {
        "completed": completed,
        "still_running": still_running,
        "not_found": not_found,
    }
    llm_output = json.dumps(result_data)

    # Build a concise preview for the UI
    preview_lines = []
    for wid, res in completed.items():
        res_str = res or ""
        first_lines = res_str[:200] + ("..." if len(res_str) > 200 else "")
        preview_lines.append(f"[{wid}] {first_lines}")
    if still_running:
        preview_lines.append(f"Still running: {', '.join(still_running)}")
    if not_found:
        preview_lines.append(f"Not found: {', '.join(not_found)}")
    preview = "\n".join(preview_lines)

    parts = []
    if completed:
        parts.append(f"{len(completed)} completed")
    if still_running:
        parts.append(f"{len(still_running)} running")
    if not_found:
        parts.append(f"{len(not_found)} not found")
    summary = ", ".join(parts) if parts else "No workers matched"

    return RichToolOutput(llm_output, {
        "display_type": "search_results",
        "summary": f"Workers: {summary}",
        "preview": preview,
        "truncated": any(len(r or "") > 200 for r in completed.values()),
        "metadata": {
            "success": bool(completed),
            "completed_count": len(completed),
            "still_running_count": len(still_running),
            "not_found_count": len(not_found),
        },
    })


@rich_function_tool(client_status="Checking worker status...")
async def get_worker_status(
    worker_id: str,
    tail: int = 5,
) -> RichToolOutput:
    """Check a worker's current status and optionally retrieve recent history.

    Non-blocking — returns immediately with whatever state the worker is in.

    Args:
        worker_id: The worker ID returned by spawn_worker.
        tail: Number of recent history items to include (default: 5).
              Each item is a message dict (role, content, tool calls, etc.).
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} not found",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id},
        })

    result_data: dict = {
        "worker_id": worker_id,
        "status": entry.status,
    }
    if entry.error is not None:
        result_data["error"] = entry.error

    # Fetch recent history
    history_items: list[dict] = []
    n = tail
    if n > 0 and entry.session_id:
        try:
            from omniagents.core.session.history_db import load_history

            spec = getattr(entry.agent, "spec", None)
            project = getattr(spec, "project_slug", None) or "omni_code"
            agent = getattr(spec, "agent_slug", None) or getattr(spec, "name", None) or "worker"
            all_history = [
                item for item in load_history(
                    entry.session_id,
                    project_slug=project,
                    agent_slug=agent,
                )
                if item.get("type") != "reasoning"
            ]
            history_items = all_history[-n:] if len(all_history) > n else all_history
        except Exception:
            pass

    if history_items:
        result_data["history"] = history_items

    llm_output = json.dumps(result_data)

    # Build preview
    preview_lines = [f"Status: {entry.status}"]
    if history_items:
        preview_lines.append(f"History: {len(history_items)} items")
    preview = "\n".join(preview_lines)

    return RichToolOutput(llm_output, {
        "display_type": "search_results",
        "summary": f"Worker {worker_id}: {entry.status}",
        "preview": preview,
        "truncated": False,
        "metadata": {
            "success": True,
            "worker_id": worker_id,
            "status": entry.status,
            "history_count": len(history_items),
        },
    })


@rich_function_tool(client_status="Sending message to worker...")
async def send_worker_message(worker_id: str, message: str) -> RichToolOutput:
    """Send a message to a running worker, injecting new instructions mid-task.

    Use this to course-correct a worker (e.g. "stop working on X and focus on Y")
    or to supply additional context it needs. The message is appended to the
    worker's conversation as a user turn; the worker will see it on its next
    reasoning step.

    Args:
        worker_id: The worker ID returned by spawn_worker.
        message: The message to send to the worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} not found",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id},
        })

    if entry.status != "running":
        llm_output = json.dumps({"worker_id": worker_id, "status": entry.status, "error": "Worker is not running."})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} is {entry.status}",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id, "status": entry.status},
        })

    agent = entry.agent
    await agent._ensure_client()
    await agent._client.other.send_user_message(run_id=entry.run_id, content=message)
    llm_output = json.dumps({"worker_id": worker_id, "status": "message_sent"})
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(llm_output, {
        "display_type": "command",
        "summary": f"Message sent to worker {worker_id}",
        "preview": f"Message: {msg_summary}",
        "truncated": False,
        "metadata": {"success": True, "worker_id": worker_id, "status": "message_sent"},
    })


@rich_function_tool(client_status="Closing worker...")
async def close_worker(worker_id: str) -> RichToolOutput:
    """Cancel a running worker and clean up its resources.

    Args:
        worker_id: The worker ID returned by spawn_worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} not found",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id},
        })

    if entry.status != "running":
        llm_output = json.dumps({"worker_id": worker_id, "status": entry.status})
        return RichToolOutput(llm_output, {
            "display_type": "command",
            "summary": f"Worker {worker_id} already {entry.status}",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id, "status": entry.status},
        })

    agent = entry.agent
    await agent._ensure_client()
    await agent._client.other.stop_run(run_id=entry.run_id)

    try:
        await asyncio.wait_for(entry.done_event.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    if entry.status == "running":
        entry.status = "cancelled"

    entry.monitor_task.cancel()
    await agent.cleanup()

    llm_output = json.dumps({"worker_id": worker_id, "status": entry.status})
    return RichToolOutput(llm_output, {
        "display_type": "command",
        "summary": f"Worker {worker_id} {entry.status}",
        "preview": llm_output,
        "truncated": False,
        "metadata": {"success": True, "worker_id": worker_id, "status": entry.status},
    })


@rich_function_tool(client_status="Resuming worker...")
async def resume_worker(worker_id: str, message: str) -> RichToolOutput:
    """Resume a previously closed or completed worker with a new message.

    Restores the worker's full conversation history and starts a new run.
    Use this to iterate on a worker's output — for example, asking it to fix
    issues you found in its previous work.

    Args:
        worker_id: The worker ID of the closed/completed worker.
        message: The follow-up instruction for the worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} not found",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id},
        })

    if entry.status == "running":
        llm_output = json.dumps({"worker_id": worker_id, "status": "error", "error": "Worker is still running. Use send_worker_message instead."})
        return RichToolOutput(llm_output, {
            "display_type": "error",
            "summary": f"Worker {worker_id} still running",
            "preview": "Use send_worker_message instead.",
            "truncated": False,
            "metadata": {"success": False, "worker_id": worker_id, "status": "running"},
        })

    old_session_id = entry.session_id
    old_agent = entry.agent

    await old_agent.cleanup()

    new_entry = await _create_and_start_worker(
        message, model=None, session_id=old_session_id,
    )
    new_entry.result = None
    _WORKERS[worker_id] = new_entry

    llm_output = json.dumps({"worker_id": worker_id, "status": "resumed", "session_id": new_entry.session_id})
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(llm_output, {
        "display_type": "command",
        "summary": f"Worker {worker_id} resumed",
        "preview": f"Task: {msg_summary}",
        "truncated": False,
        "metadata": {"success": True, "worker_id": worker_id, "status": "resumed"},
    })


# ---------------------------------------------------------------------------
# Approval metadata
# ---------------------------------------------------------------------------


@approval_metadata()
def spawn_worker_approval(task: str, model: Optional[str] = None) -> dict:
    """Generate approval metadata for spawn_worker showing the full task description."""
    lines = task.splitlines()
    preview = task

    return {
        "display_type": "file_write",
        "summary": "Spawn worker agent",
        "value": task,
        "preview": preview,
        "truncated": False,
        "metadata": {
            "operation": "spawn_worker",
            "line_count": len(lines),
            "model": model,
        },
    }
