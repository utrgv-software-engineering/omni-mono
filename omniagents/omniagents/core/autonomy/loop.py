"""Recurring loop tasks — fixed-interval, cron, and dynamic cadences.

A loop task wakes the agent with a stored prompt on a schedule:

- ``fixed_interval`` — every N seconds (min 60).
- ``fixed_cron`` — on a 5-field cron expression.
- ``dynamic`` — the agent self-paces: each fire asks the agent to call
  the ``loop_continue`` builtin tool with the next delay, or to let the
  loop conclude by not calling it.

State is mirrored into ``session.variables[VAR_KEY]`` so tasks survive
a server restart; ``maybe_rehydrate`` restores the timer tasks lazily.

Products expose the impls here as server functions / slash commands and
may install their own prompt builders via
:func:`set_loop_prompt_builders`; the generic defaults are
domain-neutral.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .arbitration import register_active_tick, unregister_active_tick
from .cron import interval_to_cron, next_cron_fire, parse_cron

_log = logging.getLogger(__name__)

VAR_KEY = "loop_tasks"
SOURCE_PREFIX = "loop.tick"
FINAL_SOURCE_PREFIX = "loop.concluded"
MAX_TASKS = 50
MIN_DYNAMIC_DELAY_SECONDS = 60
MAX_DYNAMIC_DELAY_SECONDS = 3600
DEFAULT_DYNAMIC_DELAY_SECONDS = 600
DEFAULT_EXPIRY_SECONDS = 7 * 24 * 3600

# Arbitration kind registered while a session has live loop tasks.
TICK_KIND = "loop"


# ---------------------------------------------------------------------------
# Prompt builders — the product seam.
# ---------------------------------------------------------------------------


def default_loop_tick_prompt(
    *, task_id: str, prompt: str, fires: int, expires_at: float | None, mode: str
) -> str:
    expiry = ""
    if expires_at is not None:
        expiry = f" Expires at unix time {int(expires_at)}."
    if mode == "dynamic":
        return (
            f"[loop:{task_id}] Scheduled dynamic loop fired (fire #{fires + 1}). "
            f"Prompt: {prompt}\n\n"
            "Take the next concrete step. If the loop should continue, call "
            "`loop_continue` with this task_id, a delay between 60 and 3600 seconds, "
            "and a short reason. If the task is complete, do not call `loop_continue`; "
            "summarize completion and the loop will stop."
            f"{expiry}"
        )
    return (
        f"[loop:{task_id}] Scheduled loop fired (fire #{fires + 1}). Prompt: {prompt}\n\n"
        "Take the requested step. If the underlying task is done and no future checks are needed, "
        "cancel this loop with `/loop.stop {task_id}` or ask the user to cancel it."
        f"{expiry}"
    )


def default_loop_concluded_prompt(*, task_id: str, prompt: str, fires: int) -> str:
    return (
        f"[loop:{task_id}] Loop concluded after {fires} fires. Prompt: {prompt}\n\n"
        "The recurring schedule has ended on its own. Summarize what you observed and stop; "
        "no further loop wakeups will arrive unless a new loop is scheduled."
    )


_UNSET: Any = object()

_tick_prompt_builder: Callable[..., str] = default_loop_tick_prompt
_concluded_prompt_builder: Callable[..., str] = default_loop_concluded_prompt


def set_loop_prompt_builders(
    *,
    tick: Optional[Callable[..., str]] = _UNSET,
    concluded: Optional[Callable[..., str]] = _UNSET,
) -> None:
    """Install product prompt builders for loop tasks.

    - ``tick(*, task_id, prompt, fires, expires_at, mode) -> str`` builds
      the per-fire notification body.
    - ``concluded(*, task_id, prompt, fires) -> str`` builds the final
      notification when a loop expires or exhausts.

    Pass ``None`` to restore the generic default for that builder; omit
    a keyword to leave it unchanged.
    """
    global _tick_prompt_builder, _concluded_prompt_builder
    if tick is not _UNSET:
        _tick_prompt_builder = tick or default_loop_tick_prompt
    if concluded is not _UNSET:
        _concluded_prompt_builder = concluded or default_loop_concluded_prompt


def build_tick_prompt(
    *, task_id: str, prompt: str, fires: int, expires_at: float | None, mode: str
) -> str:
    return _tick_prompt_builder(
        task_id=task_id, prompt=prompt, fires=fires, expires_at=expires_at, mode=mode
    )


def build_concluded_prompt(*, task_id: str, prompt: str, fires: int) -> str:
    return _concluded_prompt_builder(task_id=task_id, prompt=prompt, fires=fires)


# The default-prompt resolver supplies the loop body when ``/loop`` is
# invoked with an interval but no prompt text. An explicitly installed
# resolver (product policy) wins; otherwise the conventional override
# chain applies: ``<workspace>/.<prog>/loop.md`` (operator, workspace-
# local) → ``<config-dir>/loop.md`` (operator, per-user) → the product's
# packaged ``loop.md`` template (``paths.prompts`` — see
# ``omniagents.core.project.prompts``) → None, meaning "/loop requires
# an explicit prompt" — and the slash handler reports usage.
_default_prompt_resolver: Optional[Callable[[Optional[str]], Optional[str]]] = None


def set_loop_default_prompt_resolver(
    resolver: Optional[Callable[[Optional[str]], Optional[str]]],
) -> None:
    """Install the product's default-loop-prompt resolver.

    ``resolver(workspace_root)`` returns the prompt text to use when a
    loop is created without one, or None when no default exists. Pass
    ``None`` to restore the conventional file chain."""
    global _default_prompt_resolver
    _default_prompt_resolver = resolver


def _conventional_default_loop_prompt(
    workspace_root: Optional[str],
) -> Optional[str]:
    """The conventional loop-body chain: workspace override file → user
    config file → packaged product template → None.

    The workspace override dir name derives from the installed product's
    CLI prog (``.<prog>/loop.md`` — omni-code's historical ``.omni/``);
    the user-config candidate is ``product_config_dir()/loop.md``."""
    from pathlib import Path

    from omniagents.core.config import product_config_dir
    from omniagents.core.product import product_info
    from omniagents.core.project.prompts import product_prompt_template
    from omniagents.core.utils.prompt_files import resolve_prompt_file

    candidates: list[Path] = []
    if workspace_root:
        prog = (product_info().prog or "").strip()
        if prog:
            candidates.append(
                Path(workspace_root).expanduser() / f".{prog}" / "loop.md"
            )
    candidates.append(product_config_dir() / "loop.md")
    packaged = product_prompt_template("loop.md") or ""
    return resolve_prompt_file(candidates, packaged) or None


def resolve_default_loop_prompt(workspace_root: Optional[str]) -> Optional[str]:
    try:
        if _default_prompt_resolver is not None:
            return _default_prompt_resolver(workspace_root)
        return _conventional_default_loop_prompt(workspace_root)
    except Exception:
        return None


async def resolve_default_loop_prompt_in_workspace(workspace) -> Optional[str]:
    """Resolve the loop prompt through an explicitly routed Workspace.

    The synchronous resolver remains the product/config API. Server functions
    use this adapter-aware form so a sandbox path such as ``/workspace`` is
    never handed to the AgentHost's own :mod:`pathlib` implementation.
    """

    if _default_prompt_resolver is not None:
        return resolve_default_loop_prompt(
            workspace.root if workspace is not None else None
        )

    if workspace is not None:
        try:
            from omniagents.core.product import product_info
            from omniagents.core.utils.prompt_files import decode_prompt_bytes

            prog = (product_info().prog or "").strip()
            if prog:
                data = await workspace.read_bytes(f".{prog}/loop.md")
                text = decode_prompt_bytes(data)
                if text:
                    return text
        except Exception:
            pass

    # User config and packaged product prompts belong to the AgentHost, so the
    # existing synchronous resolver is correct for those two fallback layers.
    return resolve_default_loop_prompt(None)


# ---------------------------------------------------------------------------
# State.
# ---------------------------------------------------------------------------


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
                content=build_tick_prompt(
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
    expires: bool = True,
) -> dict:
    """Install a task. ``expires=False`` keeps a recurring task until it is
    cancelled, for standing schedules (a reminder every weekday) rather than
    watches; the default bounds recurring tasks to ``DEFAULT_EXPIRY_SECONDS``."""
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
        expires_at=time.time() + DEFAULT_EXPIRY_SECONDS if recurring and expires else None,
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
                content=build_concluded_prompt(
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
        register_active_tick(sid, TICK_KIND)
    except Exception as e:
        _log.warning("loop add_run_end_listener failed: %s", e)


def _maybe_remove_listener(service, session_id: str) -> None:
    if _tasks.get(session_id):
        return
    listener = _listeners.pop(session_id, None)
    if listener is None:
        return
    unregister_active_tick(session_id, TICK_KIND)
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
