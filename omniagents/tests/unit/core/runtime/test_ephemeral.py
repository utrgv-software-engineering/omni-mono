import os
from types import SimpleNamespace

import pytest

from omniagents.core.runtime import run_agent_once, temporary_environ
from omniagents.core.runtime.ephemeral import run_agent_once as _raw


def test_temporary_environ_sets_and_restores(monkeypatch):
    monkeypatch.setenv("EPH_KEEP", "orig")
    monkeypatch.delenv("EPH_NEW", raising=False)
    with temporary_environ(
        {"EPH_KEEP": "changed", "EPH_NEW": "added", "EPH_GONE": None}
    ):
        assert os.environ["EPH_KEEP"] == "changed"
        assert os.environ["EPH_NEW"] == "added"
        assert "EPH_GONE" not in os.environ
    assert os.environ["EPH_KEEP"] == "orig"
    assert "EPH_NEW" not in os.environ


class _FakeStream:
    def __init__(self, output):
        self._output = output

    async def stream_events(self):
        if False:  # pragma: no cover
            yield None

    def final_output_as(self, _type):
        return self._output


@pytest.mark.asyncio
async def test_cancelled_utility_run_cancels_sdk_background_task(monkeypatch):
    import asyncio
    import agents
    import omniagents.core.agents.builder as builder
    from unittest.mock import AsyncMock

    started, stopped = asyncio.Event(), asyncio.Event()

    async def model_request():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class PendingStream:
        def __init__(self):
            self.task = asyncio.create_task(model_request())

        async def stream_events(self):
            await asyncio.Event().wait()
            yield None

        def cancel(self):
            self.task.cancel()

    monkeypatch.setattr(
        builder, "_default_build_agent", AsyncMock(return_value="agent")
    )
    monkeypatch.setattr(agents.Runner, "run_streamed", lambda *a, **kw: PendingStream())
    task = asyncio.create_task(run_agent_once(SimpleNamespace(), "summarize"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(stopped.wait(), 1)


@pytest.mark.asyncio
async def test_run_agent_once_wires_everything(monkeypatch, tmp_path):
    captured = {}

    class _Spec:
        model_name = "original-model"

    spec = _Spec()

    async def fake_build(*, settings, mcp_servers, spec, session):
        captured["built_spec"] = spec
        captured["session"] = session
        return "BUILT_AGENT"

    def fake_run_streamed(agent, prompt, *, context, max_turns, run_config):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["context"] = context
        captured["max_turns"] = max_turns
        captured["run_config"] = run_config
        captured["env_at_run"] = os.environ.get("EPH_RUN_KEY")
        return _FakeStream("the answer")

    import omniagents.core.agents.builder as builder_mod
    import agents as agents_mod

    monkeypatch.setattr(builder_mod, "_default_build_agent", fake_build)
    monkeypatch.setattr(
        agents_mod.Runner, "run_streamed", staticmethod(fake_run_streamed)
    )
    monkeypatch.delenv("EPH_RUN_KEY", raising=False)

    sentinel_provider = object()
    session = SimpleNamespace(context={"workspace_root": "/from/session"})

    out = await run_agent_once(
        spec,
        "do the thing",
        model="override-model",
        model_provider=sentinel_provider,
        env_overrides={"EPH_RUN_KEY": "present"},
        session=session,
        context={"task_scope": "x"},
        max_turns=3,
    )

    assert out == "the answer"
    assert captured["built_spec"] is spec
    assert spec.model_name == "override-model"
    assert captured["agent"] == "BUILT_AGENT"
    assert captured["prompt"] == "do the thing"
    assert captured["context"] == {
        "workspace_root": "/from/session",
        "task_scope": "x",
    }
    assert captured["max_turns"] == 3
    assert captured["run_config"].model_provider is sentinel_provider
    # env override applied during the run, restored after
    assert captured["env_at_run"] == "present"
    assert "EPH_RUN_KEY" not in os.environ


@pytest.mark.asyncio
async def test_run_agent_once_explicit_workspace_root_wins(monkeypatch):
    captured = {}

    async def fake_build(*, settings, mcp_servers, spec, session):
        return "A"

    def fake_run_streamed(agent, prompt, *, context, max_turns, run_config):
        captured["context"] = context
        return _FakeStream("ok")

    import omniagents.core.agents.builder as builder_mod
    import agents as agents_mod

    monkeypatch.setattr(builder_mod, "_default_build_agent", fake_build)
    monkeypatch.setattr(
        agents_mod.Runner, "run_streamed", staticmethod(fake_run_streamed)
    )

    session = SimpleNamespace(context={"workspace_root": "/from/session"})
    await run_agent_once(
        SimpleNamespace(model_name="m"),
        "p",
        session=session,
        workspace_root="/explicit",
    )
    assert captured["context"]["workspace_root"] == "/explicit"
