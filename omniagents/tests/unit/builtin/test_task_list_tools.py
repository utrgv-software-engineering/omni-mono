"""Plan tools over the durable plan item.

The tests drive the tools exactly as the model does and assert on the
DURABLE STORE, because the regression this ticket fixes is invisible from
the tool's return value alone: the singleton returned a perfectly good task
list right up until a second session overwrote it.
"""

import asyncio
import json

import pytest

from omniagents.builtin.tools import task_list_tools as tlt
from omniagents.core.conversation.model import ItemKind
from omniagents.core.conversation.plans import PlanStore
from omniagents.core.conversation.store import (
    get_conversation_store,
    reset_conversation_store,
)


class _Ctx:
    def __init__(self, context=None):
        self.context = context or {}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    reset_conversation_store()
    tlt.reset_sessionless_plans()
    yield
    reset_conversation_store()
    tlt.reset_sessionless_plans()


def store():
    return get_conversation_store(None, None)


def plans():
    return PlanStore(store())


async def _call(tool, *args, **kwargs):
    return await tool._original_func(*args, **kwargs)


def _meta(out):
    return out.ui_metadata if hasattr(out, "ui_metadata") else out[1]


def session_ctx(session_id, scope=None):
    context = {"session_id": session_id}
    if scope:
        context["task_scope"] = scope
    return _Ctx(context)


# ---------------------------------------------------------------------
# Behaviour preserved from the singleton implementation
# ---------------------------------------------------------------------


class TestToolSurface:
    @pytest.mark.asyncio
    async def test_create_list_update_roundtrip(self):
        ctx = session_ctx("s1")
        out = await _call(tlt.task_create, ctx, subject="Do a thing", description="d")
        task = json.loads(str(out))["task"]
        assert task["id"] == "1"
        assert task["status"] == "pending"

        out = await _call(tlt.task_update, ctx, task_id="1", status="in_progress")
        assert json.loads(str(out))["task"]["status"] == "in_progress"

        out = await _call(tlt.task_list, ctx)
        assert json.loads(str(out))["counts"]["in_progress"] == 1

    @pytest.mark.asyncio
    async def test_dependency_edges_symmetric_and_delete_detaches(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        await _call(tlt.task_create, ctx, subject="b", description="d")
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])

        plan = plans().current("s1", "main")
        assert plan.step("1").blocks == ["2"]
        assert plan.step("2").blocked_by == ["1"]

        await _call(tlt.task_update, ctx, task_id="1", status="deleted")
        plan = plans().current("s1", "main")
        assert plan.step("1") is None
        assert plan.step("2").blocked_by == []

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        out = await _call(tlt.task_update, ctx, task_id="1", status="bogus")
        assert "invalid status" in json.loads(str(out))["error"]
        # And the step is untouched.
        assert plans().current("s1", "main").step("1").status == "pending"

    @pytest.mark.asyncio
    async def test_unknown_task_is_a_structured_not_found(self):
        ctx = session_ctx("s1")
        out = await _call(tlt.task_get, ctx, task_id="99")
        assert json.loads(str(out))["error"] == "not_found"
        assert _meta(out)["metadata"]["success"] is False

    @pytest.mark.asyncio
    async def test_blocked_is_a_first_class_status(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        out = await _call(tlt.task_update, ctx, task_id="1", status="blocked")
        assert json.loads(str(out))["task"]["status"] == "blocked"
        assert plans().current("s1", "main").counts()["blocked"] == 1

    @pytest.mark.asyncio
    async def test_tasks_snapshot_channel_is_preserved_for_shipped_clients(self):
        ctx = session_ctx("s1")
        out = await _call(tlt.task_create, ctx, subject="a", description="d")
        snapshot = _meta(out)["tasks_snapshot"]
        assert snapshot == [
            {
                "id": "1",
                "subject": "a",
                "activeForm": "a",
                "status": "pending",
                "owner": "",
                "blockedBy": [],
                "exitCriteria": "",
                "verified": None,
                "criteriaEdited": False,
            }
        ]


# ---------------------------------------------------------------------
# What the singleton could not do
# ---------------------------------------------------------------------


class TestResultShape:
    """The tool result JSON is a contract with every agent prompt and
    transcript that already references it. Pin it."""

    @pytest.mark.asyncio
    async def test_task_payload_keeps_its_wire_shape(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        await _call(tlt.task_create, ctx, subject="b", description="d")
        out = await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])

        task = json.loads(str(out))["task"]
        assert task == {
            "id": "2",
            "subject": "b",
            "description": "d",
            "activeForm": "b",
            "status": "pending",
            "owner": "",
            "blocks": [],
            "blockedBy": ["1"],
            "openBlockers": ["1"],
            "exitCriteria": "",
            "criteriaEdited": False,
            "originalExitCriteria": "",
        }

    @pytest.mark.asyncio
    async def test_open_blockers_drops_completed_dependencies(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        await _call(tlt.task_create, ctx, subject="b", description="d")
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])
        await _call(tlt.task_update, ctx, task_id="1", status="completed")

        task = json.loads(str(await _call(tlt.task_get, ctx, task_id="2")))["task"]
        assert task["blockedBy"] == ["1"]
        assert task["openBlockers"] == []

    @pytest.mark.asyncio
    async def test_list_payload_keeps_its_wire_shape(self):
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        payload = json.loads(str(await _call(tlt.task_list, ctx)))
        assert payload["tasks"] == [
            {
                "id": "1",
                "subject": "a",
                "activeForm": "a",
                "status": "pending",
                "owner": "",
                "blockedBy": [],
                "exitCriteria": "",
                "verified": None,
                "criteriaEdited": False,
            }
        ]
        assert payload["counts"] == {
            "pending": 1,
            "in_progress": 0,
            "completed": 0,
            "blocked": 0,
        }


class TestConcurrentToolCalls:
    """The SDK runs function tools concurrently (one ``asyncio.create_task``
    per tool run), so two ``task_update`` calls in one turn genuinely
    interleave. What makes the read-modify-write in ``_PlanHandle.write``
    safe is that it contains no ``await`` — an invisible property, so pin
    it: if the store ever grows an async path this test fails."""

    @pytest.mark.asyncio
    async def test_concurrent_updates_in_one_turn_do_not_lose_a_write(self):
        ctx = session_ctx("s1")
        for subject in ("a", "b", "c"):
            await _call(tlt.task_create, ctx, subject=subject, description="d")

        await asyncio.gather(
            _call(tlt.task_update, ctx, task_id="1", status="completed"),
            _call(tlt.task_update, ctx, task_id="2", status="in_progress"),
            _call(tlt.task_update, ctx, task_id="3", status="blocked"),
        )

        plan = plans().current("s1", "main")
        assert [(s.id, s.status) for s in plan.steps] == [
            ("1", "completed"),
            ("2", "in_progress"),
            ("3", "blocked"),
        ]

    @pytest.mark.asyncio
    async def test_concurrent_creates_in_one_turn_all_survive(self):
        ctx = session_ctx("s1")
        await asyncio.gather(
            *(
                _call(tlt.task_create, ctx, subject=f"task {n}", description="d")
                for n in range(5)
            )
        )
        plan = plans().current("s1", "main")
        assert len(plan.steps) == 5
        assert len({s.id for s in plan.steps}) == 5


class TestWriteTimeGenerationResolve:
    """The generation is re-resolved immediately BEFORE the write, not
    cached from the read.

    Note what this does and does not defend. The in-process race is
    unreachable today: the whole read-modify-write in ``_PlanHandle.write``
    contains no ``await``, so two concurrent tool calls cannot interleave
    inside it — which is why the behaviour originally shipped untested. What
    it does defend is the *stamped content*: the persisted ``generation`` and
    ``plan_id`` must describe the item actually written, not the one that was
    live when the tool started.
    """

    @pytest.mark.asyncio
    async def test_generation_is_stamped_from_the_write_not_the_read(self):
        """Adopted from the review's probe (315a).

        Reverting to the read-time key persists ``generation: 0`` and a
        generation-0 ``plan_id`` INTO a generation-1 plan, so the item's
        content contradicts its own identity.
        """
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="gen0", description="d")
        rec = ConversationRecorder(store())
        rec.record_archived("s1", True)  # generation 0 lands terminal
        rec.record_archived("s1", False)  # the conversation is reopened
        await _call(tlt.task_create, ctx, subject="gen1", description="d")

        plan = plans().current("s1", "main")
        assert plan.generation == 1
        item = store().get_item("s1", plan.plan_id)
        assert item.content["generation"] == 1
        assert item.content["plan_id"] == plan.plan_id

    @pytest.mark.asyncio
    async def test_a_plan_finalized_between_read_and_write_is_not_lost(self):
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="first", description="d")
        first_id = plans().current("s1", "main").plan_id

        handle = tlt._handle(ctx)
        plan = handle.read()
        assert plan.plan_id == first_id

        # The conversation is archived while the tool call is mid-flight.
        ConversationRecorder(store()).record_archived("s1", True)

        plan.steps.append(tlt.PlanStep(id=plan.next_step_id(), subject="second"))
        stored = handle.write(plan)

        assert stored.plan_id != first_id
        assert stored.generation == 1
        live = plans().current("s1", "main")
        assert [s.subject for s in live.steps] == ["first", "second"]
        assert live.plan_id == stored.plan_id
        # The persisted CONTENT must describe the item actually written, not
        # the one that was live when the tool started.
        item = store().get_item("s1", stored.plan_id)
        assert item.content["generation"] == 1
        assert item.content["plan_id"] == stored.plan_id


class TestDurability:
    @pytest.mark.asyncio
    async def test_plan_is_persisted_as_a_conversation_item(self):
        await _call(tlt.task_create, session_ctx("s1"), subject="a", description="d")
        rows = store().list_items("s1", limit=10, kinds=[ItemKind.PLAN])
        assert len(rows) == 1
        assert rows[0].long_lived is True
        assert [s["subject"] for s in rows[0].content["steps"]] == ["a"]

    @pytest.mark.asyncio
    async def test_plan_survives_dropping_all_in_process_state(self):
        """Refresh/reconnect: nothing in this process remembers the plan."""
        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="ship it", description="d")
        await _call(tlt.task_update, ctx, task_id="1", status="in_progress")

        reset_conversation_store()
        tlt.reset_sessionless_plans()

        out = await _call(tlt.task_list, session_ctx("s1"))
        tasks = json.loads(str(out))["tasks"]
        assert [(t["id"], t["status"]) for t in tasks] == [("1", "in_progress")]

    @pytest.mark.asyncio
    async def test_plan_id_is_stable_across_updates(self):
        ctx = session_ctx("s1")
        first = _meta(await _call(tlt.task_create, ctx, subject="a", description="d"))[
            "metadata"
        ]["plan_id"]
        second = _meta(
            await _call(tlt.task_update, ctx, task_id="1", status="completed")
        )["metadata"]["plan_id"]
        assert first == second
        assert first.startswith("itm_")

    @pytest.mark.asyncio
    async def test_revision_advances_on_every_write(self):
        ctx = session_ctx("s1")
        first = _meta(await _call(tlt.task_create, ctx, subject="a", description="d"))
        second = _meta(await _call(tlt.task_create, ctx, subject="b", description="d"))
        assert second["metadata"]["revision"] > first["metadata"]["revision"]

    @pytest.mark.asyncio
    async def test_a_write_after_finalization_opens_a_new_plan(self):
        """A plan finalized between read and write must not swallow the
        write: writing to a terminal item is a silent no-op, so the tool
        would tell the agent its edit landed when it did not."""
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="first plan", description="d")
        first_id = plans().current("s1", "main").plan_id
        ConversationRecorder(store()).record_archived("s1", True)

        out = await _call(tlt.task_create, ctx, subject="second plan", description="d")
        live = plans().current("s1", "main")
        assert live.plan_id != first_id
        assert [s.subject for s in live.steps] == ["second plan"]
        assert _meta(out)["metadata"]["plan_id"] == live.plan_id
        # The finalized plan is still readable — history is not rewritten.
        assert len(plans().list_plans("s1")) == 2


class TestConcurrentSessions:
    @pytest.mark.asyncio
    async def test_two_sessions_do_not_supersede_each_other(self):
        """The exact failure the module-level dict produced."""
        a = session_ctx("sess-a")
        b = session_ctx("sess-b")

        await _call(tlt.task_create, a, subject="alpha task", description="d")
        await _call(tlt.task_create, b, subject="beta task", description="d")
        await _call(tlt.task_update, b, task_id="1", status="completed")

        a_tasks = json.loads(str(await _call(tlt.task_list, a)))["tasks"]
        b_tasks = json.loads(str(await _call(tlt.task_list, b)))["tasks"]

        assert [t["subject"] for t in a_tasks] == ["alpha task"]
        assert [t["subject"] for t in b_tasks] == ["beta task"]
        assert a_tasks[0]["status"] == "pending"
        assert b_tasks[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_two_sessions_get_distinct_plan_ids(self):
        a = _meta(
            await _call(
                tlt.task_create, session_ctx("sess-a"), subject="a", description="d"
            )
        )["metadata"]["plan_id"]
        b = _meta(
            await _call(
                tlt.task_create, session_ctx("sess-b"), subject="b", description="d"
            )
        )["metadata"]["plan_id"]
        assert a != b

    @pytest.mark.asyncio
    async def test_interleaved_creates_keep_independent_step_numbering(self):
        a = session_ctx("sess-a")
        b = session_ctx("sess-b")
        await _call(tlt.task_create, a, subject="a1", description="d")
        await _call(tlt.task_create, b, subject="b1", description="d")
        await _call(tlt.task_create, a, subject="a2", description="d")
        out = await _call(tlt.task_create, b, subject="b2", description="d")
        # A shared id counter would have handed this one "4".
        assert json.loads(str(out))["task"]["id"] == "2"

    @pytest.mark.asyncio
    async def test_worker_scope_is_isolated_from_the_main_plan(self):
        main = session_ctx("s1")
        worker = session_ctx("s1", scope="worker-1")
        await _call(tlt.task_create, main, subject="parent task", description="d")
        assert json.loads(str(await _call(tlt.task_list, worker)))["tasks"] == []
        await _call(tlt.task_create, worker, subject="child task", description="d")
        assert [
            t["subject"]
            for t in json.loads(str(await _call(tlt.task_list, main)))["tasks"]
        ] == ["parent task"]


class TestTruthfulness:
    """Adopted from the review's probes (315a): the tool must never tell the
    agent an edit landed when the store refused it."""

    @pytest.mark.asyncio
    async def test_a_write_after_archive_reports_truthfully(self):
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        ConversationRecorder(store()).record_archived("s1", True)

        out = await _call(tlt.task_update, ctx, task_id="1", status="completed")
        payload = json.loads(str(out))
        if payload.get("error"):
            return
        plan = plans().current("s1", "main")
        assert plan is not None
        step = plan.step("1")
        assert step is not None and step.status == "completed"

    @pytest.mark.asyncio
    async def test_archive_then_update_does_not_resurrect_old_steps(self):
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        await _call(tlt.task_create, ctx, subject="b", description="d")
        ConversationRecorder(store()).record_archived("s1", True)
        await _call(tlt.task_update, ctx, task_id="1", status="completed")

        history = plans().list_plans("s1")
        assert [[s.subject for s in p.steps] for p in history] == [["a", "b"]]

    @pytest.mark.asyncio
    async def test_an_exhausted_scope_reports_an_error_not_success(self, monkeypatch):
        import omniagents.core.conversation.plans as plans_module

        monkeypatch.setattr(plans_module, "MAX_PLAN_GENERATIONS", 1)
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        ConversationRecorder(store()).record_archived("s1", True)
        ConversationRecorder(store()).record_archived("s1", False)

        out = await _call(tlt.task_create, ctx, subject="b", description="d")
        payload = json.loads(str(out))
        assert payload["error"] == "plan_generations_exhausted"
        assert _meta(out)["metadata"]["success"] is False
        # The failure payload must not advertise the step that was refused:
        # ``tasks_snapshot`` drives the docked panel, so a phantom row there
        # is the same lie the error text just avoided, moved one field over.
        assert [t["subject"] for t in _meta(out)["tasks_snapshot"]] == ["a"]
        assert plans().current("s1", "main") is None or [
            s.subject for s in plans().list_plans("s1")[-1].steps
        ] == ["a"]

    @pytest.mark.asyncio
    async def test_an_exhausted_update_does_not_advertise_the_refused_change(
        self, monkeypatch
    ):
        import omniagents.core.conversation.plans as plans_module

        monkeypatch.setattr(plans_module, "MAX_PLAN_GENERATIONS", 1)
        from omniagents.core.conversation.recorder import ConversationRecorder

        ctx = session_ctx("s1")
        await _call(tlt.task_create, ctx, subject="a", description="d")
        ConversationRecorder(store()).record_archived("s1", True)
        ConversationRecorder(store()).record_archived("s1", False)

        out = await _call(tlt.task_update, ctx, task_id="1", status="completed")
        payload = json.loads(str(out))
        assert payload["error"] == "plan_generations_exhausted"
        assert [t["status"] for t in _meta(out)["tasks_snapshot"]] == ["pending"]


class TestSessionlessFallback:
    @pytest.mark.asyncio
    async def test_a_sessionless_run_still_works(self):
        ctx = _Ctx()
        await _call(tlt.task_create, ctx, subject="local", description="d")
        tasks = json.loads(str(await _call(tlt.task_list, ctx)))["tasks"]
        assert [t["subject"] for t in tasks] == ["local"]

    @pytest.mark.asyncio
    async def test_sessionless_plans_are_a_process_singleton_across_runs(self):
        """The one place the old shape survives, asserted so it cannot drift
        into a surprise: two DIFFERENT sessionless runs in one process share
        a task list, so an in-process eval/CLI harness running scenarios
        sequentially sees the previous scenario's tasks unless it calls
        ``reset_sessionless_plans()``."""
        run1, run2 = _Ctx(), _Ctx()
        await _call(tlt.task_create, run1, subject="from-run-1", description="d")
        seen = json.loads(str(await _call(tlt.task_list, run2)))["tasks"]
        assert [t["subject"] for t in seen] == ["from-run-1"]

        tlt.reset_sessionless_plans()
        after = json.loads(str(await _call(tlt.task_list, run2)))["tasks"]
        assert after == []

    @pytest.mark.asyncio
    async def test_a_sessionless_plan_never_leaks_into_a_session(self):
        await _call(tlt.task_create, _Ctx(), subject="local", description="d")
        tasks = json.loads(str(await _call(tlt.task_list, session_ctx("s1"))))["tasks"]
        assert tasks == []
        assert store().list_items("s1", limit=10, kinds=[ItemKind.PLAN]) == []


# ---------------------------------------------------------------------
# Edge integrity: cycle refusal and edge removal
# ---------------------------------------------------------------------


class TestEdgeIntegrity:
    """A dependency cycle can never mean iteration in an all-of precedence
    model — only deadlock — so cycle-closing edges are refused with the
    chain named, and ``removeBlockedBy`` is the ordinary fix for a
    mis-drawn edge (the alternatives — overrideBlockers, deletion —
    bypass the guard or destroy history)."""

    async def _three(self, ctx):
        await _call(tlt.task_create, ctx, subject="a", description="d")
        await _call(tlt.task_create, ctx, subject="b", description="d")
        await _call(tlt.task_create, ctx, subject="c", description="d")

    @pytest.mark.asyncio
    async def test_direct_cycle_is_refused_and_nothing_is_written(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])
        out = await _call(tlt.task_update, ctx, task_id="1", addBlockedBy=["2"])
        payload = json.loads(str(out))
        assert payload["error"] == "dependency_cycle"
        assert payload["path"] == ["2", "1"]
        assert payload["edge"] == {"blocker": "2", "blocked": "1"}
        stored = plans().current("s1", "main")
        assert stored.step("1").blocked_by == []
        assert stored.step("2").blocked_by == ["1"]

    @pytest.mark.asyncio
    async def test_transitive_cycle_is_refused_with_the_chain(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])
        await _call(tlt.task_update, ctx, task_id="3", addBlockedBy=["2"])
        out = await _call(tlt.task_update, ctx, task_id="1", addBlockedBy=["3"])
        payload = json.loads(str(out))
        assert payload["error"] == "dependency_cycle"
        assert payload["path"] == ["3", "2", "1"]

    @pytest.mark.asyncio
    async def test_two_additions_in_one_call_cannot_form_a_cycle(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        # blocks: 1 blocks 2; blockedBy: 1 waits on 2 — together a cycle.
        out = await _call(
            tlt.task_update, ctx, task_id="1", addBlocks=["2"], addBlockedBy=["2"]
        )
        payload = json.loads(str(out))
        assert payload["error"] == "dependency_cycle"
        stored = plans().current("s1", "main")
        assert stored.step("1").blocks == []
        assert stored.step("1").blocked_by == []

    @pytest.mark.asyncio
    async def test_diamond_fan_out_fan_in_is_legal(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        await _call(tlt.task_create, ctx, subject="d", description="d")
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])
        await _call(tlt.task_update, ctx, task_id="3", addBlockedBy=["1"])
        out = await _call(tlt.task_update, ctx, task_id="4", addBlockedBy=["2", "3"])
        assert json.loads(str(out))["task"]["blockedBy"] == ["2", "3"]

    @pytest.mark.asyncio
    async def test_remove_blocked_by_is_symmetric_and_unblocks(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])
        out = await _call(tlt.task_update, ctx, task_id="2", removeBlockedBy=["1"])
        assert json.loads(str(out))["task"]["blockedBy"] == []
        stored = plans().current("s1", "main")
        assert stored.step("1").blocks == []
        assert stored.step("2").blocked_by == []
        started = await _call(tlt.task_update, ctx, task_id="2", status="in_progress")
        assert json.loads(str(started))["task"]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_remove_of_absent_edge_or_unknown_id_is_a_noop(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        out = await _call(
            tlt.task_update, ctx, task_id="2", removeBlockedBy=["1", "99"]
        )
        assert json.loads(str(out))["task"]["blockedBy"] == []

    @pytest.mark.asyncio
    async def test_removal_in_same_call_legalizes_the_addition(self):
        ctx = session_ctx("s1")
        await self._three(ctx)
        # 1 waits on 2. Reversing the edge in ONE call on task 1 must
        # work: the removal is simulated before the addition is validated.
        await _call(tlt.task_update, ctx, task_id="1", addBlockedBy=["2"])
        out = await _call(
            tlt.task_update,
            ctx,
            task_id="1",
            addBlocks=["2"],
            removeBlockedBy=["2"],
        )
        assert "error" not in json.loads(str(out))
        stored = plans().current("s1", "main")
        assert stored.step("1").blocked_by == []
        assert stored.step("2").blocked_by == ["1"]
