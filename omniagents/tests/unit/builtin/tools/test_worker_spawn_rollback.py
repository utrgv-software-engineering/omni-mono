"""Spawn-failure rollback for isolated worker workspaces
(``builtin/tools/worker_tools.spawn_worker``).

``strategy.prepare`` runs before ``manager().spawn``; a spawn failure must
clean the prepared worktree up — leftovers ride into the workspace
snapshot tar and inflate every rehydrate."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import omniagents.builtin.tools.worker_tools as wt


class FakeStrategy:
    name = "worktree"
    unavailable_message = "unavailable"

    def __init__(self):
        self.prepared: list[str] = []
        self.cleaned: list[str] = []

    async def ready(self, parent_workspace_root):
        return True

    async def prepare(self, *, worker_id, parent_workspace_root):
        self.prepared.append(worker_id)
        return SimpleNamespace(root=f"/tmp/wt/{worker_id}", sources=[], metadata={})

    async def is_parent_dirty(self, parent_workspace_root):
        return False

    async def cleanup(self, worker_id, workspace):
        self.cleaned.append(worker_id)
        return True

    def task_preamble(self, workspace):
        return None


@pytest.fixture()
def _spawn_env(monkeypatch):
    strategy = FakeStrategy()
    monkeypatch.setattr(wt, "get_isolation_strategy", lambda name: strategy)
    monkeypatch.setattr(
        wt,
        "worker_defaults",
        lambda: SimpleNamespace(default_isolation="worktree", namer=None),
    )
    monkeypatch.setattr(wt, "_workspace_root_from_ctx", lambda ctx: "/parent")
    monkeypatch.setattr(wt, "session_id_from_ctx", lambda ctx: "sess-1")
    monkeypatch.setattr(wt, "_WORKER_WORKSPACES", {})
    monkeypatch.setattr(wt, "_WORKER_ISOLATION", {})
    monkeypatch.setattr(wt, "_WORKER_META", {})
    return strategy


async def _invoke_spawn(task="do the thing"):
    # The house idiom for tool tests: call the wrapped function directly.
    fn = wt.spawn_worker._original_func
    return await fn(SimpleNamespace(context=None), task)


def test_spawn_failure_rolls_back_prepared_worktree(_spawn_env, monkeypatch):
    strategy = _spawn_env

    async def failing_spawn(**kwargs):
        raise RuntimeError("model resolution exploded")

    monkeypatch.setattr(
        wt, "manager", lambda: SimpleNamespace(spawn=failing_spawn, workers={})
    )

    result = asyncio.run(_invoke_spawn())

    assert strategy.prepared and strategy.cleaned == strategy.prepared
    assert wt._WORKER_WORKSPACES == {}
    assert wt._WORKER_ISOLATION == {}
    text = str(getattr(result, "llm_output", result))
    assert "model resolution exploded" in text


def test_parallel_spawns_get_unique_names(monkeypatch):
    """parallel_tool_calls batches spawn workers concurrently; the manager
    registers handles only at the END of spawn, so name issuance must be
    reserved synchronously or two spawns pick the same id and the second
    handle silently overwrites the first."""

    def deterministic_namer(existing):
        # Mirrors _builtin_namer's dedupe contract with a fixed base name.
        if "pip" not in existing:
            return "pip"
        n = 2
        while f"pip-{n}" in existing:
            n += 1
        return f"pip-{n}"

    monkeypatch.setattr(
        wt,
        "worker_defaults",
        lambda: SimpleNamespace(default_isolation="none", namer=deterministic_namer),
    )
    monkeypatch.setattr(wt, "_workspace_root_from_ctx", lambda ctx: "/parent")
    monkeypatch.setattr(wt, "session_id_from_ctx", lambda ctx: "sess-1")
    monkeypatch.setattr(wt, "_WORKER_META", {})
    monkeypatch.setattr(wt, "_ISSUED_WORKER_IDS", set())

    workers: dict = {}
    spawned: list[str] = []

    async def slow_spawn(*, worker_id, **kwargs):
        # The registration gap: the handle lands only after real awaits.
        await asyncio.sleep(0.01)
        workers[worker_id] = object()
        spawned.append(worker_id)

    monkeypatch.setattr(
        wt, "manager", lambda: SimpleNamespace(spawn=slow_spawn, workers=workers)
    )

    fn = wt.spawn_worker._original_func

    async def run():
        await asyncio.gather(
            fn(SimpleNamespace(context=None), "task a"),
            fn(SimpleNamespace(context=None), "task b"),
        )

    asyncio.run(run())
    assert sorted(spawned) == ["pip", "pip-2"]


def test_spawn_success_keeps_worktree(_spawn_env, monkeypatch):
    strategy = _spawn_env

    async def ok_spawn(**kwargs):
        return SimpleNamespace(run_id="r1", session_id="s1")

    monkeypatch.setattr(
        wt, "manager", lambda: SimpleNamespace(spawn=ok_spawn, workers={})
    )

    asyncio.run(_invoke_spawn())

    assert strategy.prepared and strategy.cleaned == []
    assert list(wt._WORKER_WORKSPACES) == strategy.prepared
