"""Self-scheduled agent wakeups.

Lets the agent (and the user via slash) schedule a recurring or one-shot
notification that wakes a new run. Use case: "keep an eye on this every
few hours" — the agent calls ``schedule_wakeup`` once and ends its turn;
the framework re-wakes it on the cadence until ``end_at`` / ``end_after``
exhausts the schedule or the agent calls ``cancel_wakeup``.

Single schedule per session (v1). State is mirrored into
``session.variables`` so the schedule survives a server restart —
the next entry point through this module rehydrates the tick task
lazily.

Composition with the goal autopilot: refuses while a goal loop is
active (consulted via the :mod:`arbitration` registry) so the two
ticks don't fight over the session.

Pipeline (same path as background-job completion wakeups):
  tick task -> service.enqueue_notification(role="assistant")
            -> _notification_flusher batches & calls enqueue_message
            -> drainer -> start_run(prompt_role="assistant")
            -> agent resumes from history with the wakeup as observation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .arbitration import has_active_tick


_log = logging.getLogger(__name__)


# session.variables key used for persistence across server restart.
VAR_KEY = "wakeup_schedule"
SOURCE = "wakeup.tick"
FINAL_SOURCE = "wakeup.concluded"


@dataclass
class WakeupSchedule:
    interval_seconds: float
    message: str
    recurring: bool = True
    end_at: Optional[float] = None        # unix timestamp; whichever bound fires first wins
    end_after: Optional[int] = None       # remaining fire count (mutated as it fires)
    started_at: float = field(default_factory=time.time)
    fires: int = 0                        # diagnostic: how many ticks have fired
    cancelled: bool = False
    task: Any = None                      # asyncio.Task — opaque to callers


# Session-id -> WakeupSchedule. In-memory; survives only if rehydrated
# from session.variables after a restart.
_schedules: dict[str, WakeupSchedule] = {}


# ---------------------------------------------------------------------------
# Serialisation for session.variables persistence.
# ---------------------------------------------------------------------------


def _serialize(state: WakeupSchedule) -> dict:
    return {
        "interval_seconds": state.interval_seconds,
        "message": state.message,
        "recurring": state.recurring,
        "end_at": state.end_at,
        "end_after": state.end_after,
        "started_at": state.started_at,
        "fires": state.fires,
    }


def _deserialize(data: dict) -> WakeupSchedule:
    return WakeupSchedule(
        interval_seconds=float(data.get("interval_seconds") or 0.0),
        message=str(data.get("message") or ""),
        recurring=bool(data.get("recurring", True)),
        end_at=data.get("end_at"),
        end_after=data.get("end_after"),
        started_at=float(data.get("started_at") or time.time()),
        fires=int(data.get("fires") or 0),
    )


# ---------------------------------------------------------------------------
# Snapshot + UI broadcast (mirrors the goal pattern).
# ---------------------------------------------------------------------------


def _status_label(state: WakeupSchedule) -> str:
    if state.cancelled:
        return "cancelled"
    return "active"


def wakeup_snapshot_dict(state: WakeupSchedule | None) -> Optional[dict]:
    if state is None:
        return None
    return {
        "message": state.message,
        "interval_seconds": state.interval_seconds,
        "recurring": state.recurring,
        "end_at": state.end_at,
        "end_after": state.end_after,
        "fires": state.fires,
        "started_at": state.started_at,
        "status": _status_label(state),
    }


async def _broadcast_wakeup(service, session, state: WakeupSchedule | None) -> None:
    if service is None or session is None:
        return
    try:
        await service.request_client_function(
            "ui.wakeup.update",
            {"snapshot": wakeup_snapshot_dict(state)},
            session=session,
            expect_result=False,
        )
    except Exception as e:
        _log.warning("ui.wakeup.update broadcast failed: %s", e)


async def _persist(service, session, state: WakeupSchedule | None) -> None:
    """Mirror schedule into ``session.variables[VAR_KEY]`` so it can
    rehydrate after a server restart. ``silent=True`` because the UI
    gets the richer payload via ``ui.wakeup.update``; the variable is
    a queryable source, not a push channel."""
    if service is None or session is None:
        return
    payload = _serialize(state) if state is not None else None
    try:
        await service.update_session_variables(
            session.id, {VAR_KEY: payload}, silent=True,
        )
    except Exception as e:
        _log.warning("wakeup variable sync failed: %s", e)


# ---------------------------------------------------------------------------
# Bound checking.
# ---------------------------------------------------------------------------


def _is_exhausted(state: WakeupSchedule) -> bool:
    """True if the schedule has hit either bound (or is non-recurring
    after its single fire) and should self-cancel."""
    if not state.recurring and state.fires >= 1:
        return True
    if state.end_after is not None and state.end_after <= 0:
        return True
    if state.end_at is not None and time.time() >= state.end_at:
        return True
    return False


# ---------------------------------------------------------------------------
# Tick loop — owns the timer, the enqueue_notification call, the bound
# check, and the final "concluded" notification on exhaustion.
# ---------------------------------------------------------------------------


async def _tick_loop(service, session, state: WakeupSchedule) -> None:
    sid = getattr(session, "id", None)
    if not sid:
        return
    try:
        while True:
            await asyncio.sleep(state.interval_seconds)
            if state.cancelled:
                return
            # Suppress while a run is in flight — the next tick after
            # the run ends will wake the agent if needed. Matches
            # Session._tick_loop's behaviour.
            if session.current_task and not session.current_task.done():
                continue
            # Dedup: don't pile up identical ticks while the flusher
            # hasn't fired yet.
            if any(
                n.get("source") == SOURCE
                for n in getattr(session, "pending_notifications", [])
            ):
                continue
            # Bound check BEFORE firing. If exhausted, send the
            # concluded message instead and exit.
            if _is_exhausted(state):
                await _conclude(service, session, state)
                return
            try:
                await service.enqueue_notification(
                    session_id=sid,
                    content=_format_tick_body(state),
                    source=SOURCE,
                )
                state.fires += 1
                if state.end_after is not None:
                    state.end_after -= 1
                await _persist(service, session, state)
                await _broadcast_wakeup(service, session, state)
            except Exception:
                _log.exception("wakeup tick enqueue failed session=%s", sid)
                continue
            # Post-fire exhaustion check (for one-shot or end_after=1).
            if _is_exhausted(state):
                await _conclude(service, session, state)
                return
    except asyncio.CancelledError:
        return
    except Exception:
        _log.exception("wakeup tick loop crashed session=%s", sid)


def _format_tick_body(state: WakeupSchedule) -> str:
    """Frame the wakeup with directive language (parallel to the goal
    tick and background-job framings) so the model has a clear cue.
    Includes a budget line when bounded so the agent can prioritise."""
    parts = [f"[wakeup] {state.message}"]
    bounds: list[str] = []
    if state.end_after is not None:
        # end_after is decremented AFTER the enqueue, so right now it
        # still reflects the count INCLUDING this fire.
        bounds.append(f"{state.end_after - 1} more fires after this")
    if state.end_at is not None:
        remaining = max(0.0, state.end_at - time.time())
        bounds.append(f"~{int(remaining // 60)} min until end_at")
    if bounds:
        parts.append(f"(budget: {'; '.join(bounds)})")
    parts.append(
        "Take a look and decide. If the user's underlying request is "
        "satisfied, call `cancel_wakeup` and summarize. Otherwise take "
        "the next concrete step."
    )
    return " ".join(parts)


async def _conclude(service, session, state: WakeupSchedule) -> None:
    """Send the final "watch concluded" notification, then tear down.
    The notification gives the agent a chance to summarize what it
    observed across the watch window before the schedule fully clears."""
    sid = getattr(session, "id", None)
    if not sid:
        return
    body = (
        f"[wakeup] Watch concluded after {state.fires} fires "
        f"(\"{state.message}\"). The recurring schedule has ended on its "
        f"own. Summarize what you observed and stop — no further wakeups "
        f"will arrive unless you schedule a new one."
    )
    try:
        await service.enqueue_notification(
            session_id=sid,
            content=body,
            source=FINAL_SOURCE,
        )
    except Exception:
        _log.exception("wakeup concluded enqueue failed session=%s", sid)
    await _teardown(service, session, state, broadcast_final=True)


async def _teardown(
    service, session, state: WakeupSchedule, *, broadcast_final: bool
) -> None:
    """Cancel the task, clear in-memory + persisted state, refresh UI."""
    sid = getattr(session, "id", None)
    state.cancelled = True
    task = state.task
    state.task = None
    if task is not None and not task.done():
        # ``asyncio.Task.cancel`` from inside the task itself raises
        # CancelledError on the next await — we let the loop catch it
        # and return cleanly.
        task.cancel()
    if sid:
        _schedules.pop(sid, None)
    await _persist(service, session, None)
    if broadcast_final:
        # Push one final snapshot=None so the UI panel disappears.
        await _broadcast_wakeup(service, session, None)


# ---------------------------------------------------------------------------
# Rehydration. Restores a tick task when a session loads with a saved
# schedule but no in-memory state (post-server-restart).
# ---------------------------------------------------------------------------


async def maybe_rehydrate(service, session) -> Optional[WakeupSchedule]:
    sid = getattr(session, "id", None)
    if not sid:
        return None
    existing = _schedules.get(sid)
    if existing is not None and existing.task is not None and not existing.task.done():
        return existing
    saved = None
    try:
        variables = getattr(session, "variables", None)
        if isinstance(variables, dict):
            saved = variables.get(VAR_KEY)
    except Exception:
        saved = None
    if not isinstance(saved, dict):
        return None
    state = _deserialize(saved)
    if _is_exhausted(state):
        # Saved schedule has expired while the server was down — clear
        # it so we don't keep trying to rehydrate.
        await _persist(service, session, None)
        return None
    _schedules[sid] = state
    state.task = asyncio.create_task(
        _tick_loop(service, session, state),
        name=f"wakeup-{sid[:8]}",
    )
    _log.info(
        "wakeup rehydrated session=%s interval=%.1fs end_after=%s end_at=%s",
        sid, state.interval_seconds, state.end_after, state.end_at,
    )
    return state


# ---------------------------------------------------------------------------
# Core impl (shared by the agent tool, the slash command, and any
# future caller). Keep this signature stable.
# ---------------------------------------------------------------------------


async def schedule_impl(
    service,
    session,
    *,
    interval_seconds: float,
    message: str,
    recurring: bool = True,
    end_at: Optional[float] = None,
    end_after: Optional[int] = None,
) -> dict:
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}

    # Refuse while a goal loop is active — both use ticks and we don't
    # want them stepping on each other. The goal machinery registers
    # its tick kind in the arbitration registry.
    if has_active_tick(sid, "goal"):
        return {
            "ok": False,
            "message": (
                "A /goal loop is active on this session. Use /goal.stop "
                "first or finish the goal before scheduling a wakeup."
            ),
        }

    # Validation.
    try:
        interval_seconds = float(interval_seconds)
    except Exception:
        return {"ok": False, "message": "interval_seconds must be a number."}
    if interval_seconds <= 0:
        return {"ok": False, "message": "interval_seconds must be positive."}
    if end_after is not None:
        try:
            end_after = int(end_after)
        except Exception:
            return {"ok": False, "message": "end_after must be an integer."}
        if end_after <= 0:
            return {"ok": False, "message": "end_after must be > 0."}
    if end_at is not None:
        try:
            end_at = float(end_at)
        except Exception:
            return {"ok": False, "message": "end_at must be a unix timestamp."}
        if end_at <= time.time():
            return {"ok": False, "message": "end_at must be in the future."}
    if not isinstance(message, str) or not message.strip():
        return {"ok": False, "message": "message must be a non-empty string."}

    # Replace any prior schedule on this session (single-schedule v1).
    prior = _schedules.get(sid)
    if prior is not None:
        await _teardown(service, session, prior, broadcast_final=False)

    state = WakeupSchedule(
        interval_seconds=interval_seconds,
        message=message.strip(),
        recurring=bool(recurring),
        end_at=end_at,
        end_after=end_after,
    )
    _schedules[sid] = state
    state.task = asyncio.create_task(
        _tick_loop(service, session, state),
        name=f"wakeup-{sid[:8]}",
    )

    await _persist(service, session, state)
    await _broadcast_wakeup(service, session, state)
    return {
        "ok": True,
        "message": (
            f"Wakeup scheduled every {interval_seconds:g}s "
            f"({'recurring' if recurring else 'one-shot'}). "
            f"Watch: {message.strip()}"
        ),
        "snapshot": wakeup_snapshot_dict(state),
    }


async def cancel_impl(service, session) -> dict:
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}
    state = _schedules.get(sid)
    if state is None:
        # Try rehydration — maybe a saved schedule survived a restart
        # and the agent is trying to cancel it before any other entry
        # point woke it up.
        state = await maybe_rehydrate(service, session)
        if state is None:
            return {"ok": False, "message": "No wakeup is scheduled on this session."}
    await _teardown(service, session, state, broadcast_final=True)
    return {
        "ok": True,
        "message": f"Wakeup cancelled (fired {state.fires} times before cancellation).",
        "fires": state.fires,
    }


async def status_impl(service, session, args=None) -> dict:
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context.", "snapshot": None}
    state = _schedules.get(sid)
    if state is None:
        state = await maybe_rehydrate(service, session)
    if state is None:
        return {
            "ok": True,
            "active": False,
            "snapshot": None,
            "message": "No wakeup is scheduled on this session.",
        }
    snap = wakeup_snapshot_dict(state)
    parts = [
        f"Watch: {state.message}",
        f"Every {state.interval_seconds:g}s ({'recurring' if state.recurring else 'one-shot'})",
        f"Fires so far: {state.fires}",
    ]
    if state.end_after is not None:
        parts.append(f"Remaining fires: {state.end_after}")
    if state.end_at is not None:
        remaining = max(0.0, state.end_at - time.time())
        parts.append(f"Ends in: ~{int(remaining // 60)} min")
    parts.append(f"Status: {snap['status']}")
    return {
        "ok": True,
        "active": snap["status"] == "active",
        "snapshot": snap,
        "message": "\n".join(parts),
    }
