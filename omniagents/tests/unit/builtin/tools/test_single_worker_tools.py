import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.run_context import RunContextWrapper
from omniagents.builtin.tools import single_worker_tools as single
from omniagents.builtin.tools import swarm_tools as swarm


def ctx(sid="parent"):
    return RunContextWrapper(context={"session_id": sid})


async def call(tool, *args, **kwargs):
    return json.loads(await tool._original_func(*args, **kwargs))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    single._WORKERS.clear()
    states = {}
    monkeypatch.setattr(
        single.workers, "worker_state_dict", lambda wid: states.get(wid)
    )
    monkeypatch.setattr(single.workers, "_workspace_payload", lambda _: {})
    spawn = AsyncMock(return_value='{"status":"spawned"}')
    resume = AsyncMock(return_value='{"status":"resumed"}')
    monkeypatch.setattr(single.workers, "spawn_worker_run", spawn)
    monkeypatch.setattr(single.workers.resume_worker, "_original_func", resume)
    yield states, spawn, resume
    single._WORKERS.clear()


@pytest.mark.asyncio
async def test_dispatch_resume_role_and_no_swarm(isolated):
    states, spawn, resume = isolated
    groups = set(swarm._SWARMS)
    result = await call(
        single.dispatch_worker,
        ctx(),
        "reviewer",
        "Review api.py",
        "Find bugs",
        "worktree",
    )
    assert result["status"] == "spawned"
    args = spawn.call_args
    assert args.args[2] == "worktree"
    assert "Find bugs" in args.args[1] and "Review api.py" in args.args[1]
    spec = SimpleNamespace(available_tools=[SimpleNamespace(name="read_file")])
    args.kwargs["configure_spec"](spec)
    assert [t.name for t in spec.available_tools] == ["read_file"]
    assert set(swarm._SWARMS) == groups
    worker = single._WORKERS[("parent", "reviewer")]
    states[worker.worker_id] = {"status": "completed", "result": "findings"}
    assert (await call(single.get_worker, ctx(), "reviewer"))["state"][
        "result"
    ] == "findings"
    await call(single.dispatch_worker, ctx(), "reviewer", "Check fixes")
    assert spawn.await_count == 1 and resume.await_count == 1
    assert resume.call_args.args[1] == worker.worker_id
    assert "Find bugs" in resume.call_args.args[2]


@pytest.mark.asyncio
async def test_session_ownership_and_same_name_isolation(isolated):
    await call(single.dispatch_worker, ctx(), "builder", "Implement")
    for tool in (
        single.get_worker,
        single.close_single_worker,
        single.cleanup_single_worker,
    ):
        with pytest.raises(ValueError, match="not found"):
            await call(tool, ctx("other"), "builder")
    assert await call(single.get_worker, ctx("other")) == []
    await call(single.dispatch_worker, ctx("other"), "builder", "Other task")
    assert (
        single._WORKERS[("parent", "builder")].worker_id
        != single._WORKERS[("other", "builder")].worker_id
    )


@pytest.mark.asyncio
async def test_failure_retry_running_guard_and_cleanup(isolated, monkeypatch):
    states, spawn, resume = isolated
    spawn.side_effect = ['{"status":"error"}', '{"status":"spawned"}']
    await call(single.dispatch_worker, ctx(), "builder", "Implement")
    await call(single.dispatch_worker, ctx(), "builder", "Retry")
    assert spawn.await_count == 2 and resume.await_count == 0
    worker = single._WORKERS[("parent", "builder")]
    states[worker.worker_id] = {"status": "running"}
    with pytest.raises(ValueError, match="running"):
        await call(single.dispatch_worker, ctx(), "builder", "Again")
    with pytest.raises(ValueError, match="running"):
        await call(single.cleanup_single_worker, ctx(), "builder")
    close = AsyncMock(return_value='{"status":"cancelled"}')
    cleanup = AsyncMock(return_value='{"status":"cleaned_up"}')
    monkeypatch.setattr(single.workers.close_worker, "_original_func", close)
    monkeypatch.setattr(
        single.workers.cleanup_worker_worktree, "_original_func", cleanup
    )
    await call(single.close_single_worker, ctx(), "builder")
    close.assert_awaited_once_with(worker.worker_id)
    assert not worker.cleaned
    states[worker.worker_id] = {"status": "cancelled"}
    await call(single.dispatch_worker, ctx(), "builder", "Continue")
    assert resume.await_count == 1
    await call(single.cleanup_single_worker, ctx(), "builder")
    with pytest.raises(ValueError, match="cleaned"):
        await call(single.dispatch_worker, ctx(), "builder", "Again")


@pytest.mark.asyncio
async def test_concurrent_dispatch_spawns_once(isolated):
    states, spawn, _ = isolated

    async def start(*args, **kwargs):
        await asyncio.sleep(0)
        states[kwargs["worker_id"]] = {"status": "running"}
        return '{"status":"spawned"}'

    spawn.side_effect = start
    results = await asyncio.gather(
        *(
            call(single.dispatch_worker, ctx(), "builder", "Implement")
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert spawn.await_count == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1


def test_no_recruitment_for_standalone_or_swarm_members():
    for tool in (
        "spawn_worker",
        "dispatch_worker",
        "dispatch_swarm_member",
        "create_swarm",
    ):
        with pytest.raises(ValueError, match="recruitment"):
            swarm._validate_worker_spec(
                SimpleNamespace(available_tools=[SimpleNamespace(name=tool)])
            )


def test_capability_exposes_tools_and_registry_resolves():
    from importlib.resources import files
    import yaml
    import omniagents.builtin.tools as registry
    import omniagents.builtin.capabilities as capabilities

    config = yaml.safe_load(files(capabilities).joinpath("swarms.yml").read_text())
    for name in (
        "dispatch_worker",
        "get_worker",
        "close_single_worker",
        "cleanup_single_worker",
    ):
        assert name in config["tools"]
        assert getattr(registry, name).name == name
