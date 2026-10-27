"""Tick-driven goal autopilot loop.

A goal is a session-scoped autonomous loop. Composition:

- ``start_impl`` seeds the conversation with the initial framing
  prompt, mirrors goal state into ``session.variables`` for
  consumers/tooling, and installs a periodic **tick** via
  ``session.set_tick``. The tick is what advances the loop — every
  ``tick_interval`` seconds (when the agent is idle) a brief
  notification arrives, prompting the agent to take the next step.
- ``goal_complete`` (builtin tool) flips ``state.completed``; the
  per-session run-end listener observes that at the next ``run_end``
  and tears down the tick + listener.
- Background work wakes the agent naturally via
  ``enqueue_notification``; the tick + completion notifications coalesce
  through the existing flusher, so the agent doesn't get spammed.
- ``stop_impl`` cancels the tick + listener; ``status_impl`` reports
  current turn / status without mutating anything.

No retry-on-error logic, no continuation-after-every-turn enqueue: the
tick handles wake-ups uniformly, and the agent reacts to its own
history when a previous turn errored.

Products expose these impls as server functions / slash commands. The
prompt wording is a product seam with three levels: an explicitly
installed builder (:func:`set_goal_prompt_builders`) always wins; else a
product-packaged template file (``goal_initial.md`` / ``goal_tick.md``
under the manifest's declared prompts dir — see
``omniagents.core.project.prompts``) is rendered with the goal context
(Jinja variables ``goal``, ``max_turns``); else the generic defaults
here apply — they are domain-neutral so the loop is usable without any
installation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .arbitration import register_active_tick, unregister_active_tick

_log = logging.getLogger(__name__)


DEFAULT_MAX_TURNS = 10
DEFAULT_TICK_INTERVAL_S = 5.0

# Arbitration kind registered while a goal loop is live on a session.
TICK_KIND = "goal"

# Single JSON blob mirrored into session.variables for rehydration. The
# legacy per-field variables (goal_text/goal_status/...) are kept alongside
# this for tools/skills that already read them — that's a stable
# consumption surface; this blob is the rehydrate source of truth.
VAR_KEY = "goal_state"


# ---------------------------------------------------------------------------
# Prompt builders — the product seam.
# The framework owns the clock and the state machine; the product owns the
# words. The defaults below are domain-neutral so the loop is usable
# without any installation.
# ---------------------------------------------------------------------------


def default_goal_initial_prompt(goal: str, max_turns: int) -> str:
    """Generic first user message of an autopilot loop. Frames the goal,
    the tool contract for termination (including the optional evidence
    field), and the tick model so the agent knows it's expected to keep
    working between wake-ups."""
    return f"""You are working toward a specific goal across multiple turns. Use your judgment and the available tools to make steady progress until the goal is achieved or the turn budget is exhausted.

## Goal
{goal}

## Reading the goal

Treat the goal text above as your operating contract. A well-written goal names what should be true when done, how to verify it, what must not regress, what's in scope, how to choose the next step between iterations, and what to report if blocked. Re-read it each turn — your history is your durable memory of the contract.

## Approach

1. **Explore** — Gather the context you need to act.
2. **Plan** — Break the goal into concrete, verifiable steps.
3. **Execute** — Use the available tools to carry out each step.
4. **Verify** — Check the results against whatever verification surface the goal names.
5. **Iterate** — Each turn picks up from session history. Front-load exploration and batch related work.

## Operating Context

- The user has delegated the work to you. When ambiguity is material, document your interpretation in your response and proceed with the most reasonable approach.
- Prefer making progress over waiting for input.

## How the loop wakes you

A periodic tick wakes you between turns. You'll receive a short tick notification when the loop wants you to take the next step. You'll also wake automatically when background work completes. If you have nothing left to do, call `goal_complete`.

## Termination

Call the `goal_complete` tool once the goal is achieved and verified:

    goal_complete(reason="achieved", evidence="<what you checked and what it showed>")

Call it also if you reach a true blocker (missing credentials, external system unavailable, contradictory requirements):

    goal_complete(reason="blocked", evidence="<what's missing and what would unblock progress>")

The ``evidence`` field is auditable — cite the specific check you ran and its result, the artifact you produced, or the specific blocker. The tool call stops the loop — no further tick notifications will arrive.

## Budget

You have up to {max_turns} turns before the loop self-terminates. Reaching the budget is not the same as completing the goal — if it expires, summarize progress and remaining work in your final turn.
""".strip()


def default_goal_tick_prompt(*, goal: str, max_turns: int) -> str:
    """Generic periodic-tick notification body. Short by design — the
    agent already has the goal + context in history; this is just a poke
    to take the next step."""
    return (
        f"[goal] Tick. Goal: {goal} (budget {max_turns} turns total). "
        "Take the next concrete step. Reuse context — don't re-gather what you already have. "
        'If the goal is achieved and verified, call `goal_complete(reason="achieved", evidence="...")`. '
        "If you're truly blocked on something requiring human input, "
        'call `goal_complete(reason="blocked", evidence="...")`. '
        "For long-running work, start it in the background and end your turn — "
        "the completion notification will wake you."
    )


_UNSET: Any = object()

_initial_prompt_builder: Callable[[str, int], str] = default_goal_initial_prompt
_tick_prompt_builder: Callable[..., str] = default_goal_tick_prompt


def set_goal_prompt_builders(
    *,
    initial: Optional[Callable[[str, int], str]] = _UNSET,
    tick: Optional[Callable[..., str]] = _UNSET,
) -> None:
    """Install product prompt builders for the goal loop.

    - ``initial(goal, max_turns) -> str`` builds the first user message.
    - ``tick(*, goal, max_turns) -> str`` builds the periodic tick body.

    Pass ``None`` to restore the generic default for that builder; omit
    a keyword to leave it unchanged.
    """
    global _initial_prompt_builder, _tick_prompt_builder
    if initial is not _UNSET:
        _initial_prompt_builder = initial or default_goal_initial_prompt
    if tick is not _UNSET:
        _tick_prompt_builder = tick or default_goal_tick_prompt


def _render_product_template(name: str, **context: Any) -> Optional[str]:
    """Render the installed product's packaged prompt template *name*
    with *context*, or None when no product/template exists. Best-effort:
    a broken manifest or template degrades to the generic default."""
    try:
        from omniagents.core.project.prompts import (
            product_prompt_template,
            render_prompt_template,
        )

        text = product_prompt_template(name)
        if text is None:
            return None
        return render_prompt_template(text, **context)
    except Exception:
        return None


def build_initial_prompt(goal: str, max_turns: int) -> str:
    if _initial_prompt_builder is not default_goal_initial_prompt:
        return _initial_prompt_builder(goal, max_turns)
    rendered = _render_product_template(
        "goal_initial.md", goal=goal, max_turns=max_turns
    )
    if rendered is not None:
        return rendered
    return default_goal_initial_prompt(goal, max_turns)


def build_tick_prompt(*, goal: str, max_turns: int) -> str:
    if _tick_prompt_builder is not default_goal_tick_prompt:
        return _tick_prompt_builder(goal=goal, max_turns=max_turns)
    rendered = _render_product_template("goal_tick.md", goal=goal, max_turns=max_turns)
    if rendered is not None:
        return rendered
    return default_goal_tick_prompt(goal=goal, max_turns=max_turns)


# ---------------------------------------------------------------------------
# State.
# ---------------------------------------------------------------------------


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
    # the goal loop is on hold. Resume will re-install the tick.
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
    tick_kwargs: dict[str, Any] = {
        "source": "goal.tick",
    }
    if state.safe_tool_overrides is not None:
        tick_kwargs["safe_tool_overrides"] = state.safe_tool_overrides
    session.set_tick(
        state.tick_interval,
        build_tick_prompt(goal=state.goal, max_turns=state.max_turns),
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
    if sid:
        unregister_active_tick(sid, TICK_KIND)
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
    keep the run-end listener attached so resume can re-install the tick
    cleanly. _on_run_end no-ops while state.paused is True."""
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
    but no loop machinery comes back. Called lazily from status /
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
    register_active_tick(sid, TICK_KIND)

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
# Impls — the full command bodies. Products expose these as server
# functions / slash commands.
# ---------------------------------------------------------------------------


async def start_impl(service, session, args=None):
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
    register_active_tick(sid, TICK_KIND)

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
        unregister_active_tick(sid, TICK_KIND)
        service.remove_run_end_listener(sid, _listener)
        return {"ok": False, "message": f"Failed to schedule goal tick: {e}"}

    # Kick off the first run with the framing prompt.
    initial = build_initial_prompt(goal_text, max_turns)
    try:
        await service.enqueue_message(
            session_id=sid,
            content=initial,
            role="user",
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
        unregister_active_tick(sid, TICK_KIND)
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


async def stop_impl(service, session, args=None):
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


async def status_impl(service, session, args=None):
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
    assert snap is not None  # state is non-None here
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


async def pause_impl(service, session, args=None):
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


async def resume_impl(service, session, args=None):
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
