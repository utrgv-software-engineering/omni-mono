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
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

from omniagents.core.autonomy.goal import (
    pause_impl,
    resume_impl,
    start_impl,
    status_impl,
    stop_impl,
)


# ---------------------------------------------------------------------------
# Server functions — canonical (dotted) + Ink slash aliases.
# ---------------------------------------------------------------------------


from omniagents.core.server_functions.slash import slash_command, text as slash_text


@server_function(
    description="Cancel the running goal loop on this session.",
    strict=True,
    name_override="goal.stop",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def goal_stop(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_stop, "_server_function_on_invoke", stop_impl)


@server_function(
    description="Report the status of the current goal loop on this session.",
    strict=True,
    name_override="goal.status",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def goal_status(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_status, "_server_function_on_invoke", status_impl)


@server_function(
    description="Pause the active goal loop on this session. Cancels the periodic tick but keeps the goal state and run-end listener so `/goal resume` can pick it back up.",
    strict=True,
    name_override="goal.pause",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def goal_pause(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_pause, "_server_function_on_invoke", pause_impl)


@server_function(
    description="Resume a paused goal loop on this session. Re-installs the periodic tick.",
    strict=True,
    name_override="goal.resume",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def goal_resume(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(goal_resume, "_server_function_on_invoke", resume_impl)


# ---------------------------------------------------------------------------
# Ink slash entry: /goal <goal text> -> start a new loop.
# `/goal stop|status|pause|resume` are reachable
# as slash commands via their dotted names and via UI buttons.
# ---------------------------------------------------------------------------


@slash_command(
    description="Autonomous goal loop: start one, or status / stop / pause / resume",
    usage="[<goal text> | status | stop | pause | resume]",
    args=slash_text(),
    during_run=False,
    order=30,
)
@server_function(
    description="Start an autonomous goal loop. Slash form: /goal <goal text>.",
    strict=True,
    name_override="goal",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def goal_alias(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _goal_alias_on_invoke(service, session, args=None):
    """One verb, Codex-style: a bare `/goal` reports status, a mode word
    (`stop`, `pause`, `resume`, `status`) acts on the running loop, and
    anything else is the goal to start."""
    args = args or {}
    text = str(args.get("text") or args.get("goal") or "").strip()
    action = text.lower()
    if action in ("", "status"):
        return await status_impl(service, session, {})
    if action == "stop":
        return await stop_impl(service, session, {})
    if action == "pause":
        return await pause_impl(service, session, {})
    if action == "resume":
        return await resume_impl(service, session, {})
    return await start_impl(
        service,
        session,
        {
            "goal": text,
            "max_turns": args.get("max_turns"),
            "tick_interval": args.get("tick_interval"),
            "safe_tool_overrides": args.get("safe_tool_overrides"),
        },
    )


setattr(goal_alias, "_server_function_on_invoke", _goal_alias_on_invoke)
