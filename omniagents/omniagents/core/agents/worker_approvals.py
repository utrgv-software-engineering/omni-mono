"""Worker review policy and human escalation through the owning conversation."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class WorkerApprovalContext:
    parent_service: Any
    parent_session_id: str | None
    worker_id: str
    auto_approve: bool = False

    async def escalate(self, *, kind: str, session: Any, payload: dict) -> dict | None:
        """Register a human decision on the parent's existing approval RPCs.

        Guardian already reviewed the worker's actual environment. Do not
        review again against the parent's environment or broaden its allowlist.
        The worker's awaiting task owns this request, including cancellation.
        Without a parent, leave delivery to the worker's normal pending UI path.
        """
        service = self.parent_service
        if service is None or not self.parent_session_id:
            return None
        parent = service.get_session(self.parent_session_id)
        if parent is None:
            return None
        if getattr(parent, "user_id", None) != getattr(session, "user_id", None):
            raise PermissionError("Worker approval owner does not match parent")
        is_tool = kind == "tool"
        key = "call_id" if is_tool else "request_id"
        token = f"apr_{uuid.uuid4().hex}"
        method = "tool_approval" if is_tool else "mcp_approval"
        pending = (
            service._pending_tool_approvals
            if is_tool
            else service._pending_mcp_approvals
        )
        attr = "pending_tool_approvals" if is_tool else "pending_mcp_approvals"
        replay = getattr(parent, attr, None)
        if replay is None:
            replay = {}
            setattr(parent, attr, replay)
        forwarded = {
            **payload,
            key: token,
            "session_id": parent.id,
            "worker_id": self.worker_id,
            "worker_session_id": session.id,
            "worker_run_id": payload.get("run_id"),
        }
        # A worker run is not a parent run: keep its identity as metadata.
        forwarded.pop("run_id", None)
        if is_tool:
            forwarded["source_call_id"] = payload.get("call_id")
        metadata = dict(forwarded.get("metadata") or {})
        metadata["worker_id"] = self.worker_id
        forwarded["metadata"] = metadata
        future = asyncio.get_running_loop().create_future()
        pending[token] = (future, parent)
        try:
            await service._broadcast_session(
                parent, f"{method}_requested", forwarded, queue_if_unseen=True
            )
            replay[token] = dict(forwarded)
            response = await future
            if not isinstance(response, dict):
                return {
                    "decision": "reject",
                    "rejection_message": "Invalid approval response",
                }
            # A parent UI's always-allow choice must not silently exempt
            # future swarm calls from Guardian or alter parent permissions.
            return {**response, "always_approve": False}
        finally:
            pending.pop(token, None)
            replay.pop(token, None)
            try:
                await service._broadcast_session(
                    parent, f"{method}_resolved", {key: token, "session_id": parent.id}
                )
            except Exception:
                # UI disconnection must not replace a decision or cancellation.
                pass
