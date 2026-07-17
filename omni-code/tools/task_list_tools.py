"""In-session task list tools.

A lightweight todo list scoped per-agent. State lives in module-level
dicts keyed by ``task_scope`` (a string lifted from the run context).
The supervisor uses scope ``"main"``; ``spawn_worker`` injects each
worker's id as its scope, so workers get their own private list and
don't pollute the supervisor's UI.

Mirrors the Claude Code task list semantics: tasks have a status that
walks ``pending → in_progress → completed``, optional dependency edges,
and an optional ``activeForm`` used by the UI spinner while in progress.
"""

from __future__ import annotations

import json
from typing import Any, Optional  # noqa: F401

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput


# ---------------------------------------------------------------------------
# Module-level state — keyed by scope
# ---------------------------------------------------------------------------


_TASKS_BY_SCOPE: dict[str, dict[str, dict[str, Any]]] = {}
_NEXT_ID_BY_SCOPE: dict[str, int] = {}

_DEFAULT_SCOPE = "main"
_VALID_STATUSES = ("pending", "in_progress", "completed")


def _scope_from_ctx(ctx: RunContextWrapper[Any] | None) -> str:
    if ctx is None:
        return _DEFAULT_SCOPE
    c = getattr(ctx, "context", None)
    if isinstance(c, dict):
        s = c.get("task_scope")
    else:
        s = getattr(c, "task_scope", None) if c is not None else None
    if isinstance(s, str) and s.strip():
        return s
    return _DEFAULT_SCOPE


def _store(scope: str) -> dict[str, dict[str, Any]]:
    return _TASKS_BY_SCOPE.setdefault(scope, {})


def _new_id(scope: str) -> str:
    n = _NEXT_ID_BY_SCOPE.get(scope, 1)
    _NEXT_ID_BY_SCOPE[scope] = n + 1
    return str(n)


def _summary(task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compact task view used by task_list — drops description for cheap rendering."""
    blocked_by_open = [
        bid
        for bid in task.get("blockedBy", [])
        if bid in tasks and tasks[bid]["status"] != "completed"
    ]
    return {
        "id": task["id"],
        "subject": task["subject"],
        "activeForm": task.get("activeForm") or task["subject"],
        "status": task["status"],
        "owner": task.get("owner", ""),
        "blockedBy": blocked_by_open,
    }


def _status_emoji(status: str) -> str:
    return {"pending": "·", "in_progress": "▶", "completed": "✓"}.get(status, "?")


def _snapshot(scope: str) -> list[dict[str, Any]]:
    """Compact, ordered snapshot of all tasks in a scope. Rides on every
    task tool's ``ui_metadata`` so the TUI can mirror state via the
    existing ``tool_result`` event channel — no extra plumbing."""
    store = _TASKS_BY_SCOPE.get(scope) or {}
    return [_summary(store[tid], store) for tid in sorted(store, key=int)]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@rich_function_tool(running_summary="Creating task...")
async def task_create(
    ctx: RunContextWrapper[Any],
    subject: str,
    description: str,
    activeForm: Optional[str] = None,
) -> RichToolOutput:
    """Create a new task in your in-session task list.

    Use proactively for multi-step work (≥3 non-trivial steps). Skip for
    single trivial actions or purely conversational requests.

    Args:
        subject: Short imperative title, e.g. "Run pytest on the eval suite".
        description: What the task entails. Include enough context that
            anyone (including a subagent that picks it up) can execute
            without re-deriving the requirements.
        activeForm: Present-continuous form shown in the spinner while the
            task is in_progress, e.g. "Running pytest on the eval suite".
            Falls back to ``subject`` if omitted.
    """
    scope = _scope_from_ctx(ctx)
    tasks = _store(scope)
    tid = _new_id(scope)
    task = {
        "id": tid,
        "subject": subject,
        "description": description,
        "activeForm": activeForm or subject,
        "status": "pending",
        "owner": "",
        "blocks": [],
        "blockedBy": [],
    }
    tasks[tid] = task

    llm_output = json.dumps({"task": task})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{tid}: {subject[:60]}",
            "preview": f"#{tid} [pending] {subject}",
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(scope),
            "metadata": {
                "success": True,
                "task_id": tid,
                "status": "pending",
            },
        },
    )


@rich_function_tool(running_summary="Listing tasks...")
async def task_list(ctx: RunContextWrapper[Any]) -> RichToolOutput:
    """List your tasks with status and open blockers.

    Returns summaries only — call ``task_get`` for full description.
    """
    scope = _scope_from_ctx(ctx)
    store = _store(scope)
    tasks = [_summary(store[tid], store) for tid in sorted(store, key=int)]
    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1

    llm_output = json.dumps({"tasks": tasks, "counts": counts})

    if not tasks:
        preview = "(no tasks)"
    else:
        preview = "\n".join(
            f"{_status_emoji(t['status'])} #{t['id']} {t['subject'][:80]}"
            + (f"  [blocked by {','.join(t['blockedBy'])}]" if t["blockedBy"] else "")
            for t in tasks
        )

    summary_parts = [f"{n} {s}" for s, n in counts.items() if n]
    summary = ", ".join(summary_parts) if summary_parts else "no tasks"

    return RichToolOutput(
        llm_output,
        {
            "display_type": "search_results",
            "summary": f"Tasks: {summary}",
            "preview": preview,
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(scope),
            "metadata": {"success": True, **counts, "total": len(tasks)},
        },
    )


@rich_function_tool(running_summary="Reading task...")
async def task_get(ctx: RunContextWrapper[Any], task_id: str) -> RichToolOutput:
    """Get the full record for one of your tasks by id.

    Args:
        task_id: The task id returned by task_create.
    """
    scope = _scope_from_ctx(ctx)
    store = _store(scope)
    task = store.get(task_id)
    if task is None:
        llm_output = json.dumps({"task_id": task_id, "error": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Task #{task_id} not found",
                "preview": llm_output,
                "truncated": False,
                "hidden": True,
            "tasks_snapshot": _snapshot(scope),
                "metadata": {"success": False, "task_id": task_id},
            },
        )

    llm_output = json.dumps({"task": task})
    preview = (
        f"#{task['id']} [{task['status']}] {task['subject']}\n\n"
        f"{task['description']}"
    )
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{task_id}: {task['subject'][:60]}",
            "preview": preview[:1500],
            "truncated": len(preview) > 1500,
            "hidden": True,
            "tasks_snapshot": _snapshot(scope),
            "metadata": {
                "success": True,
                "task_id": task_id,
                "status": task["status"],
            },
        },
    )


@rich_function_tool(running_summary="Updating task...")
async def task_update(
    ctx: RunContextWrapper[Any],
    task_id: str,
    status: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    activeForm: Optional[str] = None,
    owner: Optional[str] = None,
    addBlocks: Optional[list[str]] = None,
    addBlockedBy: Optional[list[str]] = None,
) -> RichToolOutput:
    """Update a task. Only fields you pass are changed.

    Mark a task ``in_progress`` BEFORE starting work and ``completed``
    immediately after finishing — that's what drives the UI status. Only
    one task should be ``in_progress`` at a time. Don't mark completed
    if the work is partial, blocked, or failing — leave it in_progress
    and create a new task for the blocker instead.

    Args:
        task_id: The task to update.
        status: ``pending`` | ``in_progress`` | ``completed``, or
            ``deleted`` to permanently remove the task.
        subject: New title.
        description: New description.
        activeForm: New present-continuous form for the spinner.
        owner: Claim the task by setting an owner string (e.g. agent
            name).
        addBlocks: Append task ids that this task blocks. Their
            ``blockedBy`` lists are updated symmetrically.
        addBlockedBy: Append task ids that must complete before this
            one starts. Symmetric edge written into the dependency's
            ``blocks`` list.
    """
    scope = _scope_from_ctx(ctx)
    store = _store(scope)
    task = store.get(task_id)
    if task is None:
        llm_output = json.dumps({"task_id": task_id, "error": "not_found"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Task #{task_id} not found",
                "preview": llm_output,
                "truncated": False,
                "hidden": True,
            "tasks_snapshot": _snapshot(scope),
                "metadata": {"success": False, "task_id": task_id},
            },
        )

    if status is not None and status == "deleted":
        # Symmetrically detach from any neighbours within the same scope.
        for other_id in task.get("blockedBy", []):
            other = store.get(other_id)
            if other and task_id in other.get("blocks", []):
                other["blocks"].remove(task_id)
        for other_id in task.get("blocks", []):
            other = store.get(other_id)
            if other and task_id in other.get("blockedBy", []):
                other["blockedBy"].remove(task_id)
        del store[task_id]
        llm_output = json.dumps({"task_id": task_id, "status": "deleted"})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "command",
                "summary": f"Task #{task_id} deleted",
                "preview": llm_output,
                "truncated": False,
                "hidden": True,
            "tasks_snapshot": _snapshot(scope),
                "metadata": {"success": True, "task_id": task_id, "status": "deleted"},
            },
        )

    if status is not None and status not in _VALID_STATUSES:
        llm_output = json.dumps(
            {"task_id": task_id, "error": f"invalid status: {status}"}
        )
        return RichToolOutput(
            llm_output,
            {
                "display_type": "error",
                "summary": f"Invalid status: {status}",
                "preview": f"Valid: {', '.join(_VALID_STATUSES)} or 'deleted'",
                "truncated": False,
                "hidden": True,
            "tasks_snapshot": _snapshot(scope),
                "metadata": {"success": False, "task_id": task_id},
            },
        )

    if status is not None:
        task["status"] = status
    if subject is not None:
        task["subject"] = subject
    if description is not None:
        task["description"] = description
    if activeForm is not None:
        task["activeForm"] = activeForm
    if owner is not None:
        task["owner"] = owner

    if addBlocks:
        for other_id in addBlocks:
            if other_id == task_id or other_id not in store:
                continue
            if other_id not in task["blocks"]:
                task["blocks"].append(other_id)
            other = store[other_id]
            if task_id not in other["blockedBy"]:
                other["blockedBy"].append(task_id)

    if addBlockedBy:
        for other_id in addBlockedBy:
            if other_id == task_id or other_id not in store:
                continue
            if other_id not in task["blockedBy"]:
                task["blockedBy"].append(other_id)
            other = store[other_id]
            if task_id not in other["blocks"]:
                other["blocks"].append(task_id)

    llm_output = json.dumps({"task": task})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{task_id} → {task['status']}",
            "preview": f"#{task_id} [{task['status']}] {task['subject']}",
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(scope),
            "metadata": {
                "success": True,
                "task_id": task_id,
                "status": task["status"],
            },
        },
    )
