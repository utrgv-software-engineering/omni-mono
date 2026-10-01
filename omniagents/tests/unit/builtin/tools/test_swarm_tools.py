"""Swarm contract tests exercise ownership, actual runtime reuse and mail routing."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.run_context import RunContextWrapper
from omniagents.builtin.tools import swarm_tools as sw
from omniagents.core.tools import RichToolOutput


def ctx(sid="owner-session"):
    return RunContextWrapper(context={"session_id": sid, "workspace_root": "/tmp"})


async def call(tool, *args, **kwargs):
    return json.loads(await tool._original_func(*args, **kwargs))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    sw._SWARMS.clear()
    monkeypatch.setattr(sw.workers, "worker_state_dict", lambda _: None)
    monkeypatch.setattr(sw.workers, "_workspace_payload", lambda _: {})
    yield
    sw._SWARMS.clear()


async def group():
    data = await call(sw.create_swarm, ctx(), "Ship a tested change")
    sid = data["swarm_id"]
    await call(sw.add_swarm_member, ctx(), sid, "builder", "Implement the API")
    await call(
        sw.add_swarm_member, ctx(), sid, "reviewer", "Find compatibility failures"
    )
    return sid


@pytest.mark.asyncio
async def test_ownership_and_membership_boundaries():
    sid = await group()
    for tool, args in [
        (sw.get_swarm, (sid,)),
        (sw.add_swarm_member, (sid, "intruder", "role")),
        (sw.dispatch_swarm_member, (sid, "builder", "task")),
        (sw.message_swarm_member, (sid, "builder", "hello")),
        (sw.close_swarm, (sid, "done")),
        (sw.cleanup_swarm_member, (sid, "builder")),
    ]:
        with pytest.raises(ValueError, match="not found"):
            await call(tool, ctx("other-session"), *args)
    assert await call(sw.get_swarm, ctx("other-session")) == []
    with pytest.raises(ValueError, match="Duplicate"):
        await call(sw.add_swarm_member, ctx(), sid, "builder", "duplicate")
    with pytest.raises(ValueError, match="reserved"):
        await call(sw.add_swarm_member, ctx(), sid, "owner", "role")


@pytest.mark.asyncio
async def test_dispatch_uses_worker_adapter_and_binds_member_tools(monkeypatch):
    sid = await group()
    spawn = AsyncMock(return_value=RichToolOutput('{"status":"spawned"}'))
    monkeypatch.setattr(sw.workers, "spawn_worker_run", spawn)
    await call(
        sw.dispatch_swarm_member, ctx(), sid, "builder", "Edit api.py", "worktree"
    )
    args = spawn.call_args
    assert args.args[2] == "worktree"
    assert "Ship a tested change" in args.args[1]
    assert "Implement the API" in args.args[1]
    assert "Edit api.py" in args.args[1]
    original_tools = [SimpleNamespace(name="read_file")]
    spec = SimpleNamespace(available_tools=original_tools)
    args.kwargs["configure_spec"](spec)
    assert len(original_tools) == 1
    tools = {t.name: t for t in spec.available_tools}
    assert set(tools) == {"read_file", "swarm_member_message", "swarm_member_context"}
    row = await call(tools["swarm_member_message"], "reviewer", "API ready")
    assert row["from"] == "builder" and row["delivery"] == "mailbox"
    with pytest.raises(ValueError, match="Unknown member"):
        await call(tools["swarm_member_message"], "outsider", "hello")
    view = await call(tools["swarm_member_context"])
    assert len(view["members"]) == 2
    resume = AsyncMock(return_value=RichToolOutput('{"status":"resumed"}'))
    monkeypatch.setattr(sw.workers.resume_worker, "_original_func", resume)
    await call(
        sw.dispatch_swarm_member, ctx(), sid, "builder", "Fix the review findings"
    )
    resume.assert_awaited_once()
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_failed_spawn_retries_instead_of_resuming(monkeypatch):
    sid = await group()
    spawn = AsyncMock(
        side_effect=[
            RichToolOutput('{"status":"error"}'),
            RichToolOutput('{"status":"spawned"}'),
        ]
    )
    monkeypatch.setattr(sw.workers, "spawn_worker_run", spawn)
    await call(sw.dispatch_swarm_member, ctx(), sid, "builder", "task")
    assert not sw._SWARMS[sid].members["builder"].started
    await call(sw.dispatch_swarm_member, ctx(), sid, "builder", "task")
    assert spawn.await_count == 2


@pytest.mark.asyncio
async def test_live_mail_and_budget_without_automatic_respawn(monkeypatch):
    sid = await group()
    swarm = sw._SWARMS[sid]
    swarm.members["reviewer"].started = True
    send = AsyncMock(return_value="message_sent")
    monkeypatch.setattr(
        sw.workers, "manager", lambda: SimpleNamespace(send_message=send)
    )
    row = await sw._message(swarm, "builder", "reviewer", "Please check schema")
    assert row["delivery"] == "message_sent"
    assert "from builder" in send.call_args.args[1]
    send.return_value = "completed"
    await sw._message(swarm, "builder", "reviewer", "Second finding")
    assert len(swarm.messages) == 2
    monkeypatch.setattr(sw, "MAX_MESSAGES", 2)
    with pytest.raises(ValueError, match="budget"):
        await sw._message(swarm, "builder", "reviewer", "Third finding")
    assert len(swarm.messages) == 2


@pytest.mark.asyncio
async def test_close_and_cleanup_do_not_destroy_running_work(monkeypatch):
    sid = await group()
    state = {"status": "running"}
    monkeypatch.setattr(sw.workers, "worker_state_dict", lambda _: state)
    with pytest.raises(ValueError, match="running"):
        await call(sw.close_swarm, ctx(), sid, "done")
    with pytest.raises(ValueError, match="running"):
        await call(sw.cleanup_swarm_member, ctx(), sid, "builder")
    close = AsyncMock(return_value=RichToolOutput('{"status":"cancelled"}'))
    cleanup = AsyncMock(return_value=RichToolOutput('{"status":"cleaned_up"}'))
    monkeypatch.setattr(sw.workers.close_worker, "_original_func", close)
    monkeypatch.setattr(sw.workers.cleanup_worker_worktree, "_original_func", cleanup)
    result = await call(sw.close_swarm, ctx(), sid, "Cancelled experiment", True)
    assert result["status"] == "closed"
    cleanup.assert_not_awaited()
    state["status"] = "cancelled"
    await call(sw.cleanup_swarm_member, ctx(), sid, "builder")
    cleanup.assert_awaited_once()
    with pytest.raises(ValueError, match="closed"):
        await call(sw.dispatch_swarm_member, ctx(), sid, "builder", "task")


@pytest.mark.asyncio
async def test_serialized_dispatch_prevents_duplicate_spawn(monkeypatch):
    sid = await group()

    async def spawn(*args, **kwargs):
        await asyncio.sleep(0)
        return RichToolOutput('{"status":"spawned"}')

    mock = AsyncMock(side_effect=spawn)
    resume = AsyncMock(
        return_value=RichToolOutput('{"status":"error","error":"running"}')
    )
    monkeypatch.setattr(sw.workers, "spawn_worker_run", mock)
    monkeypatch.setattr(sw.workers.resume_worker, "_original_func", resume)
    await asyncio.gather(
        *(
            call(sw.dispatch_swarm_member, ctx(), sid, "builder", "task")
            for _ in range(2)
        )
    )
    assert mock.await_count == 1 and resume.await_count == 1
