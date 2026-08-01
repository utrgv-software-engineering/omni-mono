"""Postgres conversation store — the SAME contract as SQLite.

Skipped unless ``OMNIAGENTS_TEST_PG_URL`` is set (mirroring
``tests/unit/core/session/test_history_db_postgres.py``). Each test runs
under a unique ``tenant_id`` so concurrent runs against a shared database
cannot collide.

Run locally::

    docker run --rm -d --name pg-test -p 55432:5432 \\
        -e POSTGRES_PASSWORD=test postgres:16
    OMNIAGENTS_TEST_PG_URL=postgresql://postgres:test@localhost:55432/postgres \\
        pytest tests/unit/core/conversation/test_postgres_store.py

The store-contract classes are imported from ``test_store`` and re-run
against the PG backend, so the two backends cannot drift: a behaviour that
holds on SQLite and not on Postgres fails here.
"""

from __future__ import annotations

import os
import uuid

import pytest

from omniagents.core.conversation.model import ItemKind, ItemStatus, TurnStatus
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.service import ConversationService

from .test_store import (  # noqa: F401 — re-run against the PG backend
    TestItems,
    TestProjectionWrite,
    TestSeqAllocation,
    TestPageCounts,
    TestBulkThreadReads,
    TestThreadListing,
    TestThreadSearch,
    TestUnboundedDescendantWalk,
    TestThreads,
    TestTurns,
    make_item,
    make_thread,
    make_turn,
    seed_threads,
)

PG_URL = os.getenv("OMNIAGENTS_TEST_PG_URL")
try:  # the ``session-postgres`` extra; absent in the default dev env
    import psycopg  # noqa: F401
    import psycopg_pool  # noqa: F401

    _PSYCOPG = True
except ImportError:  # pragma: no cover - depends on the installed extras
    _PSYCOPG = False

pytestmark = pytest.mark.skipif(
    not PG_URL or not _PSYCOPG,
    reason="needs OMNIAGENTS_TEST_PG_URL and the 'session-postgres' extra",
)


@pytest.fixture
def store(monkeypatch):
    """A PG store under a throwaway tenant, torn down after the test."""
    from omniagents.core.conversation.store import PgConversationStore

    tenant = f"test_{uuid.uuid4().hex[:12]}"
    backend = PgConversationStore(PG_URL, tenant_id=tenant)
    yield backend
    # Every thread the test created, whoever owns it: the organization
    # suites seed hundreds, and a tenant left populated would make the next
    # run's ``total`` assertions wrong.
    from omniagents.core.conversation.model import UNSCOPED, ThreadFilter

    try:
        while True:
            rows = backend.list_threads(ThreadFilter(UNSCOPED), limit=500)
            if not rows:
                break
            for thread in rows:
                backend.delete_thread(thread.thread_id)
    except Exception:
        pass
    backend.close()


def test_the_shared_contract_suite_is_actually_rebound(store):
    """Guard against the import above silently running on SQLite."""
    from omniagents.core.conversation.store import PgConversationStore

    assert isinstance(store, PgConversationStore)


def test_tenants_are_isolated(monkeypatch):
    from omniagents.core.conversation.store import PgConversationStore

    left = PgConversationStore(PG_URL, tenant_id=f"test_{uuid.uuid4().hex[:12]}")
    right = PgConversationStore(PG_URL, tenant_id=f"test_{uuid.uuid4().hex[:12]}")
    try:
        left.upsert_thread(make_thread(title="left"))
        assert left.get_thread("t1").title == "left"
        assert right.get_thread("t1") is None
    finally:
        left.delete_thread("t1")
        left.close()
        right.close()


def test_shards_are_isolated_within_a_tenant():
    from omniagents.core.conversation.store import PgConversationStore

    tenant = f"test_{uuid.uuid4().hex[:12]}"
    alpha = PgConversationStore(PG_URL, tenant_id=tenant, project_slug="alpha")
    beta = PgConversationStore(PG_URL, tenant_id=tenant, project_slug="beta")
    try:
        alpha.upsert_thread(make_thread(title="alpha"))
        assert alpha.get_thread("t1").title == "alpha"
        assert beta.get_thread("t1") is None
    finally:
        alpha.delete_thread("t1")
        alpha.close()
        beta.close()


def test_jsonb_payloads_round_trip_with_nesting(store):
    store.upsert_thread(make_thread(usage={"nested": {"a": [1, 2, {"b": True}]}}))
    assert store.get_thread("t1").usage == {"nested": {"a": [1, 2, {"b": True}]}}


def test_a_recorded_conversation_reads_back_from_postgres(store):
    """The headline criterion, on the cloud backend."""
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("sess-1", user_id="u1")
    for method, payload in [
        ("run_started", {"run_id": "r1", "prompt": "hi", "prompt_role": "user"}),
        (
            "tool_called",
            {"run_id": "r1", "tool": "execute_bash", "input": "{}", "call_id": "c1"},
        ),
        (
            "tool_result",
            {"run_id": "r1", "tool": "execute_bash", "output": "ok", "call_id": "c1"},
        ),
        ("message_output", {"run_id": "r1", "content": "done"}),
        (
            "run_end",
            {"run_id": "r1", "end_reason": "completed", "usage": {"input_tokens": 5}},
        ),
    ]:
        recorder.record("sess-1", method, {"session_id": "sess-1", **payload})

    service = ConversationService(store=store)
    thread = service.get_thread("sess-1")
    assert thread["turn_count"] == 1
    assert thread["item_count"] == 3
    assert thread["usage"] == {"input_tokens": 5}
    assert (
        service.list_turns("sess-1")["turns"][0]["status"] == TurnStatus.COMPLETED.value
    )
    items = service.list_items("sess-1")["items"]
    assert [item["kind"] for item in items] == [
        ItemKind.USER_MESSAGE.value,
        ItemKind.TOOL_CALL.value,
        ItemKind.AGENT_MESSAGE.value,
    ]
    assert {item["status"] for item in items} == {ItemStatus.COMPLETED.value}


def test_pagination_over_a_large_thread(store):
    store.upsert_thread(make_thread(last_seq=1200, item_count=1200))
    for seq in range(1, 1201):
        store.upsert_item(make_item(seq))
    service = ConversationService(store=store)
    seen = []
    cursor = None
    while True:
        page = service.list_items("t1", limit=250, cursor=cursor)
        seen.extend(item["seq"] for item in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert seen == list(range(1, 1201))


# ---------------------------------------------------------------------
# Regressions with a Postgres-specific failure mode
# ---------------------------------------------------------------------


def test_reading_then_writing_destroys_nothing_on_postgres(store):
    """REGRESSION with a PG-specific shape.

    On SQLite the rolled-back ordinal cursor let an ``INSERT OR REPLACE``
    delete the row holding the reused seq. On Postgres the same rollback
    raised ``UniqueViolation`` on ``idx_conversation_items_seq``, which the
    recorder's best-effort handler swallowed — so every subsequent item on
    the thread was silently dropped instead. Both are fixed by the same
    monotonic cursor; this asserts the PG side.
    """
    from omniagents.core.session.history_db import append_message, register_session

    thread_id = "sess-1"
    register_session(thread_id)
    append_message(thread_id, {"role": "user", "content": "legacy opener"})

    service = ConversationService(store=store)
    recorder = ConversationRecorder(store)

    def run(run_id, prompt):
        for method, payload in [
            (
                "run_started",
                {"run_id": run_id, "prompt": prompt, "prompt_role": "user"},
            ),
            (
                "tool_called",
                {
                    "run_id": run_id,
                    "tool": "read_file",
                    "input": "{}",
                    "call_id": f"{run_id}-c1",
                },
            ),
            (
                "tool_result",
                {
                    "run_id": run_id,
                    "tool": "read_file",
                    "output": "x",
                    "call_id": f"{run_id}-c1",
                },
            ),
            ("message_output", {"run_id": run_id, "content": f"reply {run_id}"}),
            ("run_end", {"run_id": run_id, "end_reason": "completed"}),
        ]:
            recorder.record(thread_id, method, {"session_id": thread_id, **payload})

    service.list_items(thread_id)
    run("turn1", "first")
    service.list_items(thread_id)
    run("turn2", "second")

    items = service.list_items(thread_id, limit=100)["items"]
    seqs = [item["seq"] for item in items]
    assert len(seqs) == len(set(seqs)), f"duplicate ordinals: {seqs}"
    rendered = [
        item["content"].get("text") or item["content"].get("tool") for item in items
    ]
    assert "legacy opener" in rendered
    assert "reply turn1" in rendered and "reply turn2" in rendered
    assert rendered.count("read_file") == 2


def test_resuming_a_legacy_session_keeps_its_history_on_postgres(store):
    from omniagents.core.session.history_db import append_message, register_session

    thread_id = "sess-1"
    register_session(thread_id)
    for row in [
        {"role": "user", "content": "old question"},
        {
            "type": "function_call",
            "call_id": "o1",
            "name": "read_file",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "o1", "output": "old contents"},
    ]:
        append_message(thread_id, row)

    recorder = ConversationRecorder(store)
    recorder.record(
        thread_id,
        "run_started",
        {"session_id": thread_id, "run_id": "new", "prompt": "new question"},
    )

    rendered = [
        item["content"].get("text") or item["content"].get("tool")
        for item in ConversationService(store=store).list_items(thread_id, limit=100)[
            "items"
        ]
    ]
    assert "old question" in rendered
    assert "read_file" in rendered
    assert "new question" in rendered


def test_a_fork_carries_the_transcript_on_postgres(store):
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("sess-1", user_id="u1")
    for method, payload in [
        ("run_started", {"run_id": "r1", "prompt": "work", "prompt_role": "user"}),
        ("message_output", {"run_id": "r1", "content": "done"}),
        ("run_end", {"run_id": "r1", "end_reason": "completed"}),
    ]:
        recorder.record("sess-1", method, {"session_id": "sess-1", **payload})

    recorder.record(
        "sess-1", "session_forked", {"session_id": "sess-1", "new_session_id": "sess-2"}
    )
    parent = store.list_items("sess-1", limit=50)
    child = store.list_items("sess-2", limit=50)
    assert len(child) == len(parent) == 2
    assert [i.content for i in child] == [i.content for i in parent]
    assert store.get_thread("sess-2").parent_thread_id == "sess-1"


def test_a_pre_upgrade_row_is_backfilled_and_becomes_searchable(store):
    """A deployment that already ran #312 holds items with no
    ``search_text``. Without a backfill its ENTIRE existing history indexes
    as empty and the search box returns nothing — the generated tsvector
    can only summarize a column that has been filled in.
    """
    from omniagents.core.conversation.model import (
        Item,
        ItemKind,
        ItemStatus,
        ThreadFilter,
    )
    from omniagents.core.conversation.store import PgConversationStore

    tenant = f"backfill_{uuid.uuid4().hex[:10]}"
    first = PgConversationStore(PG_URL, tenant_id=tenant)
    try:
        first.upsert_thread(make_thread(thread_id="legacy", user_id="u1", title="x"))
        first.upsert_item(
            Item(
                item_id="itm_legacy",
                thread_id="legacy",
                turn_id="r1",
                seq=1,
                kind=ItemKind.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                created_at=1.0,
                updated_at=1.0,
                content={"text": "pre-upgrade transcript about marmosets"},
            )
        )
        # Regress the row to its pre-upgrade shape: the column did not
        # exist, so it is NULL and the generated tsvector is empty.
        with first._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversation_items SET search_text = NULL "
                    "WHERE tenant_id = %s",
                    (tenant,),
                )
            conn.commit()
        assert (
            first.search("marmosets", ThreadFilter(user_id="u1"), limit=5)[1] == 0
        ), "the regression did not take"
    finally:
        first.close()

    # A fresh open is the upgrade: it must backfill.
    second = PgConversationStore(PG_URL, tenant_id=tenant)
    try:
        hits, total = second.search("marmosets", ThreadFilter(user_id="u1"), limit=5)
        assert total == 1, "pre-upgrade history is permanently unsearchable"
        assert "marmosets" in hits[0].preview
        # Idempotent: a caught-up store re-opens without changing anything.
        third = PgConversationStore(PG_URL, tenant_id=tenant)
        try:
            assert (
                third.search("marmosets", ThreadFilter(user_id="u1"), limit=5)[1] == 1
            )
        finally:
            third.close()
    finally:
        second.delete_thread("legacy")
        second.close()


def test_the_generated_tsvector_is_normalized_in_a_FRESH_schema():
    """Exercise the DDL, not a table that already exists.

    ``ADD COLUMN IF NOT EXISTS`` is a no-op against a deployed table, so a
    parity check run on a pre-existing database proves nothing about the
    expression this build would CREATE. This one bootstraps the schema from
    scratch in a throwaway Postgres schema, which is the only way the
    generated column's definition is actually under test.

    (The same property is why changing that expression needs a migration —
    recorded as a follow-up on the PR.)
    """
    import psycopg

    from omniagents.core.conversation.model import (
        Item,
        ItemKind,
        ItemStatus,
        ThreadFilter,
    )
    from omniagents.core.conversation.store import PgConversationStore

    schema = f"parity_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(PG_URL, autocommit=True)
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        dsn = f"{PG_URL}?options=-csearch_path%3D{schema}"
        store = PgConversationStore(dsn, tenant_id=schema)
        try:
            store.upsert_thread(make_thread(thread_id="t1", user_id="u1", title="x"))
            store.upsert_item(
                Item(
                    item_id="itm_1",
                    thread_id="t1",
                    turn_id=None,
                    seq=1,
                    kind=ItemKind.TOOL_CALL,
                    status=ItemStatus.COMPLETED,
                    created_at=1.0,
                    updated_at=1.0,
                    content={
                        "text": "edited widgets.py and src/core/thing_two.rs "
                        "for user@example.com"
                    },
                )
            )
            scope = ThreadFilter(user_id="u1")
            for query in (
                "widgets",
                "widgets.py",
                "py",
                "thing_two",
                "thing",
                "example.com",
                "src/core",
            ):
                assert store.search(query, scope, limit=5)[1] == 1, (
                    f"{query!r} did not match: the freshly created tsvector "
                    "does not tokenize the way SQLite's index does"
                )
        finally:
            store.close()
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def test_a_plan_round_trips_and_lands_terminally_on_postgres(store):
    """Plans are ordinary items with a ``long_lived`` flag and a JSONB
    payload, so the two behaviours worth proving on the cloud backend are
    that the flag survives the round trip (a plan swept at run_end would be
    unwritable afterwards) and that JSONB holds the nested step list."""
    from omniagents.core.conversation.plans import Plan, PlanStep, PlanStore

    recorder = ConversationRecorder(store)
    recorder.ensure_thread("sess-1", user_id="u1")
    plans = PlanStore(store)

    key, generation = plans.current_key("sess-1", "main")
    plan = Plan(plan_id="", thread_id="sess-1", scope="main", generation=generation)
    plan.steps = [
        PlanStep(id="1", subject="write it", status="completed"),
        PlanStep(id="2", subject="test it", status="blocked", blocked_by=["1"]),
    ]
    recorder.record("sess-1", "run_started", {"run_id": "run-1", "prompt": "go"})
    recorder.record_plan(
        "sess-1", natural_key=key, content=plan.to_content(), run_id="run-1"
    )
    recorder.record("sess-1", "run_end", {"run_id": "run-1", "end_reason": "completed"})

    stored = plans.current("sess-1", "main")
    assert stored is not None
    assert stored.status == ItemStatus.STARTED.value  # long_lived: not swept
    assert [(s.id, s.status) for s in stored.steps] == [
        ("1", "completed"),
        ("2", "blocked"),
    ]
    assert stored.step("2").blocked_by == ["1"]

    recorder.record_archived("sess-1", True)
    landed = store.get_item("sess-1", stored.plan_id)
    assert landed.status is ItemStatus.CANCELLED
    assert landed.content["finalized_by"] == "thread_archived"

    # A finalized plan is not reopened; the next one gets its own identity.
    next_key, next_generation = plans.current_key("sess-1", "main")
    assert next_generation == 1
    assert next_key != key


def test_a_run_diff_round_trips_and_settles_on_postgres(store):
    recorder = ConversationRecorder(store)
    recorder.ensure_thread("sess-1", user_id="u1")
    recorder.record("sess-1", "run_started", {"run_id": "run-1", "prompt": "go"})
    recorder.record_run_diff(
        "sess-1",
        run_id="run-1",
        snapshot={
            "diff": "--- a/a.txt\n+++ b/a.txt\n",
            "files": [{"path": "a.txt", "change_type": "modified"}],
            "stats": {"files_changed": 1, "additions": 1, "deletions": 1},
            "truncated": False,
        },
    )
    revised = recorder.record_run_diff(
        "sess-1",
        run_id="run-1",
        snapshot={
            "diff": "--- a/a.txt\n+++ b/a.txt\n@@\n",
            "files": [
                {"path": "a.txt", "change_type": "modified"},
                {"path": "b.txt", "change_type": "added"},
            ],
            "stats": {"files_changed": 2, "additions": 3, "deletions": 1},
            "truncated": False,
        },
    )
    assert revised.revision == 1
    recorder.record("sess-1", "run_end", {"run_id": "run-1", "end_reason": "completed"})

    service = ConversationService(store=store)
    result = service.get_run_diff("sess-1")
    assert result["turn_id"] == "run-1"
    assert [f["path"] for f in result["run_diff"]["files"]] == ["a.txt", "b.txt"]
    assert result["run_diff"]["stats"]["files_changed"] == 2
    # Settled by run_end rather than swept as a failed orphan.
    assert result["run_diff"]["status"] == ItemStatus.COMPLETED.value


def test_get_plan_reads_history_across_generations_on_postgres(store):
    from omniagents.core.conversation.plans import Plan, PlanStep, PlanStore

    recorder = ConversationRecorder(store)
    recorder.ensure_thread("sess-1")
    plans = PlanStore(store)

    for subject in ("first plan", "second plan"):
        key, generation = plans.current_key("sess-1", "main")
        plan = Plan(plan_id="", thread_id="sess-1", scope="main", generation=generation)
        plan.steps = [PlanStep(id="1", subject=subject)]
        recorder.record_plan("sess-1", natural_key=key, content=plan.to_content())
        recorder.record_archived("sess-1", True)

    result = ConversationService(store=store).get_plan("sess-1")
    assert [p["steps"][0]["subject"] for p in result["plans"]] == [
        "first plan",
        "second plan",
    ]
    assert result["plan"] is None  # both generations are terminal
