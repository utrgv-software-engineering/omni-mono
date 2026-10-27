"""Exclusive run reservation with a heartbeat lease.

One runtime at a time executes a session's run. The holder records a
reservation in the session's durable inbox and refreshes its heartbeat while
the run is alive. A reservation whose heartbeat is older than ``LEASE_TTL``
belongs to a runtime that died; the next runtime takes it over, marks the
interrupted work, and proceeds. Nothing waits for an operator.

What survives a takeover is the transcript: dangling tool calls are closed as
"outcome unknown" (see ``Session.refresh_owned_history`` and
``AgentService._close_interrupted_run``) so the model is told, once, that a
command may have partially run. That is the whole recovery story.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
import time
from typing import Any
import uuid

logger = logging.getLogger(__name__)

current_ownership = ContextVar("runtime_ownership", default=None)

#: A reservation is presumed dead once its heartbeat is this old.
LEASE_TTL = 30.0
#: How often a live holder refreshes its heartbeat.
HEARTBEAT_INTERVAL = 5.0

_IDENTITY_KEYS = ("run_id", "owner", "generation")


def lease_expired(record, now: float | None = None) -> bool:
    """True when ``record`` (an owner or claim) has no fresh heartbeat."""
    if not isinstance(record, dict):
        return True
    stamp = record.get("heartbeat")
    if not isinstance(stamp, (int, float)):
        # A record written by an older build carries no heartbeat. That
        # runtime cannot refresh one either, so it is stale by definition.
        return True
    return (time.time() if now is None else now) - stamp > LEASE_TTL


def _identity(record):
    return (
        {key: record.get(key) for key in _IDENTITY_KEYS}
        if isinstance(record, dict)
        else None
    )


@dataclass
class RunOwnership:
    store: Any
    thread_id: str
    run_id: str
    owner: str
    generation: int
    #: The reservation this one displaced because its heartbeat had lapsed.
    took_over_from: dict | None = None
    transferred: bool = False
    entered: bool = False
    released: bool = False
    closing: bool = False
    active_operations: int = 0
    #: Informational: a supervised command could not confirm its cleanup.
    #: It no longer blocks release; the transcript carries the uncertainty.
    execution_uncertain: bool = False
    _heartbeat_task: asyncio.Task | None = field(default=None, repr=False)
    #: What else the run held, let go of once its reservation is released
    #: (its MCP servers: AgentService._mcp_hold). Run once, in order.
    _on_release: list = field(default_factory=list, repr=False)

    def add_release_callback(self, callback) -> None:
        """Run ``callback()`` once this reservation is released (at once if it is)."""
        if self.released:
            callback()
        else:
            self._on_release.append(callback)

    def _matches(self, record):
        return _identity(record) == {
            "run_id": self.run_id,
            "owner": self.owner,
            "generation": self.generation,
        }

    def assert_current(self):
        def check(state, receipt):
            if (
                self.released
                or self.closing
                or not self._matches(state.get("runtime_owner"))
            ):
                raise RuntimeError(
                    "Run ownership is no longer current; execution refused"
                )
            return None, None

        self.store.mutate_queue(self.thread_id, check)

    def heartbeat(self) -> bool:
        """Refresh the lease. False when the reservation is no longer ours."""
        if self.released:
            return False

        def touch(state, receipt):
            owner = state.get("runtime_owner")
            if not self._matches(owner):
                return False, None
            owner["heartbeat"] = time.time()
            return True, None

        return self.store.mutate_queue(self.thread_id, touch)

    def start_heartbeat(self) -> None:
        """Keep the lease fresh from the running event loop until release."""
        if self._heartbeat_task is not None or self.released:
            return

        async def beat():
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                try:
                    if not await asyncio.to_thread(self.heartbeat):
                        return
                except Exception:
                    # Storage hiccup: keep trying; the TTL is generous.
                    continue

        self._heartbeat_task = asyncio.create_task(
            beat(), name=f"lease-heartbeat:{self.run_id}"
        )

    def _stop_heartbeat(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task is not None and not task.done():
            task.cancel()

    @contextmanager
    def operation(self):
        """Track admitted local I/O until it settles, even during cancellation."""
        self.assert_current()
        self.active_operations += 1
        try:
            yield
        finally:
            self.active_operations -= 1
            if self.closing and self.active_operations == 0:
                self.release()

    def release(self):
        if self.released:
            return
        self.closing = True
        if self.active_operations:
            # Closing rejects new work immediately; the last admitted
            # operation performs the release when it settles.
            return

        def finish(state, receipt):
            if state.get("runtime_owner") is None and self._matches(
                state.get("last_runtime_owner")
            ):
                return None, None  # Commit succeeded but its acknowledgment was lost.
            if not self._matches(state.get("runtime_owner")):
                # Our lease lapsed and another runtime took the session.
                # There is nothing of ours left to release.
                return None, None
            state["last_runtime_owner"] = _identity(state["runtime_owner"])
            state["runtime_owner"] = None
            return None, None

        try:
            self.store.mutate_queue(self.thread_id, finish)
            self._stop_heartbeat()
            self.released = True
        finally:
            # (what the run held is let go of even when the store can't be
            # told: a held MCP server set would otherwise stay open)
            callbacks, self._on_release = self._on_release, []
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    logger.debug("Run release callback failed", exc_info=True)

    def record_recovery_scope(self, scope, *, journal=None):
        """Kept for call sites that fence I/O on a current reservation."""
        self.assert_current()


def reserve_run(store, thread_id):
    from omniagents.core.session.queue_inbox import current_dispatch

    owner = uuid.uuid4().hex
    run_id = f"run_{uuid.uuid4().hex}"

    def claim(state, receipt):
        if state.get("archived"):
            raise RuntimeError("Session is archived")
        now = time.time()
        previous = state.get("runtime_owner")
        displaced = None
        if previous is not None:
            if not lease_expired(previous, now):
                raise RuntimeError(
                    "Session has an active runtime owner; a run is already executing"
                )
            # The holder stopped heartbeating: it is gone. Take over. Queue
            # items it had claimed go back to pending so they dispatch again;
            # whether their run started is settled by their receipt on the
            # next refresh, not guessed here.
            displaced = _identity(previous)
            state["last_runtime_owner"] = displaced
            for entry in state["items"]:
                if entry.get("owner") is not None:
                    entry["owner"] = None
                    entry.pop("claimed_at", None)
        for entry in state["items"]:
            if entry.get("owner") is None:
                continue
            if current_dispatch.get() == (
                thread_id,
                entry["item"]["id"],
                entry["owner"],
            ):
                continue
            if lease_expired({"heartbeat": entry.get("claimed_at")}, now):
                entry["owner"] = None
                entry.pop("claimed_at", None)
                continue
            raise RuntimeError(
                "Session has an active queue dispatcher; a run is about to start"
            )
        generation = state.get("runtime_generation", 0) + 1
        state["runtime_generation"] = generation
        state["runtime_owner"] = {
            "run_id": run_id,
            "owner": owner,
            "generation": generation,
            "heartbeat": now,
        }
        state.pop("runtime_recovery", None)
        return (generation, displaced), None

    generation, displaced = store.mutate_queue(thread_id, claim)
    return RunOwnership(
        store, thread_id, run_id, owner, generation, took_over_from=displaced
    )
