"""Server functions for the tick-driven ``/goal`` autopilot loop.

A goal is a session-scoped autonomous loop. Composition:

- ``/goal <text>`` (or ``/goal-start``) seeds the conversation with the
  initial framing prompt, mirrors goal state into ``session.variables``
  for consumers/tooling, and installs a periodic **tick** via
  ``session.set_tick``. The tick is what advances the loop — every
  ``tick_interval`` seconds (when the agent is idle) a brief
  notification arrives, prompting the agent to take the next step.
- ``goal_complete`` (tool) flips ``state.completed``; the per-session
  run-end listener observes that at the next ``run_end`` and tears
  down the tick + listener.
- Background work (bash jobs, workers) wakes the agent naturally via
  ``enqueue_notification``; the tick + completion notifications coalesce
  through the existing flusher, so the agent doesn't get spammed.
- ``/goal.stop`` cancels the tick + listener; ``/goal.status`` reports
  current turn / status without mutating anything.

No retry-on-error logic, no continuation-after-every-turn enqueue: the
tick handles wake-ups uniformly, and the agent reacts to its own
history when a previous turn errored.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from omniagents import server_function

from omni_code.goal_prompts import (
    build_goal_initial_prompt,
    build_goal_tick_prompt,
)

_log = logging.getLogger(__name__)


DEFAULT_MAX_TURNS = 10
DEFAULT_TICK_INTERVAL_S = 5.0

# Single JSON blob mirrored into session.variables for rehydration. The
# legacy per-field variables (goal_text/goal_status/...) are kept alongside
# this for tools/skills that already read them — that's a stable
# consumption surface; this blob is the rehydrate source of truth.
VAR_KEY = "goal_state"


@dataclass
class GoalState:
    goal: str
    max_turns: int = DEFAULT_MAX_TURNS
    tick_interval: float = DEFAULT_TICK_INTERVAL_S
    safe_tool_overrides: Optional[dict[str, Any]] = None
    turn: int = 0
    cancelled: bool = False
    completed: bool = False
    paused: bool = False
    completion_reason: str = ""
    # Evidence string captured from goal_complete(reason="achieved",
    # evidence=...). Auditable artifact attached to the terminal state;
    # surfaces in snapshot, status output, and the variable mirror so
    # downstream consumers can record what the agent claims as proof.
    # The goal text itself is free-form prose; the user writes the
    # verification surface, constraints, and blocked-stop condition
    # into that prose. The agent reads them each turn from history.
    evidence: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    last_reason: Optional[str] = None
    listener: Any = None  # opaque callable for remove_run_end_listener


# Session-id -> GoalState. In-memory; one goal per session.
_goals: dict[str, GoalState] = {}


def _serialize(state: GoalState) -> dict:
    """Full serialization for session.variables[VAR_KEY] persistence.
    Excludes runtime-only fields (listener)."""
    return {
        "goal": state.goal,
        "max_turns": state.max_turns,
        "tick_interval": state.tick_interval,
        "safe_tool_overrides": state.safe_tool_overrides,
        "turn": state.turn,
        "cancelled": state.cancelled,
        "completed": state.completed,
        "paused": state.paused,
        "completion_reason": state.completion_reason,
        "evidence": state.evidence,
        "started_at": state.started_at,
        "last_reason": state.last_reason,
    }


def _deserialize(data: dict) -> GoalState:
    return GoalState(
        goal=str(data.get("goal") or ""),
        max_turns=int(data.get("max_turns") or DEFAULT_MAX_TURNS),
        tick_interval=float(data.get("tick_interval") or DEFAULT_TICK_INTERVAL_S),
        safe_tool_overrides=(
            data.get("safe_tool_overrides")
            if isinstance(data.get("safe_tool_overrides"), dict)
            else None
        ),
        turn=int(data.get("turn") or 0),
        cancelled=bool(data.get("cancelled", False)),
        completed=bool(data.get("completed", False)),
        paused=bool(data.get("paused", False)),
        completion_reason=str(data.get("completion_reason") or ""),
        evidence=data.get("evidence"),
        started_at=float(data.get("started_at") or time.time()),
        last_reason=data.get("last_reason"),
    )


# ---------------------------------------------------------------------------
# Snapshot + broadcast.
# Frontends subscribe to ``ui.goal.update`` and render the snapshot in a
# docked panel. None snapshot means "no goal set" (panel hides).
# State is also mirrored into ``session.variables`` so consumers without a
# UI panel (launcher, eval harness) can read it via the standard variable
# change broadcast.
# ---------------------------------------------------------------------------


def _status_label(state: GoalState) -> str:
    # Completed and cancelled are terminal — they win over paused. paused
    # is a non-terminal "hold" applied to an otherwise-active loop.
    if state.completed:
        return "completed"
    if state.cancelled:
        return "cancelled"
    if state.paused:
        return "paused"
    return "active"


def goal_snapshot_dict(state: GoalState | None) -> Optional[dict]:
    if state is None:
        return None
    return {
        "goal": state.goal,
        "turn": state.turn,
        "max_turns": state.max_turns,
        "tick_interval": state.tick_interval,
        "last_reason": state.last_reason,
        "status": _status_label(state),
        "started_at": state.started_at,
        "completion_reason": state.completion_reason or None,
        "evidence": state.evidence,
    }


def _mirror_variables(state: GoalState | None) -> dict:
    """Variables to mirror into ``session.variables`` so other tools /
    consumer apps can observe goal state alongside the UI broadcast.
    The full serialized state lives under VAR_KEY for rehydrate; the
    per-field variables here are a stable consumption surface that
    skills/tools already read."""
    if state is None:
        return {
            VAR_KEY: None,
            "goal_text": None,
            "goal_status": None,
            "goal_turn": None,
            "goal_max_turns": None,
            "goal_started_at": None,
            "goal_completion_reason": None,
            "goal_evidence": None,
        }
    return {
        VAR_KEY: _serialize(state),
        "goal_text": state.goal,
        "goal_status": _status_label(state),
        "goal_turn": state.turn,
        "goal_max_turns": state.max_turns,
        "goal_started_at": state.started_at,
        "goal_completion_reason": state.completion_reason or None,
        "goal_evidence": state.evidence,
    }


async def _broadcast_goal(service, session, state: GoalState | None) -> None:
    if service is None or session is None:
        return
    try:
        await service.request_client_function(
            "ui.goal.update",
            {"snapshot": goal_snapshot_dict(state)},
            session=session,
            expect_result=False,
        )
    except Exception as e:
        _log.warning("ui.goal.update broadcast failed: %s", e)


async def _sync_variables(service, session, state: GoalState | None) -> None:
    """Mirror goal state into ``session.variables`` so tools/skills can
    read it via ``session_variables(ctx)``. Passes ``silent=True`` because
    the UI gets goal state through ``ui.goal.update`` (richer payload);
    the variable mirror is a queryable source, not a push channel."""
    if service is None or session is None:
        return
    try:
        await service.update_session_variables(
            session.id,
            _mirror_variables(state),
            silent=True,
        )
    except Exception as e:
        _log.warning("goal variable sync failed: %s", e)


# ---------------------------------------------------------------------------
# Loop body — fired from the per-session run-end listener.
# Responsibilities: detect goal_complete, count turns, enforce max_turns,
# refresh snapshot. The tick scheduler (in omniagents.Session) handles
# wake-ups directly — no continuation enqueue happens here.
# ---------------------------------------------------------------------------


async def _on_run_end(service, session, payload: dict) -> None:
    sid = getattr(session, "id", None)
    if not sid:
        return
    state = _goals.get(sid)
    if state is None or state.cancelled:
        return

    # Tool-driven completion: goal_complete was called during the run.
    if state.completed:
        await _stop_goal(service, session, state)
        _log.info(
            "goal complete via tool for session=%s turn=%d reason=%s evidence=%s",
            sid,
            state.turn,
            state.completion_reason,
            (
                (state.evidence[:120] + "…")
                if state.evidence and len(state.evidence) > 120
                else state.evidence
            ),
        )
        return

    # Paused: don't count the run as a goal turn, don't enqueue
    # continuation. The user is interacting with the thread normally and
    # the goal loop is on hold. /goal.resume will re-install the tick.
    if state.paused:
        return

    reason = payload.get("end_reason") or payload.get("reason") or ""
    state.last_reason = reason
    state.turn += 1

    # Budget exhausted — tell the agent and tear down.
    if state.turn >= state.max_turns:
        state.cancelled = True
        try:
            await service.enqueue_notification(
                session_id=sid,
                content=(
                    f"[goal] Reached max_turns={state.max_turns} without "
                    f"calling goal_complete. Stopping autopilot. Summarize "
                    f"progress and remaining work in your next response."
                ),
                source="goal.exhausted",
            )
        except Exception:
            pass
        await _stop_goal(service, session, state)
        return

    # Normal turn end — refresh UI + variables. Tick wakes the agent for
    # the next step; no continuation enqueue here.
    await _broadcast_goal(service, session, state)
    await _sync_variables(service, session, state)


def _drop_pending_tick_notifications(session) -> None:
    """Drop any queued goal.tick notifications that haven't flushed —
    otherwise they'd trigger one ghost run after stop/pause. Shared
    between teardown (stop) and pause."""
    try:
        session.pending_notifications = [
            n for n in session.pending_notifications if n.get("source") != "goal.tick"
        ]
    except Exception:
        pass


def _install_tick(session, state: GoalState) -> None:
    """Install the periodic tick that wakes the agent each interval.
    Shared between start, resume, and rehydrate."""
    tick_kwargs = {
        "source": "goal.tick",
    }
    if state.safe_tool_overrides is not None:
        tick_kwargs["safe_tool_overrides"] = state.safe_tool_overrides
    session.set_tick(
        state.tick_interval,
        build_goal_tick_prompt(goal=state.goal, max_turns=state.max_turns),
        **tick_kwargs,
    )


async def _stop_goal(service, session, state: GoalState) -> None:
    """Tear down the loop: cancel tick, remove listener, broadcast final
    snapshot, sync final variables, drop any queued tick notifications."""
    sid = getattr(session, "id", None)
    try:
        session.cancel_tick()
    except Exception:
        pass
    _drop_pending_tick_notifications(session)
    if state.listener is not None and sid:
        try:
            service.remove_run_end_listener(sid, state.listener)
        except Exception:
            pass
        state.listener = None
    await _broadcast_goal(service, session, state)
    await _sync_variables(service, session, state)


async def _pause_goal(service, session, state: GoalState) -> None:
    """Mark paused, cancel the tick, drop queued tick notifications, but
    keep the run-end listener attached so /goal.resume can re-install
    the tick cleanly. _on_run_end no-ops while state.paused is True."""
    try:
        session.cancel_tick()
    except Exception:
        pass
    _drop_pending_tick_notifications(session)
    await _broadcast_goal(service, session, state)
    await _sync_variables(service, session, state)


async def _resume_goal(service, session, state: GoalState) -> None:
    """Re-install the tick. Listener was kept attached on pause so no
    re-registration needed."""
    try:
        _install_tick(session, state)
    except Exception as e:
        _log.warning("goal resume: failed to re-install tick: %s", e)
    await _broadcast_goal(service, session, state)
    await _sync_variables(service, session, state)


async def maybe_rehydrate(service, session) -> Optional[GoalState]:
    """Restore an in-memory GoalState from session.variables[VAR_KEY]
    after a server restart. Re-registers the run-end listener; re-installs
    the tick only when not paused. Terminal states (completed/cancelled)
    are not re-activated — they remain visible via the variable mirror
    but no loop machinery comes back. Called lazily from goal.status /
    stop / pause / resume when in-memory state is missing."""
    sid = getattr(session, "id", None)
    if not sid:
        return None
    existing = _goals.get(sid)
    if existing is not None and existing.listener is not None:
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
    if state.completed or state.cancelled:
        return None
    _goals[sid] = state

    async def _listener(payload):
        try:
            await _on_run_end(service, session, payload)
        except Exception:
            _log.exception("goal loop iteration failed for session=%s", sid)

    state.listener = _listener
    try:
        service.add_run_end_listener(sid, _listener)
    except Exception as e:
        _log.warning("goal rehydrate: add_run_end_listener failed: %s", e)
    if not state.paused:
        try:
            _install_tick(session, state)
        except Exception as e:
            _log.warning("goal rehydrate: install_tick failed: %s", e)
    _log.info(
        "goal rehydrated session=%s turn=%d/%d paused=%s",
        sid,
        state.turn,
        state.max_turns,
        state.paused,
    )
    return state


# ---------------------------------------------------------------------------
# Server functions — canonical (dotted) + Ink slash aliases.
# ---------------------------------------------------------------------------


async def _goal_start_on_invoke(service, session, args=None):
    args = args or {}
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}

    goal_text = (args.get("goal") or args.get("text") or "").strip()
    if not goal_text:
        return {"ok": False, "message": "Usage: /goal <goal>"}

    existing = _goals.get(sid)
    if existing and not (existing.cancelled or existing.completed):
        return {
            "ok": False,
            "message": (
                f"A goal is already running on this session "
                f"(turn {existing.turn}/{existing.max_turns}). "
                f"Use /goal.stop first."
            ),
        }

    max_turns = int(args.get("max_turns") or DEFAULT_MAX_TURNS)
    tick_interval = float(args.get("tick_interval") or DEFAULT_TICK_INTERVAL_S)
    safe_tool_overrides = args.get("safe_tool_overrides")
    if not isinstance(safe_tool_overrides, dict):
        safe_tool_overrides = None
    state = GoalState(
        goal=goal_text,
        max_turns=max_turns,
        tick_interval=tick_interval,
        safe_tool_overrides=safe_tool_overrides,
    )
    _goals[sid] = state

    # Wire run-end listener — keep a strong ref via state.listener so we
    # can remove it later without name-mangling.
    async def _listener(payload):
        try:
            await _on_run_end(service, session, payload)
        except Exception:
            _log.exception("goal loop iteration failed for session=%s", sid)

    state.listener = _listener
    service.add_run_end_listener(sid, _listener)

    # Mirror state into session.variables and push UI snapshot.
    await _sync_variables(service, session, state)
    await _broadcast_goal(service, session, state)

    # Start the periodic tick — wakes the agent each interval when idle
    # and no tick notification is already queued. Coalesces, so no spam.
    try:
        _install_tick(session, state)
    except Exception as e:
        _goals.pop(sid, None)
        service.remove_run_end_listener(sid, _listener)
        return {"ok": False, "message": f"Failed to schedule goal tick: {e}"}

    # Kick off the first run with the framing prompt.
    initial = build_goal_initial_prompt(goal_text, max_turns)
    try:
        await service.enqueue_message(
            session_id=sid,
            content=initial,
            role="user",
            trigger_run=True,
            source="goal.start",
            safe_tool_overrides=safe_tool_overrides,
        )
    except Exception as e:
        try:
            session.cancel_tick()
        except Exception:
            pass
        service.remove_run_end_listener(sid, _listener)
        _goals.pop(sid, None)
        await _sync_variables(service, session, None)
        await _broadcast_goal(service, session, None)
        return {"ok": False, "message": f"Failed to start goal: {e}"}

    return {
        "ok": True,
        "message": (
            f"Goal started: {goal_text}\n"
            f"Max turns: {max_turns} · Tick interval: {tick_interval}s\n"
            f"Stop with /goal.stop. Pause with /goal.pause. Inspect with /goal.status."
        ),
        "goal": goal_text,
        "max_turns": max_turns,
        "tick_interval": tick_interval,
    }


@server_function(
    description="Cancel the running goal loop on this session.",
    strict=True,
    name_override="goal.stop",
)
async def goal_stop(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_stop_on_invoke(service, session, args=None):
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}
    state = _goals.get(sid)
    if state is None:
        # Maybe the loop survived a restart in session.variables — try
        # to restore so we can cancel it cleanly.
        state = await maybe_rehydrate(service, session)
    if state is None:
        return {"ok": False, "message": "No goal is running on this session."}
    if state.cancelled:
        return {"ok": False, "message": "Goal already cancelled."}
    state.cancelled = True
    state.paused = False
    await _stop_goal(service, session, state)
    return {
        "ok": True,
        "message": (f"Goal cancelled. Reached turn {state.turn}/{state.max_turns}."),
        "turn": state.turn,
        "max_turns": state.max_turns,
    }


setattr(goal_stop, "_server_function_on_invoke", _goal_stop_on_invoke)


@server_function(
    description="Report the status of the current goal loop on this session.",
    strict=True,
    name_override="goal.status",
)
async def goal_status(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_status_on_invoke(service, session, args=None):
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context.", "snapshot": None}
    state = _goals.get(sid)
    if state is None:
        state = await maybe_rehydrate(service, session)
    if state is None:
        return {
            "ok": True,
            "message": "No goal is set on this session.",
            "active": False,
            "snapshot": None,
        }
    snap = goal_snapshot_dict(state)
    active = snap["status"] == "active"
    parts = [
        f"Goal: {state.goal}",
        f"Turn: {state.turn}/{state.max_turns}",
        f"Tick interval: {state.tick_interval}s",
        f"Last end_reason: {state.last_reason or '(none yet)'}",
        f"Status: {snap['status']}",
    ]
    if state.evidence:
        parts.append(f"Evidence: {state.evidence}")
    if state.completion_reason:
        parts.append(f"Completion reason: {state.completion_reason}")
    return {
        "ok": True,
        "active": active,
        "snapshot": snap,
        "message": "\n".join(parts),
    }


setattr(goal_status, "_server_function_on_invoke", _goal_status_on_invoke)


@server_function(
    description="Pause the active goal loop on this session. Cancels the periodic tick but keeps the goal state and run-end listener so /goal.resume can pick it back up.",
    strict=True,
    name_override="goal.pause",
)
async def goal_pause(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_pause_on_invoke(service, session, args=None):
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}
    state = _goals.get(sid)
    if state is None:
        state = await maybe_rehydrate(service, session)
    if state is None:
        return {"ok": False, "message": "No goal is running on this session."}
    if state.completed or state.cancelled:
        return {
            "ok": False,
            "message": f"Goal is already {_status_label(state)}; can't pause.",
        }
    if state.paused:
        return {"ok": True, "message": "Goal is already paused.", "paused": True}
    state.paused = True
    await _pause_goal(service, session, state)
    return {
        "ok": True,
        "message": (
            f"Goal paused at turn {state.turn}/{state.max_turns}. "
            f"Resume with /goal.resume."
        ),
        "turn": state.turn,
        "max_turns": state.max_turns,
    }


setattr(goal_pause, "_server_function_on_invoke", _goal_pause_on_invoke)


@server_function(
    description="Resume a paused goal loop on this session. Re-installs the periodic tick.",
    strict=True,
    name_override="goal.resume",
)
async def goal_resume(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_resume_on_invoke(service, session, args=None):
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}
    state = _goals.get(sid)
    if state is None:
        state = await maybe_rehydrate(service, session)
    if state is None:
        return {"ok": False, "message": "No goal is running on this session."}
    if state.completed or state.cancelled:
        return {
            "ok": False,
            "message": f"Goal is {_status_label(state)}; can't resume.",
        }
    if not state.paused:
        return {"ok": True, "message": "Goal is already active.", "paused": False}
    state.paused = False
    await _resume_goal(service, session, state)
    return {
        "ok": True,
        "message": (f"Goal resumed at turn {state.turn}/{state.max_turns}."),
        "turn": state.turn,
        "max_turns": state.max_turns,
    }


setattr(goal_resume, "_server_function_on_invoke", _goal_resume_on_invoke)


# ---------------------------------------------------------------------------
# Ink slash entry: /goal <goal text> -> start a new loop.
# /goal.stop / /goal.status / /goal.pause / /goal.resume are reachable
# as slash commands via their dotted names and via UI buttons.
# ---------------------------------------------------------------------------


@server_function(
    description="Start an autonomous goal loop. Slash form: /goal <goal text>.",
    strict=True,
    name_override="goal",
)
async def goal_alias(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_alias_on_invoke(service, session, args=None):
    args = args or {}
    return await _goal_start_on_invoke(
        service,
        session,
        {
            "goal": args.get("text") or args.get("goal"),
            "max_turns": args.get("max_turns"),
            "tick_interval": args.get("tick_interval"),
            "safe_tool_overrides": args.get("safe_tool_overrides"),
        },
    )


setattr(goal_alias, "_server_function_on_invoke", _goal_alias_on_invoke)
