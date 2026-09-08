"""Join durable conversation history with this runtime's live capabilities.

A persisted request is evidence that a prompt was displayed, not that a
waiter survived a process restart. Do not rewrite shared history on a read:
another runtime may still own it. Instead make unavailable controls historical
in this runtime's snapshot. Plans and artifacts deliberately remain durable.
"""

from typing import Any


def snapshot_state_events(session: Any) -> list[dict]:
    """Replaceable display state, not pending commands to execute or answer."""
    events = []
    active = session.current_task is not None and not session.current_task.done()
    fields = [("client_request", "active_session_state_payload")]
    if active:
        fields += [
            ("run_status", "active_run_status_payload"),
            ("client_request", "active_run_set_status_payload"),
        ]
    for method, field in fields:
        payload = getattr(session, field, None)
        if payload:
            events.append(
                {"method": method, "params": {**payload, "session_id": session.id}}
            )
    return events


def snapshot_queue(inbox: dict, session: Any) -> list[dict]:
    """Project the exact inbox read with the items, without starting/claiming work."""
    active = session.current_task is not None and not session.current_task.done()
    draining = session.drainer_task is not None and not session.drainer_task.done()
    result = []
    for entry in inbox.get("items", []):
        item = dict(entry["item"])
        owner = entry["owner"]
        if owner is not None:
            if owner == session.queue_owner and draining:
                continue
            item["state"] = "dispatch_uncertain"
        elif (
            item.get("state", "pending") == "pending"
            and inbox.get("runtime_owner")
            and not active
        ):
            item["error"] = (
                "Waiting for the previous runtime to finish or be reconciled. "
                "This message has not started."
            )
        result.append(item)
    return result


def reconcile_runtime_controls(
    items: list[dict], session: Any, runtime: Any
) -> list[dict]:
    def live_requests(attribute: str) -> set[str]:
        return {
            request_id
            for request_id, (future, owner) in getattr(runtime, attribute, {}).items()
            if owner is session and not future.done()
        }

    tools = live_requests("_pending_tool_approvals")
    mcp_requests = live_requests("_pending_mcp_approvals")
    elicitations = live_requests("_pending_elicitations")
    result = []
    for item in items:
        content = item.get("content") or {}
        if item.get("kind") == "tool_call" and item.get("status") == "started":
            task = getattr(session, "current_task", None)
            live = (
                task is not None
                and not task.done()
                and item.get("turn_id") == getattr(session, "active_run_id", None)
            )
            if not live:
                item = {
                    **item,
                    "status": "failed",
                    "content": {
                        **content,
                        "execution_state": "uncertain",
                        "output": "Execution outcome unknown: the runtime that invoked this tool is no longer available. Do not assume it is safe to repeat.",
                        "metadata": {"display_type": "error"},
                        "finalized_by": "runtime_reconciliation",
                    },
                }
        pending = None
        request_id = None
        payloads = {}
        if item.get("kind") == "approval":
            mcp = content.get("approval_kind") == "mcp"
            pending = mcp_requests if mcp else tools
            request_id = content.get("request_id" if mcp else "call_id")
            payloads = getattr(
                session,
                "pending_mcp_approvals" if mcp else "pending_tool_approvals",
                {},
            )
        elif item.get("kind") == "elicitation":
            pending = elicitations
            request_id = content.get("elicitation_id")
            payloads = getattr(session, "pending_elicitations", {})
        if (
            item.get("status") == "started"
            and pending is not None
            and request_id in pending
        ):
            # The durable item is a history projection, not the full live
            # prompt. Preserve presentation and expiry details on reconnect
            # without mutating history or resurrecting an unavailable waiter.
            payload = payloads.get(request_id, {})
            details = {
                key: payload[key]
                for key in (
                    "metadata",
                    "server_label",
                    "tool_label",
                    "expires_at",
                    "timeout_ms",
                    "persist_response",
                    "item_id",
                )
                if key in payload
            }
            if details:
                item = {**item, "content": {**content, **details}}
        if (
            item.get("status") == "started"
            and pending is not None
            and request_id not in pending
        ):
            item = {
                **item,
                "status": "failed",
                "content": {
                    **content,
                    "finalized_by": "runtime_reconciliation",
                    "reason": "The runtime that requested this response is no longer available.",
                },
            }
        result.append(item)
    return result
