import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from agents import Agent, function_tool
from agents.tool_context import ToolContext
from omniagents.core.orchestration.runtime import execute
from omniagents.core.orchestration.dispatch import Dispatcher
from omniagents.core.tools.output_budget import install_output_budget
from omniagents.core.tools import result_store


@pytest.mark.asyncio
async def test_parallel_calls_and_unawaited_calls_settle():
    calls = []

    async def dispatch(method, params):
        await asyncio.sleep(0.01)
        calls.append(params["name"])
        return {"value": params["name"]}

    result = await execute(
        'const x = await Promise.all([tools.call("a"), tools.call("b")]); text(x.map(v=>v.value)); tools.call("c");',
        dispatch,
    )
    assert json.loads(result["output"]) == ["a", "b"]
    assert sorted(calls) == ["a", "b", "c"]
    assert not result["errors"]


@pytest.mark.asyncio
async def test_no_host_apis_and_fresh_runtime():
    dispatch = AsyncMock()
    result = await execute(
        "globalThis.secret=123; text([typeof process, typeof require, typeof fetch, typeof std, typeof os]);",
        dispatch,
    )
    assert json.loads(result["output"]) == ["undefined"] * 5
    assert (await execute("text(typeof secret)", dispatch))["output"] == "undefined"
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_cpu_limit_interrupts_loop():
    with pytest.raises(RuntimeError, match="interrupted|CPU"):
        await asyncio.wait_for(
            execute("while(true) {}", AsyncMock(), cpu_seconds=0.05), 5
        )


@pytest.mark.asyncio
async def test_memory_limit_and_unresolved_promise():
    result = await execute(
        'const x=[]; while(true) x.push(new Array(100000).fill("x"));', AsyncMock()
    )
    assert result["errors"]
    with pytest.raises(RuntimeError, match="unresolved promise"):
        await execute("await new Promise(()=>{})", AsyncMock())


@pytest.mark.asyncio
async def test_emission_budget_and_error_after_call():
    dispatched = AsyncMock(return_value={"value": "done"})
    result = await execute(
        'await tools.call("one"); text("x".repeat(80000)); throw Error("later failed")',
        dispatched,
    )
    assert len(result["output"]) == 40000
    assert "Emitted output truncated" in result["errors"]
    assert any("later failed" in e for e in result["errors"])
    assert dispatched.await_count == 1


@pytest.mark.asyncio
async def test_approval_wait_does_not_consume_compute_watchdog():
    # The tool wait outlasts the watchdog three times over. The watchdog still
    # covers worker startup and its final reply, which a fully loaded machine
    # (the parallel suite) can starve for well over half a second.
    async def dispatch(*args):
        await asyncio.sleep(15.0)
        return "approved"

    result = await execute(
        'text(await tools.call("slow"))',
        dispatch,
        watchdog_seconds=5.0,
        cpu_seconds=0.05,
    )
    assert result["output"] == "approved"


@pytest.mark.asyncio
async def test_cancel_propagates_without_replay():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def dispatch(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    task = asyncio.create_task(execute('await tools.call("sideeffect")', dispatch))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    return ("product", "agent")


def test_durable_results_small_structured_and_scoped(scope):
    first = result_store.retain(scope, "one", "foo", "c1", {"rows": [1, 2]})
    second = result_store.retain(scope, "one", "foo", "c2", "small")
    assert result_store.get_result(scope, "one", first["id"])["value"] == {
        "rows": [1, 2]
    }
    page = result_store.list_results(scope, "one", limit=1)
    assert page["results"][0]["id"] == first["id"]
    assert (
        result_store.list_results(scope, "one", after=page["next_after"])["results"][0][
            "id"
        ]
        == second["id"]
    )
    with pytest.raises(ValueError, match="unavailable"):
        result_store.get_result(scope, "two", first["id"])
    with pytest.raises(ValueError):
        result_store.get_result(scope, "one", "../escape")


def test_large_result_reference_is_readable(scope):
    envelope = result_store.retain(scope, "one", "foo", "c", "a" * 1_100_000)
    fetched = result_store.get_result(scope, "one", envelope["id"])
    assert fetched["inline"] is False
    assert "value" not in fetched
    assert (
        result_store.read_output(scope, "one", envelope["id"], 1_000_000, 10)["text"]
        == "a" * 10
    )


@pytest.mark.asyncio
async def test_nested_dispatch_requires_approval_and_retains_original(scope):
    calls = []

    @function_tool(needs_approval=True)
    async def inspect_file() -> str:
        """Return a long minified line."""
        calls.append("ran")
        return "a" * 90000 + "NEEDLE" + "b" * 90000

    agent = Agent(name="test", tools=[inspect_file])
    install_output_budget(agent, scope)
    context = ToolContext(
        context={"session_id": "one"},
        tool_name="exec_javascript",
        tool_call_id="outer",
        tool_arguments="{}",
        agent=agent,
    )
    service = SimpleNamespace(
        request_tool_approval=AsyncMock(return_value={"decision": "reject"})
    )
    hooks = SimpleNamespace(
        assert_owner=lambda: None,
        on_tool_start=AsyncMock(),
        on_tool_end=AsyncMock(),
        run_id="run",
    )
    dispatcher = Dispatcher(
        context, agent, SimpleNamespace(id="one"), service, scope, hooks
    )
    dispatcher._event = AsyncMock()
    dispatcher.tools = {t.name: t for t in await agent.get_all_tools(context)}
    with pytest.raises(PermissionError):
        await dispatcher("tools.call", {"name": "inspect_file"})
    assert calls == []
    hooks.on_tool_start.assert_not_called()
    service.request_tool_approval.return_value = {"decision": "approve"}
    result = await execute(
        'const r=await tools.call("inspect_file"); text(r.value.slice(90000,90006)); throw Error("later")',
        dispatcher,
    )
    assert result["output"] == "NEEDLE"
    assert calls == ["ran"]
    hooks.on_tool_start.assert_awaited_once()
    hooks.on_tool_end.assert_awaited_once()
    reference = result_store.list_results(scope, "one")["results"][0]["id"]
    assert len(result_store.get_result(scope, "one", reference)["value"]) == 180006
    preview = hooks.on_tool_end.call_args.args[-1]
    assert len(str(preview).encode()) <= 40000
    assert "NEEDLE" not in str(preview)
    assert preview.ui_metadata["output_retention"]["reference"] == reference


@pytest.mark.asyncio
async def test_transcript_is_current_session_only_and_excludes_reasoning():
    service = SimpleNamespace(
        list_items=AsyncMock(return_value={"items": [], "next_cursor": None})
    )
    hooks = SimpleNamespace(assert_owner=lambda: None)
    dispatcher = Dispatcher(
        None, None, SimpleNamespace(id="owner"), service, (None, None), hooks
    )
    assert (await dispatcher("transcript.list", {"limit": 10}))["items"] == []
    assert service.list_items.call_args.args == ("owner",)
    assert "reasoning" not in service.list_items.call_args.kwargs["kinds"]
    with pytest.raises(ValueError):
        await dispatcher("transcript.list", {"thread_id": "other"})


@pytest.mark.asyncio
async def test_storage_failure_does_not_repeat_side_effect(scope, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(result_store, "retain", fail)
    calls = []

    @function_tool
    async def effect() -> str:
        """A side effect."""
        calls.append(1)
        return "done"

    agent = Agent(name="test", tools=[effect])
    install_output_budget(agent, scope)
    ctx = ToolContext(
        context={"session_id": "one"},
        tool_name="effect",
        tool_call_id="c",
        tool_arguments="{}",
        agent=agent,
    )
    wrapped = next(t for t in await agent.get_all_tools(ctx) if t.name == "effect")
    envelope, preview = await wrapped._invoke_retained(ctx, "{}")
    assert envelope["retention"] == "unavailable"
    assert envelope["value"] == "done"
    assert str(preview) == "done"
    assert calls == [1]


@pytest.mark.asyncio
async def test_lost_owner_after_approval_prevents_dispatch(scope):
    owner = [True]

    def assert_owner():
        if not owner[0]:
            raise RuntimeError("ownership lost")

    async def approve(**kwargs):
        owner[0] = False
        return {"decision": "approve"}

    @function_tool(needs_approval=True)
    async def effect() -> str:
        """Do something."""
        raise AssertionError("must not run")

    agent = Agent(name="test", tools=[effect])
    ctx = ToolContext(
        context={"session_id": "one"},
        tool_name="exec_javascript",
        tool_call_id="c",
        tool_arguments="{}",
        agent=agent,
    )
    hooks = SimpleNamespace(
        assert_owner=assert_owner, on_tool_start=AsyncMock(), run_id="run"
    )
    dispatcher = Dispatcher(
        ctx,
        agent,
        SimpleNamespace(id="one"),
        SimpleNamespace(request_tool_approval=approve),
        scope,
        hooks,
    )
    dispatcher.tools = {"effect": effect}
    with pytest.raises(RuntimeError, match="ownership lost"):
        await dispatcher("tools.call", {"name": "effect"})
    hooks.on_tool_start.assert_not_called()


@pytest.mark.asyncio
async def test_retention_preserves_media_structure(scope):
    from agents.tool import ToolOutputText, ToolOutputImage

    result = result_store.retain(
        scope,
        "one",
        "media",
        "c",
        [
            ToolOutputText(text="hello"),
            ToolOutputImage(image_url="data:image/png;base64,AAAA"),
        ],
    )
    saved = result_store.get_result(scope, "one", result["id"])["value"]
    assert saved[0] == {"type": "text", "text": "hello"}
    assert saved[1]["image_url"] == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_catalog_excludes_disabled_and_recursive_tools(scope, monkeypatch):
    from omniagents.core.orchestration import dispatch

    @function_tool
    async def allowed() -> str:
        """A supported tool."""
        return "ok"

    @function_tool(is_enabled=False)
    async def disabled() -> str:
        """Disabled tool."""
        raise AssertionError("must not run")

    @function_tool
    async def guarded() -> str:
        """SDK guarded tool stays direct-only."""
        raise AssertionError("must not run")

    @function_tool
    async def recursive() -> str:
        """Orchestration cannot recursively call itself."""
        raise AssertionError("must not run")

    setattr(guarded, "tool_input_guardrails", [object()])
    setattr(recursive, "_orchestrator", True)
    agent = Agent(name="test", tools=[allowed, disabled, guarded, recursive])
    install_output_budget(agent, scope)
    ctx = ToolContext(
        context={"session_id": "one"},
        tool_name="exec_javascript",
        tool_call_id="c",
        tool_arguments="{}",
        agent=agent,
    )
    session = SimpleNamespace(id="one", active_run_id="run")
    service = SimpleNamespace(
        _session_manager=SimpleNamespace(_project_slug=scope[0], _agent_slug=scope[1])
    )
    monkeypatch.setattr(dispatch, "current_service", lambda: service)
    monkeypatch.setattr(dispatch, "session_from_ctx", lambda ctx: session)
    dispatcher = await Dispatcher.create(ctx)
    names = {tool["name"] for tool in await dispatcher("tools.list", {})}
    assert names == {"allowed", "guarded", "read_tool_output"}
    for name in ("disabled", "recursive", "invented"):
        with pytest.raises(ValueError, match="unavailable"):
            await dispatcher("tools.call", {"name": name})
