from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeGuard, cast

from .local import LocalAgent

logger = logging.getLogger(__name__)


def _default_worker_safety(spec: Any, parent_service: Any = None) -> bool:
    """Enable review unless auto-approval was explicitly configured."""
    from omniagents.core.agents.specs import SafeAgentOptions

    opts = (
        copy.deepcopy(getattr(spec, "safe_agent_options", None)) or SafeAgentOptions()
    )
    parent_spec = getattr(parent_service, "spec", None)
    parent_opts = getattr(parent_spec, "safe_agent_options", None)
    auto_approve = bool(
        opts.skip_approvals or getattr(parent_opts, "skip_approvals", False)
    )
    parent_security = getattr(parent_spec, "security_config", None) or {}
    security = getattr(spec, "security_config", None) or {}
    if "enforced" in (security.get("safety_mode"), parent_security.get("safety_mode")):
        auto_approve = False
    opts.skip_approvals = auto_approve
    spec.use_safe_agent = True
    spec.safe_agent_options = opts
    return auto_approve


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
    worker_id: str = ""
    parent_session_id: str | None = None
    #: Principal the worker acts as (the parent session's user). Stamped on
    #: the worker agent's in-process channel so its session is CREATED owned
    #: by that user — under enforced authorization an unowned worker thread
    #: would be unreadable by the very user whose agent spawned it.
    principal: Any = None
    parent_service: Any = None
    configure_spec: Callable[[Any], None] | None = None
    #: Session namespace the worker persists under — the PARENT's
    #: (project_slug, agent_slug), not a worker-private one. The parent
    #: serve authorizes and reads worker threads through ITS store; a
    #: private namespace makes them unowned AND itemless from the parent's
    #: perspective.
    project_slug: str | None = None
    agent_slug: str | None = None


# Worker transcript events forwarded to the observer: the narrative
# subset a client needs to render a live mini-transcript. ``token`` is
# deliberately excluded (per-token chatter); approvals/client_requests
# are answered by the monitor, not observed.
RELAYED_WORKER_EVENTS = frozenset(
    {
        "run_started",
        "run_status",
        "message_output",
        "tool_called",
        "tool_result",
        "run_end",
    }
)

# Superset mirrored VERBATIM (payloads keep the worker service's replay
# stamps) to clients viewing the worker's session through the parent serve
# — the launcher's read-only transcript viewer. Adds ``token`` (live
# typing; transient, never journaled) and ``item_updated`` (canonical item
# revisions). Interaction events (approvals, client_requests) are
# deliberately NOT mirrored: the monitor auto-answers those and a
# read-only viewer must not be prompted or double-respond. They are still
# journaled, so any live seq gap self-heals via client resume against the
# shared per-namespace journal.
MIRRORED_WORKER_EVENTS = RELAYED_WORKER_EVENTS | frozenset({"token", "item_updated"})


class WorkerManager:
    def __init__(
        self,
        *,
        spec_loader: Callable[[str | None], tuple[Any, dict | None]],
        agent_factory: Callable[[Any, Any], Any] | None = None,
        on_complete: Callable[["WorkerHandle"], Awaitable[None]] | None = None,
        on_event: Callable[["WorkerHandle", str, dict], Awaitable[None]] | None = None,
        on_session_event: (
            Callable[["WorkerHandle", str, dict], Awaitable[None]] | None
        ) = None,
        host_resources_provider: Callable[[], Any | None] | None = None,
    ) -> None:
        self._spec_loader = spec_loader
        # The factory receives (spec, host_resources): workers join the
        # parent AgentHost's registries so their runs route through the
        # same environments as everything else in the process.
        self._agent_factory = agent_factory or (
            lambda spec, resources: LocalAgent(
                spec=spec, settings={}, host_resources=resources
            )
        )
        self._on_complete = on_complete
        self._on_event = on_event
        self._on_session_event = on_session_event
        self._host_resources_provider = host_resources_provider
        self._workers: dict[str, WorkerHandle] = {}

    async def _finalize(self, handle: WorkerHandle) -> None:
        """Signal terminal state and notify any on_complete observer.

        Called from every terminal path in _monitor_worker (success,
        cancellation, error, queue close, connection failure) so the
        notification fires regardless of how the worker ended."""
        handle.done_event.set()
        if self._on_complete is not None:
            try:
                await self._on_complete(handle)
            except Exception:
                # Notification failure must never poison the worker
                # lifecycle. Caller logs through its own path.
                pass

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
            await self._finalize(handle)
            return

        while True:
            try:
                msg = await agent._event_queue.get()
            except Exception:
                handle.status = "error"
                handle.error = "Event queue closed unexpectedly."
                await self._finalize(handle)
                return

            method = msg.get("method", "")
            params = msg.get("params", {})

            # Mirror the session-event superset to viewers of the worker's
            # own thread (verbatim, stamps intact) before anything else —
            # a mirror failure must never affect the lifecycle.
            if self._on_session_event is not None and method in MIRRORED_WORKER_EVENTS:
                try:
                    await self._on_session_event(handle, method, params)
                except Exception:
                    pass

            # Stream the narrative subset to the observer (parent session)
            # before local handling — the transcript relay must see run_end
            # too, and a relay failure must never affect the lifecycle.
            if self._on_event is not None and method in RELAYED_WORKER_EVENTS:
                try:
                    await self._on_event(handle, method, params)
                except Exception:
                    pass

            if method == "client_request":
                # Generic client_request from server (artifacts, status, etc.).
                # Workers auto-acknowledge so the run is not blocked.
                request_id = params.get("request_id")
                if request_id:
                    try:
                        await agent._client.other.client_response(
                            request_id=request_id, ok=True, result={"ack": True}
                        )
                    except Exception:
                        pass
                continue

            if method in {"tool_approval_requested", "mcp_approval_requested"}:
                # Reviews are resolved by the service (Guardian, then parent
                # UI). Without a parent UI the request remains pending.
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

            await self._finalize(handle)
            return

    async def _create_worker(
        self,
        *,
        worker_id: str,
        task_description: str,
        model: str | None,
        session_id: str | None = None,
        context: dict | None = None,
        parent_session_id: str | None = None,
        principal: Any = None,
        parent_service: Any = None,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        configure_spec: Callable[[Any], None] | None = None,
    ) -> WorkerHandle:
        spec, _runtime = self._spec_loader(model)
        spec = copy.copy(spec)
        if configure_spec is not None:
            spec = copy.copy(spec)
            configure_spec(spec)
        auto_approve = _default_worker_safety(spec, parent_service)
        # Persist the worker in the spawning agent's session namespace (see
        # WorkerHandle.project_slug). The spec drives the worker service's
        # SessionManager slugs, so stamping it here is the one chokepoint.
        if project_slug is not None:
            spec.project_slug = project_slug
        if agent_slug is not None:
            spec.agent_slug = agent_slug
        variables = getattr(spec, "variables", None)
        if variables is None:
            spec.variables = {}
        elif not isinstance(variables, dict):
            spec.variables = dict(variables)
        spec.variables["worker_name"] = worker_id

        resources = (
            self._host_resources_provider()
            if self._host_resources_provider is not None
            else None
        )
        agent = self._agent_factory(spec, resources)
        await agent.initialize()
        from omniagents.core.agents.worker_approvals import WorkerApprovalContext

        worker_service = getattr(agent, "_agent_service", None)
        if worker_service is not None:
            worker_service._worker_approval_context = WorkerApprovalContext(
                parent_service, parent_session_id, worker_id, auto_approve
            )
        await agent._ensure_client()
        client = agent._client
        assert client is not None  # _ensure_client() guarantees a live client

        # The worker acts AS the spawning user: attach the parent principal
        # to the worker's in-process channel BEFORE start_run, so the worker
        # session is created owned by that user and its thread stays
        # readable to them under enforced authorization. This MUST go
        # through ``set_principal`` (the stored channel object) — reading
        # ``service.channel`` here would hit the ContextVar property and
        # return THIS task's channel, not the worker's.
        if principal is not None:
            setter = getattr(agent, "set_principal", None)
            if callable(setter):
                setter(principal)

        # Execution derives ONLY from a routed environment lease. The
        # spawner names the environment (the parent's own, or a worktree
        # environment it registered on the shared AgentHost) via the
        # context; the worker's run selects it explicitly. No environment
        # named -> explicitly targetless (thinking-only worker).
        environment_id: str | None = None
        if isinstance(context, dict):
            raw = context.get("environment_id")
            if isinstance(raw, str) and raw.strip():
                environment_id = raw

        environment_selection: dict[str, Any] = (
            {"mode": "explicit", "environment_id": environment_id}
            if environment_id is not None
            else {"mode": "none"}
        )

        start_kwargs: dict[str, Any] = {
            "prompt": task_description,
            "environment_selection": environment_selection,
            "session_id": session_id,
        }
        start_kwargs["context"] = {
            **(context or {}),
            "worker_id": worker_id,
            "parent_session_id": parent_session_id,
        }

        # Explicit resumption restores workers archived by older runtimes.
        # New workers use source=worker for visibility, never lifecycle archive.
        try:
            if session_id:
                await client.other.update_thread(thread_id=session_id, status="active")
            resp = await client.other.start_run(**start_kwargs)
        except BaseException:
            await agent.cleanup()
            raise
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
            worker_id=worker_id,
            parent_session_id=parent_session_id,
            principal=principal,
            parent_service=parent_service,
            project_slug=project_slug,
            agent_slug=agent_slug,
            configure_spec=configure_spec,
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
        parent_session_id: str | None = None,
        principal: Any = None,
        parent_service: Any = None,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        configure_spec: Callable[[Any], None] | None = None,
    ) -> WorkerHandle:
        handle = await self._create_worker(
            worker_id=worker_id,
            task_description=task_description,
            model=model,
            context=context,
            parent_session_id=parent_session_id,
            principal=principal,
            parent_service=parent_service,
            project_slug=project_slug,
            agent_slug=agent_slug,
            configure_spec=configure_spec,
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
            if inspect_is_awaitable(loaded):
                history_items = await loaded
            else:
                # TypeGuard doesn't narrow the negative branch.
                history_items = cast("list[dict]", loaded)
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
            parent_session_id=handle.parent_session_id,
            principal=handle.principal,
            parent_service=handle.parent_service,
            project_slug=handle.project_slug,
            agent_slug=handle.agent_slug,
            configure_spec=handle.configure_spec,
        )
        new_handle.result = None
        self._workers[worker_id] = new_handle
        return new_handle


def inspect_is_awaitable(value: Any) -> TypeGuard[Awaitable[Any]]:
    return hasattr(value, "__await__")
