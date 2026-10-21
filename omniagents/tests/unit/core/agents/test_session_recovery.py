import asyncio

import pytest

from omniagents.core.agents import service as service_module
from omniagents.core.runtime.ownership import reserve_run
from omniagents.core.session import queue_inbox

from ..runtime.lease_support import expire_lease
from .test_environment_run_retirement import make_service


@pytest.mark.asyncio
async def test_new_prompt_takes_over_a_dead_owner_and_tells_the_model(monkeypatch):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("recover-on-start")
    store = service._queue_store(session)
    owner = reserve_run(store, session.id)
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
    expire_lease(store, session.id)
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
    assert session.runtime_ownership.took_over_from["run_id"] == owner.run_id
    assert any(
        "execution outcome unknown" in str(row.get("output", ""))
        for row in session.history
    )
    with pytest.raises(RuntimeError, match="no longer current"):
        owner.assert_current()
    assert queue_inbox.runtime_owner(store, session.id) is None


@pytest.mark.asyncio
async def test_takeover_closes_the_interrupted_turn_in_the_transcript(monkeypatch):
    from omniagents.core.conversation.model import ItemStatus, TurnStatus
    from omniagents.core.conversation.recorder import recorder_for_session

    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("close-interrupted")
    store = service._queue_store(session)
    owner = reserve_run(store, session.id)
    recorder = recorder_for_session(session)
    recorder.open_turn(session.id, owner.run_id, prompt="do it", prompt_role="user")
    claimed = recorder.claim_tool_execution(
        session.id,
        {"run_id": owner.run_id, "call_id": "c", "tool": "execute_bash", "input": "{}"},
        "tool-owner",
    )
    expire_lease(store, session.id)

    async def stream(agent, prompt, *args, **kwargs):
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    await service.start_run(
        "continue", {"mode": "explicit", "environment_id": "a"}, session_id=session.id
    )
    await asyncio.wait_for(session.current_task, 2)
    assert store.get_turn(session.id, owner.run_id).status == TurnStatus.FAILED
    item = store.get_item(session.id, claimed.item_id)
    assert item.status == ItemStatus.FAILED
    assert item.content["execution_state"] == "uncertain"


@pytest.mark.asyncio
async def test_live_foreign_owner_is_not_displaced(monkeypatch):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("live-owner")
    owner = reserve_run(service._queue_store(session), session.id)
    with pytest.raises(RuntimeError, match="active runtime owner"):
        await service.start_run(
            "continue",
            {"mode": "explicit", "environment_id": "a"},
            session_id=session.id,
        )
    owner.assert_current()
    owner.release()


@pytest.mark.asyncio
async def test_stopping_a_foreground_host_command_frees_the_next_run(
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
        session_id="stop-then-continue",
    )
    session = service._session_manager.get(result["session_id"])
    try:
        async with asyncio.timeout(5):
            while not pidfile.exists() or not pidfile.read_text():
                await asyncio.sleep(0.01)
        task = session.current_task
        await service.stop_run(task.get_name())
        await asyncio.gather(task, return_exceptions=True)
        async with asyncio.timeout(5):
            while Path(f"/proc/{int(pidfile.read_text())}").exists():
                await asyncio.sleep(0.01)
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
