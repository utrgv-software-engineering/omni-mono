"""Durable queued-command acceptance and dispatch claims.

A claim is a short lease: the dispatcher that took it either starts the run
(whose receipt then settles the item) or dies, in which case the claim lapses
after ``LEASE_TTL`` and the item is dispatched again. See
``omniagents.core.runtime.ownership`` for the run-level lease it pairs with.
"""

from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
import hashlib
import json
import time

current_dispatch = ContextVar("queue_dispatch_owner", default=None)


def _claim_expired(entry, now=None):
    from omniagents.core.runtime.ownership import lease_expired

    return lease_expired({"heartbeat": entry.get("claimed_at")}, now)


def release_stale_claims(state, now=None):
    """Drop claims whose dispatcher stopped refreshing. Returns True if any."""
    released = False
    for entry in state.get("items", []):
        if entry.get("owner") is not None and _claim_expired(entry, now):
            entry["owner"] = None
            entry.pop("claimed_at", None)
            released = True
    return released


def accept(store, thread_id, item, *, submission_id=None, payload=None, limit=100):
    if submission_id is not None and (
        not isinstance(submission_id, str) or not 1 <= len(submission_id) <= 128
    ):
        raise ValueError(
            "submission_id must be a nonempty string of at most 128 characters"
        )
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # Freeze caller-owned nested attachments/variables before persistence.
    item = json.loads(json.dumps(item))

    def update(state, receipt):
        if receipt is not None:
            if receipt.get("digest") != digest:
                raise ValueError("submission_id was already used for different content")
            if receipt.get("status") == "completed":
                return deepcopy(receipt["result"]), None
            raise RuntimeError(
                "Submission outcome is unresolved; it will not be sent twice"
            )
        items = state["items"]
        if state.get("archived"):
            return {
                "ok": False,
                "reason": "session_archived",
                "session_id": thread_id,
            }, None
        if len(items) >= limit:
            return {
                "ok": False,
                "reason": "queue_full",
                "depth": len(items),
                "session_id": thread_id,
            }, None
        items.append({"item": item, "owner": None})
        result = {
            "ok": True,
            "id": item["id"],
            "depth": len(items),
            "session_id": thread_id,
            "enqueued_at": item["enqueued_at"],
        }
        receipt = (
            {"digest": digest, "status": "completed", "result": result}
            if submission_id
            else None
        )
        return result, receipt

    return store.mutate_queue(thread_id, update, submission_id=submission_id)


def entries(store, thread_id):
    return store.mutate_queue(
        thread_id, lambda state, receipt: (deepcopy(state["items"]), None)
    )


def runtime_owner(store, thread_id):
    return store.mutate_queue(
        thread_id, lambda state, receipt: (deepcopy(state.get("runtime_owner")), None)
    )


def claim(store, thread_id, item_id, owner):
    from omniagents.core.runtime.ownership import lease_expired

    def update(state, receipt):
        items = state["items"]
        now = time.time()
        runtime_owner = state.get("runtime_owner")
        if state.get("archived"):
            return False, None
        if runtime_owner is not None and not lease_expired(runtime_owner, now):
            return False, None
        release_stale_claims(state, now)
        # One dispatcher at a time; a live competing claim blocks this one.
        pending = [
            entry
            for entry in items
            if entry["item"].get("state", "pending") == "pending"
        ]
        if not pending or any(entry["owner"] is not None for entry in items):
            return False, None
        if pending[0]["item"]["id"] != item_id:
            return False, None
        pending[0]["owner"] = owner
        pending[0]["claimed_at"] = now
        return True, None

    return store.mutate_queue(thread_id, update)


def release_claim_if_stale(store, thread_id, item_id):
    """Return the item to pending when its dispatcher's claim has lapsed."""

    def update(state, receipt):
        for entry in state["items"]:
            if entry["item"]["id"] == item_id and entry.get("owner") is not None:
                if _claim_expired(entry):
                    entry["owner"] = None
                    entry.pop("claimed_at", None)
                    return True, None
                return False, None
        return False, None

    return store.mutate_queue(thread_id, update)


def finish(store, thread_id, item_id, owner):
    def update(state, receipt):
        for index, entry in enumerate(state["items"]):
            if entry["item"]["id"] == item_id and entry["owner"] == owner:
                del state["items"][index]
                return True, None
        return False, None

    return store.mutate_queue(thread_id, update)


def fail(store, thread_id, item_id, owner, error):
    def update(state, receipt):
        for entry in state["items"]:
            if entry["item"]["id"] == item_id and entry["owner"] == owner:
                entry["owner"] = None
                entry["item"].update(state="failed", error=error)
                return True, None
        return False, None

    return store.mutate_queue(thread_id, update)


def cancel(store, thread_id, item_id):
    def update(state, receipt):
        for index, entry in enumerate(state["items"]):
            if entry["item"]["id"] == item_id:
                # Cancel always removes the item. If a dispatcher had already
                # handed it to start_run, that run is already visible in the
                # transcript and is stopped through stop_run, not here.
                del state["items"][index]
                return {"ok": True, "id": item_id, "depth": len(state["items"])}, None
        return {"ok": False, "id": item_id, "reason": "not_found"}, None

    return store.mutate_queue(thread_id, update)


def buffer_notification(store, thread_id, notification):
    notification = json.loads(json.dumps(notification))

    def update(state, receipt):
        buffered = state.setdefault("notifications", [])
        if state.get("archived") or len(buffered) >= 1000:
            return {
                "ok": False,
                "reason": "session_archived" if state.get("archived") else "queue_full",
            }, None
        buffered.append(notification)
        return {"ok": True, "buffered": len(buffered), "session_id": thread_id}, None

    return store.mutate_queue(thread_id, update)


def notifications(store, thread_id):
    return store.mutate_queue(
        thread_id,
        lambda state, receipt: (deepcopy(state.get("notifications", [])), None),
    )


def promote_notifications(store, thread_id, make_item, *, limit=100):
    def update(state, receipt):
        buffered = state.get("notifications", [])
        if not buffered or state.get("archived") or len(state["items"]) >= limit:
            return False, None
        item = make_item(buffered)
        state["items"].append({"item": item, "owner": None})
        state["notifications"] = []
        return True, None

    return store.mutate_queue(thread_id, update)
