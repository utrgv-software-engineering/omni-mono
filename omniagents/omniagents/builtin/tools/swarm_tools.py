"""Opt-in, session-owned swarm coordination over the existing worker runtime.

Records, membership and mailboxes persist in the owning conversation inbox.
Live locks and member tool closures are rebuilt from these records.
Member communication tools are bound to membership by the host, never by
model-supplied sender/session IDs. Only the owner gets lifecycle operations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agents.run_context import RunContextWrapper
from omniagents.builtin.tools import worker_tools as workers
from omniagents.core.tools import rich_function_tool, session_id_from_ctx


@dataclass
class Member:
    name: str
    role: str
    worker_id: str
    task: str = ""
    started: bool = False
    cleaned: bool = False


@dataclass
class Swarm:
    id: str
    owner: str
    objective: str
    members: dict[str, Member] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    result: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    service: Any = field(default=None, repr=False)
    revision: int = 0


_SWARMS: dict[str, Swarm] = {}
_log = logging.getLogger(__name__)
MAX_MEMBERS = 16
MAX_MESSAGES = 200


from contextlib import contextmanager


@contextmanager
def _parent_scope(swarm):
    from omniagents.core.agents.service import (
        bind_current_service,
        reset_current_service,
    )
    from omniagents.core.session.helpers import (
        set_current_session_id,
        reset_current_session_id,
    )

    service_token = bind_current_service(swarm.service)
    session_token = set_current_session_id(swarm.owner)
    try:
        yield
    finally:
        reset_current_session_id(session_token)
        reset_current_service(service_token)


def _journal(service, parent_id):
    if service is None or not callable(getattr(service, "_queue_store", None)):
        return None
    from omniagents.core.workers.durable import for_session

    parent = service.get_session(parent_id)
    return for_session(service, parent) if parent is not None else None


def restore_groups(service, parent_id):
    journal = _journal(service, parent_id)
    if journal is None:
        return
    rows = {row["worker_id"]: row for row in journal.rows()}
    for key, data in journal.groups().items():
        if data.get("kind") != "swarm":
            continue
        existing = _SWARMS.get(key)
        if existing is not None and existing.revision == data["revision"]:
            existing.service = service
            for member in existing.members.values():
                if member.worker_id in rows:
                    member.started = True
                    member.cleaned = bool(rows[member.worker_id].get("cleaned"))
            continue
        swarm = Swarm(
            key,
            parent_id,
            data["objective"],
            closed=data["closed"],
            result=data["result"],
            messages=data["messages"],
            service=service,
            revision=data["revision"],
        )
        swarm.members = {
            name: Member(**member) for name, member in data["members"].items()
        }
        for member in swarm.members.values():
            if member.worker_id in rows:
                member.started = True
                member.cleaned = bool(rows[member.worker_id].get("cleaned"))
        _SWARMS[key] = swarm


def _save(swarm):
    journal = _journal(swarm.service, swarm.owner)
    if journal is None:
        return
    from dataclasses import asdict

    swarm.revision = journal.save_group(
        swarm.id,
        {
            "kind": "swarm",
            "objective": swarm.objective,
            "closed": swarm.closed,
            "result": swarm.result,
            "messages": swarm.messages,
            "members": {name: asdict(member) for name, member in swarm.members.items()},
        },
        swarm.revision,
    )


def _owned(ctx: RunContextWrapper[Any], swarm_id: str) -> Swarm:
    from omniagents.core.agents.service import current_service

    sid = session_id_from_ctx(ctx)
    restore_groups(current_service(), sid)
    swarm = _SWARMS.get(swarm_id)
    if not sid or swarm is None or swarm.owner != sid:
        raise ValueError("Swarm not found in this session")
    return swarm


def _active(swarm: Swarm) -> None:
    if swarm.closed:
        raise ValueError("Swarm is closed")


def _member(swarm: Swarm, name: str) -> Member:
    if name not in swarm.members:
        raise ValueError(f"Unknown member: {name}")
    return swarm.members[name]


def _snapshot(swarm: Swarm) -> dict[str, Any]:
    members = []
    for member in swarm.members.values():
        state = workers.worker_state_dict(member.worker_id) if member.started else None
        members.append(
            {
                "name": member.name,
                "role": member.role,
                "task": member.task,
                "worker_id": member.worker_id,
                "state": state
                or {"status": "unavailable" if member.started else "planned"},
                "workspace_cleaned": member.cleaned,
                "workspace": workers._workspace_payload(member.worker_id),
            }
        )
    return {
        "swarm_id": swarm.id,
        "objective": swarm.objective,
        "status": "closed" if swarm.closed else "open",
        "result": swarm.result,
        "members": members,
        "messages": list(swarm.messages),
    }


def swarm_snapshots(parent_session_id: str | None) -> list[dict[str, Any]]:
    """Read-only UI snapshot, strictly scoped to the owning parent session."""
    if not parent_session_id:
        return []
    from omniagents.core.agents.service import current_service

    restore_groups(current_service(), parent_session_id)
    return [_snapshot(s) for s in _SWARMS.values() if s.owner == parent_session_id]


async def _publish(swarm: Swarm) -> None:
    _save(swarm)
    from omniagents.core.runtime.subagents import broadcast_subagents_snapshot

    await broadcast_subagents_snapshot(swarm.service, swarm.owner)


async def _message(
    swarm: Swarm, sender: str, recipient: str, message: str
) -> dict[str, Any]:
    _active(swarm)
    if recipient != "owner":
        target = _member(swarm, recipient)
    if not message.strip():
        raise ValueError("Message must not be empty")
    if len(swarm.messages) >= MAX_MESSAGES:
        raise ValueError("Swarm message budget exhausted; report results to the owner")
    row = {
        "id": len(swarm.messages) + 1,
        "from": sender,
        "to": recipient,
        "message": message,
        "delivery": "mailbox",
    }
    swarm.messages.append(row)
    _save(swarm)
    # Store before attempting delivery: idle/planned recipients can read their
    # mailbox on dispatch/resume. Sending never starts a new run by itself.
    if recipient != "owner" and target.started:
        try:
            row["delivery"] = await workers.manager().send_message(
                target.worker_id, f"[swarm {swarm.id}; from {sender}] {message}"
            )
        except Exception:  # noqa: BLE001 - any transport failure leaves mail available
            _log.exception(
                "Swarm message retained in mailbox after live delivery failed"
            )
            row["delivery"] = "mailbox"
    await _publish(swarm)
    return dict(row)


def _validate_worker_spec(spec: Any) -> None:
    forbidden = {"workers", "swarms"}
    if any(
        getattr(cap, "capability_name", None) in forbidden
        for cap in getattr(spec, "resolved_capabilities", [])
    ):
        raise ValueError(
            "Delegated workers require a worker spec without workers/swarms capabilities"
        )
    if any(
        getattr(tool, "name", None)
        in {"spawn_worker", "create_swarm", "dispatch_swarm_member", "dispatch_worker"}
        for tool in spec.available_tools
    ):
        raise ValueError("Delegated workers cannot have recruitment tools")


def _configure_member(swarm: Swarm, member: Member) -> Callable[[Any], None]:
    @rich_function_tool
    async def swarm_member_message(recipient: str, message: str) -> str:
        """Send a relevant finding or dependency to a teammate, or to 'owner'.

        Messages never automatically restart completed teammates. Avoid
        acknowledgments and social chatter; the swarm has a message budget.
        """
        with _parent_scope(swarm):
            return json.dumps(await _message(swarm, member.name, recipient, message))

    @rich_function_tool
    async def swarm_member_context() -> str:
        """Read the shared objective, member roles/status, and your mailbox."""
        with _parent_scope(swarm):
            snapshot = _snapshot(swarm)
        snapshot["messages"] = [
            m
            for m in swarm.messages
            if m["to"] == member.name or m["from"] == member.name
        ]
        return json.dumps(snapshot)

    def configure(spec: Any) -> None:
        _validate_worker_spec(spec)
        # Copy the list so a cached product spec's tool list is never mutated.
        spec.available_tools = list(spec.available_tools) + [
            swarm_member_message,
            swarm_member_context,
        ]

    return configure


@rich_function_tool
async def create_swarm(ctx: RunContextWrapper[Any], objective: str) -> str:
    """Create a task-scoped swarm owned by this session. Does not start workers."""
    sid = session_id_from_ctx(ctx)
    if not sid or not objective.strip():
        raise ValueError("A session and nonempty objective are required")
    if sum(s.owner == sid and not s.closed for s in _SWARMS.values()) >= 8:
        raise ValueError("Close an existing swarm before creating another")
    swarm = Swarm(f"swarm-{uuid4().hex}", sid, objective)
    from omniagents.core.agents.service import current_service

    swarm.service = current_service()
    _SWARMS[swarm.id] = swarm
    await _publish(swarm)
    return json.dumps(_snapshot(swarm))


@rich_function_tool
async def add_swarm_member(
    ctx: RunContextWrapper[Any], swarm_id: str, name: str, role: str
) -> str:
    """Plan a named member with role instructions. Dispatch separately after defining the team."""
    swarm = _owned(ctx, swarm_id)
    async with swarm.lock:
        _active(swarm)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) or name == "owner":
            raise ValueError(
                "Use a lowercase member name (letters, digits, _ or -); owner is reserved"
            )
        if (
            name in swarm.members
            or len(swarm.members) >= MAX_MEMBERS
            or not role.strip()
        ):
            raise ValueError("Duplicate member, empty role, or member limit reached")
        swarm.members[name] = Member(name, role, f"{swarm.id}-{name}")
        await _publish(swarm)
        return json.dumps(_snapshot(swarm))


@rich_function_tool
async def dispatch_swarm_member(
    ctx: RunContextWrapper[Any],
    swarm_id: str,
    name: str,
    task: str,
    isolation: str | None = None,
    allow_dirty: bool = False,
) -> str:
    """Start a planned member or resume a finished member with a concrete task.

    Include file ownership, inputs, boundaries, and acceptance criteria.
    Existing isolation is reused on resume. Review and integrate results
    before cleaning workspaces. Completions use normal worker notifications.
    """
    swarm = _owned(ctx, swarm_id)
    async with swarm.lock:
        _active(swarm)
        member = _member(swarm, name)
        if not task.strip() or member.cleaned:
            raise ValueError("Task is empty or member workspace was already cleaned")
        brief = (
            f"Swarm: {swarm.id}\nObjective: {swarm.objective}\n"
            f"Your name: {name}\nYour role: {member.role}\nAssignment: {task}\n"
            "Use swarm_member_context to see teammates and messages. "
            "Use swarm_member_message for relevant findings/dependencies. "
            "Do not recruit workers. Finish with your result for the owner."
        )
        member.task = task
        _save(swarm)
        if member.started:
            output = await getattr(workers.resume_worker, "_original_func")(
                ctx, member.worker_id, brief
            )
        else:
            output = await workers.spawn_worker_run(
                ctx,
                brief,
                isolation,
                allow_dirty,
                worker_id=member.worker_id,
                configure_spec=_configure_member(swarm, member),
                recipe={
                    "kind": "swarm",
                    "version": 1,
                    "swarm_id": swarm.id,
                    "member": member.name,
                },
            )
        data = json.loads(str(output))
        if data.get("status") in {"spawned", "resumed"}:
            member.started = True
            member.task = task
        await _publish(swarm)
        return json.dumps({"swarm_id": swarm_id, "member": name, **data})


@rich_function_tool
async def get_swarm(ctx: RunContextWrapper[Any], swarm_id: str | None = None) -> str:
    """Inspect owned swarms, assignments, worker results/workspaces, and message delivery."""
    if swarm_id is not None:
        return json.dumps(_snapshot(_owned(ctx, swarm_id)))
    sid = session_id_from_ctx(ctx)
    if not sid:
        raise ValueError("A session is required")
    from omniagents.core.agents.service import current_service

    restore_groups(current_service(), sid)
    return json.dumps([_snapshot(s) for s in _SWARMS.values() if s.owner == sid])


@rich_function_tool
async def message_swarm_member(
    ctx: RunContextWrapper[Any], swarm_id: str, name: str, message: str
) -> str:
    """Send owner guidance to a member; does not restart a finished worker."""
    return json.dumps(await _message(_owned(ctx, swarm_id), "owner", name, message))


@rich_function_tool
async def close_swarm(
    ctx: RunContextWrapper[Any],
    swarm_id: str,
    result: str,
    cancel_running: bool = False,
) -> str:
    """Record the integrated result and close the group. Explicitly opt in to cancel live work.

    Workspaces are retained until cleanup_swarm_member is called after integration.
    """
    swarm = _owned(ctx, swarm_id)
    async with swarm.lock:
        _active(swarm)
        running = [
            m
            for m in swarm.members.values()
            if (workers.worker_state_dict(m.worker_id) or {}).get("status") == "running"
        ]
        if running and not cancel_running:
            raise ValueError("Members are running; wait or explicitly cancel_running")
        for member in running:
            await getattr(workers.close_worker, "_original_func")(member.worker_id)
        swarm.closed = True
        swarm.result = result
        await _publish(swarm)
        return json.dumps(_snapshot(swarm))


@rich_function_tool
async def cleanup_swarm_member(
    ctx: RunContextWrapper[Any], swarm_id: str, name: str
) -> str:
    """Remove a member's isolated workspace AFTER reviewing and integrating its changes.

    The member cannot be dispatched again after cleanup. Allowed after closing a swarm.
    """
    swarm = _owned(ctx, swarm_id)
    async with swarm.lock:
        member = _member(swarm, name)
        state = workers.worker_state_dict(member.worker_id) or {}
        if state.get("status") == "running":
            raise ValueError("Cannot clean a running member")
        output = await getattr(workers.cleanup_worker_worktree, "_original_func")(
            member.worker_id
        )
        data = json.loads(str(output))
        if data.get("status") in {"cleaned_up", "no_worktree"}:
            member.cleaned = True
        await _publish(swarm)
        return json.dumps(data)
