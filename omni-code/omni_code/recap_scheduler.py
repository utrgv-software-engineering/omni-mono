"""Idle auto-recap scheduler.

Fires a session recap (the same ``generate_and_broadcast_recap`` path as
``/recap``) when a conversation has gone quiet, so a user returning to a
session they stepped away from gets a catch-up without asking.

Rules (all must hold) — fixed product behavior, not configurable:
  1. Idle >= ``IDLE_THRESHOLD_S`` (180s) since the last completed turn.
  2. At least ``MIN_TURNS`` (3) completed turns — short sessions don't qualify.
  3. Never twice in a row — after a recap fires, the session must see fresh
     activity (a new completed turn) before another recap can fire.

This lives at the omni-code orchestration layer, not on the sandbox: the
trigger is a conversation-lifecycle signal (turns, idle-since-turn), which
has no sandbox analog. It's the recap counterpart to ``/goal``'s run-end
listener pattern.

The recap itself runs via ``Runner.run`` on a throwaway agent (see
``server_functions/recap.py``), NOT through the service run path — so it
produces no ``run_end`` on the user's session and can't feed back into the
turn count or re-arm itself. No loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_log = logging.getLogger("omni_code.recap")

# Fixed thresholds. This is a general omni-code feature, not a per-deployment
# knob — deliberately module constants with no constructor / profile override.
IDLE_THRESHOLD_S = 180.0
MIN_TURNS = 3
_WATCHER_INTERVAL_S = 20.0


@dataclass
class _RecapState:
    completed_turns: int = 0
    last_turn_end_at: float = field(default_factory=time.monotonic)
    # Armed = "fresh activity has happened since the last recap, so a recap
    # is allowed to fire." Set True on every run_end; set False the moment
    # we fire. This is rule 3 ("never twice in a row").
    armed: bool = False
    # Guards against launching a second recap task while one is still in
    # flight for this session.
    firing: bool = False


class RecapScheduler:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._state: dict[str, _RecapState] = {}
        self._listeners: dict[str, Any] = {}
        self._watcher: Optional[asyncio.Task] = None

    # ---- Lifecycle ----

    async def start(self) -> None:
        if self._watcher is not None and not self._watcher.done():
            return
        _log.info(
            "recap scheduler started (idle=%.0fs, min_turns=%d)",
            IDLE_THRESHOLD_S,
            MIN_TURNS,
        )
        self._watcher = asyncio.create_task(self._run_watcher(), name="recap-watcher")

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
        sm = getattr(self._service, "_session_manager", None)
        by_id = getattr(sm, "_by_id", None)
        return by_id if isinstance(by_id, dict) else {}

    def _instrument(self, session_id: str) -> None:
        """Install a run_end listener that records turn count + timestamp and
        re-arms. Idempotent per session."""
        if session_id in self._listeners:
            return
        st = self._state.setdefault(session_id, _RecapState())

        async def _on_run_end(_payload: Any) -> None:
            st.completed_turns += 1
            st.last_turn_end_at = time.monotonic()
            st.armed = True  # fresh activity → recap allowed again (rule 3)

        self._listeners[session_id] = _on_run_end
        try:
            self._service.add_run_end_listener(session_id, _on_run_end)
        except Exception:
            self._listeners.pop(session_id, None)

    def _should_fire(self, session: Any, st: _RecapState) -> bool:
        if not st.armed or st.firing:
            return False
        if st.completed_turns < MIN_TURNS:  # rule 2
            return False
        if time.monotonic() - st.last_turn_end_at < IDLE_THRESHOLD_S:  # rule 1
            return False
        # Don't recap while the agent is mid-run or has imminent queued work.
        task = getattr(session, "current_task", None)
        if task is not None and not task.done():
            return False
        if getattr(session, "pending_notifications", None):
            return False
        return True

    async def _run_watcher(self) -> None:
        try:
            while True:
                await asyncio.sleep(_WATCHER_INTERVAL_S)
                for sid, session in list(self._sessions().items()):
                    # First sighting → instrument and move on; we don't have
                    # a turn baseline yet so it can't qualify this tick.
                    if sid not in self._listeners:
                        self._instrument(sid)
                        continue
                    st = self._state.get(sid)
                    if st is None or not self._should_fire(session, st):
                        continue
                    # Disarm BEFORE firing (rule 3) and mark in-flight so a
                    # slow recap can't double-fire on the next tick.
                    st.armed = False
                    st.firing = True
                    asyncio.create_task(
                        self._fire(sid, session, st), name=f"recap-fire-{sid[:8]}"
                    )
        except asyncio.CancelledError:
            raise

    async def _fire(self, session_id: str, session: Any, st: _RecapState) -> None:
        try:
            from server_functions.recap import generate_and_broadcast_recap

            await generate_and_broadcast_recap(self._service, session)
            _log.info("[recap] auto-recap fired for session=%s", session_id[:8])
        except Exception as exc:
            # Leave disarmed — don't retry-storm. Next completed turn re-arms.
            _log.warning("[recap] auto-recap failed for session=%s: %s", session_id[:8], exc)
        finally:
            st.firing = False
