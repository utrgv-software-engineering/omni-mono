from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from omniagents import server_function

from omni_code.cron_schedule import (
    interval_to_cron,
    next_cron_fire,
    parse_cron,
    split_loop_interval_and_prompt,
)
from omni_code.loop_prompts import (
    build_loop_concluded_prompt,
    build_loop_tick_prompt,
    resolve_default_loop_prompt,
)

_log = logging.getLogger(__name__)

VAR_KEY = "loop_tasks"
SOURCE_PREFIX = "loop.tick"
FINAL_SOURCE_PREFIX = "loop.concluded"
MAX_TASKS = 50
MIN_DYNAMIC_DELAY_SECONDS = 60
MAX_DYNAMIC_DELAY_SECONDS = 3600
DEFAULT_DYNAMIC_DELAY_SECONDS = 600
DEFAULT_EXPIRY_SECONDS = 7 * 24 * 3600


@dataclass
class LoopTask:
    id: str
    prompt: str
    mode: str
    cron: Optional[str] = None
    interval_seconds: Optional[float] = None
    recurring: bool = True
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    next_fire_at: Optional[float] = None
    fires: int = 0
    status: str = "active"
    last_fire_at: Optional[float] = None
    last_reason: Optional[str] = None
    jitter_seconds: float = 0.0
    task: Any = None


_tasks: dict[str, dict[str, LoopTask]] = {}
_listeners: dict[str, Any] = {}


def _serialize_task(task: LoopTask) -> dict:
    return {
        "id": task.id,
        "prompt": task.prompt,
        "mode": task.mode,
        "cron": task.cron,
        "interval_seconds": task.interval_seconds,
        "recurring": task.recurring,
        "created_at": task.created_at,
        "expires_at": task.expires_at,
        "next_fire_at": task.next_fire_at,
        "fires": task.fires,
        "status": task.status,
        "last_fire_at": task.last_fire_at,
        "last_reason": task.last_reason,
        "jitter_seconds": task.jitter_seconds,
    }


def _deserialize_task(data: dict) -> LoopTask:
    return LoopTask(
        id=str(data.get("id") or _new_task_id()),
        prompt=str(data.get("prompt") or ""),
        mode=str(data.get("mode") or "fixed_interval"),
        cron=data.get("cron"),
        interval_seconds=data.get("interval_seconds"),
        recurring=bool(data.get("recurring", True)),
        created_at=float(data.get("created_at") or time.time()),
        expires_at=data.get("expires_at"),
        next_fire_at=data.get("next_fire_at"),
        fires=int(data.get("fires") or 0),
        status=str(data.get("status") or "active"),
        last_fire_at=data.get("last_fire_at"),
        last_reason=data.get("last_reason"),
        jitter_seconds=float(data.get("jitter_seconds") or 0.0),
    )


def _snapshot_task(task: LoopTask) -> dict:
    return _serialize_task(task)


def loop_snapshot_dict(session_id: str) -> list[dict]:
    return [_snapshot_task(task) for task in _tasks.get(session_id, {}).values()]


async def _persist(service, session) -> None:
    sid = getattr(session, "id", None)
    if not sid:
        return
    payload = [
        _serialize_task(task)
        for task in _tasks.get(sid, {}).values()
        if task.status == "active"
    ]
    try:
        await service.update_session_variables(
            session.id, {VAR_KEY: payload or None}, silent=True
        )
    except Exception as e:
        _log.warning("loop variable sync failed: %s", e)


async def _broadcast_loop(service, session) -> None:
    if service is None or session is None:
        return
    sid = getattr(session, "id", None)
    try:
        await service.request_client_function(
            "ui.loop.update",
            {"snapshot": loop_snapshot_dict(sid) if sid else []},
            session=session,
            expect_result=False,
        )
    except Exception as e:
        _log.warning("ui.loop.update broadcast failed: %s", e)


async def maybe_rehydrate(service, session) -> dict[str, LoopTask]:
    sid = getattr(session, "id", None)
    if not sid:
        return {}
    existing = _tasks.get(sid)
    if existing is not None:
        return existing
    raw = None
    try:
        variables = getattr(session, "variables", None)
        if isinstance(variables, dict):
            raw = variables.get(VAR_KEY)
    except Exception:
        raw = None
    tasks: dict[str, LoopTask] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            task = _deserialize_task(item)
            if task.status != "active" or _is_expired(task):
                continue
            tasks[task.id] = task
    _tasks[sid] = tasks
    _ensure_listener(service, session)
    for task in tasks.values():
        _start_task(service, session, task)
    if raw and not tasks:
        await _persist(service, session)
    return tasks


def _start_task(service, session, task: LoopTask) -> None:
    if task.task is not None and not task.task.done():
        return
    task.task = asyncio.create_task(
        _task_loop(service, session, task), name=f"loop-{task.id}"
    )


async def _task_loop(service, session, task: LoopTask) -> None:
    sid = getattr(session, "id", None)
    if not sid:
        return
    try:
        while task.status == "active":
            now = time.time()
            if task.next_fire_at is None:
                task.next_fire_at = _compute_next_fire(task, now)
                await _persist(service, session)
            delay = max(0.0, task.next_fire_at - time.time())
            await asyncio.sleep(delay)
            if task.status != "active":
                return
            if session.current_task and not session.current_task.done():
                task.next_fire_at = time.time() + 5
                continue
            source = _source(task)
            if any(
                n.get("source") == source
                for n in getattr(session, "pending_notifications", [])
            ):
                task.next_fire_at = time.time() + 5
                continue
            if _is_expired(task):
                await _conclude(service, session, task)
                return
            await service.enqueue_notification(
                session_id=sid,
                content=build_loop_tick_prompt(
                    task_id=task.id,
                    prompt=task.prompt,
                    fires=task.fires,
                    expires_at=task.expires_at,
                    mode=task.mode,
                ),
                source=source,
            )
            task.fires += 1
            task.last_fire_at = time.time()
            if not task.recurring:
                await _conclude(service, session, task)
                return
            if task.mode == "dynamic":
                task.next_fire_at = None
            else:
                task.next_fire_at = _compute_next_fire(task, task.last_fire_at)
            await _persist(service, session)
            await _broadcast_loop(service, session)
            if task.mode == "dynamic":
                return
    except asyncio.CancelledError:
        return
    except Exception:
        _log.exception("loop task crashed session=%s task=%s", sid, task.id)


async def continue_impl(
    service, session, *, task_id: str, delay_seconds: float, reason: str
) -> dict:
    tasks = await maybe_rehydrate(service, session)
    task = tasks.get(task_id)
    if task is None or task.status != "active":
        return {"ok": False, "message": f"No active loop task with id {task_id}."}
    if task.mode != "dynamic":
        return {
            "ok": False,
            "message": "loop_continue is only valid for dynamic loop tasks.",
        }
    delay_seconds = float(delay_seconds)
    if (
        delay_seconds < MIN_DYNAMIC_DELAY_SECONDS
        or delay_seconds > MAX_DYNAMIC_DELAY_SECONDS
    ):
        return {"ok": False, "message": "delay_seconds must be between 60 and 3600."}
    task.next_fire_at = time.time() + delay_seconds
    task.last_reason = str(reason or "").strip() or None
    _start_task(service, session, task)
    await _persist(service, session)
    await _broadcast_loop(service, session)
    return {
        "ok": True,
        "message": f"Loop {task_id} will continue in {delay_seconds:g}s.",
        "snapshot": _snapshot_task(task),
    }


async def create_impl(
    service,
    session,
    *,
    prompt: str,
    interval_seconds: Optional[float] = None,
    cron: Optional[str] = None,
    dynamic: bool = False,
    recurring: bool = True,
) -> dict:
    sid = getattr(session, "id", None)
    if not sid:
        return {"ok": False, "message": "No session context."}
    tasks = await maybe_rehydrate(service, session)
    _ensure_listener(service, session)
    if len([task for task in tasks.values() if task.status == "active"]) >= MAX_TASKS:
        return {
            "ok": False,
            "message": f"This session already has {MAX_TASKS} loop tasks.",
        }
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "message": "Loop prompt must be non-empty."}
    mode = "dynamic" if dynamic else "fixed_cron" if cron else "fixed_interval"
    if dynamic:
        next_fire_at = time.time() + DEFAULT_DYNAMIC_DELAY_SECONDS
        interval_seconds = None
        cron = None
    elif cron:
        parse_cron(cron)
        next_fire_at = next_cron_fire(cron)
    else:
        interval_seconds = max(60.0, float(interval_seconds or 0))
        cron = interval_to_cron(interval_seconds)
        next_fire_at = time.time() + interval_seconds
    task_id = _new_task_id()
    task = LoopTask(
        id=task_id,
        prompt=prompt,
        mode=mode,
        cron=cron,
        interval_seconds=interval_seconds,
        recurring=bool(recurring),
        expires_at=time.time() + DEFAULT_EXPIRY_SECONDS if recurring else None,
        next_fire_at=next_fire_at,
        jitter_seconds=_jitter_for_id(task_id, interval_seconds),
    )
    if task.jitter_seconds:
        task.next_fire_at = (task.next_fire_at or time.time()) + task.jitter_seconds
    tasks[task.id] = task
    _start_task(service, session, task)
    await _persist(service, session)
    await _broadcast_loop(service, session)
    return {
        "ok": True,
        "message": _created_message(task),
        "task": _snapshot_task(task),
        "snapshot": loop_snapshot_dict(sid),
    }


async def delete_impl(service, session, *, task_id: str) -> dict:
    tasks = await maybe_rehydrate(service, session)
    task = tasks.get(task_id)
    if task is None:
        return {"ok": False, "message": f"No loop task with id {task_id}."}
    await _teardown_task(service, session, task, status="cancelled")
    return {"ok": True, "message": f"Loop {task_id} cancelled.", "task_id": task_id}


async def clear_impl(service, session) -> dict:
    tasks = await maybe_rehydrate(service, session)
    count = 0
    for task in list(tasks.values()):
        await _teardown_task(
            service, session, task, status="cancelled", broadcast=False
        )
        count += 1
    await _persist(service, session)
    await _broadcast_loop(service, session)
    return {"ok": True, "message": f"Cancelled {count} loop task(s).", "count": count}


async def _teardown_task(
    service, session, task: LoopTask, *, status: str, broadcast: bool = True
) -> None:
    sid = getattr(session, "id", None)
    task.status = status
    runtime_task = task.task
    task.task = None
    if runtime_task is not None and not runtime_task.done():
        runtime_task.cancel()
    if sid:
        _tasks.setdefault(sid, {}).pop(task.id, None)
        _maybe_remove_listener(service, sid)
    await _persist(service, session)
    if broadcast:
        await _broadcast_loop(service, session)


async def _conclude(service, session, task: LoopTask) -> None:
    sid = getattr(session, "id", None)
    if sid:
        try:
            await service.enqueue_notification(
                session_id=sid,
                content=build_loop_concluded_prompt(
                    task_id=task.id, prompt=task.prompt, fires=task.fires
                ),
                source=f"{FINAL_SOURCE_PREFIX}.{task.id}",
            )
        except Exception:
            _log.exception(
                "loop concluded enqueue failed session=%s task=%s", sid, task.id
            )
    await _teardown_task(service, session, task, status="expired", broadcast=True)


def _compute_next_fire(task: LoopTask, after: float) -> float:
    if task.mode == "dynamic":
        return after + DEFAULT_DYNAMIC_DELAY_SECONDS
    if task.cron and task.mode == "fixed_cron":
        return next_cron_fire(task.cron, after) + task.jitter_seconds
    return after + max(60.0, float(task.interval_seconds or 60.0)) + task.jitter_seconds


def _is_expired(task: LoopTask) -> bool:
    return task.expires_at is not None and time.time() >= task.expires_at


def _source(task: LoopTask) -> str:
    return f"{SOURCE_PREFIX}.{task.id}"


def _new_task_id() -> str:
    return hashlib.sha1(f"{time.time_ns()}".encode()).hexdigest()[:8]


def _jitter_for_id(task_id: str, interval_seconds: Optional[float]) -> float:
    if not interval_seconds:
        return 0.0
    window = min(1800.0, max(0.0, interval_seconds / 2.0))
    if window <= 0:
        return 0.0
    return int(task_id[:4], 16) % int(window + 1)


def _created_message(task: LoopTask) -> str:
    if task.mode == "dynamic":
        cadence = "dynamic cadence"
    elif task.interval_seconds:
        cadence = f"every {task.interval_seconds:g}s"
    else:
        cadence = f"cron {task.cron}"
    return f"Loop {task.id} scheduled ({cadence}). Prompt: {task.prompt}"


def _ensure_listener(service, session) -> None:
    sid = getattr(session, "id", None)
    if not sid or sid in _listeners:
        return

    async def _listener(payload):
        try:
            await _on_run_end(service, session, payload)
        except Exception:
            _log.exception("loop run-end listener failed session=%s", sid)

    try:
        service.add_run_end_listener(sid, _listener)
        _listeners[sid] = _listener
    except Exception as e:
        _log.warning("loop add_run_end_listener failed: %s", e)


def _maybe_remove_listener(service, session_id: str) -> None:
    if _tasks.get(session_id):
        return
    listener = _listeners.pop(session_id, None)
    if listener is None:
        return
    try:
        service.remove_run_end_listener(session_id, listener)
    except Exception:
        pass


async def _on_run_end(service, session, payload: dict) -> None:
    sid = getattr(session, "id", None)
    if not sid:
        return
    tasks = list(_tasks.get(sid, {}).values())
    changed = False
    for task in tasks:
        if (
            task.mode == "dynamic"
            and task.status == "active"
            and task.last_fire_at is not None
            and task.next_fire_at is None
        ):
            await _teardown_task(
                service, session, task, status="completed", broadcast=False
            )
            changed = True
    if changed:
        await _persist(service, session)
        await _broadcast_loop(service, session)


def _format_status(tasks: dict[str, LoopTask]) -> str:
    active = [task for task in tasks.values() if task.status == "active"]
    if not active:
        return "No loop tasks are scheduled on this session."
    lines = []
    for task in sorted(active, key=lambda item: item.next_fire_at or 0):
        next_fire = int(task.next_fire_at or 0)
        cadence = (
            "dynamic"
            if task.mode == "dynamic"
            else (
                f"every {task.interval_seconds:g}s"
                if task.interval_seconds
                else f"cron {task.cron}"
            )
        )
        preview = task.prompt.replace("\n", " ")[:80]
        lines.append(
            f"{task.id}: {cadence} · fires={task.fires} · next={next_fire} · {preview}"
        )
    return "\n".join(lines)


def _workspace_root(session) -> str | None:
    try:
        variables = getattr(session, "variables", None)
        if isinstance(variables, dict):
            value = variables.get("workspace_root") or variables.get("cwd")
            if isinstance(value, str) and value.strip():
                return value
    except Exception:
        return None
    return None


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
        prompt = resolve_default_loop_prompt(_workspace_root(session))
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
