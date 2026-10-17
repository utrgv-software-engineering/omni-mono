import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from agents import Agent, function_tool
from agents.tool_context import ToolContext
from agents.tool_guardrails import tool_output_guardrail, ToolGuardrailFunctionOutput
from omniagents.core.orchestration import cells, subagents
from omniagents.core.tools import result_store
from omniagents.core.tools.output_budget import install_output_budget
from omniagents.core.runtime.tool_durability import DurableToolHooks, active_tool_hooks
from omniagents.core.session.manager import Session
from omniagents.core.conversation.recorder import recorder_for_session
from omniagents.core.conversation.service import ConversationService
from omniagents.core.orchestration.dispatch import Dispatcher


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    return ("product", "agent")


def dispatcher(scope, session_id="one", run_id="run"):
    return SimpleNamespace(
        scope=scope,
        session=SimpleNamespace(id=session_id),
        hooks=SimpleNamespace(run_id=run_id, assert_owner=lambda: None),
    )


@pytest.mark.asyncio
async def test_yield_wait_and_durable_completion(scope, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def execute(code, dispatch, on_output):
        await on_output({"output": "first", "errors": []})
        started.set()
        await finish.wait()
        return {"output": "first second", "errors": []}

    monkeypatch.setattr(cells, "execute", execute)
    d = dispatcher(scope)
    first = await cells.start(d, "", 0)
    assert first["status"] == "running"
    await started.wait()
    page = await cells.wait(d, first["cell_id"], 0)
    assert page["output"] == "first"
    with pytest.raises(ValueError, match="unavailable"):
        await cells.wait(dispatcher(scope, "other"), first["cell_id"], 0)
    finish.set()
    done = await cells.wait(d, first["cell_id"], 1000, page["next_offset"])
    assert done["output"] == " second"
    assert done["status"] == "completed"
    assert not cells._live
    assert (await cells.wait(dispatcher(scope, run_id="later"), first["cell_id"], 0))[
        "output"
    ] == "first second"


@pytest.mark.asyncio
async def test_cancellation_and_run_cleanup(scope, monkeypatch):
    started = asyncio.Event()

    async def execute(code, dispatch, on_output):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(cells, "execute", execute)
    d = dispatcher(scope)
    first = await cells.start(d, "", 0)
    await started.wait()
    with pytest.raises(ValueError, match="different active run"):
        await cells.wait(dispatcher(scope, run_id="other"), first["cell_id"], 0)
    await cells.close_run("one", "unrelated")
    assert (await cells.wait(d, first["cell_id"], 0))["status"] == "running"
    await cells.close_run("one", "run")
    assert (await cells.wait(d, first["cell_id"], 0))["status"] == "cancelled"
    assert not cells._live


@pytest.mark.asyncio
async def test_crashed_cell_is_not_replayed(scope):
    cells._save(
        scope,
        "one",
        "dead",
        "old",
        "running",
        {"output": "already emitted", "errors": []},
    )
    row = await cells.wait(dispatcher(scope), "dead", 0)
    assert row["status"] == "interrupted"
    assert row["output"] == "already emitted"
    assert "not replayed" in row["errors"][0]


@pytest.mark.asyncio
async def test_output_guardrail_precedes_capture(scope):
    @tool_output_guardrail
    def redact(data):
        assert data.output == "SECRET"
        return ToolGuardrailFunctionOutput.reject_content("redacted")

    @function_tool(tool_output_guardrails=[redact])
    async def secret() -> str:
        """A guarded result."""
        return "SECRET"

    agent = Agent(name="guarded", tools=[secret])
    install_output_budget(agent, scope)
    ctx = ToolContext(
        context={"session_id": "one"},
        agent=agent,
        tool_name="secret",
        tool_call_id="c",
        tool_arguments="{}",
    )
    tool = next(t for t in await agent.get_all_tools(ctx) if t.name == "secret")
    envelope, preview = await tool._invoke_retained(ctx, "{}")
    assert envelope["value"] == "redacted"
    assert result_store.get_result(scope, "one", envelope["id"])["value"] == "redacted"
    assert not tool.tool_output_guardrails  # SDK must not run them twice.


@pytest.mark.asyncio
async def test_subagent_transcript_survives_invocation_and_is_scoped(
    scope, monkeypatch
):
    parent = Session(*scope)
    parent.id = "parent"
    parent.active_run_id = "run"
    recorder = recorder_for_session(parent)
    recorder.ensure_thread(parent.id)
    hooks = DurableToolHooks(parent, "run")
    token = active_tool_hooks.set(hooks)
    monkeypatch.setattr(subagents, "session_from_ctx", lambda ctx: parent)

    async def invoke(ctx, args):
        child = subagents.active_child.get()
        child.recorder.record(
            child.thread_id,
            "message_output",
            {
                "run_id": child.run_id,
                "message_id": "message",
                "content": "child answer",
            },
        )
        return "child answer"

    tool = subagents.wrap_agent_tool(SimpleNamespace(on_invoke_tool=invoke), "explorer")
    ctx = ToolContext(
        context={"session_id": "parent"},
        tool_name="explore",
        tool_call_id="child-call",
        tool_arguments="{}",
    )
    try:
        assert await tool.on_invoke_tool(ctx, "task") == "child answer"
    finally:
        active_tool_hooks.reset(token)
    service = SimpleNamespace(
        _conversation_service=lambda: ConversationService(
            project_slug=scope[0], agent_slug=scope[1]
        )
    )
    d = Dispatcher(ctx, None, parent, service, scope, hooks)
    listed = await d("subagents.list", {})
    child_id = listed["threads"][0]["thread_id"]
    transcript = await d("subagents.transcript", {"id": child_id})
    assert any(
        item["content"].get("text") == "child answer" for item in transcript["items"]
    )
    assert subagents.active_child.get() is None
    outsider = Dispatcher(
        ctx,
        None,
        SimpleNamespace(id="other", user_id=None),
        service,
        scope,
        SimpleNamespace(assert_owner=lambda: None),
    )
    with pytest.raises(ValueError, match="unavailable"):
        await outsider("subagents.transcript", {"id": child_id})


@pytest.mark.asyncio
async def test_guardrail_tripwire_cannot_be_caught_in_javascript(scope):
    from agents.exceptions import ToolInputGuardrailTripwireTriggered
    from omniagents.core.orchestration.runtime import execute

    error = ToolInputGuardrailTripwireTriggered(
        object(), ToolGuardrailFunctionOutput.allow()
    )

    async def dispatch(method, params):
        raise error

    with pytest.raises(ToolInputGuardrailTripwireTriggered):
        await execute(
            'try { await tools.call("blocked") } catch(e) { text("ignored") }', dispatch
        )


@pytest.mark.asyncio
async def test_cell_tripwire_fences_further_execution(scope, monkeypatch):
    from agents.exceptions import ToolInputGuardrailTripwireTriggered

    error = ToolInputGuardrailTripwireTriggered(
        object(), ToolGuardrailFunctionOutput.allow()
    )

    async def execute(*args, **kwargs):
        raise error

    monkeypatch.setattr(cells, "execute", execute)
    d = dispatcher(scope)
    try:
        with pytest.raises(ToolInputGuardrailTripwireTriggered):
            await cells.start(d, "", 1000)
        with pytest.raises(ToolInputGuardrailTripwireTriggered):
            cells.raise_fatal("one", "run")
    finally:
        await cells.close_run("one", "run")
    cells.raise_fatal("one", "run")


@pytest.mark.asyncio
async def test_native_media_is_validated_and_separate_from_text():
    from omniagents.core.orchestration.runtime import execute
    from omniagents.builtin.tools.javascript import format_result

    result = await execute(
        'image("data:image/png;base64,AAAA"); file({file_data:"data:application/pdf;base64,AAAA", filename:"report.pdf"}); text("selected")',
        AsyncMock(),
    )
    assert result["output"] == "selected"
    assert [part["type"] for part in result["media"]] == ["image", "file"]
    output = format_result(result)
    assert output[0].type == "text"
    assert output[1]["image_url"] == "data:image/png;base64,AAAA"
    with pytest.raises(Exception):
        await execute('image({detail:"bogus"})', AsyncMock())


def test_cells_are_discoverable_after_restart(scope):
    cells._save(
        scope, "one", "first", "old", "completed", {"output": "done", "errors": []}
    )
    cells._save(
        scope, "one", "second", "old", "running", {"output": "partial", "errors": []}
    )
    page = cells.list_cells(scope, "one", limit=1)
    assert page["cells"][0]["id"] == "first"
    last = cells.list_cells(scope, "one", after=page["next_after"])
    assert last["cells"][0]["status"] == "interrupted"
    assert not cells.list_cells(scope, "other")["cells"]


@pytest.mark.asyncio
async def test_worker_links_read_canonical_store_and_reject_other_parent(scope):
    parent = Session(*scope)
    parent.id = "parent-worker"
    parent.active_run_id = "run"
    recorder = recorder_for_session(parent)
    recorder.ensure_thread(parent.id)
    recorder.ensure_thread("worker-session", source="worker")
    recorder.record(
        "worker-session", "run_started", {"run_id": "worker-run", "prompt": "task"}
    )
    recorder.record(
        "worker-session",
        "message_output",
        {
            "run_id": "worker-run",
            "content": "persisted worker answer",
            "message_id": "answer",
        },
    )
    service = SimpleNamespace(
        get_session=lambda sid: parent if sid == parent.id else None,
        _conversation_service=lambda: ConversationService(
            project_slug=scope[0], agent_slug=scope[1]
        ),
    )
    worker_service = SimpleNamespace(
        _session_manager=SimpleNamespace(_project_slug=scope[0], _agent_slug=scope[1])
    )
    subagents.link_worker(
        service, parent.id, worker_service, "worker-session", "worker-id"
    )
    d = Dispatcher(
        None, None, parent, service, scope, SimpleNamespace(assert_owner=lambda: None)
    )
    page = await d("subagents.list", {})
    reference = page["threads"][0]["thread_id"]
    result = await d("subagents.transcript", {"id": reference})
    assert any(
        i["content"].get("text") == "persisted worker answer" for i in result["items"]
    )
    d.session = SimpleNamespace(id="other", user_id=None)
    with pytest.raises(ValueError, match="unavailable"):
        await d("subagents.transcript", {"id": reference})


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["host", "docker", "wasm", "remote"])
async def test_dispatch_preserves_bound_workspace_context(backend):
    from omniagents.workspace import set_workspace, reset_workspace, current_workspace
    from omniagents.core.orchestration.runtime import execute

    workspace = SimpleNamespace(backend=backend)
    token = set_workspace(workspace)

    async def dispatch(method, params):
        assert current_workspace() is workspace
        return {"value": backend}

    try:
        result = await execute('text((await tools.call("inspect")).value)', dispatch)
    finally:
        reset_workspace(token)
    assert result["output"] == backend
