"""The factory's kill switch: stop all of its AI work, and resume it.

Workplace holds the authoritative state: the factory's own stop
(``factory.stop`` in its store) and, in an installation, the stop of every
factory (``installation_state``). Either one stops the factory. While stopped,
Workplace refuses chat messages (``check``) and the event gateway skips what it
would deliver; the runtime is told (``/integrations/stop``) and refuses turns,
model calls and tool calls itself, so a runtime Workplace does not manage still
stops. ``maintain`` keeps the runtime in line across restarts on either side.

Who may: a factory's administrators stop and resume it; a stop made by an
installation administrator needs one to resume. Every stop and resume needs a
reason and is audited. If the state cannot be read, the factory is treated as
stopped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .accounts import admin, me

logger = logging.getLogger(__name__)

KEY = "factory.stop"
UNREADABLE = {
    "reason": "the factory's stop state could not be read",
    "scope": "unknown",
}


class Reason(BaseModel):
    reason: str


def describe(state: Optional[dict]) -> str:
    if not state:
        return ""
    who = state.get("stopped_by")
    reason = state.get("reason")
    scope = (
        "All factories are"
        if state.get("scope") == "installation"
        else "This factory is"
    )
    return (
        f"{scope} stopped"
        + (f" by {who}" if who else "")
        + (f": {reason}" if reason else "")
        + ". An administrator can resume it."
    )


def reason_of(body: Reason) -> str:
    reason = body.reason.strip()
    if not reason or len(reason) > 500:
        raise HTTPException(422, "Give a reason (at most 500 characters)")
    return reason


class KillSwitch:
    def __init__(self, workplace: Any) -> None:
        self.w = workplace
        #: The installation's stop of every factory (an installation sets it).
        self.installation_state: Callable[[], Optional[dict]] = lambda: None
        #: Whether an account is an installation administrator (standalone: any admin).
        self.installation_admin: Callable[[Any], bool] = lambda account: bool(
            account.admin
        )
        #: Called with whether the factory is now stopped (an installation stops
        #: and starts the runtime it manages).
        self.on_change: Optional[Callable[[bool], Awaitable[None]]] = None

    def name(self) -> str:
        try:
            return self.w.slack.name or "The factory"
        except AttributeError:
            return "The factory"

    # -- state ---------------------------------------------------------------

    def own(self) -> Optional[dict]:
        return self.w.store.meta(KEY)

    def state(self) -> Optional[dict]:
        """The stop in force, the installation's first; fails closed."""
        try:
            return self.installation_state() or self.own()
        except Exception:  # noqa: BLE001 - an unreadable state is a stop
            logger.exception("kill switch: stop state unreadable; treating as stopped")
            return dict(UNREADABLE)

    def check(self) -> None:
        state = self.state()
        if state:
            raise HTTPException(409, describe(state))

    def view(self) -> dict:
        state = self.state()
        return {"stopped": state, "message": describe(state), "own": self.own()}

    # -- changes -------------------------------------------------------------

    async def stop(self, account: Any, reason: str) -> dict:
        state = {
            "stopped_by": account.username,
            "stopped_at": time.time(),
            "reason": reason,
            "scope": "factory",
            "by_installation_admin": self.installation_admin(account),
        }
        self.w.store.set_meta(KEY, state)
        self.w.audit_log.record(
            "factory.stopped", actor=account.username, reason=reason
        )
        await self.changed()
        return self.view()

    async def resume(self, account: Any, reason: str) -> dict:
        own = self.own()
        if not own:
            raise HTTPException(409, "This factory is not stopped")
        if own.get("by_installation_admin") and not self.installation_admin(account):
            raise HTTPException(
                403,
                "An installation administrator stopped this factory; one must resume it",
            )
        self.w.store.set_meta(KEY, None)
        self.w.audit_log.record(
            "factory.resumed", actor=account.username, reason=reason
        )
        await self.changed()
        await self.w.notifier.send(
            "Factory resumed",
            f"{self.name()} was resumed by {account.username}.",
            [("Factory", self.name()), ("By", account.username), ("Reason", reason)],
        )
        return self.view()

    async def changed(self) -> None:
        """Tell the runtime now, and whoever runs it."""
        try:
            await self.sync()
        except (httpx.HTTPError, ValueError):
            # ``maintain`` keeps trying; Workplace refuses work meanwhile.
            logger.warning("kill switch: runtime not reached; retrying", exc_info=True)
        if self.on_change is not None:
            await self.on_change(self.state() is not None)

    # -- the runtime ---------------------------------------------------------

    def endpoint(self) -> tuple[Optional[str], Optional[str]]:
        config = self.w.factory_config
        if config is None:
            return None, None
        try:
            factory = config.load().get("factory") or {}
        except Exception:  # noqa: BLE001 - no usable configuration, no runtime
            return None, None
        url = str(factory.get("url") or "").rstrip("/") or None
        token = str(factory.get("admin_auth_token") or "") or None
        return url, token

    async def sync(self, client: Optional[httpx.AsyncClient] = None) -> None:
        """Make the runtime's stop match Workplace's."""
        url, token = self.endpoint()
        if not url or not token:
            return
        desired = self.state()
        headers = {"Authorization": f"Bearer {token}"}
        own_client = client is None
        client = client or httpx.AsyncClient(timeout=15, trust_env=False)
        try:
            response = await client.get(url + "/integrations/stop", headers=headers)
            if response.status_code == 404:
                return  # a runtime without the kill switch: Workplace still refuses work
            response.raise_for_status()
            current = response.json().get("stopped")
            if desired and not current:
                body = {
                    k: v for k, v in desired.items() if k != "by_installation_admin"
                }
                (
                    await client.post(
                        url + "/integrations/stop", json=body, headers=headers
                    )
                ).raise_for_status()
            elif current and not desired:
                (
                    await client.delete(url + "/integrations/stop", headers=headers)
                ).raise_for_status()
        finally:
            if own_client:
                await client.aclose()

    async def maintain(self, interval: float = 5) -> None:
        """Keep the runtime in line, and count whether it answered (uptime)."""
        samples = {"up": 0, "down": 0}
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            while True:
                answered = None
                try:
                    if self.endpoint()[0]:
                        await self.sync(client)
                        answered = True
                except (httpx.HTTPError, ValueError):
                    answered = False  # the runtime is down or starting; try again
                except Exception:  # noqa: BLE001 - never let the loop die
                    logger.exception("kill switch: runtime sync failed")
                if answered is not None and not self.state():
                    # A stopped factory's runtime is down on purpose: not counted.
                    samples["up" if answered else "down"] += 1
                if sum(samples.values()) >= 12:
                    self.record_availability(samples)
                    samples = {"up": 0, "down": 0}
                await asyncio.sleep(interval)

    def record_availability(self, samples: dict) -> None:
        key = "runtime.availability." + time.strftime("%Y-%m-%d", time.gmtime())
        counts = self.w.store.meta(key) or {}
        self.w.store.set_meta(
            key,
            {k: counts.get(k, 0) + samples.get(k, 0) for k in ("up", "down")},
        )

    # -- API -----------------------------------------------------------------

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/workplace/api/factory/stop")
        def stop_state(request: Request):
            me(request)
            return self.view()

        @router.post("/workplace/api/factory/stop")
        async def stop_factory(body: Reason, request: Request):
            return await self.stop(admin(request), reason_of(body))

        @router.post("/workplace/api/factory/resume")
        async def resume_factory(body: Reason, request: Request):
            return await self.resume(admin(request), reason_of(body))

        return router
