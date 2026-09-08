"""Exercise the real SDK's persistence/event ordering without network calls."""

import pytest
from agents import Agent, Model, RunConfig, Runner
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
)

from omniagents.core.conversation.model import ItemKind
from omniagents.core.conversation.recorder import recorder_for_session
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import get_conversation_store
from omniagents.core.session.manager import Session


class CompletedResponseModel(Model):
    async def get_response(self, *args, **kwargs):
        raise AssertionError("Only streaming is expected")

    async def stream_response(self, *args, **kwargs):
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=0,
            response=Response(
                id="resp_persisted",
                created_at=0,
                object="response",
                model="offline-test",
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                output=[
                    ResponseOutputMessage(
                        id="msg_persisted",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            {
                                "type": "output_text",
                                "text": "saved answer",
                                "annotations": [],
                            }
                        ],
                    )
                ],
            ),
        )


class FailAfterSavingSession(Session):
    async def add_items(self, items):
        await super().add_items(items)
        if any(item.get("id") == "msg_persisted" for item in items):
            raise RuntimeError("post-persistence failure")


@pytest.mark.asyncio
async def test_sdk_persisted_response_survives_failure_before_event_consumption():
    session = FailAfterSavingSession("p", "a")
    session.active_run_id = "run_saved"
    recorder = recorder_for_session(session)
    recorder.ensure_thread(session.id)
    recorder.open_turn(session.id, "run_saved", prompt="question", prompt_role="user")
    result = Runner.run_streamed(
        Agent(name="offline", model=CompletedResponseModel()),
        "question",
        session=session,
        run_config=RunConfig(tracing_disabled=True),
    )
    # A slow consumer lets the producer finish persisting and fail first.
    # This uses the real SDK task/queue/error policy, not a mocked event loop.
    with pytest.raises(RuntimeError, match="post-persistence failure"):
        await result.run_loop_task
    received = []
    with pytest.raises(RuntimeError, match="post-persistence failure"):
        async for event in result.stream_events():
            received.append(event)
    assert not any(
        getattr(getattr(event, "item", None), "type", None) == "message_output_item"
        for event in received
    )
    reloaded = Session("p", "a", session.id)
    assert any(row.get("id") == "msg_persisted" for row in reloaded.history)
    store = get_conversation_store("p", "a")
    messages = [
        item
        for item in store.list_items(session.id, limit=100)
        if item.kind == ItemKind.AGENT_MESSAGE
    ]
    assert len(messages) == 1
    assert messages[0].content == {
        "text": "saved answer",
        "message_id": "msg_persisted",
    }
    assert messages[0].turn_id == "run_saved"


@pytest.mark.parametrize("event_first", [False, True])
def test_persistence_and_event_converge_before_compaction(event_first):
    session = Session("p", "a")
    session.active_run_id = "run_saved"
    recorder = recorder_for_session(session)
    recorder.ensure_thread(session.id)
    recorder.open_turn(session.id, "run_saved", prompt="question", prompt_role="user")
    payload = {"run_id": "run_saved", "message_id": "msg_saved", "content": "answer"}
    if event_first:
        recorder.record(session.id, "message_output", payload)
    row = {
        "type": "message",
        "role": "assistant",
        "id": "msg_saved",
        "status": "completed",
        "content": [{"type": "output_text", "text": "answer"}],
    }
    session.append_message(row)
    session.append_message(dict(row))
    recorder.record_compaction_checkpoint(
        session.id,
        run_id="run_saved",
        summary="checkpoint",
        kind="context_summary",
        version=1,
    )
    recorder.record(session.id, "message_output", payload)
    items = recorder.store.list_items(session.id, limit=100)
    assert [item.kind for item in items] == [
        ItemKind.USER_MESSAGE,
        ItemKind.AGENT_MESSAGE,
        ItemKind.COMPACTION,
    ]
    assert items[1].turn_id == "run_saved"
    assert recorder.store.get_turn(session.id, "run_saved").item_count == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},
        {"status": "in_progress"},
        {"status": "incomplete"},
        {"role": "user"},
        {"type": "reasoning"},
        {"omniagents": {"kind": "context_summary", "version": 1}},
    ],
)
def test_persistence_does_not_promote_other_recording_paths(overrides):
    session = Session("p", "a")
    recorder = recorder_for_session(session)
    recorder.ensure_thread(session.id)
    row = {
        "type": "message",
        "role": "assistant",
        "id": "msg_saved",
        "status": "completed",
        "content": "answer",
        **overrides,
    }
    assert recorder.record_persisted_message(session.id, run_id=None, item=row) is None
    assert recorder.store.list_items(session.id, limit=100) == []


def test_persisted_message_notifications_keep_one_identity_and_owner():
    session = Session("p", "a")
    store = get_conversation_store("p", "a")
    updates = []
    recorder = ConversationRecorder(store, on_settled=updates.append)
    recorder.ensure_thread(session.id)
    row = {
        "type": "message",
        "role": "assistant",
        "id": "msg_saved",
        "status": "completed",
        "content": "answer",
    }
    recorder.record_persisted_message(session.id, run_id=None, item=row)
    recorder.record_persisted_message(session.id, run_id=None, item=row)
    assert len(updates) == 2
    assert updates[0] == updates[1]
    assert len(store.list_items(session.id, limit=100)) == 1
    assert updates[0].thread_id == session.id
    assert updates[0].source_ref["event"] == "message_output"


def test_sdk_resaving_projected_input_does_not_create_a_new_response():
    from omniagents.core.session.history_db import append_message

    row = {
        "type": "message",
        "role": "assistant",
        "id": "msg_historical",
        "status": "completed",
        "content": "older answer",
    }
    append_message("legacy", row, project_slug="p", agent_slug="a")
    session = Session("p", "a", "legacy")
    session.active_run_id = "new_run"
    recorder = recorder_for_session(session)
    recorder.ensure_thread(session.id)
    before = recorder.store.list_items(session.id, limit=100)
    assert len(before) == 1
    session.append_message(dict(row))
    assert recorder.store.list_items(session.id, limit=100) == before
