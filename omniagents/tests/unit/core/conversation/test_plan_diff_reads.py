"""``get_plan`` / ``get_run_diff``: the read side of plans and run diffs.

These are the reads a client uses to render "what is the plan now" and
"what did this run change" without paging items or knowing the server's
scope/generation conventions, so the tests assert on the exact wire shape.
"""

import pytest

from omniagents.core.conversation.plans import Plan, PlanStep, PlanStore
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.service import ConversationService, TurnNotFound
from omniagents.core.conversation.store import SqliteConversationStore

THREAD = "sess-read"


@pytest.fixture
def store(tmp_path):
    backend = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    yield backend
    backend.close()


@pytest.fixture
def recorder(store):
    rec = ConversationRecorder(store)
    rec.ensure_thread(THREAD)
    return rec


@pytest.fixture
def service(store):
    return ConversationService(store=store)


def put_plan(recorder, store, scope, steps):
    plans = PlanStore(store)
    key, generation = plans.current_key(THREAD, scope)
    plan = plans.current(THREAD, scope) or Plan(
        plan_id="", thread_id=THREAD, scope=scope, generation=generation
    )
    plan.steps = list(steps)
    return recorder.record_plan(
        THREAD, natural_key=key, content=plan.to_content(), run_id="run-1"
    )


class TestGetPlan:
    def test_absent_plan_is_null_not_an_error(self, service, recorder):
        result = service.get_plan(THREAD)
        assert result["plan"] is None
        assert result["plans"] == []
        assert result["scope"] == "main"

    def test_returns_structured_steps_not_prose(self, service, store, recorder):
        put_plan(
            recorder,
            store,
            "main",
            [
                PlanStep(id="1", subject="write it", status="completed"),
                PlanStep(id="2", subject="test it", status="in_progress"),
                PlanStep(id="3", subject="ship it", status="blocked"),
            ],
        )
        plan = service.get_plan(THREAD)["plan"]
        assert [(s["id"], s["status"]) for s in plan["steps"]] == [
            ("1", "completed"),
            ("2", "in_progress"),
            ("3", "blocked"),
        ]
        assert plan["counts"] == {
            "pending": 0,
            "in_progress": 1,
            "completed": 1,
            "blocked": 1,
        }
        assert plan["item_id"] == plan["plan_id"]
        assert plan["status"] == "started"

    def test_scope_selects_the_plan(self, service, store, recorder):
        put_plan(recorder, store, "main", [PlanStep(id="1", subject="main work")])
        put_plan(recorder, store, "worker-2", [PlanStep(id="1", subject="worker work")])

        assert service.get_plan(THREAD)["plan"]["steps"][0]["subject"] == "main work"
        assert (
            service.get_plan(THREAD, scope="worker-2")["plan"]["steps"][0]["subject"]
            == "worker work"
        )

    def test_history_exposes_finalized_plans(self, service, store, recorder):
        put_plan(recorder, store, "main", [PlanStep(id="1", subject="old")])
        recorder.record_archived(THREAD, True)
        put_plan(recorder, store, "main", [PlanStep(id="1", subject="new")])

        result = service.get_plan(THREAD)
        assert result["plan"]["steps"][0]["subject"] == "new"
        assert [p["steps"][0]["subject"] for p in result["plans"]] == ["old", "new"]
        assert [p["status"] for p in result["plans"]] == ["cancelled", "started"]

    def test_unknown_thread_raises_thread_not_found(self, service):
        from omniagents.core.conversation.service import ThreadNotFound

        with pytest.raises(ThreadNotFound):
            service.get_plan("nope")


class TestGetRunDiff:
    def _turn(self, recorder, run_id):
        recorder.record(THREAD, "run_started", {"run_id": run_id, "prompt": "go"})

    def test_absent_diff_is_null_not_an_error(self, service, recorder):
        self._turn(recorder, "run-1")
        result = service.get_run_diff(THREAD)
        assert result["turn_id"] == "run-1"
        assert result["run_diff"] is None

    def test_defaults_to_the_latest_turn(self, service, recorder):
        self._turn(recorder, "run-1")
        recorder.record_run_diff(
            THREAD, run_id="run-1", snapshot={"diff": "old", "files": []}
        )
        recorder.record(
            THREAD, "run_end", {"run_id": "run-1", "end_reason": "completed"}
        )
        self._turn(recorder, "run-2")
        recorder.record_run_diff(
            THREAD, run_id="run-2", snapshot={"diff": "new", "files": []}
        )

        assert service.get_run_diff(THREAD)["run_diff"]["diff"] == "new"

    def test_a_historical_turn_still_exposes_its_diff(self, service, recorder):
        self._turn(recorder, "run-1")
        recorder.record_run_diff(
            THREAD, run_id="run-1", snapshot={"diff": "old", "files": []}
        )
        recorder.record(
            THREAD, "run_end", {"run_id": "run-1", "end_reason": "completed"}
        )
        self._turn(recorder, "run-2")

        result = service.get_run_diff(THREAD, turn_id="run-1")
        assert result["run_diff"]["diff"] == "old"
        assert result["run_diff"]["status"] == "completed"

    def test_unknown_turn_raises_the_reserved_code(self, service, recorder):
        self._turn(recorder, "run-1")
        with pytest.raises(TurnNotFound) as excinfo:
            service.get_run_diff(THREAD, turn_id="run-nope")
        assert excinfo.value.code == -32100
        assert excinfo.value.data == {
            "kind": "turn_not_found",
            "thread_id": THREAD,
            "turn_id": "run-nope",
        }

    def test_a_thread_with_no_turns_answers_null(self, service, recorder):
        result = service.get_run_diff(THREAD)
        assert result == {"thread_id": THREAD, "turn_id": None, "run_diff": None}

    def test_wire_shape_carries_identity_and_revision(self, service, recorder):
        self._turn(recorder, "run-1")
        recorder.record_run_diff(
            THREAD,
            run_id="run-1",
            snapshot={
                "diff": "d",
                "files": [{"path": "a.txt", "change_type": "added"}],
                "stats": {"files_changed": 1},
            },
        )
        run_diff = service.get_run_diff(THREAD)["run_diff"]
        assert run_diff["files"][0]["path"] == "a.txt"
        assert run_diff["stats"]["files_changed"] == 1
        assert run_diff["item_id"].startswith("itm_")
        assert run_diff["revision"] == 0
        assert run_diff["status"] == "started"
