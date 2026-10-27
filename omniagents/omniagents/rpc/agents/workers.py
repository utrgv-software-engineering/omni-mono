from __future__ import annotations

import asyncio
import copy
import logging
import uuid
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
    #: Model ref the worker was spawned with (the parent session's selected
    #: model), reused on resume so a resumed worker keeps it. None = the
    #: product default.
    model: str | None = None
    journal: Any = None
    record: dict | None = None
    heartbeat_task: asyncio.Task | None = None
    managed_queue: bool = False
    closed: bool = False


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
        self.owner = uuid.uuid4().hex
        self._activation_locks: dict[tuple, asyncio.Lock] = {}
        self._scoped_workers: dict[tuple, WorkerHandle] = {}

    async def _finalize(self, handle: WorkerHandle) -> None:
        """Signal terminal state and notify any on_complete observer.

        Called from every terminal path in _monitor_worker (success,
        cancellation, error, queue close, connection failure) so the
        notification fires regardless of how the worker ended."""
        if handle.journal is not None:
            row = handle.record
            assert row is not None
            handle.record = handle.journal.finish(
                handle.worker_id,
                row["generation"],
                row["owner"],
                handle.status,
                handle.result,
                handle.error,
            )
            handle.status = handle.record["status"]
            if handle.parent_service is not None:
                getter = getattr(handle.parent_service, "get_session", None)
                parent = getter(handle.parent_session_id) if callable(getter) else None
                if parent is not None:
                    try:
                        handle.parent_service._refresh_queue(parent)
                    except Exception:
                        logger.exception(
                            "Worker report persisted; parent wakeup will retry on reconnect"
                        )
        if handle.heartbeat_task is not None:
            handle.heartbeat_task.cancel()
        handle.done_event.set()
        if self._on_complete is not None:
            try:
                await self._on_complete(handle)
            except Exception:
                # Notification failure must never poison the worker
                # lifecycle. Caller logs through its own path.
                pass

    def _scope(self):
        from omniagents.core.agents.service import current_service
        from omniagents.core.session.helpers import current_session_id

        service, sid = current_service(), current_session_id()
        manager = getattr(service, "_session_manager", None)
        if manager is None or sid is None:
            return None
        return manager._project_slug, manager._agent_slug, sid

    @property
    def workers(self) -> dict[str, WorkerHandle]:
        scope = self._scope()
        if scope is None:
            return self._workers
        return {
            key[-1]: value
            for key, value in self._scoped_workers.items()
            if key[:3] == scope
        }

    def _lookup(self, worker_id):
        return self.workers.get(worker_id)

    def _remember(self, handle):
        self._workers[handle.worker_id] = handle
        self._scoped_workers[
            (
                handle.project_slug,
                handle.agent_slug,
                handle.parent_session_id,
                handle.worker_id,
            )
        ] = handle

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

            if method == "run_started" and handle.managed_queue:
                handle.run_id = params.get("run_id", handle.run_id)
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
            if not handle.managed_queue or handle.closed:
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
        journal: Any = None,
        record: dict | None = None,
        queued_only: bool = False,
    ) -> WorkerHandle:
        if journal is not None and not queued_only:
            assert record is not None
            journal.change(
                worker_id, record["generation"], record["owner"], status="ready"
            )
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
        if record is not None:
            start_kwargs["submission_id"] = (
                f"worker-start:{worker_id}:{record['generation']}"
            )
        start_kwargs["context"] = {
            **(context or {}),
            "worker_id": worker_id,
            "parent_session_id": parent_session_id,
        }

        if record is not None and worker_service is not None:
            # Allocate the durable child under its owner's identity before any
            # direct model/config call can materialize it outside the RPC channel.
            child_session = worker_service._session_manager.get_or_create(
                session_id, user_id=record["user_id"]
            )
            if child_session.user_id != record["user_id"]:
                await agent.cleanup()
                raise PermissionError("Worker conversation owner mismatch")
            child_session.context = start_kwargs["context"]
            from omniagents.core.conversation.recorder import ensure_conversation_thread

            ensure_conversation_thread(child_session)

        # Explicit resumption restores workers archived by older runtimes.
        # New workers use source=worker for visibility, never lifecycle archive.
        try:
            if session_id and model and worker_service is not None:
                from omniagents.core.models import model_store

                store = model_store()
                if store is not None and store.single_provider:
                    result = await worker_service.set_session_model(session_id, model)
                    if not result.get("ok"):
                        raise ValueError(
                            "The worker could not inherit the parent model."
                        )
            if session_id and (record is None or record["generation"] > 1):
                await client.other.update_thread(thread_id=session_id, status="active")
            if queued_only:
                await client.other.resume_session(session_id=session_id)
                resp = {"run_id": "", "session_id": session_id}
            else:
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

        if worker_service is not None:
            from omniagents.core.orchestration.subagents import link_worker

            try:
                link_worker(
                    parent_service,
                    parent_session_id,
                    worker_service,
                    sess_id,
                    worker_id,
                )
            except BaseException:
                await agent.cleanup()
                raise

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
            model=model,
            journal=journal,
            record=record,
            managed_queue=worker_service is not None,
        )
        if queued_only:
            handle.status = record["status"] if record else "completed"
            handle.done_event.set()
        if journal is not None and not queued_only:
            assert record is not None
            handle.record = journal.change(
                worker_id,
                record["generation"],
                record["owner"],
                status="running",
                run_id=run_id,
            )
            handle.heartbeat_task = asyncio.create_task(self._heartbeat(handle))
        if worker_service is not None:

            async def dispatch(**kwargs):
                await handle.done_event.wait()
                if handle.closed or (handle.record or {}).get("closed"):
                    raise ValueError("Worker is closed")
                if handle.journal is not None:
                    from omniagents.core.workers.recovery import restore_environment

                    env_id = await restore_environment(
                        resources, handle.journal.get(worker_id)
                    )
                    kwargs["environment_selection"] = (
                        {"mode": "explicit", "environment_id": env_id}
                        if env_id
                        else {"mode": "none"}
                    )
                    handle.record = handle.journal.resume(
                        worker_id, kwargs["submission_id"], kwargs["prompt"], self.owner
                    )
                handle.done_event.clear()
                handle.status, handle.result, handle.error = "running", None, None
                if handle.journal is not None:
                    handle.heartbeat_task = asyncio.create_task(self._heartbeat(handle))
                try:
                    result = await worker_service.start_run(**kwargs)
                    handle.run_id = result["run_id"]
                    if handle.journal is not None and not handle.done_event.is_set():
                        row = handle.record
                        assert row is not None
                        handle.record = handle.journal.change(
                            worker_id,
                            row["generation"],
                            row["owner"],
                            run_id=handle.run_id,
                            status="running",
                        )
                    return result
                except Exception as exc:
                    handle.status, handle.error = "error", str(exc)
                    await self._finalize(handle)
                    raise

            worker_service._worker_queue_dispatch = dispatch
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
        journal: Any = None,
        record: dict | None = None,
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
            session_id=record["session_id"] if record else None,
            journal=journal,
            record=record,
        )
        self._remember(handle)
        return handle

    async def _heartbeat(self, handle):
        try:
            while not handle.done_event.is_set():
                await asyncio.sleep(5)
                row = handle.record
                latest = handle.journal.heartbeat(
                    handle.worker_id, row["generation"], row["owner"]
                )
                if latest.get("cancel_requested"):
                    await handle.agent._client.other.stop_run(run_id=handle.run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker ownership lost; stopping stale execution")
            if handle.agent is not None:
                await handle.agent.cleanup()

    def restore(self, journal, service, project_slug, agent_slug, principal=None):
        from omniagents.core.workers.durable import ACTIVE
        from omniagents.core.workers.recovery import reconcile

        if principal is None and journal.user_id is not None:
            from omniagents.core.providers.auth.base import UserIdentity

            principal = UserIdentity(user_id=journal.user_id)
        for row in reconcile(journal):
            old = self._scoped_workers.get(
                (project_slug, agent_slug, journal.parent_id, row["worker_id"])
            )
            if (
                old is not None
                and old.agent is not None
                and old.journal is not None
                and old.record is not None
            ):
                if (
                    old.journal.parent_id == journal.parent_id
                    and old.record["generation"] == row["generation"]
                    and old.record["owner"] == row["owner"]
                ):
                    old.record = row
                    old.status = row["status"]
                    if row.get("result") is not None:
                        old.result = row["result"]
                    old.error = row.get("error")
                    if not old.managed_queue and old.status not in {
                        "running",
                        "ready",
                        "resuming",
                        "provisioning",
                        "accepted",
                    }:
                        old.done_event.set()
                    continue
            event = (
                old.done_event
                if old is not None and old.session_id == row["session_id"]
                else asyncio.Event()
            )
            if row["status"] not in ACTIVE:
                event.set()
            else:
                event.clear()
            restored = WorkerHandle(
                agent=None,
                run_id=row["run_id"],
                session_id=row["session_id"],
                done_event=event,
                monitor_task=None,
                status=row["status"],
                result=row.get("result"),
                error=row.get("error"),
                worker_id=row["worker_id"],
                parent_session_id=journal.parent_id,
                principal=principal,
                parent_service=service,
                project_slug=project_slug,
                agent_slug=agent_slug,
                journal=journal,
                record=row,
                closed=bool(row.get("closed")),
            )
            self._remember(restored)

    async def wait(
        self, worker_ids: list[str], timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        from omniagents.core.workers.durable import ACTIVE

        completed: dict[str, str | None] = {}
        still_running: list[str] = []
        not_found: list[str] = []
        pending_events: dict[str, asyncio.Event] = {}

        for worker_id in worker_ids:
            handle = self._lookup(worker_id)
            if handle is None:
                not_found.append(worker_id)
            elif handle.status not in ACTIVE:
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
                handle = self._lookup(worker_id)
                assert handle is not None
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
        handle = self._lookup(worker_id)
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

    async def _activate(self, handle):
        """Attach an idle/recovered worker without inventing a prompt or run."""
        key = (
            handle.project_slug,
            handle.agent_slug,
            handle.parent_session_id,
            handle.worker_id,
        )
        async with self._activation_locks.setdefault(key, asyncio.Lock()):
            handle = self._scoped_workers.get(key, handle)
            row = (
                handle.journal.get(handle.worker_id)
                if handle.journal
                else handle.record or {}
            )
            if handle.closed or row.get("closed") or row.get("cleaned"):
                raise ValueError("Worker is closed")
            if handle.agent is not None and getattr(handle.agent, "_initialized", True):
                return handle
            if handle.journal is None:
                raise ValueError("Worker conversation is unavailable")
            from omniagents.core.workers.recovery import restore_environment
            from omniagents.core.workers.recipes import reconstruct
            from omniagents.core.workers.durable import ACTIVE

            if row["status"] in ACTIVE:
                raise ValueError("Worker execution is still owned by another runtime")
            resources = (
                self._host_resources_provider()
                if self._host_resources_provider
                else None
            )
            env_id = await restore_environment(resources, row)
            configure = reconstruct(
                row["recipe"],
                handle.parent_service,
                handle.parent_session_id,
                worker_id=handle.worker_id,
            )
            restored = await self._create_worker(
                worker_id=handle.worker_id,
                task_description="",
                model=handle.model,
                session_id=handle.session_id,
                context={
                    "environment_id": env_id,
                    "workspace_root": row["environment"].get("root"),
                },
                parent_session_id=handle.parent_session_id,
                principal=handle.principal,
                parent_service=handle.parent_service,
                project_slug=handle.project_slug,
                agent_slug=handle.agent_slug,
                configure_spec=configure,
                journal=handle.journal,
                record=row,
                queued_only=True,
            )
            self._remember(restored)
            return restored

    async def drain_queued(self, worker_id):
        handle = self._lookup(worker_id)
        if handle is None:
            return
        handle = await self._activate(handle)
        service = handle.agent._agent_service
        session = service.get_session(handle.session_id)
        service._refresh_queue(session)
        service._ensure_drainer(session)

    async def send_message(
        self, worker_id: str, message: str, operation_id: str | None = None
    ) -> str:
        handle = self._lookup(worker_id)
        if handle is None:
            return "not_found"
        if handle.journal is not None or handle.managed_queue:
            handle = await self._activate(handle)
            service = handle.agent._agent_service
            result = await service.enqueue_message(
                handle.session_id,
                message,
                source="worker.message",
                submission_id=operation_id or uuid.uuid4().hex,
            )
            if not result.get("ok"):
                raise ValueError(result.get("reason", "Message admission failed"))
            return "message_queued"
        # Compatibility for custom transports without an AgentService.
        if handle.status != "running":
            return handle.status
        await handle.agent._ensure_client()
        await handle.agent._client.other.send_user_message(
            run_id=handle.run_id, content=message
        )
        return "message_sent"

    async def close(self, worker_id: str) -> str:
        handle = self._lookup(worker_id)
        if handle is None:
            return "not_found"
        if handle.journal is not None or handle.managed_queue:
            handle.closed = True
            if handle.journal is not None:
                handle.record = handle.journal.close(worker_id)

                def reject_pending(state, receipt):
                    for entry in state.get("items", []):
                        entry["owner"] = None
                        entry["item"].update(
                            state="failed", error="Worker closed by parent"
                        )
                    return None, None

                handle.journal.store.mutate_queue(handle.session_id, reject_pending)
            if handle.agent is not None:
                service = handle.agent._agent_service
                session = service.get_session(handle.session_id)
                if session.drainer_task:
                    session.drainer_task.cancel()
                await handle.agent._ensure_client()
                if session.current_task and not session.current_task.done():
                    await handle.agent._client.other.stop_run(run_id=handle.run_id)
                    try:
                        await asyncio.wait_for(handle.done_event.wait(), 5)
                    except asyncio.TimeoutError:
                        pass
                if handle.monitor_task:
                    handle.monitor_task.cancel()
                await handle.agent.cleanup()
            handle.status = "closed"
            handle.done_event.set()
            return "closed"
        if handle.status != "running" or handle.agent is None:
            if handle.record:
                handle.status = handle.record["status"]
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
        self,
        worker_id: str,
        message: str,
        context: dict | None = None,
        operation_id: str | None = None,
    ) -> WorkerHandle | None:
        handle = self._lookup(worker_id)
        if handle is None or handle.status == "running":
            return None
        if handle.closed or (handle.record or {}).get("closed"):
            raise ValueError("Worker is closed")
        if handle.monitor_task is not None:
            handle.monitor_task.cancel()
        if handle.parent_service is not None and handle.parent_session_id:
            getter = getattr(handle.parent_service, "get_session", None)
            parent = getter(handle.parent_session_id) if callable(getter) else None
            from omniagents.core.models import model_store

            store = model_store()
            if store is not None and store.single_provider and parent is not None:
                store.reconcile_session_model(parent)
                handle.model = store._run_model_ref or parent.active_model
        old_session_id = handle.session_id
        if handle.agent is not None:
            await handle.agent.cleanup()
        if handle.journal is not None:
            handle.record = handle.journal.resume(
                worker_id, operation_id or uuid.uuid4().hex, message, self.owner
            )
        new_handle = await self._create_worker(
            worker_id=worker_id,
            task_description=message,
            model=handle.model,
            session_id=old_session_id,
            context=context,
            parent_session_id=handle.parent_session_id,
            principal=handle.principal,
            parent_service=handle.parent_service,
            project_slug=handle.project_slug,
            agent_slug=handle.agent_slug,
            configure_spec=handle.configure_spec,
            journal=handle.journal,
            record=handle.record,
        )
        new_handle.result = None
        self._remember(new_handle)
        return new_handle


def inspect_is_awaitable(value: Any) -> TypeGuard[Awaitable[Any]]:
    return hasattr(value, "__await__")
