"""Every production call site that feeds the domain store, driven for real.

The recorder's own tests exercise it in isolation, which makes coverage
*look* complete while leaving the wiring undefended: deleting each hook
from its call site left the whole suite green. These tests drive the real
code paths — ``run_checkpoint_compaction``, ``AgentService.send_user_message``,
``tool_approval_response`` / ``mcp_approval_response``, ``archive_session``,
``delete_session``, and ``Session.append_message`` — so removing a hook
fails here.
"""

from __future__ import annotations

import asyncio

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core.conversation.model import ItemKind
from omniagents.core.conversation.service import ConversationService
from omniagents.core.conversation.store import (
    get_conversation_store,
    reset_conversation_store,
)


@pytest.fixture
def store():
    """The store the production code will resolve for the default shard."""
    reset_conversation_store()
    backend = get_conversation_store(None, None)
    yield backend
    reset_conversation_store()


def items_of(store, thread_id, kind=None):
    rows = store.list_items(thread_id, limit=500)
    return [row for row in rows if kind is None or row.kind is kind]


# ---------------------------------------------------------------------
# Session.append_message -> reasoning items
# ---------------------------------------------------------------------


def test_appending_a_reasoning_row_records_a_reasoning_item(store):
    from omniagents.core.session.manager import Session

    session = Session(None, None, session_id="sess-reasoning")
    session.active_run_id = "run1"
    session.append_message(
        {
            "type": "reasoning",
            "id": "rs_42",
            "summary": [{"type": "summary_text", "text": "think first"}],
        }
    )

    [reasoning] = items_of(store, "sess-reasoning", ItemKind.REASONING)
    assert reasoning.content["summary"] == "think first"
    assert reasoning.content["reasoning_id"] == "rs_42"
    assert reasoning.turn_id == "run1"


def test_appending_a_plain_message_records_no_reasoning_item(store):
    from omniagents.core.session.manager import Session

    session = Session(None, None, session_id="sess-plain")
    session.append_message({"role": "user", "content": "hi"})
    assert items_of(store, "sess-plain", ItemKind.REASONING) == []


# ---------------------------------------------------------------------
# run_checkpoint_compaction -> compaction items
# ---------------------------------------------------------------------


def test_running_a_checkpoint_compaction_records_a_compaction_item(store):
    from omniagents.core.runtime.compaction import run_checkpoint_compaction
    from omniagents.core.session.manager import Session

    session = Session(None, None, session_id="sess-compact")
    session.append_message({"role": "user", "content": "do a lot of work"})
    session.append_message({"role": "assistant", "content": "did it"})

    async def summarize(prompt: str) -> str:
        return "the work so far"

    asyncio.run(run_checkpoint_compaction(session, summarize))

    [checkpoint] = items_of(store, "sess-compact", ItemKind.COMPACTION)
    assert "the work so far" in checkpoint.content["summary"]
    assert checkpoint.content["checkpoint_kind"] == "context_summary"

    thread = store.get_thread("sess-compact")
    assert thread.compaction["checkpoint_item_id"] == checkpoint.item_id
    assert thread.compaction["compacted_below_seq"] == checkpoint.seq


# ---------------------------------------------------------------------
# AgentService call sites
# ---------------------------------------------------------------------


@pytest.fixture
def service():
    from omniagents.core.agents.service import AgentService
    from omniagents.core.agents.specs import AgentSpec

    async def build_agent(settings, mcp, app):  # pragma: no cover - never run
        raise AssertionError("no agent build in these tests")

    spec = AgentSpec(
        name="CallSiteAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "hi",
        build_agent=build_agent,
    )
    return AgentService(
        spec, AgentHostResources.create("test-unit-core-conversation-call_sites-host")
    )


def test_send_user_message_records_a_user_message_item(store, service):
    session = service._session_manager.get_or_create("sess-send")
    # ``send_user_message`` resolves its session through the run map.
    service._session_manager._task_map["run-send"] = session

    asyncio.run(service.send_user_message("run-send", "one more thing"))

    texts = [
        item.content["text"]
        for item in items_of(store, "sess-send", ItemKind.USER_MESSAGE)
    ]
    assert texts == ["one more thing"]


def test_tool_approval_response_records_the_decision(store, service):
    session = service._session_manager.get_or_create("sess-approve")
    recorder_seed = {
        "session_id": "sess-approve",
        "run_id": "r1",
        "call_id": "c1",
        "tool_name": "execute_bash",
        "arguments": {"command": "rm -rf /"},
    }
    service._record_conversation(
        session,
        lambda rec: rec.record(
            "sess-approve", "tool_approval_requested", recorder_seed
        ),
    )

    async def respond():
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        service._pending_tool_approvals["c1"] = (future, session)
        await service.tool_approval_response("c1", "reject", rejection_message="nope")

    asyncio.run(respond())

    [approval] = items_of(store, "sess-approve", ItemKind.APPROVAL)
    assert approval.content["decision"] == "reject"
    assert approval.content["rejection_message"] == "nope"


def test_mcp_approval_response_records_the_decision(store, service):
    session = service._session_manager.get_or_create("sess-mcp")
    service._record_conversation(
        session,
        lambda rec: rec.record(
            "sess-mcp",
            "mcp_approval_requested",
            {
                "session_id": "sess-mcp",
                "run_id": "r1",
                "request_id": "q1",
                "server_label": "docs",
                "tool_name": "search",
                "arguments": {},
            },
        ),
    )

    async def respond():
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        service._pending_mcp_approvals["q1"] = (future, session)
        await service.mcp_approval_response("q1", "approve")

    asyncio.run(respond())

    [approval] = items_of(store, "sess-mcp", ItemKind.APPROVAL)
    assert approval.content["decision"] == "approve"


def test_archive_session_moves_the_thread_to_archived(store, service):
    from omniagents.core.session.history_db import append_message, register_session

    register_session("sess-archive")
    append_message("sess-archive", {"role": "user", "content": "hi"})
    service._session_manager.get_or_create("sess-archive")

    assert asyncio.run(service.archive_session("sess-archive")) is True
    assert store.get_thread("sess-archive").status.value == "archived"


def test_delete_session_removes_the_domain_rows(store, service):
    from omniagents.core.session.history_db import append_message, register_session

    register_session("sess-delete")
    append_message("sess-delete", {"role": "user", "content": "secret transcript"})

    conversation = ConversationService(store=store)
    assert conversation.list_items("sess-delete")["items"]
    assert store.get_thread("sess-delete") is not None

    assert asyncio.run(service.delete_session("sess-delete")) is True

    assert store.get_thread("sess-delete") is None
    assert store.list_items("sess-delete", limit=10) == []
    assert store.count_items("sess-delete") == 0


# ---------------------------------------------------------------------
# Materialization ordering — the guard now lives in append_message
# ---------------------------------------------------------------------


def test_an_assistant_role_wakeup_on_a_legacy_session_is_recorded_once(store):
    """REGRESSION (N1): background wakeups were recorded twice.

    ``start_run`` writes a non-user prompt to history ~400 lines before the
    ``run_started`` broadcast materializes the thread, so the projection
    picked up the row that had just been written and ``open_turn`` then
    recorded the same prompt again — one `adapter` item and one `recorded`
    item with identical content. It fired for every notification-batch,
    bash and worker wakeup on any session predating this build.

    This drives the real path: legacy history, then the exact
    append-then-broadcast sequence ``start_run`` performs for
    ``prompt_role="assistant"``.
    """
    from omniagents.core.conversation.model import ItemKind
    from omniagents.core.session.history_db import append_message, register_session
    from omniagents.core.session.manager import Session

    thread_id = "sess-wakeup"
    register_session(thread_id)
    append_message(thread_id, {"role": "user", "content": "earlier question"})
    append_message(thread_id, {"role": "assistant", "content": "earlier answer"})

    session = Session(None, None, session_id=thread_id)
    wakeup = "2 background jobs finished"
    # 1. start_run's append for a non-user prompt role.
    session.append_message(
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": wakeup, "annotations": []}],
            "type": "message",
        }
    )
    # 2. the run_started broadcast, which opens the turn and records the prompt.
    from omniagents.core.conversation.recorder import record_session_event

    record_session_event(
        session,
        "run_started",
        {
            "run_id": "run-wakeup",
            "session_id": thread_id,
            "prompt": wakeup,
            "prompt_role": "assistant",
        },
    )

    rows = store.list_items(thread_id, limit=100)
    wakeups = [
        item
        for item in rows
        if item.kind is ItemKind.AGENT_MESSAGE and item.content.get("text") == wakeup
    ]
    assert len(wakeups) == 1, [(i.seq, i.source, i.content.get("text")) for i in rows]
    # The pre-existing transcript is still there.
    texts = [item.content.get("text") for item in rows]
    assert "earlier question" in texts
    assert "earlier answer" in texts


def test_every_history_append_materializes_first(store):
    """The guard is structural, not per-call-site.

    Four separate call sites had to be fixed individually before the guard
    was moved into ``Session.append_message``. This pins that ANY append —
    including ones this feature never touched — materializes the thread
    before the row lands, so the pattern cannot recur.
    """
    from omniagents.core.session.manager import Session

    session = Session(None, None, session_id="sess-any-append")
    assert store.get_thread("sess-any-append") is None
    session.append_message({"role": "assistant", "content": "arbitrary"})
    thread = store.get_thread("sess-any-append")
    assert thread is not None
    assert thread.projected_at is not None


# ---------------------------------------------------------------------
# The fork materialization exemption
# ---------------------------------------------------------------------


def test_the_fork_loop_claims_no_thread_row(store):
    """``SessionManager.fork_session`` must leave the child unmaterialized.

    Every history write materializes the thread first
    (``_materialize_conversation_thread``), which is right everywhere except
    inside the fork's replay loop: the child is meant to be materialized by
    ``record_fork`` from the parent's richer DOMAIN rows. If the loop
    claimed the child instead, it would stamp ``projected_at`` while the
    child's history was still empty — and ``backfill_thread`` refuses a
    materialized thread forever.
    """
    from omniagents.core.session.manager import SessionManager

    manager = SessionManager(None, None)
    parent = manager.get_or_create("fork-parent")
    parent.append_message({"role": "user", "content": "q1"})
    parent.append_message({"role": "assistant", "content": "a1"})

    child = manager.fork_session(parent, new_session_id="fork-child")

    assert store.get_thread(child.id) is None, (
        "the fork loop claimed the child thread; record_fork can no longer "
        "materialize it, and a swallowed session_forked broadcast would "
        "leave the transcript permanently blank"
    )


def test_a_fork_whose_record_fork_never_runs_still_reads_back(store):
    """REGRESSION: the fork defect, reachable through a failure window.

    ``AgentService.fork_session`` broadcasts ``session_forked`` inside a
    bare ``try/except Exception: pass``, so ``record_fork`` not running is
    a reachable path — not a hypothetical. This drives exactly that: fork
    the session, never record it, then read the child.

    With the exemption the loop leaves no thread row, so the read projects
    the copied history and the transcript is intact. Without it the child
    is claimed at ``projected_at`` with empty history and reads back as 0
    items, permanently.
    """
    from omniagents.core.session.manager import SessionManager

    manager = SessionManager(None, None)
    parent = manager.get_or_create("fork-parent")
    parent.append_message({"role": "user", "content": "q1"})
    parent.append_message({"role": "assistant", "content": "a1"})

    child = manager.fork_session(parent, new_session_id="fork-child")
    # record_fork deliberately NOT called — the swallowed-broadcast path.

    page = ConversationService(store=store).list_items(child.id, limit=50)
    texts = [item["content"].get("text") for item in page["items"]]
    assert texts == ["q1", "a1"], page["items"]
