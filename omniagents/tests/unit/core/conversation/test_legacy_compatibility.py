"""Sessions stored by the PREVIOUS release must stay readable.

The fixture is a real prior-version STORAGE round-trip: conversation
content written through the ``Session.append_message`` of ``omniagents`` at
commit 385e7b7 — the tip of ``main`` before the canonical domain objects
existed — and read back with that build's ``history_db.load_history()``,
with ``sys.path`` pointed at that worktree and ``omniagents.__file__``
asserted to resolve there.

Being precise about what that does and does not prove: the row SHAPES and
the serialization are the previous release's, so this is a genuine
compatibility test against prior-version storage output. The conversation
content itself was authored for the test — it is not a captured transcript
from a live agent run.

Regeneration procedure is recorded in the fixture's own ``_comment``.
"""

import json
from pathlib import Path

import pytest

from omniagents.core.conversation.service import ConversationService
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.session.history_db import append_message, register_session

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "legacy_session_history_385e7b7.json"
)


@pytest.fixture
def legacy():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


@pytest.fixture
def seeded(legacy, store):
    """Replay the prior release's rows into this release's history table."""
    session_id = legacy["session_id"]
    register_session(session_id)
    for message in legacy["history"]:
        append_message(session_id, message)
    return session_id, ConversationService(store=store)


def test_the_fixture_is_a_prior_version_round_trip(legacy):
    assert legacy["generated_from_commit"] == "385e7b7"
    assert len(legacy["history"]) == 18


def test_a_prior_version_session_reads_as_a_thread(seeded):
    session_id, service = seeded
    thread = service.get_thread(session_id)
    assert thread["thread_id"] == session_id
    assert thread["source"] == "adapter"
    assert thread["turn_count"] == 3
    assert thread["item_count"] == 13


def test_call_output_pairs_collapse_into_single_completed_items(seeded):
    session_id, service = seeded
    items = service.list_items(session_id, limit=100)["items"]
    tools = [item for item in items if item["kind"] == "tool_call"]
    assert len(tools) == 5  # 10 raw rows (5 calls + 5 outputs)
    assert all(tool["status"] == "completed" for tool in tools)
    assert [tool["content"]["tool"] for tool in tools] == [
        "read_file",
        "grep_files",
        "write_file",
        "execute_bash",
        "mcp_docs__search",
    ]
    assert [tool["content"]["tool_kind"] for tool in tools] == [
        "file",
        "search",
        "file",
        "command",
        "mcp",
    ]
    assert tools[0]["content"]["output"] == "def parse(s): ..."


def test_every_projected_item_is_terminal(seeded):
    """A conversation that already finished must not read as if work is
    still in flight."""
    session_id, service = seeded
    items = service.list_items(session_id, limit=100)["items"]
    assert items
    assert {item["status"] for item in items} == {"completed"}


def test_turn_boundaries_are_inferred_at_user_messages(seeded):
    session_id, service = seeded
    turns = service.list_turns(session_id)["turns"]
    assert [turn["ordinal"] for turn in turns] == [1, 2, 3]
    assert all(turn["source"] == "adapter" for turn in turns)
    assert all(turn["turn_id"].startswith("trn_legacy_") for turn in turns)
    assert all(turn["status"] == "completed" for turn in turns)


def test_the_compaction_checkpoint_is_recovered(seeded):
    session_id, service = seeded
    items = service.list_items(session_id, limit=100)["items"]
    checkpoints = [item for item in items if item["kind"] == "compaction"]
    assert len(checkpoints) == 1
    assert checkpoints[0]["content"]["checkpoint_kind"] == "context_summary"
    thread = service.get_thread(session_id)
    assert thread["compaction"]["checkpoint_item_id"] == checkpoints[0]["item_id"]
    assert thread["compaction"]["compacted_below_seq"] == checkpoints[0]["seq"]


def test_reasoning_and_messages_survive_the_projection(seeded):
    session_id, service = seeded
    items = service.list_items(session_id, limit=100)["items"]
    kinds = [item["kind"] for item in items]
    assert kinds == [
        "user_message",
        "reasoning",
        "tool_call",
        "tool_call",
        "agent_message",
        "tool_call",
        "tool_call",
        "agent_message",
        "user_message",
        "compaction",
        "user_message",
        "tool_call",
        "agent_message",
    ]
    messages = [item for item in items if item["kind"] == "agent_message"]
    assert [message["content"]["text"] for message in messages] == [
        "Found it.",
        "Test added and passing.",
        "Done.",
    ]


def test_pagination_works_over_a_migrated_thread(seeded):
    session_id, service = seeded
    seen = []
    cursor = None
    while True:
        page = service.list_items(session_id, limit=4, cursor=cursor)
        seen.extend(item["seq"] for item in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert seen == list(range(1, 14))


def test_migration_is_stable_across_repeated_reads(seeded):
    session_id, service = seeded
    first = service.list_items(session_id, limit=100)["items"]
    second = service.list_items(session_id, limit=100)["items"]
    assert first == second
