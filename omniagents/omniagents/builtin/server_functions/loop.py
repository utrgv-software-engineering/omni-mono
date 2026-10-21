"""Builtin server functions for recurring ``/loop`` and ``/cron`` tasks.

The machinery (task state, timers, persistence, rehydration, broadcast)
lives in ``omniagents.core.autonomy.loop``. This module keeps the ``@server_function`` registrations that parse
slash arguments and delegate to the framework impls. Products install
their prompt builders via ``set_loop_prompt_builders`` and (optionally)
a default-prompt source via ``set_loop_default_prompt_resolver`` —
without one, ``/loop`` requires explicit prompt text.
"""

from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.execution import require_request_context
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

from omniagents.core.autonomy import split_loop_interval_and_prompt
from omniagents.core.autonomy.loop import (  # noqa: F401  (re-exported surface)
    VAR_KEY,
    LoopTask,
    _listeners,
    _tasks,
    _teardown_task,
    clear_impl,
    continue_impl,
    create_impl,
    delete_impl,
    loop_snapshot_dict,
    maybe_rehydrate,
    resolve_default_loop_prompt_in_workspace,
    _format_status,
)


from omniagents.core.server_functions.slash import slash_command, text as slash_text


@slash_command(
    description="Recurring prompt: start one, or status / stop [id] / clear",
    usage="[<interval>] <prompt> | status | stop [id] | clear",
    args=slash_text(),
    aliases=("loops",),
    during_run=False,
    order=40,
)
@server_function(
    description="Run a prompt on a recurring loop. Slash form: /loop [interval] [prompt].",
    strict=True,
    name_override="loop",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.OPTIONAL_WORKSPACE_READ,
)
async def loop_alias(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _loop_alias_on_invoke(service, session, args=None):
    """One verb: bare `/loop` lists, `stop [id]` / `clear` act, anything
    else is `[interval] <prompt>` to start."""
    args = args or {}
    text = str(args.get("text") or args.get("prompt") or "")
    head, _, rest = text.strip().partition(" ")
    action = head.lower()
    if action in ("", "status"):
        return await _loop_status_on_invoke(service, session, {})
    if action == "stop":
        return await _loop_stop_on_invoke(service, session, {"text": rest.strip()})
    if action == "clear":
        return await _loop_clear_on_invoke(service, session, {})
    interval_seconds, prompt, _token = split_loop_interval_and_prompt(text)
    if not prompt:
        lease = require_request_context().environment_lease
        prompt = (
            await resolve_default_loop_prompt_in_workspace(
                lease.workspace if lease is not None else None
            )
            or ""
        )
    if not prompt:
        return {
            "ok": False,
            "message": (
                "Usage: /loop [interval] <prompt> — no default loop prompt "
                "is configured for this deployment."
            ),
        }
    dynamic = interval_seconds is None
    return await create_impl(
        service,
        session,
        prompt=prompt,
        interval_seconds=interval_seconds,
        dynamic=dynamic,
        recurring=True,
    )


setattr(loop_alias, "_server_function_on_invoke", _loop_alias_on_invoke)


@server_function(
    description="List scheduled loop tasks on this session.",
    strict=True,
    name_override="loop.status",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def loop_status(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _loop_status_on_invoke(service, session, args=None):
    sid = getattr(session, "id", None)
    tasks = await maybe_rehydrate(service, session)
    return {
        "ok": True,
        "active": bool(tasks),
        "snapshot": loop_snapshot_dict(sid) if sid else [],
        "message": _format_status(tasks),
    }


setattr(loop_status, "_server_function_on_invoke", _loop_status_on_invoke)


@server_function(
    description="Cancel a scheduled loop task by id.",
    strict=True,
    name_override="loop.stop",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def loop_stop(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _loop_stop_on_invoke(service, session, args=None):
    args = args or {}
    task_id = str(args.get("text") or args.get("task_id") or "").strip()
    tasks = await maybe_rehydrate(service, session)
    if not task_id:
        if len(tasks) == 1:
            task_id = next(iter(tasks))
        else:
            return {
                "ok": False,
                "message": "Usage: /loop stop <task_id>\n" + _format_status(tasks),
                "snapshot": loop_snapshot_dict(getattr(session, "id", "")),
            }
    return await delete_impl(service, session, task_id=task_id)


setattr(loop_stop, "_server_function_on_invoke", _loop_stop_on_invoke)


@server_function(
    description="Cancel all scheduled loop tasks on this session.",
    strict=True,
    name_override="loop.clear",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def loop_clear(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _loop_clear_on_invoke(service, session, args=None):
    return await clear_impl(service, session)


setattr(loop_clear, "_server_function_on_invoke", _loop_clear_on_invoke)


@server_function(
    description="Create a scheduled prompt from a cron expression.",
    strict=True,
    name_override="cron.create",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def cron_create(
    cron: str, prompt: str, recurring: bool = True, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _cron_create_on_invoke(service, session, args=None):
    args = args or {}
    return await create_impl(
        service,
        session,
        prompt=str(args.get("prompt") or ""),
        cron=str(args.get("cron") or ""),
        recurring=bool(args.get("recurring", True)),
    )


setattr(cron_create, "_server_function_on_invoke", _cron_create_on_invoke)


@server_function(
    description="List scheduled cron/loop tasks on this session.",
    strict=True,
    name_override="cron.list",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def cron_list(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(cron_list, "_server_function_on_invoke", _loop_status_on_invoke)


@server_function(
    description="Delete a scheduled cron/loop task by id.",
    strict=True,
    name_override="cron.delete",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def cron_delete(task_id: str, _session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _cron_delete_on_invoke(service, session, args=None):
    args = args or {}
    return await delete_impl(
        service,
        session,
        task_id=str(args.get("task_id") or args.get("text") or "").strip(),
    )


setattr(cron_delete, "_server_function_on_invoke", _cron_delete_on_invoke)
