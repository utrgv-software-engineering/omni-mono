import asyncio

import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.runtime.ownership import reserve_run
from omniagents.core.runtime.ownership_recovery import recovery_snapshot
from .test_environment_run_retirement import make_service


@pytest.mark.asyncio
async def test_new_prompt_recovers_dead_host_owner_and_preserves_unknown_tool(
    monkeypatch,
):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("recover-on-start")
    store = service._queue_store(session)
    owner = reserve_run(store, session.id)
    owner.record_recovery_scope("host")

    def dead(state, receipt):
        state["runtime_recovery"]["process"]["start"] = "previous-process"
        return None, None

    store.mutate_queue(session.id, dead)
    session.append_message({"role": "user", "content": "original"})
    session.append_message(
        {
            "type": "function_call",
            "name": "execute_bash",
            "call_id": "interrupted-call",
            "arguments": "{}",
            "omniagents": {"run_id": owner.run_id},
        }
    )
    prompts = []

    async def stream(agent, prompt, *args, **kwargs):
        prompts.append(prompt)
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    result = await service.start_run(
        "continue", {"mode": "explicit", "environment_id": "a"}, session_id=session.id
    )
    task = session.current_task
    assert task is not None
    await asyncio.wait_for(task, 2)
    assert result["session_id"] == session.id
    assert prompts == ["continue"]
    assert session.runtime_ownership.generation == 2
    assert any(
        "execution outcome unknown" in str(row.get("output", ""))
        for row in session.history
    )
    with pytest.raises(RuntimeError, match="no longer current"):
        owner.assert_current()


@pytest.mark.asyncio
async def test_recover_command_handles_legacy_owner_without_erasing_history(
    monkeypatch,
):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("legacy-recovery")
    store = service._queue_store(session)
    reserve_run(store, session.id)

    def legacy(state, receipt):
        state.pop("runtime_recovery")
        return None, None

    store.mutate_queue(session.id, legacy)
    session.append_message({"role": "user", "content": "preserve me"})
    original = list(session.history)
    result = await service.server_call("recover", {}, session.id)
    assert result["reason"] == "legacy_owner_unverifiable"
    assert recovery_snapshot(store, session.id)["owner"] is not None
    result = await service.server_call(
        "recover", {"abandon_unknown_run": True}, session.id
    )
    assert result["recovered"] and result["mode"] == "operator_abandonment"
    assert session.history == original
    reserve_run(store, session.id).release()


@pytest.mark.asyncio
async def test_recover_command_does_not_supersede_live_foreign_owner(monkeypatch):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("live-recovery")
    owner = reserve_run(service._queue_store(session), session.id)
    result = await service.server_call(
        "recover", {"abandon_unknown_run": True}, session.id
    )
    assert not result["recovered"]
    owner.assert_current()
    owner.release()


@pytest.mark.asyncio
async def test_recover_stops_foreground_host_command_and_allows_next_run(
    monkeypatch, tmp_path
):
    import sys
    from pathlib import Path
    from omniagents.execution import (
        EnvironmentKind,
        EnvironmentState,
        ExecutionEnvironmentDescriptor,
        WorkspaceResource,
    )
    from omniagents.workspace.host import HostWorkspace

    if sys.platform != "linux":
        pytest.skip("Linux process supervision")
    service = make_service(monkeypatch)
    workspace = HostWorkspace(str(tmp_path), scratch_root=str(tmp_path / "scratch"))
    service.workspace_registry.register(WorkspaceResource("host-workspace"))
    service.environment_manager.register_initial(
        ExecutionEnvironmentDescriptor(
            environment_id="host",
            workspace_id="host-workspace",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=1,
        ),
        workspace,
    )
    pidfile = tmp_path / "foreground.pid"

    async def stream(agent, prompt, *args, **kwargs):
        if prompt == "long command":
            await workspace.exec(
                [
                    sys.executable,
                    "-c",
                    "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)",
                    str(pidfile),
                ],
                shell=False,
            )
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    result = await service.start_run(
        "long command",
        {"mode": "explicit", "environment_id": "host"},
        session_id="stop-recover",
    )
    session = service._session_manager.get(result["session_id"])
    try:
        async with asyncio.timeout(5):
            while not pidfile.exists() or not pidfile.read_text():
                await asyncio.sleep(0.01)
        response = await service.server_call("recover", {}, session.id)
        assert response.get("reason") == "no_runtime_owner"
        assert not Path(f"/proc/{int(pidfile.read_text())}").exists()
        assert session.runtime_ownership.released
        await service.start_run(
            "continue",
            {"mode": "explicit", "environment_id": "host"},
            session_id=session.id,
        )
        await asyncio.wait_for(session.current_task, 2)
        assert session.runtime_ownership.generation == 2
    finally:
        if session.current_task is not None and not session.current_task.done():
            await service._session_manager.cancel(session.current_task.get_name())
