"""Approvals a person must give, across every conversation and automation run.

Tools a project names in ``security.human_approval_tools`` always wait for a
person. Their pending approvals live in the runtime's agent services; this
keeps them in the event store too, so whoever runs the factory (Workplace's
approval inbox) can list and answer them, and they outlast a restart:

* a pending approval the runtime still holds is answered directly, with the
  approver recorded in the runtime's audit;
* one whose run ended with an earlier runtime is ``interrupted``: approving it
  re-drives the call in a new turn of its conversation, with exactly that call
  (tool and arguments) approved once; declining tells the conversation;
* an approval left for seven days expires, declined.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator, Optional, Tuple

from omniagents.core.agents.human_approval import preapprove

logger = logging.getLogger(__name__)

EXPIRY_SECONDS = 7 * 86400


class PersonApprovals:
    def __init__(self, store: Any, runtimes: Any) -> None:
        self.store = store
        self.runtimes = runtimes

    def live(self) -> Iterator[Tuple[str, Any, str, dict]]:
        """``(agent, service, call_id, payload)`` for each held approval a
        person must give."""
        for agent, service in self.runtimes.keyed_services():
            for call_id, (future, session) in list(
                getattr(service, "_pending_tool_approvals", {}).items()
            ):
                if future.done():
                    continue
                payload = (getattr(session, "pending_tool_approvals", {}) or {}).get(
                    call_id
                ) or {}
                if payload.get("requires_person"):
                    yield agent, service, call_id, {**payload, "session_id": session.id}

    def sync(self) -> None:
        live = set()
        for agent, _service, call_id, payload in self.live():
            live.add(call_id)
            self.store.record_approval(
                {
                    "call_id": call_id,
                    "agent": agent,
                    "session_id": payload["session_id"],
                    "tool": payload.get("tool_label") or payload.get("tool_name") or "",
                    "server": payload.get("server_label"),
                    "arguments": payload.get("arguments"),
                    "run_id": payload.get("run_id"),
                    "requested_at": time.time(),
                }
            )
        for row in self.store.approvals(("pending",)):
            if row["call_id"] not in live:
                # Answered in the conversation itself, or its run was stopped.
                self.store.settle_approval(row["call_id"], "settled_in_conversation")

    def listing(self) -> list:
        self.sync()
        return self.store.approvals()

    def _held(self, call_id: str) -> Optional[Any]:
        for _agent, service, held, _payload in self.live():
            if held == call_id:
                return service
        return None

    async def decide(
        self, call_id: str, approve: bool, approver: str, reason: str = ""
    ) -> dict:
        row = self.store.approval(call_id)
        if row is None or row["status"] not in ("pending", "interrupted"):
            raise LookupError("This approval is no longer waiting")
        decision = "approve" if approve else "reject"
        service = self._held(call_id)
        if service is not None:
            await service._answer_tool_approval(
                call_id,
                decision,
                False,
                None if approve else (reason or f"Declined by {approver}"),
                approver,
            )
        else:
            runtime = await self.runtimes.app(row["agent"])
            if runtime is None:
                raise LookupError("The approval's agent no longer exists")
            if approve:
                preapprove(row["session_id"], row["tool"], row["arguments"])
                message = (
                    f"{approver} approved your call to {row['tool']} with the "
                    "arguments you proposed, after the run that asked for it ended. "
                    "Make that call now if it is still needed."
                )
            else:
                message = f"{approver} declined your call to {row['tool']}" + (
                    f": {reason}" if reason else "."
                )
            await runtime.state.agent_service.enqueue_message(
                row["session_id"], message, role="user", source="approval"
            )
        self.store.settle_approval(
            call_id, "approved" if approve else "declined", approver, reason
        )
        return self.store.approval(call_id)

    async def expire(self, now: Optional[float] = None) -> int:
        now = now or time.time()
        expired = 0
        for row in self.store.approvals():
            if row["requested_at"] + EXPIRY_SECONDS > now:
                continue
            service = self._held(row["call_id"])
            if service is not None:
                await service._answer_tool_approval(
                    row["call_id"],
                    "reject",
                    False,
                    "No one approved it within seven days",
                )
            self.store.settle_approval(row["call_id"], "expired", None, "seven days")
            expired += 1
        return expired
