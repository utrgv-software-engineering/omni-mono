"""Idle-trigger scheduler: fire an action when a conversation goes quiet.

Products register :class:`IdleTrigger` actions (e.g. an auto-recap) and the
scheduler fires each one when its session has gone idle. The trigger is a
conversation-lifecycle signal (completed turns, idle-since-turn), which has
no sandbox analog — this runs at the orchestration layer.

Rules per trigger (all must hold):
  1. Idle >= ``trigger.idle_seconds`` since the last completed turn.
  2. At least ``trigger.min_turns`` completed turns — short sessions
     don't qualify.
  3. Never twice in a row — after a trigger fires, the session must see
     fresh activity (a new completed turn) before it can fire again.

The action must NOT run through the service run path: it should use a
throwaway/ephemeral agent run (see ``omniagents.core.runtime.run_agent_once``)
so it produces no ``run_end`` on the session and cannot feed back into the
turn count or re-arm itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

_log = logging.getLogger("omniagents.autonomy.idle")

_WATCHER_INTERVAL_S = 20.0


@dataclass(frozen=True)
class IdleTrigger:
    """An action to run when a session has gone idle.

    ``action`` is awaited with ``(service, session)``.
    """

    name: str
    action: Callable[[Any, Any], Awaitable[Any]]
    idle_seconds: float = 180.0
    min_turns: int = 3


@dataclass
class _TriggerState:
    # Armed = "fresh activity has happened since this trigger last fired,
    # so it is allowed to fire." Set True on every run_end; set False the
    # moment we fire. This is rule 3 ("never twice in a row").
    armed: bool = False
    # Guards against launching a second action while one is still in
    # flight for this (session, trigger).
    firing: bool = False


@dataclass
class _SessionState:
    completed_turns: int = 0
    last_turn_end_at: float = field(default_factory=time.monotonic)
    triggers: dict[str, _TriggerState] = field(default_factory=dict)

    def trigger_state(self, name: str) -> _TriggerState:
        return self.triggers.setdefault(name, _TriggerState())


class IdleTriggerScheduler:
    """Watches a service's active sessions and fires idle triggers.

    The service must expose ``active_sessions()`` and
    ``add_run_end_listener(session_id, cb)`` /
    ``remove_run_end_listener(session_id, cb)``.
    """

    def __init__(
        self,
        service: Any,
        triggers: Optional[list[IdleTrigger]] = None,
        *,
        watcher_interval_s: float = _WATCHER_INTERVAL_S,
    ) -> None:
        self._service = service
        self._triggers: list[IdleTrigger] = list(triggers or [])
        self._watcher_interval_s = watcher_interval_s
        self._state: dict[str, _SessionState] = {}
        self._listeners: dict[str, Any] = {}
        self._watcher: Optional[asyncio.Task] = None

    def register(self, trigger: IdleTrigger) -> None:
        """Add a trigger. Registering an existing name replaces it."""
        self._triggers = [t for t in self._triggers if t.name != trigger.name]
        self._triggers.append(trigger)

    # ---- Lifecycle ----

    async def start(self) -> None:
        if self._watcher is not None and not self._watcher.done():
            return
        _log.info(
            "idle-trigger scheduler started (triggers=%s)",
            [t.name for t in self._triggers],
        )
        self._watcher = asyncio.create_task(self._run_watcher(), name="idle-watcher")

    async def stop(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass
        # Detach listeners we installed.
        for sid, cb in list(self._listeners.items()):
            try:
                self._service.remove_run_end_listener(sid, cb)
            except Exception:
                pass
        self._listeners.clear()
        self._state.clear()

    # ---- Internals ----

    def _sessions(self) -> dict[str, Any]:
        try:
            return {s.id: s for s in self._service.active_sessions()}
        except Exception:
            return {}

    def _instrument(self, session_id: str) -> None:
        """Install a run_end listener that records turn count + timestamp and
        re-arms every trigger. Idempotent per session."""
        if session_id in self._listeners:
            return
        st = self._state.setdefault(session_id, _SessionState())

        async def _on_run_end(_payload: Any) -> None:
            st.completed_turns += 1
            st.last_turn_end_at = time.monotonic()
            # Fresh activity → every trigger allowed again (rule 3).
            for trigger in self._triggers:
                st.trigger_state(trigger.name).armed = True

        self._listeners[session_id] = _on_run_end
        try:
            self._service.add_run_end_listener(session_id, _on_run_end)
        except Exception:
            self._listeners.pop(session_id, None)

    def _should_fire(
        self,
        session: Any,
        st: _SessionState,
        trigger: IdleTrigger,
        ts: _TriggerState,
    ) -> bool:
        if not ts.armed or ts.firing:
            return False
        if st.completed_turns < trigger.min_turns:  # rule 2
            return False
        if time.monotonic() - st.last_turn_end_at < trigger.idle_seconds:  # rule 1
            return False
        # Don't fire while the agent is mid-run or has imminent queued work.
        task = getattr(session, "current_task", None)
        if task is not None and not task.done():
            return False
        if getattr(session, "pending_notifications", None):
            return False
        return True

    async def _run_watcher(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._watcher_interval_s)
                for sid, session in list(self._sessions().items()):
                    # First sighting → instrument and move on; we don't have
                    # a turn baseline yet so it can't qualify this tick.
                    if sid not in self._listeners:
                        self._instrument(sid)
                        continue
                    st = self._state.get(sid)
                    if st is None:
                        continue
                    for trigger in self._triggers:
                        ts = st.trigger_state(trigger.name)
                        if not self._should_fire(session, st, trigger, ts):
                            continue
                        # Disarm BEFORE firing (rule 3) and mark in-flight so
                        # a slow action can't double-fire on the next tick.
                        ts.armed = False
                        ts.firing = True
                        asyncio.create_task(
                            self._fire(sid, session, trigger, ts),
                            name=f"idle-fire-{trigger.name}-{sid[:8]}",
                        )
        except asyncio.CancelledError:
            raise

    async def _fire(
        self, session_id: str, session: Any, trigger: IdleTrigger, ts: _TriggerState
    ) -> None:
        try:
            await trigger.action(self._service, session)
            _log.info(
                "[idle] trigger %s fired for session=%s", trigger.name, session_id[:8]
            )
        except Exception as exc:
            # Leave disarmed — don't retry-storm. Next completed turn re-arms.
            _log.warning(
                "[idle] trigger %s failed for session=%s: %s",
                trigger.name,
                session_id[:8],
                exc,
            )
        finally:
            ts.firing = False
