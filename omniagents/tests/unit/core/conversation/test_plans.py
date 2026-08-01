"""Durable plans: persistence, identity, generations, lifecycle.

Every test here asserts on what is IN THE STORE, because the whole point of
the ticket is that a plan survives the process that produced it.
"""

import pytest

from omniagents.core.conversation.model import ItemKind, ItemStatus, derive_item_id
from omniagents.core.conversation.plans import (
    Plan,
    PlanStep,
    PlanStore,
    link,
    plan_natural_key,
    unlink_all,
)
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore

THREAD = "sess-plan"


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


@pytest.fixture
def recorder(store):
    rec = ConversationRecorder(store)
    rec.ensure_thread(THREAD, user_id="u1")
    return rec


def write_plan(recorder, plans, thread_id, scope, steps):
    key, generation = plans.current_key(thread_id, scope)
    plan = plans.current(thread_id, scope) or Plan(
        plan_id=derive_item_id(thread_id, key),
        thread_id=thread_id,
        scope=scope,
        generation=generation,
    )
    plan.steps = list(steps)
    item = recorder.record_plan(
        thread_id, natural_key=key, content=plan.to_content(), run_id="run-1"
    )
    return Plan.from_item(item, scope=scope, generation=generation)


class TestPersistence:
    def test_plan_is_stored_as_a_long_lived_plan_item(self, store, recorder):
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])

        rows = store.list_items(THREAD, limit=10, kinds=[ItemKind.PLAN])
        assert len(rows) == 1
        assert rows[0].kind is ItemKind.PLAN
        assert rows[0].long_lived is True
        assert rows[0].status is ItemStatus.STARTED

    def test_plan_id_is_stable_across_updates(self, store, recorder):
        plans = PlanStore(store)
        first = write_plan(
            recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")]
        )
        second = write_plan(
            recorder,
            plans,
            THREAD,
            "main",
            [PlanStep(id="1", subject="a", status="completed")],
        )
        assert first.plan_id == second.plan_id
        assert second.revision > first.revision

    def test_plan_id_is_derivable_without_reading_the_store(self, store, recorder):
        plans = PlanStore(store)
        expected = derive_item_id(THREAD, plan_natural_key("main"))
        stored = write_plan(
            recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")]
        )
        assert stored.plan_id == expected

    def test_plan_survives_a_fresh_store_object(self, tmp_path):
        path = str(tmp_path / "domain.db")
        first = SqliteConversationStore(path=path)
        recorder = ConversationRecorder(first)
        recorder.ensure_thread(THREAD)
        write_plan(
            recorder,
            PlanStore(first),
            THREAD,
            "main",
            [PlanStep(id="1", subject="ship it", status="in_progress")],
        )
        first.close()

        reopened = SqliteConversationStore(path=path)
        try:
            plan = PlanStore(reopened).current(THREAD, "main")
            assert plan is not None
            assert [(s.id, s.status) for s in plan.steps] == [("1", "in_progress")]
        finally:
            reopened.close()


class TestSessionIsolation:
    def test_two_threads_hold_independent_plans(self, store):
        recorder = ConversationRecorder(store)
        recorder.ensure_thread("sess-a")
        recorder.ensure_thread("sess-b")
        plans = PlanStore(store)

        write_plan(recorder, plans, "sess-a", "main", [PlanStep(id="1", subject="A")])
        write_plan(recorder, plans, "sess-b", "main", [PlanStep(id="1", subject="B")])

        a = plans.current("sess-a", "main")
        b = plans.current("sess-b", "main")
        assert a is not None and b is not None
        assert a.plan_id != b.plan_id
        assert [s.subject for s in a.steps] == ["A"]
        assert [s.subject for s in b.steps] == ["B"]

    def test_scopes_within_one_thread_are_independent(self, store, recorder):
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="main")])
        write_plan(
            recorder, plans, THREAD, "worker-7", [PlanStep(id="1", subject="worker")]
        )
        assert [s.subject for s in plans.current(THREAD, "main").steps] == ["main"]
        assert [s.subject for s in plans.current(THREAD, "worker-7").steps] == [
            "worker"
        ]


class TestLifecycle:
    def test_run_end_does_not_finalize_a_plan(self, store, recorder):
        """The ``long_lived`` exemption, proved against real traffic."""
        plans = PlanStore(store)
        recorder.record(THREAD, "run_started", {"run_id": "run-1", "prompt": "go"})
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        recorder.record(
            THREAD, "run_end", {"run_id": "run-1", "end_reason": "completed"}
        )

        plan = plans.current(THREAD, "main")
        assert plan is not None
        assert plan.status == ItemStatus.STARTED.value
        assert plan.is_open

    def test_plan_accumulates_steps_across_two_runs(self, store, recorder):
        plans = PlanStore(store)
        recorder.record(THREAD, "run_started", {"run_id": "run-1", "prompt": "go"})
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        recorder.record(
            THREAD, "run_end", {"run_id": "run-1", "end_reason": "completed"}
        )

        recorder.record(THREAD, "run_started", {"run_id": "run-2", "prompt": "more"})
        current = plans.current(THREAD, "main")
        current.steps.append(PlanStep(id="2", subject="b"))
        key, _ = plans.current_key(THREAD, "main")
        recorder.record_plan(
            THREAD, natural_key=key, content=current.to_content(), run_id="run-2"
        )

        plan = plans.current(THREAD, "main")
        assert [s.id for s in plan.steps] == ["1", "2"]

    def test_archiving_lands_an_unfinished_plan_as_cancelled(self, store, recorder):
        plans = PlanStore(store)
        write_plan(
            recorder,
            plans,
            THREAD,
            "main",
            [
                PlanStep(id="1", subject="a", status="completed"),
                PlanStep(id="2", subject="b"),
            ],
        )
        recorder.record_archived(THREAD, True)

        item = store.get_item(THREAD, derive_item_id(THREAD, plan_natural_key("main")))
        assert item.status is ItemStatus.CANCELLED
        assert item.content["finalized_by"] == "thread_archived"

    def test_archiving_lands_a_finished_plan_as_completed(self, store, recorder):
        plans = PlanStore(store)
        write_plan(
            recorder,
            plans,
            THREAD,
            "main",
            [PlanStep(id="1", subject="a", status="completed")],
        )
        recorder.record_archived(THREAD, True)

        item = store.get_item(THREAD, derive_item_id(THREAD, plan_natural_key("main")))
        assert item.status is ItemStatus.COMPLETED

    def test_a_plan_with_no_steps_lands_cancelled_not_completed(self, store, recorder):
        """ "Every step completed" is vacuously true of an empty plan.
        Reporting a plan that never had a step as ``completed`` would tell
        the history that work finished which was never even described."""
        plans = PlanStore(store)
        key, _ = plans.current_key(THREAD, "main")
        recorder.record_plan(THREAD, natural_key=key, content={"steps": []})
        recorder.record_archived(THREAD, True)

        item = store.get_item(THREAD, derive_item_id(THREAD, plan_natural_key("main")))
        assert item.status is ItemStatus.CANCELLED

    def test_a_plan_whose_only_steps_were_deleted_lands_cancelled(
        self, store, recorder
    ):
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        write_plan(recorder, plans, THREAD, "main", [])
        recorder.record_archived(THREAD, True)

        item = store.get_item(THREAD, derive_item_id(THREAD, plan_natural_key("main")))
        assert item.status is ItemStatus.CANCELLED

    def test_finalized_by_reaches_the_wire(self, store, recorder):
        """A history view showing ``cancelled`` without saying why is an
        unexplained failure."""
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        recorder.record_archived(THREAD, True)

        wire = plans.list_plans(THREAD)[0].to_wire()
        assert wire["status"] == "cancelled"
        assert wire["finalized_by"] == "thread_archived"

    def test_a_live_plan_has_no_finalized_by(self, store, recorder):
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        assert plans.current(THREAD, "main").to_wire()["finalized_by"] is None

    def test_unarchiving_does_not_finalize(self, store, recorder):
        plans = PlanStore(store)
        write_plan(recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        recorder.record_archived(THREAD, False)
        assert plans.current(THREAD, "main").is_open

    def test_archiving_pushes_the_landed_plan(self, store):
        settled = []
        rec = ConversationRecorder(store, on_settled=settled.append)
        rec.ensure_thread(THREAD)
        plans = PlanStore(store)
        write_plan(rec, plans, THREAD, "main", [PlanStep(id="1", subject="a")])
        assert settled == []

        rec.record_archived(THREAD, True)
        assert [item.kind for item in settled] == [ItemKind.PLAN]
        assert settled[0].status is ItemStatus.CANCELLED
        assert settled[0].content["finalized_by"] == "thread_archived"

    def test_a_new_plan_opens_after_the_previous_one_landed(self, store, recorder):
        plans = PlanStore(store)
        first = write_plan(
            recorder, plans, THREAD, "main", [PlanStep(id="1", subject="a")]
        )
        recorder.record_archived(THREAD, True)

        second = write_plan(
            recorder, plans, THREAD, "main", [PlanStep(id="1", subject="new plan")]
        )
        assert second.plan_id != first.plan_id
        assert second.generation == 1
        # The finalized plan is still readable — history is not rewritten.
        assert len(plans.list_plans(THREAD)) == 2


class TestForks:
    """A fork copies the parent's domain rows. A plan's id is not an opaque
    handle — the store LOOKS IT UP by ``plan:<scope>[#gen]`` — so a plan
    re-addressed the generic way lands where nothing can find it."""

    def _fork(self, store, parent="sess-parent", child="sess-child"):
        recorder = ConversationRecorder(store)
        recorder.ensure_thread(parent)
        plans = PlanStore(store)
        write_plan(
            recorder,
            plans,
            parent,
            "main",
            [
                PlanStep(id="1", subject="a", status="completed"),
                PlanStep(id="2", subject="b", status="in_progress"),
            ],
        )
        recorder.record_fork(parent, child)
        return recorder, plans

    def test_a_fork_carries_its_plan_at_the_canonical_id(self, store):
        recorder, plans = self._fork(store)
        child_plan = plans.current("sess-child", "main")
        assert child_plan is not None
        assert child_plan.plan_id == derive_item_id(
            "sess-child", plan_natural_key("main")
        )
        assert [(s.id, s.status) for s in child_plan.steps] == [
            ("1", "completed"),
            ("2", "in_progress"),
        ]

    def test_a_fork_does_not_grow_a_second_open_plan(self, store):
        recorder, plans = self._fork(store)
        # The child continues planning: this must REVISE the inherited plan,
        # not mint a second long-lived one that no sweep would ever close.
        current = plans.current("sess-child", "main")
        current.steps.append(PlanStep(id="3", subject="c"))
        key, _ = plans.current_key("sess-child", "main")
        recorder.record_plan(
            "sess-child", natural_key=key, content=current.to_content()
        )

        rows = store.list_items("sess-child", limit=10, kinds=[ItemKind.PLAN])
        assert len(rows) == 1
        assert len([r for r in rows if not r.is_terminal]) == 1
        assert [s.id for s in plans.current("sess-child", "main").steps] == [
            "1",
            "2",
            "3",
        ]

    def test_a_forked_plan_stays_long_lived_and_open(self, store):
        recorder, plans = self._fork(store)
        item = store.get_item(
            "sess-child", derive_item_id("sess-child", plan_natural_key("main"))
        )
        assert item.long_lived is True
        assert item.status is ItemStatus.STARTED

    def test_a_forked_plan_generation_is_preserved(self, store):
        recorder = ConversationRecorder(store)
        recorder.ensure_thread("sess-parent")
        plans = PlanStore(store)
        write_plan(
            recorder, plans, "sess-parent", "main", [PlanStep(id="1", subject="a")]
        )
        recorder.record_archived("sess-parent", True)
        recorder.record_archived("sess-parent", False)
        write_plan(
            recorder, plans, "sess-parent", "main", [PlanStep(id="1", subject="b")]
        )
        assert plans.current("sess-parent", "main").generation == 1

        recorder.record_fork("sess-parent", "sess-child")
        child = plans.current("sess-child", "main")
        assert child.generation == 1
        assert child.plan_id == derive_item_id(
            "sess-child", plan_natural_key("main", 1)
        )
        assert [s.subject for s in child.steps] == ["b"]

    def test_a_non_plan_item_still_forks_generically(self, store):
        recorder = ConversationRecorder(store)
        recorder.ensure_thread("sess-parent")
        recorder.record(
            "sess-parent", "run_started", {"run_id": "r1", "prompt": "hello"}
        )
        recorder.record_fork("sess-parent", "sess-child")
        child_items = store.list_items(
            "sess-child", limit=10, kinds=[ItemKind.USER_MESSAGE]
        )
        assert len(child_items) == 1
        assert child_items[0].source_ref["forked_from_thread_id"] == "sess-parent"


class TestGenerationExhaustion:
    def test_an_exhausted_scope_refuses_the_write(self, store, recorder, monkeypatch):
        """Handing back a terminal key made the write a silent no-op while
        the tool reported success."""
        import omniagents.core.conversation.plans as plans_module

        monkeypatch.setattr(plans_module, "MAX_PLAN_GENERATIONS", 2)
        plans = PlanStore(store)
        for _ in range(2):
            key, _gen = plans.current_key(THREAD, "main")
            recorder.record_plan(THREAD, natural_key=key, content={"steps": []})
            recorder.record_archived(THREAD, True)

        with pytest.raises(plans_module.PlanGenerationsExhausted):
            plans.current_key(THREAD, "main")

    def test_reads_still_answer_when_generations_are_exhausted(
        self, store, recorder, monkeypatch
    ):
        import omniagents.core.conversation.plans as plans_module

        monkeypatch.setattr(plans_module, "MAX_PLAN_GENERATIONS", 2)
        plans = PlanStore(store)
        for subject in ("first", "second"):
            key, _gen = plans.current_key(THREAD, "main")
            recorder.record_plan(
                THREAD,
                natural_key=key,
                content={"steps": [{"id": "1", "subject": subject}]},
            )
            recorder.record_archived(THREAD, True)

        current = plans.current(THREAD, "main")
        assert current is not None
        assert [s.subject for s in current.steps] == ["second"]


class TestDerivedState:
    def test_counts_cover_every_status(self):
        plan = Plan(
            plan_id="p",
            thread_id=THREAD,
            scope="main",
            generation=0,
            steps=[
                PlanStep(id="1", subject="a", status="completed"),
                PlanStep(id="2", subject="b", status="blocked"),
                PlanStep(id="3", subject="c", status="blocked"),
            ],
        )
        assert plan.counts() == {
            "pending": 0,
            "in_progress": 0,
            "completed": 1,
            "blocked": 2,
        }

    def test_open_blockers_exclude_completed_dependencies(self):
        plan = Plan(plan_id="p", thread_id=THREAD, scope="main", generation=0)
        plan.steps = [
            PlanStep(id="1", subject="a", status="completed"),
            PlanStep(id="2", subject="b"),
            PlanStep(id="3", subject="c"),
        ]
        link(plan, "1", "3")
        link(plan, "2", "3")
        assert plan.open_blockers(plan.step("3")) == ["2"]

    def test_link_writes_both_edges(self):
        plan = Plan(plan_id="p", thread_id=THREAD, scope="main", generation=0)
        plan.steps = [PlanStep(id="1", subject="a"), PlanStep(id="2", subject="b")]
        link(plan, "1", "2")
        assert plan.step("1").blocks == ["2"]
        assert plan.step("2").blocked_by == ["1"]

    def test_unlink_all_detaches_both_directions(self):
        plan = Plan(plan_id="p", thread_id=THREAD, scope="main", generation=0)
        plan.steps = [
            PlanStep(id="1", subject="a"),
            PlanStep(id="2", subject="b"),
            PlanStep(id="3", subject="c"),
        ]
        link(plan, "1", "2")
        link(plan, "2", "3")
        unlink_all(plan, "2")
        assert plan.step("1").blocks == []
        assert plan.step("3").blocked_by == []

    def test_next_step_id_continues_after_a_restore(self):
        plan = Plan(
            plan_id="p",
            thread_id=THREAD,
            scope="main",
            generation=0,
            steps=[PlanStep(id="1", subject="a"), PlanStep(id="7", subject="b")],
        )
        assert plan.next_step_id() == "2"
        plan.steps.append(PlanStep(id="2", subject="c"))
        assert plan.next_step_id() == "3"

    def test_unknown_step_status_degrades_to_pending(self):
        step = PlanStep.from_dict({"id": "1", "subject": "a", "status": "wat"})
        assert step.status == "pending"

    def test_unusable_step_rows_are_dropped_not_fatal(self, store, recorder):
        plans = PlanStore(store)
        key, _ = plans.current_key(THREAD, "main")
        recorder.record_plan(
            THREAD,
            natural_key=key,
            content={"steps": [{"no": "id"}, {"id": "2", "subject": "ok"}]},
            run_id=None,
        )
        plan = plans.current(THREAD, "main")
        assert [s.id for s in plan.steps] == ["2"]

    def test_summaries_carry_structured_status_not_prose(self):
        plan = Plan(plan_id="p", thread_id=THREAD, scope="main", generation=0)
        plan.steps = [
            PlanStep(id="1", subject="a"),
            PlanStep(id="2", subject="b", active_form="Doing b", status="in_progress"),
        ]
        link(plan, "1", "2")
        summaries = plan.to_summaries()
        assert summaries[1] == {
            "id": "2",
            "subject": "b",
            "activeForm": "Doing b",
            "status": "in_progress",
            "owner": "",
            "blockedBy": ["1"],
        }
