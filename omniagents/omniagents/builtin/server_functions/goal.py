"""Builtin server functions for the tick-driven ``/goal`` autopilot loop.

The machinery (state, ticks, run-end listener, rehydration, snapshot
broadcast) lives in ``omniagents.core.autonomy.goal``. This module:

This module keeps the ``@server_function`` registrations (slash
commands / dotted names) that delegate to the framework impls. Products
install their own prompt builders via
``omniagents.core.autonomy.set_goal_prompt_builders`` (generic defaults
apply otherwise).
"""

from __future__ import annotations

from typing import Optional

from omniagents import server_function

from omniagents.core.autonomy.goal import (  # noqa: F401  (re-exported surface)
    DEFAULT_MAX_TURNS,
    DEFAULT_TICK_INTERVAL_S,
    VAR_KEY,
    GoalState,
    _goals,
    goal_snapshot_dict,
    maybe_rehydrate,
    pause_impl,
    resume_impl,
    start_impl,
    status_impl,
    stop_impl,
)
# Backwards-compatible handler aliases (tests and older callers import
# these names from this module).
_goal_start_on_invoke = start_impl
_goal_stop_on_invoke = stop_impl
_goal_status_on_invoke = status_impl
_goal_pause_on_invoke = pause_impl
_goal_resume_on_invoke = resume_impl


# ---------------------------------------------------------------------------
# Server functions — canonical (dotted) + Ink slash aliases.
# ---------------------------------------------------------------------------


@server_function(
    description="Cancel the running goal loop on this session.",
    strict=True,
    name_override="goal.stop",
)
async def goal_stop(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_stop, "_server_function_on_invoke", _goal_stop_on_invoke)


@server_function(
    description="Report the status of the current goal loop on this session.",
    strict=True,
    name_override="goal.status",
)
async def goal_status(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_status, "_server_function_on_invoke", _goal_status_on_invoke)


@server_function(
    description="Pause the active goal loop on this session. Cancels the periodic tick but keeps the goal state and run-end listener so /goal.resume can pick it back up.",
    strict=True,
    name_override="goal.pause",
)
async def goal_pause(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_pause, "_server_function_on_invoke", _goal_pause_on_invoke)


@server_function(
    description="Resume a paused goal loop on this session. Re-installs the periodic tick.",
    strict=True,
    name_override="goal.resume",
)
async def goal_resume(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


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
    return await start_impl(
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
