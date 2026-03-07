from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Optional

from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata
from omniagents.rpc.agents import WorkerManager


_WORKER_AGENT_YAML = (
    Path(__file__).resolve().parent.parent / "omni_agents" / "worker" / "agent.yml"
)

# ---------------------------------------------------------------------------
# Whimsical worker name generator
# ---------------------------------------------------------------------------

_NAMES = [
    "blix",
    "coda",
    "nyx",
    "zeph",
    "remo",
    "tavi",
    "wren",
    "juno",
    "kip",
    "arlo",
    "miko",
    "vex",
    "pip",
    "zuri",
    "echo",
    "bolt",
    "nova",
    "rue",
    "otto",
    "dex",
    "sage",
    "flux",
    "cleo",
    "hex",
    "finn",
    "lux",
    "gus",
    "ivy",
    "rox",
    "ziggy",
]

_ADJECTIVES = [
    "lil",
    "big",
    "old",
    "tiny",
    "mad",
    "wee",
]

_TITLES = [
    "the swift",
    "the steady",
    "the bold",
    "the keen",
]

_SUFFIXES = [
    "jr",
    "mk2",
    "prime",
    "redux",
    "lite",
]


def _generate_worker_name() -> str:
    """Generate a whimsical worker name.

    ~80% of the time returns a bare name (e.g. ``pip``).
    ~20% of the time adds flair via an adjective, title, or suffix.
    On collision with an existing worker, appends ``-2``, ``-3``, etc.
    """
    name = random.choice(_NAMES)

    # 20% chance of flair
    if random.random() < 0.2:
        style = random.choice(["adj", "title", "suffix"])
        if style == "adj":
            name = f"{random.choice(_ADJECTIVES)}-{name}"
        elif style == "title":
            title = random.choice(_TITLES).replace(" ", "-")
            name = f"{name}-{title}"
        else:
            name = f"{name}-{random.choice(_SUFFIXES)}"

    # Handle collisions
    if name not in _WORKERS:
        return name
    n = 2
    while f"{name}-{n}" in _WORKERS:
        n += 1
    return f"{name}-{n}"


# ---------------------------------------------------------------------------
# Worker registry
# ---------------------------------------------------------------------------


_WORKERS: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_and_configure_spec(model: Optional[str]):
    from omniagents.core.config.loader import load_agent_spec_from_yaml
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = (
        resolve_model_for_runtime(model) if model else resolve_model_for_runtime()
    )

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

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    spec.resolve_default_model_config = lambda: (
        model_runtime if isinstance(model_runtime, dict) else None
    )

    return spec, model_runtime


_MANAGER = WorkerManager(spec_loader=_load_and_configure_spec)
_WORKERS = _MANAGER.workers


async def _history_loader(entry, tail: int) -> list[dict]:
    from omniagents.core.session.history_db import load_history

    if tail <= 0 or not entry.session_id:
        return []
    spec = getattr(entry.agent, "spec", None)
    project = getattr(spec, "project_slug", None) or "omni_code"
    agent = getattr(spec, "agent_slug", None) or getattr(spec, "name", None) or "worker"
    all_history = [
        item
        for item in load_history(
            entry.session_id,
            project_slug=project,
            agent_slug=agent,
        )
        if item.get("type") != "reasoning"
    ]
    return all_history[-tail:] if len(all_history) > tail else all_history


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
    worker_id = _generate_worker_name()
    await _MANAGER.spawn(worker_id=worker_id, task_description=task, model=model)
    llm_output = json.dumps({"worker_id": worker_id, "status": "spawned"})
    task_summary = task[:80] + "..." if len(task) > 80 else task
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} spawned",
            "preview": f"Task: {task_summary}",
            "truncated": False,
            "metadata": {
                "success": True,
                "worker_id": worker_id,
                "status": "spawned",
            },
        },
    )


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
    result_data = await _MANAGER.wait(worker_ids, timeout_seconds)
    completed = result_data["completed"]
    still_running = result_data["still_running"]
    not_found = result_data["not_found"]
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

    return RichToolOutput(
        llm_output,
        {
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
        },
    )


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
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    result_data = await _MANAGER.get_status(
        worker_id, tail=tail, history_loader=_history_loader
    )
    history_items = result_data.get("history", [])

    if history_items:
        result_data["history"] = history_items

    llm_output = json.dumps(result_data)

    # Build preview
    preview_lines = [f"Status: {entry.status}"]
    if history_items:
        preview_lines.append(f"History: {len(history_items)} items")
    preview = "\n".join(preview_lines)

    return RichToolOutput(
        llm_output,
        {
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
        },
    )


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
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status != "running":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": entry.status,
                "error": "Worker is not running.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} is {entry.status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": entry.status,
                },
            },
        )

    send_status = await _MANAGER.send_message(worker_id, message)
    if send_status != "message_sent":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": send_status,
                "error": "Worker is not running.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} is {send_status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": send_status,
                },
            },
        )
    llm_output = json.dumps({"worker_id": worker_id, "status": "message_sent"})
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Message sent to worker {worker_id}",
            "preview": f"Message: {msg_summary}",
            "truncated": False,
            "metadata": {
                "success": True,
                "worker_id": worker_id,
                "status": "message_sent",
            },
        },
    )


@rich_function_tool(client_status="Closing worker...")
async def close_worker(worker_id: str) -> RichToolOutput:
    """Cancel a running worker and clean up its resources.

    Args:
        worker_id: The worker ID returned by spawn_worker.
    """
    entry = _WORKERS.get(worker_id)
    if entry is None:
        llm_output = json.dumps({"worker_id": worker_id, "status": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status != "running":
        llm_output = json.dumps({"worker_id": worker_id, "status": entry.status})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "command",
                "summary": f"Worker {worker_id} already {entry.status}",
                "preview": llm_output,
                "truncated": False,
                "metadata": {
                    "success": True,
                    "worker_id": worker_id,
                    "status": entry.status,
                },
            },
        )

    status = await _MANAGER.close(worker_id)

    llm_output = json.dumps({"worker_id": worker_id, "status": status})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} {status}",
            "preview": llm_output,
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id, "status": status},
        },
    )


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
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} not found",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    if entry.status == "running":
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": "error",
                "error": "Worker is still running. Use send_worker_message instead.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} still running",
                "preview": "Use send_worker_message instead.",
                "truncated": False,
                "metadata": {
                    "success": False,
                    "worker_id": worker_id,
                    "status": "running",
                },
            },
        )

    new_entry = await _MANAGER.resume(worker_id, message)
    if new_entry is None:
        llm_output = json.dumps(
            {
                "worker_id": worker_id,
                "status": "error",
                "error": "Failed to resume worker.",
            }
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Worker {worker_id} failed to resume",
                "preview": llm_output,
                "truncated": False,
                "metadata": {"success": False, "worker_id": worker_id},
            },
        )

    llm_output = json.dumps(
        {
            "worker_id": worker_id,
            "status": "resumed",
            "session_id": new_entry.session_id,
        }
    )
    msg_summary = message[:60] + "..." if len(message) > 60 else message
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Worker {worker_id} resumed",
            "preview": f"Task: {msg_summary}",
            "truncated": False,
            "metadata": {"success": True, "worker_id": worker_id, "status": "resumed"},
        },
    )


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
