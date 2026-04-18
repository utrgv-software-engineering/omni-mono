from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .local import LocalAgent


@dataclass
class WorkerHandle:
    agent: Any
    run_id: str
    session_id: str
    done_event: asyncio.Event
    monitor_task: asyncio.Task | None
    status: str = "running"
    result: str | None = None
    error: str | None = None


class WorkerManager:
    def __init__(
        self,
        *,
        spec_loader: Callable[[str | None], tuple[Any, dict | None]],
        agent_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._spec_loader = spec_loader
        self._agent_factory = agent_factory or (
            lambda spec: LocalAgent(spec=spec, settings={})
        )
        self._workers: dict[str, WorkerHandle] = {}

    @property
    def workers(self) -> dict[str, WorkerHandle]:
        return self._workers

    async def _monitor_worker(self, handle: WorkerHandle) -> None:
        agent = handle.agent
        try:
            await agent._ensure_client()
        except Exception:
            handle.status = "error"
            handle.error = "Failed to connect to worker agent."
            handle.done_event.set()
            return

        while True:
            try:
                msg = await agent._event_queue.get()
            except Exception:
                handle.status = "error"
                handle.error = "Event queue closed unexpectedly."
                handle.done_event.set()
                return

            method = msg.get("method", "")
            params = msg.get("params", {})

            if method == "client_request":
                request_id = params.get("request_id")
                if request_id:
                    try:
                        await agent._client.other.client_response(
                            request_id=request_id, ok=True, result={"approved": True}
                        )
                    except Exception:
                        pass
                continue

            if method == "message_output":
                handle.result = params.get("content", "")
                continue

            if method != "run_end":
                continue

            if params.get("run_id") != handle.run_id:
                continue

            error_info = params.get("error")
            end_reason = params.get("end_reason", "completed")
            if error_info:
                handle.status = "error"
                msg_text = (
                    error_info.get("message", "")
                    if isinstance(error_info, dict)
                    else str(error_info)
                )
                handle.error = msg_text
                if not handle.result:
                    handle.result = f"[error] {msg_text}"
            elif end_reason == "cancelled":
                handle.status = "cancelled"
                if not handle.result:
                    handle.result = "[cancelled]"
            else:
                handle.status = "completed"
                if not handle.result:
                    handle.result = "Worker completed but produced no output."

            handle.done_event.set()
            return

    async def _create_worker(
        self,
        *,
        worker_id: str,
        task_description: str,
        model: str | None,
        session_id: str | None = None,
        context: dict | None = None,
    ) -> WorkerHandle:
        spec, _runtime = self._spec_loader(model)
        variables = getattr(spec, "variables", None)
        if variables is None:
            spec.variables = {}
        elif not isinstance(variables, dict):
            spec.variables = dict(variables)
        spec.variables["worker_name"] = worker_id

        agent = self._agent_factory(spec)
        await agent.initialize()
        await agent._ensure_client()

        start_kwargs: dict[str, Any] = {
            "prompt": task_description,
            "session_id": session_id,
        }
        if context is not None:
            start_kwargs["context"] = context

        resp = await agent._client.other.start_run(**start_kwargs)
        raw = getattr(resp, "result", resp)
        if isinstance(raw, dict):
            run_id = raw.get("run_id", "")
            sess_id = raw.get("session_id", "")
        else:
            run_id = ""
            sess_id = ""

        handle = WorkerHandle(
            agent=agent,
            run_id=run_id,
            session_id=sess_id,
            done_event=asyncio.Event(),
            monitor_task=None,
        )
        handle.monitor_task = asyncio.create_task(
            self._monitor_worker(handle), name=f"worker-monitor-{run_id or worker_id}"
        )
        return handle

    async def spawn(
        self,
        *,
        worker_id: str,
        task_description: str,
        model: str | None = None,
        context: dict | None = None,
    ) -> WorkerHandle:
        handle = await self._create_worker(
            worker_id=worker_id,
            task_description=task_description,
            model=model,
            context=context,
        )
        self._workers[worker_id] = handle
        return handle

    async def wait(
        self, worker_ids: list[str], timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        completed: dict[str, str | None] = {}
        still_running: list[str] = []
        not_found: list[str] = []
        pending_events: dict[str, asyncio.Event] = {}

        for worker_id in worker_ids:
            handle = self._workers.get(worker_id)
            if handle is None:
                not_found.append(worker_id)
            elif handle.status != "running":
                completed[worker_id] = (
                    handle.result
                    if handle.status == "completed"
                    else f"[{handle.status}] {handle.error}"
                )
            else:
                pending_events[worker_id] = handle.done_event

        if completed or not pending_events:
            still_running.extend(pending_events.keys())
            return {
                "completed": completed,
                "still_running": still_running,
                "not_found": not_found,
            }

        waiters = {
            worker_id: asyncio.create_task(event.wait(), name=f"wait-{worker_id}")
            for worker_id, event in pending_events.items()
        }
        done, _ = await asyncio.wait(
            waiters.values(),
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        done_names = {task.get_name() for task in done}
        for worker_id, waiter in waiters.items():
            if waiter.get_name() in done_names:
                handle = self._workers[worker_id]
                completed[worker_id] = (
                    handle.result
                    if handle.status == "completed"
                    else f"[{handle.status}] {handle.error}"
                )
            else:
                waiter.cancel()
                still_running.append(worker_id)

        return {
            "completed": completed,
            "still_running": still_running,
            "not_found": not_found,
        }

    async def get_status(
        self,
        worker_id: str,
        *,
        tail: int = 5,
        history_loader: (
            Callable[[WorkerHandle, int], Awaitable[list[dict]] | list[dict]] | None
        ) = None,
    ) -> dict[str, Any]:
        handle = self._workers.get(worker_id)
        if handle is None:
            return {"worker_id": worker_id, "status": "not_found"}

        result: dict[str, Any] = {"worker_id": worker_id, "status": handle.status}
        if handle.error is not None:
            result["error"] = handle.error

        history_items: list[dict] = []
        if tail > 0 and history_loader is not None:
            loaded = history_loader(handle, tail)
            history_items = await loaded if inspect_is_awaitable(loaded) else loaded
        if history_items:
            result["history"] = history_items
        return result

    async def send_message(self, worker_id: str, message: str) -> str:
        handle = self._workers.get(worker_id)
        if handle is None:
            return "not_found"
        if handle.status != "running":
            return handle.status
        agent = handle.agent
        await agent._ensure_client()
        await agent._client.other.send_user_message(
            run_id=handle.run_id, content=message
        )
        return "message_sent"

    async def close(self, worker_id: str) -> str:
        handle = self._workers.get(worker_id)
        if handle is None:
            return "not_found"
        if handle.status != "running":
            return handle.status

        agent = handle.agent
        await agent._ensure_client()
        await agent._client.other.stop_run(run_id=handle.run_id)
        try:
            await asyncio.wait_for(handle.done_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        if handle.status == "running":
            handle.status = "cancelled"
        if handle.monitor_task is not None:
            handle.monitor_task.cancel()
        await agent.cleanup()
        return handle.status

    async def resume(
        self, worker_id: str, message: str, context: dict | None = None
    ) -> WorkerHandle | None:
        handle = self._workers.get(worker_id)
        if handle is None or handle.status == "running":
            return None
        old_session_id = handle.session_id
        await handle.agent.cleanup()
        new_handle = await self._create_worker(
            worker_id=worker_id,
            task_description=message,
            model=None,
            session_id=old_session_id,
            context=context,
        )
        new_handle.result = None
        self._workers[worker_id] = new_handle
        return new_handle


def inspect_is_awaitable(value: Any) -> bool:
    return hasattr(value, "__await__")
