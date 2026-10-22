"""Compaction must see saved tool results and change the next real SDK request."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import Agent, RunConfig, Runner, function_tool

from omniagents.core.runtime.bridge import _strip_phase_from_model_input
from omniagents.core.runtime.compaction import make_auto_compact_token_hook
from omniagents.core.runtime.compaction_boundary import compaction_input_filter
from omniagents.core.session.manager import Session
from .test_tool_durability import ToolModel


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("approval", [False, True])
async def test_sdk_compacts_after_tool_save_and_sends_replacement(native, approval):
    session = Session("compaction", "boundary")
    entered = asyncio.Event()
    release = asyncio.Event()
    requests = []
    snapshots = []
    tool_calls = []

    class RecordingModel(ToolModel):
        async def stream_response(self, *args, **kwargs):
            requests.append(kwargs.get("input", args[1] if len(args) > 1 else None))
            if len(requests) == 2:
                self.calls = 0  # Ask for one more tool after compaction.
            async for event in super().stream_response(*args, **kwargs):
                if len(requests) <= 2:
                    event.response.id = f"response-{len(requests)}"
                    event.response.output[0].call_id = f"call-{len(requests)}"
                yield event

    @function_tool(needs_approval=approval)
    async def effect() -> str:
        """Produce a large result, after explicit release."""
        entered.set()
        await release.wait()
        tool_calls.append(True)
        return "large-result-" * 1000 if len(tool_calls) == 1 else "small result"

    async def server_call(*args, **kwargs):
        snapshots.append(await session.get_items())
        session.append_message(
            {
                "role": "assistant",
                "content": "checkpoint",
                "omniagents": {"kind": "context_summary", "version": 1},
            }
        )

    service = SimpleNamespace(
        _runtime_hooks={"token": [make_auto_compact_token_hook(state_key="test")]},
        server_call=server_call,
    )
    sdk_session = session
    if native:
        from omniagents.core.session.compaction import wrap_with_compaction

        async def endpoint(**kwargs):
            snapshots.append(await session.get_items())
            return SimpleNamespace(
                output=[{"role": "assistant", "content": "checkpoint"}]
            )

        sdk_session = wrap_with_compaction(
            session,
            {
                "enabled": True,
                "model": "gpt-6-astra",
                "threshold_tokens": 900,
                "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
            },
        )
        session._sdk_compaction_session = sdk_session
    agent = Agent(name="boundary", model=RecordingModel(), tools=[effect])
    result = Runner.run_streamed(
        agent,
        "use the tool",
        session=sdk_session,
        run_config=RunConfig(
            tracing_disabled=True,
            call_model_input_filter=compaction_input_filter(
                session, service, 1000, _strip_phase_from_model_input
            ),
        ),
    )
    try:
        if approval:
            async for _ in result.stream_events():
                pass
            assert snapshots == []
            assert len(result.interruptions) == 1
            state = result.to_state()
            state.approve(result.interruptions[0], always_approve=True)
            result = Runner.run_streamed(
                agent,
                state,
                session=sdk_session,
                run_config=RunConfig(
                    tracing_disabled=True,
                    call_model_input_filter=compaction_input_filter(
                        session, service, 1000, _strip_phase_from_model_input
                    ),
                ),
            )
        await asyncio.wait_for(entered.wait(), 2)
        assert snapshots == []
        release.set()
        async for _ in result.stream_events():
            pass
        assert len(snapshots) == 1
        assert any(row.get("type") == "function_call_output" for row in snapshots[0])
        assert len(requests) == 3
        assert requests[1] == [{"role": "assistant", "content": "checkpoint"}]
        assert "large-result" not in str(requests[2])
        assert "small result" in str(requests[2])
        assert "large-result" not in str(await session.get_items())
    finally:
        result.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["native", "pending", "disabled"])
async def test_boundary_does_not_compact_with_native_wrapper_or_pending_tool(guard):
    session = SimpleNamespace(
        history=[],
        variables={"test": {"auto_compact": guard != "disabled"}},
        get_items=AsyncMock(return_value=[]),
    )
    if guard == "native":
        session._sdk_compaction_session = object()
    items = [{"role": "user", "content": "x" * 8000}]
    if guard == "pending":
        items.append({"type": "function_call", "call_id": "pending"})
    service = SimpleNamespace(
        _runtime_hooks={"token": [make_auto_compact_token_hook(state_key="test")]},
        server_call=AsyncMock(),
    )
    data = SimpleNamespace(model_data=SimpleNamespace(input=items, instructions=""))
    result = await compaction_input_filter(
        session, service, 1000, _strip_phase_from_model_input
    )(data)
    assert result.input == items
    service.server_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails_again", [False, True])
async def test_real_bridge_overflow_recovery_uses_native_once(fails_again):
    from omniagents.core.runtime.bridge import stream_agent
    from omniagents.core.runtime.compaction import make_auto_compact_error_hook
    from omniagents.core.session.compaction import wrap_with_compaction
    from omniagents.builtin.server_functions.compact import compact
    from .test_persisted_response import CompletedResponseModel

    requests = []

    class OverflowModel(CompletedResponseModel):
        async def stream_response(self, *args, **kwargs):
            requests.append(kwargs.get("input", args[1] if len(args) > 1 else None))
            if len(requests) == 1 or fails_again:
                raise RuntimeError("maximum context length exceeded")
            async for event in super().stream_response(*args, **kwargs):
                yield event

    session = Session("native", "recovery")
    endpoint = AsyncMock(
        return_value=SimpleNamespace(
            output=[{"role": "user", "content": "compact state"}]
        )
    )
    native = wrap_with_compaction(
        session,
        {
            "enabled": True,
            "model": "gpt-6-astra",
            "threshold_tokens": 100000,
            "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
        },
    )
    session._sdk_compaction_session = native
    hook = make_auto_compact_error_hook(state_key="test")

    class Service:
        _runtime_hooks = {}
        set_client_status = AsyncMock()

        async def server_call(self, *args, **kwargs):
            return await compact(self, session)

        async def invoke_runtime_hooks(self, event, **kwargs):
            return await hook(service=self, event=event, **kwargs)

    async def run():
        return await stream_agent(
            Agent(name="recovery", model=OverflowModel()),
            "user request",
            session,
            None,
            service=Service(),
            sdk_session=native,
        )

    if fails_again:
        with pytest.raises(RuntimeError, match="maximum context length"):
            await run()
    else:
        await run()
    endpoint.assert_awaited_once()
    assert len(requests) == 2
    assert requests[1] == [{"role": "user", "content": "compact state"}]
    assert sum(row.get("content") == "user request" for row in session.history) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_compaction_before_the_first_call_keeps_the_new_message(native):
    # Saved context is already over the threshold (a large image does this),
    # so compaction runs before the turn's first model call — when the new
    # message is not saved yet. It must still reach the model.
    from .test_persisted_response import CompletedResponseModel

    session = Session("compaction", "first-call")
    session.append_message({"role": "user", "content": "earlier " + "x" * 8000})
    session.append_message({"role": "assistant", "content": "earlier answer"})
    requests = []

    class RecordingModel(CompletedResponseModel):
        async def stream_response(self, *args, **kwargs):
            requests.append(kwargs.get("input", args[1] if len(args) > 1 else None))
            async for event in super().stream_response(*args, **kwargs):
                yield event

    async def server_call(*args, **kwargs):
        session.append_message(
            {
                "role": "assistant",
                "content": "checkpoint",
                "omniagents": {"kind": "context_summary", "version": 1},
            }
        )

    service = SimpleNamespace(
        _runtime_hooks={"token": [make_auto_compact_token_hook(state_key="test")]},
        server_call=server_call,
    )
    sdk_session = session
    if native:
        from omniagents.core.session.compaction import wrap_with_compaction

        async def endpoint(**kwargs):
            return SimpleNamespace(
                output=[{"role": "assistant", "content": "checkpoint"}]
            )

        sdk_session = wrap_with_compaction(
            session,
            {
                "enabled": True,
                "model": "gpt-6-astra",
                "threshold_tokens": 900,
                "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
            },
        )
        session._sdk_compaction_session = sdk_session
    result = Runner.run_streamed(
        Agent(name="first-call", model=RecordingModel()),
        "the new question",
        session=sdk_session,
        run_config=RunConfig(
            tracing_disabled=True,
            call_model_input_filter=compaction_input_filter(
                session, service, 1000, _strip_phase_from_model_input
            ),
        ),
    )
    async for _ in result.stream_events():
        pass
    assert "checkpoint" in str(requests[0])
    assert requests[0][-1]["content"] == "the new question"
    assert (
        sum(
            row.get("content") == "the new question"
            for row in await session.get_items()
        )
        == 1
    )
