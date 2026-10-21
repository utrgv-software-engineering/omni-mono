"""Explicit parent messaging and optional peer communication over the durable worker journal.

No team object or shared objective is required. The host binds the sender and
parent; models can choose only a visible recipient. Mailbox admission precedes
best-effort live delivery and is deduplicated by the tool invocation identity.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, session_id_from_ctx
from .durable import for_session


def journal_for(service, parent_id):
    parent = service.get_session(parent_id)
    if parent is None:
        raise ValueError("Worker parent is unavailable")
    return for_session(service, parent)


@contextmanager
def parent_scope(service, parent_id):
    from omniagents.core.agents.service import (
        bind_current_service,
        reset_current_service,
    )
    from omniagents.core.session.helpers import (
        set_current_session_id,
        reset_current_session_id,
    )

    service_token = bind_current_service(service)
    session_token = set_current_session_id(parent_id)
    try:
        yield
    finally:
        reset_current_session_id(session_token)
        reset_current_service(service_token)


def available(row):
    return bool(
        row
        and not row.get("cleaned")
        and not row.get("closed")
        and (
            row.get("recipe", {}).get("messaging") == "explicit"
            or row.get("recipe", {}).get("communication")
        )
    )


def enabled(row):
    return bool(
        row
        and row.get("recipe", {}).get("communication")
        and not row.get("cleaned")
        and not row.get("closed")
    )


def inspect_worker(journal, worker_id=None):
    """Owner view, also retaining the historical mailbox of legacy swarm members."""

    def read(data, state):
        messages = [
            m
            for m in data.get("messages", [])
            if worker_id is None or worker_id in (m["from"], m["to"])
        ]
        legacy = []
        for group_id, group in data["groups"].items():
            if group.get("kind") != "swarm":
                continue
            members = group["members"]
            selected = [
                m
                for m in members.values()
                if worker_id is None or m["worker_id"] == worker_id
            ]
            if selected:
                names = {m["name"] for m in selected}
                legacy.append(
                    {
                        "id": group_id,
                        "objective": group["objective"],
                        "closed": group["closed"],
                        "members": selected,
                        "messages": [
                            m
                            for m in group["messages"]
                            if worker_id is None
                            or m["from"] in names
                            or m["to"] in names
                        ],
                    }
                )
        return {"messages": messages, "legacy_swarms": legacy}

    return journal.mutate(read)


def context(journal, worker_id):
    def read(data, state):
        own = data["workers"].get(worker_id)
        if not available(own):
            raise ValueError("Worker communication is unavailable")
        peers = [
            {
                "worker_id": row["worker_id"],
                "name": row["recipe"].get("name"),
                "role": row["recipe"].get("role", ""),
                "task": row["task"],
                "status": row["status"],
            }
            for row in data["workers"].values()
            if enabled(own) and enabled(row)
        ]
        return {
            "worker_id": worker_id,
            "peers": peers,
            "messages": [
                m for m in data.get("messages", []) if worker_id in (m["from"], m["to"])
            ],
        }

    return journal.mutate(read)


def accept_message(journal, sender, recipient, message, operation_id):
    if not message.strip() or len(message) > 16000:
        raise ValueError("Worker messages must contain 1–16000 characters")

    def update(data, state):
        rows = data["workers"]
        if not available(rows.get(sender)):
            raise ValueError("Worker communication is unavailable")
        if recipient != "owner" and (
            not enabled(rows.get(sender))
            or recipient == sender
            or not enabled(rows.get(recipient))
        ):
            raise ValueError(
                "Recipient is not an available peer in this parent session"
            )
        messages = data.setdefault("messages", [])
        for old in messages:
            if old["from"] == sender and old["operation_id"] == operation_id:
                if (old["to"], old["message"]) != (recipient, message):
                    raise ValueError("Message identity reused with different input")
                return old
        if len(messages) >= 500:
            raise ValueError(
                "Worker mailbox limit reached; the owner can inspect your transcript"
            )
        row = {
            "id": uuid.uuid4().hex,
            "operation_id": operation_id,
            "from": sender,
            "to": recipient,
            "message": message,
            "created_at": time.time(),
            "delivery": "mailbox",
        }
        messages.append(row)
        if recipient == "owner":
            # Admit the mailbox entry and parent wakeup in one transaction.
            state.setdefault("notifications", []).append(
                {
                    "content": f"[worker message] {sender}: {message}",
                    "source": "worker.message",
                    "enqueued_at": row["created_at"],
                    "worker_message_id": row["id"],
                }
            )
            row["delivery"] = "message_queued"
        return row

    return journal.mutate(update)


async def send(service, parent_id, worker_id, recipient, message, operation_id):
    journal = journal_for(service, parent_id)
    row = accept_message(journal, worker_id, recipient, message, operation_id)
    if recipient == "owner":
        try:
            service._refresh_queue(service.get_session(parent_id))
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Worker message persisted; parent refresh will retry on reconnect"
            )
        return row
    if row["delivery"] != "mailbox":
        return row
    from omniagents.builtin.tools import worker_tools

    try:
        with parent_scope(service, parent_id):
            worker_tools._restore_workers(parent_id)
            delivery = await worker_tools.manager().send_message(
                recipient,
                f"[worker {worker_id}] {message}",
                operation_id=f"worker-mail:{row['id']}",
            )
        if delivery in {"message_sent", "message_queued"}:

            def acknowledge(data, state):
                for saved in data["messages"]:
                    if saved["id"] == row["id"]:
                        saved["delivery"] = delivery
                        return saved

            row = journal.mutate(acknowledge)
    except Exception:
        # The recipient can still inspect the durable mailbox; never restart it.
        import logging

        logging.getLogger(__name__).exception(
            "Worker message retained after live delivery failed"
        )
    return row


def configure_worker(service, parent_id, worker_id):
    def authorized(ctx):
        journal = journal_for(service, parent_id)
        row = journal.get(worker_id)
        if not available(row) or session_id_from_ctx(ctx) != row["session_id"]:
            raise PermissionError("Worker communication belongs to a different child")
        return journal

    @rich_function_tool
    async def worker_context(ctx: RunContextWrapper[Any]) -> str:
        """Discover opted-in sibling workers and read your incoming/outgoing mailbox.

        The host restricts this to your parent session. No shared goal is required.
        """
        return json.dumps(context(authorized(ctx), worker_id))

    @rich_function_tool
    async def worker_message(
        ctx: RunContextWrapper[Any], recipient: str, message: str
    ) -> str:
        """Send findings to a worker_id from worker_context, or to 'owner'.

        Messages to owner enqueue a parent turn. Ordinary text responses do not.
        Use this tool to report findings, ask questions, or hand work back.
        Messages start idle recipients automatically; busy recipients finish their current run first.
        """
        authorized(ctx)
        return json.dumps(
            await send(
                service,
                parent_id,
                worker_id,
                recipient,
                message,
                getattr(ctx, "tool_call_id", None) or uuid.uuid4().hex,
            )
        )

    def configure(spec):
        from omniagents.builtin.tools.swarm_tools import _validate_worker_spec

        _validate_worker_spec(spec)
        spec.available_tools = list(spec.available_tools) + [
            worker_context,
            worker_message,
        ]

    return configure
