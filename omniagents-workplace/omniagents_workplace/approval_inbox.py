"""The approval inbox: every approval a person must give, in one place.

Tools a factory names in ``security.human_approval_tools`` always wait for a
person, in a chat or in an automation run no one is watching. The runtime keeps
them (``/integrations/approvals``); this lists them for the factory's approvers
(its administrators), takes their decisions, and audits each with the
approver. New requests are posted to the Teams channel, with the tool and the
agent but never the call's arguments.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import admin

logger = logging.getLogger(__name__)

SEEN_KEY = "approvals.announced"


class ApprovalDecision(BaseModel):
    decision: str
    reason: str = Field(default="", max_length=2000)


class ApprovalInbox:
    def __init__(self, workplace: Any) -> None:
        self.w = workplace

    def endpoint(self) -> tuple[Optional[str], Optional[str]]:
        return self.w.kill_switch.endpoint()

    async def listing(self, client: Optional[httpx.AsyncClient] = None) -> list:
        url, token = self.endpoint()
        if not url or not token:
            return []
        own = client is None
        client = client or httpx.AsyncClient(timeout=15, trust_env=False)
        try:
            response = await client.get(
                url + "/integrations/approvals",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return response.json().get("approvals", [])
        finally:
            if own:
                await client.aclose()

    async def decide(
        self,
        account: Any,
        call_id: str,
        body: ApprovalDecision,
        client: Optional[httpx.AsyncClient] = None,
    ) -> dict:
        if body.decision not in ("approve", "reject"):
            raise HTTPException(422, "Approve or reject")
        url, token = self.endpoint()
        if not url or not token:
            raise HTTPException(503, "The factory is unavailable")
        own = client is None
        client = client or httpx.AsyncClient(timeout=30, trust_env=False)
        try:
            response = await client.post(
                url + f"/integrations/approvals/{call_id}",
                json={
                    "decision": body.decision,
                    "approver": account.username,
                    "reason": body.reason,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            if own:
                await client.aclose()
        if response.status_code >= 400:
            detail = (response.json() if response.content else {}).get("detail")
            raise HTTPException(response.status_code, detail or "The decision failed")
        row = response.json()
        self.w.audit_log.record(
            "approval.decided",
            actor=account.username,
            call=call_id,
            tool=row.get("tool"),
            decision=body.decision,
            reason=body.reason or None,
        )
        return row

    async def announce(self, client: Optional[httpx.AsyncClient] = None) -> int:
        """Post approvals no one has been told about to the Teams channel."""
        try:
            pending = await self.listing(client)
        except (httpx.HTTPError, ValueError):
            return 0
        seen = set(self.w.store.meta(SEEN_KEY) or [])
        fresh = [a for a in pending if a["call_id"] not in seen]
        for approval in fresh:
            await self.w.notifier.send(
                "Approval needed",
                f"{approval['agent']} wants to use {approval['tool']}. "
                "Open the factory's approvals to decide.",
                [("Agent", approval["agent"]), ("Tool", approval["tool"])],
            )
        if fresh:
            current = {a["call_id"] for a in pending}
            self.w.store.set_meta(SEEN_KEY, sorted((seen & current) | current))
        return len(fresh)

    async def maintain(self, interval: float = 15) -> None:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            while True:
                try:
                    await self.announce(client)
                except Exception:  # noqa: BLE001 - the loop outlives a failure
                    logger.exception("approvals: announcing failed")
                await asyncio.sleep(interval)

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/workplace/api/factory/approvals")
        async def approvals(request: Request):
            admin(request)
            try:
                return {"approvals": await self.listing()}
            except httpx.HTTPError:
                raise HTTPException(503, "The factory is unavailable") from None

        @router.post("/workplace/api/factory/approvals/{call_id}")
        async def decide(call_id: str, body: ApprovalDecision, request: Request):
            return await self.decide(admin(request), call_id, body)

        return router
