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
    resolve_default_loop_prompt,
    workspace_root_from_session,
    _format_status,
)


@server_function(
    description="Run a prompt on a recurring loop. Slash form: /loop [interval] [prompt].",
    strict=True,
    name_override="loop",
)
async def loop_alias(
    text: Optional[str] = None, _session_id: Optional[str] = None
) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


async def _loop_alias_on_invoke(service, session, args=None):
    args = args or {}
    text = str(args.get("text") or args.get("prompt") or "")
    interval_seconds, prompt, _token = split_loop_interval_and_prompt(text)
    if not prompt:
        prompt = resolve_default_loop_prompt(workspace_root_from_session(session)) or ""
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
                "message": "Usage: /loop.stop <task_id>\n" + _format_status(tasks),
                "snapshot": loop_snapshot_dict(getattr(session, "id", "")),
            }
    return await delete_impl(service, session, task_id=task_id)


setattr(loop_stop, "_server_function_on_invoke", _loop_stop_on_invoke)


@server_function(
    description="Cancel all scheduled loop tasks on this session.",
    strict=True,
    name_override="loop.clear",
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
)
async def cron_list(_session_id: Optional[str] = None) -> dict:
    return {"ok": False, "error": "use on_invoke handler"}


setattr(cron_list, "_server_function_on_invoke", _loop_status_on_invoke)


@server_function(
    description="Delete a scheduled cron/loop task by id.",
    strict=True,
    name_override="cron.delete",
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
