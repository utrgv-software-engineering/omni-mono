"""Environment retirement owns exact runs, not mutable session bindings."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session.manager import SessionManager
from omniagents.execution import (
    AgentHostResources,
    EnvironmentKind,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    WorkspaceResource,
)
from omniagents.execution.errors import (
    EnvironmentChangedError,
    EnvironmentUnavailableError,
)
from omniagents.workspace.base import Workspace


def make_service(monkeypatch):
    async def build(*args):
        return SimpleNamespace(tools=[], mcp_servers=[])

    service = AgentService(
        AgentSpec(
            name="retirement",
            welcome_text="",
            get_agent_instructions=lambda: "",
            build_agent=build,
        ),
        AgentHostResources.create("retirement-host"),
    )
    for name in ("a", "b"):
        service.workspace_registry.register(WorkspaceResource(f"workspace-{name}"))
        workspace = Mock(spec=Workspace)
        workspace.root = "/workspace"
        service.environment_manager.register_initial(
            ExecutionEnvironmentDescriptor(
                environment_id=name,
                workspace_id=f"workspace-{name}",
                kind=EnvironmentKind.CONTAINER,
                state=EnvironmentState.READY,
                generation=1,
            ),
            workspace,
        )

    async def stream(agent, prompt, session, channel, **kwargs):
        await service.request_tool_approval(
            session=session,
            call_id=f"call-{session.id}",
            tool_name="execute_bash",
            arguments='{"command":"echo approved"}',
            run_id=asyncio.current_task().get_name(),
        )
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    return service


async def start(service, environment, session_id=None):
    result = await service.start_run(
        "approval",
        {"mode": "explicit", "environment_id": environment},
        session_id=session_id,
    )
    session = service._session_manager.get(result["session_id"])
    async with asyncio.timeout(2):
        while not session.pending_tool_approvals:
            await asyncio.sleep(0)
    return session, session.current_task


@pytest.mark.asyncio
async def test_shared_session_reservation_blocks_another_service_during_preparation(
    monkeypatch,
):
    first = make_service(monkeypatch)
    second = make_service(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = first._build_agent_for_session

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(first, "_build_agent_for_session", delayed)
    preparing = asyncio.create_task(start(first, "a", "shared-owner"))
    await entered.wait()
    try:
        with pytest.raises(RuntimeError, match="active runtime owner"):
            await start(second, "b", "shared-owner")
        assert second._session_manager.get("shared-owner").current_task is None
    finally:
        release.set()
    session, task = await preparing
    await first.stop_run(task.get_name())
    await asyncio.gather(task, return_exceptions=True)
    other, other_task = await start(second, "b", session.id)
    await second.stop_run(other_task.get_name())
    await asyncio.gather(other_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_preparation_releases_only_its_reservation(monkeypatch):
    service = make_service(monkeypatch)
    entered = asyncio.Event()
    original = service._build_agent_for_session

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_build_agent_for_session", blocked)
    preparing = asyncio.create_task(start(service, "a", "cancel-preparation"))
    await entered.wait()
    preparing.cancel()
    await asyncio.gather(preparing, return_exceptions=True)
    monkeypatch.setattr(service, "_build_agent_for_session", original)
    session, task = await start(service, "a", "cancel-preparation")
    await service.stop_run(task.get_name())
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_finished_local_run_recovers_after_release_storage_failure(monkeypatch):
    service = make_service(monkeypatch)
    session, task = await start(service, "a", "release-storage-failure")
    owner = session.runtime_ownership
    mutate = owner.store.mutate_queue

    def unavailable(thread_id, update, **kwargs):
        if update.__name__ == "finish":
            raise RuntimeError("release storage unavailable")
        return mutate(thread_id, update, **kwargs)

    monkeypatch.setattr(owner.store, "mutate_queue", unavailable)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert owner.closing and not owner.released
    monkeypatch.setattr(owner.store, "mutate_queue", mutate)
    next_session, next_task = await start(service, "a", session.id)
    assert owner.released
    assert next_session.runtime_ownership.generation == owner.generation + 1
    await service.stop_run(next_task.get_name())
    await asyncio.gather(next_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_environment_retirement_closes_only_its_session_mcp_workers(monkeypatch):
    service = make_service(monkeypatch)
    entered, closed = [], []

    class Server:
        name = "fixture"

        async def __aenter__(self):
            self.owner = asyncio.current_task()
            entered.append(self)
            return self

        async def __aexit__(self, *args):
            assert asyncio.current_task() is self.owner
            closed.append(self)

    service.spec.mcp_servers = [SimpleNamespace()]
    monkeypatch.setattr(
        service_module, "build_mcp_servers", lambda *a, **kw: [Server()]
    )
    a = service._session_manager.get_or_create("mcp-a")
    b = service._session_manager.get_or_create("mcp-b")
    lease_a = await service.environment_manager.acquire("a")
    lease_b = await service.environment_manager.acquire("b")
    first, duplicate = await asyncio.gather(
        service._ensure_session_mcp_servers(a, lease_a),
        service._ensure_session_mcp_servers(a, lease_a),
    )
    second = await service._ensure_session_mcp_servers(b, lease_b)
    assert first == duplicate and len(entered) == 2
    await service.agent_host_stop_environment("a")
    assert closed == first
    assert not a._mcp_servers_active
    assert b._mcp_servers_active == second
    assert not b._mcp_lifecycle_task.done()
    await service.agent_host_stop_environment("b")
    assert closed == first + second
    assert not service.environment_manager._resource_tasks


@pytest.mark.asyncio
async def test_mcp_rebind_is_not_closed_by_old_environment_retirement(monkeypatch):
    service = make_service(monkeypatch)
    closed = []

    class Server:
        name = "fixture"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self)

    service.spec.mcp_servers = [SimpleNamespace()]
    monkeypatch.setattr(
        service_module, "build_mcp_servers", lambda *a, **kw: [Server()]
    )
    session = service._session_manager.get_or_create("moves")
    first = await service._ensure_session_mcp_servers(
        session, await service.environment_manager.acquire("a")
    )
    second = await service._ensure_session_mcp_servers(
        session, await service.environment_manager.acquire("b")
    )
    assert closed == first
    await service.agent_host_stop_environment("a")
    assert session._mcp_servers_active == second
    assert not session._mcp_lifecycle_task.done()
    await service.agent_host_stop_environment("b")
    assert closed == first + second


@pytest.mark.asyncio
async def test_cancelled_mcp_start_caller_does_not_duplicate_the_owned_worker(
    monkeypatch,
):
    service = make_service(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    class Server:
        name = "fixture"

        async def __aenter__(self):
            nonlocal count
            count += 1
            entered.set()
            await release.wait()
            return self

        async def __aexit__(self, *args):
            pass

    service.spec.mcp_servers = [SimpleNamespace()]
    monkeypatch.setattr(
        service_module, "build_mcp_servers", lambda *a, **kw: [Server()]
    )
    session = service._session_manager.get_or_create("cancelled-caller")
    lease = await service.environment_manager.acquire("a")
    first = asyncio.create_task(service._ensure_session_mcp_servers(session, lease))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(service._ensure_session_mcp_servers(session, lease))
    release.set()
    assert len(await second) == 1
    assert count == 1
    await service.agent_host_stop_environment("a")
    assert not service.environment_manager._resource_tasks


@pytest.mark.asyncio
async def test_resource_admission_and_retirement_order(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    lease = await manager.acquire("a")
    run_done, worker_done = asyncio.Event(), asyncio.Event()
    order = []

    async def run():
        await run_done.wait()
        order.append("run finished")

    async def cancel():
        run_done.set()
        await run_task

    async def close():
        assert run_task.done()
        worker_done.set()
        await worker_task
        order.append("worker finished")

    run_task = await manager.start_run_task(
        lease, lambda: asyncio.create_task(run()), cancel
    )
    worker_task = await manager.start_resource_task(
        lease, lambda: asyncio.create_task(worker_done.wait()), close
    )
    await manager.stop("a")
    assert order == ["run finished", "worker finished"]
    factory = Mock()
    with pytest.raises(EnvironmentUnavailableError):
        await manager.start_resource_task(lease, factory, close)
    factory.assert_not_called()
    assert not manager._resource_tasks


@pytest.mark.asyncio
async def test_resource_timeout_keeps_retirement_fenced_and_retryable(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    lease = await manager.acquire("a")
    release = asyncio.Event()
    attempts = 0

    async def close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("still closing")
        release.set()
        await worker

    worker = await manager.start_resource_task(
        lease, lambda: asyncio.create_task(release.wait()), close
    )
    with pytest.raises(RuntimeError, match="still closing"):
        await manager.stop("a")
    assert (await manager.describe("a")).state is EnvironmentState.STOPPING
    assert not worker.done()
    with pytest.raises(EnvironmentUnavailableError):
        await manager.acquire("a")
    await manager.stop("a")
    assert worker.done()
    assert not manager._resource_tasks


@pytest.mark.asyncio
async def test_failed_resource_owner_is_not_silently_retired(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    lease = await manager.acquire("a")

    async def failed():
        raise RuntimeError("resource cleanup failed")

    async def close():
        await worker

    worker = await manager.start_resource_task(
        lease, lambda: asyncio.create_task(failed()), close
    )
    with pytest.raises(RuntimeError, match="resource cleanup failed"):
        await worker
    await asyncio.sleep(0)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="resource cleanup failed"):
            await manager.stop("a")
        assert (await manager.describe("a")).state is EnvironmentState.STOPPING


@pytest.mark.asyncio
async def test_stop_resolves_only_owning_runs_and_reconnect_approvals(monkeypatch):
    service = make_service(monkeypatch)
    a, task_a = await start(service, "a")
    b, task_b = await start(service, "b")
    approval_b = dict(b.pending_tool_approvals)
    try:
        result = await service.agent_host_stop_environment("a")
        assert result["state"] == "stopped"
        assert task_a.done()
        assert a.active_run_id is None
        assert not a.pending_tool_approvals
        assert all(
            session is not a for _, session in service._pending_tool_approvals.values()
        )
        snapshot = (await service.queue_status(a.id, include_snapshot=True))["snapshot"]
        assert not any(
            item["kind"] == "approval" and item["status"] == "started"
            for item in snapshot["items"]
        )
        assert not task_b.done()
        assert b.pending_tool_approvals == approval_b
        # Rebinding the same conversation must not make an old run id point
        # at its new task. A late stop for the old run cannot cancel it.
        fresh, fresh_task = await start(service, "b", a.id)
        assert await service.stop_run(task_a.get_name()) is False
        assert fresh.current_task is fresh_task and not fresh_task.done()
    finally:
        await service.agent_host_stop_environment("b")
    assert not service.environment_manager._run_tasks


@pytest.mark.asyncio
async def test_stop_fences_start_that_already_acquired_a_lease(monkeypatch):
    service = make_service(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._build_agent_for_session

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "_build_agent_for_session", delayed)
    pending = asyncio.create_task(
        service.start_run(
            "late",
            {"mode": "explicit", "environment_id": "a"},
            session_id="late",
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.agent_host_stop_environment("a")
    release.set()
    with pytest.raises(EnvironmentUnavailableError):
        await pending
    assert service._session_manager.get("late").current_task is None
    assert not service._pending_tool_approvals


@pytest.mark.asyncio
async def test_stop_does_not_close_resources_until_run_cleanup_finishes(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    lease = await manager.acquire("a")
    cleaning, release = asyncio.Event(), asyncio.Event()
    close = AsyncMock()
    manager._environments["a"] = replace(
        manager._environments["a"], lifecycle_controller=SimpleNamespace(close=close)
    )

    async def worker():
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()

    async def cancel():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    task = await manager.start_run_task(
        lease, lambda: asyncio.create_task(worker()), cancel
    )
    await asyncio.sleep(0)
    stopping = asyncio.create_task(manager.stop("a"))
    await asyncio.wait_for(cleaning.wait(), 2)
    assert (await manager.describe("a")).state is EnvironmentState.STOPPING
    with pytest.raises(EnvironmentUnavailableError):
        await manager.start_run_task(lease, Mock(), cancel)
    close.assert_not_called()
    release.set()
    await stopping
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_run_cancellation_never_targets_new_session_task():
    manager = SessionManager("retirement", "identity")
    session = manager.get_or_create()
    old = asyncio.create_task(asyncio.Event().wait(), name="old")
    manager.bind_task(old, session)
    newer = asyncio.create_task(asyncio.Event().wait(), name="new")
    manager.bind_task(newer, session)
    stream = Mock()
    session.current_stream_result = stream
    try:
        assert await manager.cancel("old")
        assert old.done()
        assert not newer.done()
        assert session.current_task is newer
        assert session.current_stream_result is stream
        stream.cancel.assert_not_called()
    finally:
        session.current_stream_result = None
        await manager.cancel("new")


@pytest.mark.asyncio
async def test_stale_generation_cannot_register_a_run(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    old = await manager.acquire("a")
    await manager.replace("a", old.workspace)
    factory = Mock()
    with pytest.raises(EnvironmentChangedError):
        await manager.start_run_task(old, factory, AsyncMock())
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_unconfirmed_cancellation_stays_fenced_and_retryable(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    lease = await manager.acquire("a")
    close = AsyncMock()
    manager._environments["a"] = replace(
        manager._environments["a"], lifecycle_controller=SimpleNamespace(close=close)
    )
    release = asyncio.Event()
    task = await manager.start_run_task(
        lease,
        lambda: asyncio.create_task(release.wait()),
        AsyncMock(return_value=False),
    )
    try:
        with pytest.raises(RuntimeError, match="did not finish cancellation"):
            await manager.stop("a")
        assert (await manager.describe("a")).state is EnvironmentState.STOPPING
        close.assert_not_called()
        with pytest.raises(EnvironmentUnavailableError):
            await manager.acquire("a")
        release.set()
        await task
        assert (await manager.stop("a")).state is EnvironmentState.STOPPED
        close.assert_awaited_once()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cancel_timeout_keeps_exact_task_for_retry():
    manager = SessionManager("retirement", "timeout")
    session = manager.get_or_create()
    started, release = asyncio.Event(), asyncio.Event()

    async def worker():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(worker(), name="slow-cancel")
    manager.bind_task(task, session)
    await started.wait()
    try:
        async with asyncio.timeout(1):
            assert not await manager.cancel(task.get_name(), timeout=0.01)
        assert session.current_task is task
        assert manager.get_by_run_id(task.get_name()) is session
        assert not task.done()
        assert not await manager.cancel(task.get_name(), timeout=0.01)
        assert not task.done()
        assert task.cancelling() == 1
        release.set()
        await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnected_stop_caller_does_not_interrupt_retirement(monkeypatch):
    service = make_service(monkeypatch)
    manager = service.environment_manager
    entered, release = asyncio.Event(), asyncio.Event()

    async def close(lease):
        entered.set()
        await release.wait()

    manager._environments["a"] = replace(
        manager._environments["a"], lifecycle_controller=SimpleNamespace(close=close)
    )
    caller = asyncio.create_task(manager.stop("a"))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    retry = asyncio.create_task(manager.stop("a"))
    release.set()
    assert (await retry).state is EnvironmentState.STOPPED


@pytest.mark.asyncio
async def test_session_cannot_restart_during_prior_run_request_cleanup(monkeypatch):
    service = make_service(monkeypatch)
    session, task = await start(service, "a")
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._resolve_session_elicitations

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        await original(*args, **kwargs)

    monkeypatch.setattr(service, "_resolve_session_elicitations", delayed)
    stopping = asyncio.create_task(service.stop_run(task.get_name()))
    await entered.wait()
    original_binding = await service.thread_bindings.get(session.id)
    try:
        with pytest.raises(RuntimeError, match="Run already active"):
            await service.start_run(
                "too early", {"mode": "explicit", "environment_id": "b"}, session.id
            )
        assert session.current_task is task
        assert await service.thread_bindings.get(session.id) == original_binding
    finally:
        release.set()
        await stopping


@pytest.mark.asyncio
async def test_concurrent_cancel_callers_do_not_interrupt_run_cleanup():
    manager = SessionManager("retirement", "concurrent-cancel")
    session = manager.get_or_create()
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = []

    async def worker():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.append(True)

    task = asyncio.create_task(worker(), name="cancel-once")
    manager.bind_task(task, session)
    await started.wait()
    first = asyncio.create_task(manager.cancel(task.get_name()))
    await cleaning.wait()
    second = asyncio.create_task(manager.cancel(task.get_name()))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not task.done()
    release.set()
    assert await second
    assert cleaned == [True]
