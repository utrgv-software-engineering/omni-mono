"""Recorder: runtime events → persisted domain objects.

The acceptance criterion these tests defend is *"a completed conversation
reconstructs SOLELY from persisted domain objects"*. So each test drives
the recorder with the exact notification payloads the runtime emits, then
asserts on what is IN THE STORE — never on the events that produced it.
"""

import pytest

from omniagents.core.conversation.model import (
    Item,
    ItemKind,
    ItemStatus,
    TurnStatus,
    derive_item_id,
)
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore

THREAD = "sess-1"
RUN = "run_abc"


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


def feed(recorder, *events):
    for method, payload in events:
        recorder.record(THREAD, method, {"session_id": THREAD, **payload})


def items(store, **kwargs):
    return store.list_items(THREAD, limit=1000, **kwargs)


def only(store, kind):
    return [item for item in items(store) if item.kind is kind]


# ---------------------------------------------------------------------
# Thread + turn lifecycle
# ---------------------------------------------------------------------


class TestThreadAndTurns:
    def test_ensure_thread_is_idempotent(self, store, recorder):
        first = recorder.ensure_thread(THREAD, user_id="u1")
        second = recorder.ensure_thread(THREAD, user_id="u1")
        assert first.created_at == second.created_at
        assert store.get_thread(THREAD).user_id == "u1"

    def test_run_started_opens_a_turn_and_records_the_prompt(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi", "prompt_role": "user"}),
        )
        turn = store.get_turn(THREAD, RUN)
        assert turn.ordinal == 1
        assert turn.status is TurnStatus.RUNNING
        assert turn.prompt == "hi"
        prompts = only(store, ItemKind.USER_MESSAGE)
        assert [p.content["text"] for p in prompts] == ["hi"]
        assert prompts[0].status is ItemStatus.COMPLETED

    def test_the_prompt_keeps_what_it_was_sent_with(self, store, recorder):
        sent = [
            {
                "type": "image",
                "url": "data:image/jpeg;base64,AAAA",
                "mime": "image/png",
            },
            {"type": "file", "filename": "notes.pdf"},
        ]
        feed(
            recorder,
            (
                "run_started",
                {"run_id": RUN, "prompt": "what is this?", "attachments": sent},
            ),
        )
        [prompt] = only(store, ItemKind.USER_MESSAGE)
        assert prompt.content["attachments"] == sent
        from omniagents.core.conversation.model import derive_search_text

        assert "AAAA" not in derive_search_text(prompt.kind, prompt.content)

    def test_assistant_role_prompt_records_as_an_agent_message(self, store, recorder):
        """Notification-batch wakeups arrive with prompt_role='assistant' so
        the transcript reads as the agent observing its own background work,
        not as fake user input."""
        feed(
            recorder,
            (
                "run_started",
                {
                    "run_id": RUN,
                    "prompt": "3 jobs finished",
                    "prompt_role": "assistant",
                },
            ),
        )
        assert only(store, ItemKind.USER_MESSAGE) == []
        assert only(store, ItemKind.AGENT_MESSAGE)[0].role == "assistant"

    def test_turn_ordinals_are_dense_across_runs(self, store, recorder):
        for index in range(3):
            feed(recorder, ("run_started", {"run_id": f"run{index}", "prompt": "x"}))
        assert [t.ordinal for t in store.list_turns(THREAD, limit=10)] == [1, 2, 3]

    def test_duplicate_run_started_does_not_open_a_second_turn(self, store, recorder):
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        assert store.count_turns(THREAD) == 1
        assert store.count_items(THREAD) == 1

    def test_run_status_records_retry_attempts(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "run_status",
                {"run_id": RUN, "status": "retrying", "attempt": 2, "total": 3},
            ),
        )
        assert store.get_turn(THREAD, RUN).attempts == 2

    def test_run_end_lands_the_turn_terminally_with_usage_and_model(
        self, store, recorder
    ):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "run_end",
                {
                    "run_id": RUN,
                    "end_reason": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                    "model": "gpt-x",
                    "model_ref": "catalog/gpt-x",
                },
            ),
        )
        turn = store.get_turn(THREAD, RUN)
        assert turn.status is TurnStatus.COMPLETED
        assert turn.usage == {"input_tokens": 10, "output_tokens": 4}
        assert turn.model == "gpt-x"
        assert turn.model_ref == "catalog/gpt-x"
        assert turn.completed_at is not None

    def test_run_end_error_is_preserved(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "run_end",
                {
                    "run_id": RUN,
                    "end_reason": "error",
                    "error": {"message": "boom", "type": "ValueError"},
                },
            ),
        )
        turn = store.get_turn(THREAD, RUN)
        assert turn.status is TurnStatus.FAILED
        assert turn.error == {"message": "boom", "type": "ValueError"}

    def test_token_totals_replace_rather_than_accumulate(self, store, recorder):
        """``token`` carries CUMULATIVE run totals. Summing the deltas would
        multiply a turn's usage by the number of streamed events."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("token", {"run_id": RUN, "delta": {}, "totals": {"input_tokens": 5}}),
            ("token", {"run_id": RUN, "delta": {}, "totals": {"input_tokens": 9}}),
        )
        assert store.get_turn(THREAD, RUN).usage == {"input_tokens": 9}

    def test_thread_usage_is_the_sum_of_settled_turns(self, store, recorder):
        for index in range(2):
            run = f"run{index}"
            feed(
                recorder,
                ("run_started", {"run_id": run, "prompt": "hi"}),
                ("token", {"run_id": run, "delta": {}, "totals": {"input_tokens": 7}}),
                (
                    "run_end",
                    {
                        "run_id": run,
                        "end_reason": "completed",
                        "usage": {"input_tokens": 7},
                    },
                ),
            )
        assert store.get_thread(THREAD).usage == {"input_tokens": 14}

    def test_a_second_run_end_cannot_rewrite_a_settled_turn(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("run_end", {"run_id": RUN, "end_reason": "completed"}),
            ("run_end", {"run_id": RUN, "end_reason": "error"}),
        )
        assert store.get_turn(THREAD, RUN).status is TurnStatus.COMPLETED


# ---------------------------------------------------------------------
# Item lifecycle
# ---------------------------------------------------------------------


class TestItemLifecycle:
    def test_tool_call_starts_then_completes_as_one_item(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.STARTED
        assert tool.revision == 0

        feed(
            recorder,
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "ok",
                    "call_id": "c1",
                },
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.COMPLETED
        assert tool.content["output"] == "ok"
        assert tool.revision == 1
        assert store.count_items(THREAD) == 2  # prompt + ONE tool item

    def test_running_summary_is_recorded_then_superseded_by_the_result(
        self, store, recorder
    ):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "input": "{}",
                    "call_id": "c1",
                    "metadata": {"summary": "Running shell command..."},
                },
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        # A canonical-rendering client titles the in-flight card from this.
        assert tool.content["metadata"] == {"summary": "Running shell command..."}

        # A result WITHOUT metadata must clear it — "Running…" phrasing may
        # never survive on a finished item.
        feed(
            recorder,
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "ok",
                    "call_id": "c1",
                },
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.content["metadata"] == {}

        # And a result WITH metadata replaces it wholesale.
        feed(
            recorder,
            (
                "tool_called",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "input": "{}",
                    "call_id": "c2",
                    "metadata": {"summary": "Running shell command..."},
                },
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "ok",
                    "call_id": "c2",
                    "metadata": {"summary": "Ran npm test", "display_type": "command"},
                },
            ),
        )
        tools = only(store, ItemKind.TOOL_CALL)
        assert tools[-1].content["metadata"] == {
            "summary": "Ran npm test",
            "display_type": "command",
        }

    def test_error_metadata_lands_the_tool_item_as_failed(self, store, recorder):
        feed(
            recorder,
            (
                "tool_called",
                {"run_id": RUN, "tool": "read_file", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "read_file",
                    "output": "ENOENT",
                    "call_id": "c1",
                    "metadata": {"display_type": "error"},
                },
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.FAILED
        assert tool.content["metadata"] == {"display_type": "error"}

    def test_duplicate_tool_result_cannot_rewrite_the_final_state(
        self, store, recorder
    ):
        feed(
            recorder,
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "first",
                    "call_id": "c1",
                },
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "second",
                    "call_id": "c1",
                },
            ),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.content["output"] == "first"
        assert tool.revision == 1

    def test_duplicate_tool_called_does_not_allocate_a_second_ordinal(
        self, store, recorder
    ):
        feed(
            recorder,
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
        )
        assert store.count_items(THREAD) == 1
        assert store.get_thread(THREAD).last_seq == 1

    def test_a_tool_result_with_no_prior_call_is_ignored(self, store, recorder):
        feed(
            recorder,
            (
                "tool_result",
                {"run_id": RUN, "tool": "x", "output": "o", "call_id": "ghost"},
            ),
        )
        assert store.count_items(THREAD) == 0

    def test_tool_events_without_a_call_id_are_not_recorded(self, store, recorder):
        """An uncorrelatable half-pair would create an item that could never
        reach a terminal state through the normal path."""
        feed(recorder, ("tool_called", {"run_id": RUN, "tool": "x", "input": "{}"}))
        assert store.count_items(THREAD) == 0

    def test_run_end_finalizes_every_still_open_item(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "c2",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
            ("run_end", {"run_id": RUN, "end_reason": "error"}),
        )
        assert store.open_items(THREAD) == []
        for item in items(store):
            assert item.status in (
                ItemStatus.COMPLETED,
                ItemStatus.FAILED,
                ItemStatus.CANCELLED,
            )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.FAILED
        assert tool.content["finalized_by"] == "run_end"

    def test_cancelled_run_finalizes_open_items_as_cancelled(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
            ("run_end", {"run_id": RUN, "end_reason": "cancelled"}),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.CANCELLED

    def test_run_end_finalizes_items_that_carry_no_turn_id(self, store, recorder):
        """REGRESSION: the sweep was scoped by ``turn_id``, so an
        elicitation or client_request raised without a ``run_id`` stayed
        ``started`` forever — in-flight state surviving run_end."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("tool_called", {"tool": "execute_bash", "input": "{}", "call_id": "c1"}),
            (
                "elicitation_requested",
                {"elicitation_id": "e1", "kind": "form", "message": "?"},
            ),
            ("run_end", {"run_id": RUN, "end_reason": "completed"}),
        )
        assert store.open_items(THREAD) == []
        assert all(item.is_terminal for item in items(store))

    def test_run_end_does_not_disturb_already_terminal_items(self, store, recorder):
        feed(
            recorder,
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "execute_bash",
                    "output": "ok",
                    "call_id": "c1",
                },
            ),
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("run_end", {"run_id": RUN, "end_reason": "cancelled"}),
        )
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.status is ItemStatus.COMPLETED
        assert "finalized_by" not in tool.content


# ---------------------------------------------------------------------
# Coverage of every mapped concept the ticket enumerates
# ---------------------------------------------------------------------


class TestConceptCoverage:
    def test_commands_files_and_search_carry_their_taxonomy(self, store, recorder):
        pairs = [
            ("c1", "execute_bash", "command"),
            ("c2", "write_file", "file"),
            ("c3", "grep_files", "search"),
        ]
        for call_id, tool, _ in pairs:
            feed(
                recorder,
                (
                    "tool_called",
                    {"run_id": RUN, "tool": tool, "input": "{}", "call_id": call_id},
                ),
                (
                    "tool_result",
                    {"run_id": RUN, "tool": tool, "output": "done", "call_id": call_id},
                ),
            )
        recorded = {
            item.content["tool"]: item.content["tool_kind"]
            for item in only(store, ItemKind.TOOL_CALL)
        }
        assert recorded == {tool: kind for _, tool, kind in pairs}

    def test_plan_tools_are_recorded_as_plan_items(self, store, recorder):
        feed(
            recorder,
            (
                "tool_called",
                {
                    "run_id": RUN,
                    "tool": "task_create",
                    "input": '{"title":"step 1"}',
                    "call_id": "p1",
                },
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "task_create",
                    "output": "created",
                    "call_id": "p1",
                },
            ),
        )
        [plan] = only(store, ItemKind.TOOL_CALL)
        assert plan.content["tool_kind"] == "plan"
        assert plan.status is ItemStatus.COMPLETED

    def test_mcp_tool_calls_keep_server_and_tool_labels(self, store, recorder):
        feed(
            recorder,
            (
                "tool_called",
                {
                    "run_id": RUN,
                    "tool": "mcp_docs__search",
                    "input": "{}",
                    "call_id": "m1",
                    "server_label": "docs",
                    "tool_label": "search",
                },
            ),
            (
                "tool_result",
                {
                    "run_id": RUN,
                    "tool": "mcp_docs__search",
                    "output": "hits",
                    "call_id": "m1",
                    "server_label": "docs",
                },
            ),
        )
        [call] = only(store, ItemKind.TOOL_CALL)
        assert call.content["tool_kind"] == "mcp"
        assert call.content["server_label"] == "docs"
        assert call.content["tool_label"] == "search"

    def test_tool_approval_records_prompt_decision_and_resolution(
        self, store, recorder
    ):
        feed(
            recorder,
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "c1",
                    "tool_name": "execute_bash",
                    "arguments": {"command": "rm -rf /"},
                },
            ),
        )
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.status is ItemStatus.STARTED
        assert approval.content["approval_kind"] == "tool"
        assert approval.content["tool_kind"] == "command"

        recorder.record_approval_decision(
            THREAD,
            approval_kind="tool",
            resource_id="c1",
            decision="reject",
            rejection_message="no",
        )
        feed(recorder, ("tool_approval_resolved", {"call_id": "c1"}))
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.status is ItemStatus.COMPLETED
        assert approval.content["decision"] == "reject"
        assert approval.content["rejection_message"] == "no"

    def test_mcp_approval_records_server_and_decision(self, store, recorder):
        feed(
            recorder,
            (
                "mcp_approval_requested",
                {
                    "run_id": RUN,
                    "request_id": "r1",
                    "server_label": "docs",
                    "tool_name": "search",
                    "arguments": {},
                },
            ),
        )
        recorder.record_approval_decision(
            THREAD, approval_kind="mcp", resource_id="r1", decision="approve"
        )
        feed(recorder, ("mcp_approval_resolved", {"request_id": "r1"}))
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.content["approval_kind"] == "mcp"
        assert approval.content["server_label"] == "docs"
        assert approval.content["decision"] == "approve"
        assert approval.status is ItemStatus.COMPLETED

    def test_elicitation_records_prompt_and_answer(self, store, recorder):
        feed(
            recorder,
            (
                "elicitation_requested",
                {
                    "run_id": RUN,
                    "elicitation_id": "elc_1",
                    "kind": "select",
                    "message": "which?",
                    "options": ["a", "b"],
                },
            ),
            (
                "elicitation_resolved",
                {
                    "elicitation_id": "elc_1",
                    "status": "answered",
                    "action": "accept",
                    "value": {"choice": "a"},
                },
            ),
        )
        [elicitation] = only(store, ItemKind.ELICITATION)
        assert elicitation.status is ItemStatus.COMPLETED
        assert elicitation.content["elicitation_kind"] == "select"
        assert elicitation.content["value"] == {"choice": "a"}

    def test_cancelled_elicitation_lands_as_cancelled(self, store, recorder):
        feed(
            recorder,
            (
                "elicitation_requested",
                {"elicitation_id": "elc_1", "kind": "confirm", "message": "ok?"},
            ),
            (
                "elicitation_resolved",
                {"elicitation_id": "elc_1", "status": "cancelled"},
            ),
        )
        assert only(store, ItemKind.ELICITATION)[0].status is ItemStatus.CANCELLED

    def test_artifacts_are_recorded_from_the_ui_client_request(self, store, recorder):
        feed(
            recorder,
            (
                "client_request",
                {
                    "run_id": RUN,
                    "request_id": "req1",
                    "function": "ui.add_artifact",
                    "args": {
                        "title": "Report",
                        "content": "# hi",
                        "mode": "markdown",
                        "artifact_id": "a1",
                    },
                },
            ),
        )
        [artifact] = only(store, ItemKind.ARTIFACT)
        assert artifact.status is ItemStatus.COMPLETED
        assert artifact.content == {
            "artifact_id": "a1",
            "title": "Report",
            "mode": "markdown",
            "content": "# hi",
        }

    def test_redisplaying_an_artifact_updates_it_in_place(self, store, recorder):
        """REGRESSION: the second version was silently dropped.

        Terminal-once governs an item's STATUS. Artifact content is
        explicitly revisable — re-issuing ``ui.add_artifact`` with the same
        id means "here is the new version" — and ``revision`` records it."""
        base = {"run_id": RUN, "function": "ui.add_artifact"}
        feed(
            recorder,
            (
                "client_request",
                {
                    **base,
                    "request_id": "q1",
                    "args": {"artifact_id": "a1", "title": "v1", "content": "FIRST"},
                },
            ),
            (
                "client_request",
                {
                    **base,
                    "request_id": "q2",
                    "args": {"artifact_id": "a1", "title": "v2", "content": "SECOND"},
                },
            ),
        )
        [artifact] = only(store, ItemKind.ARTIFACT)
        assert artifact.content["content"] == "SECOND"
        assert artifact.content["title"] == "v2"
        assert artifact.revision == 1
        assert artifact.status is ItemStatus.COMPLETED

    def test_redisplaying_the_same_artifact_id_does_not_duplicate(
        self, store, recorder
    ):
        payload = {
            "run_id": RUN,
            "request_id": "req1",
            "function": "ui.add_artifact",
            "args": {"title": "Report", "content": "v1", "artifact_id": "a1"},
        }
        feed(recorder, ("client_request", payload))
        feed(recorder, ("client_request", {**payload, "request_id": "req2"}))
        assert len(only(store, ItemKind.ARTIFACT)) == 1

    def test_non_artifact_client_requests_are_not_conversation_items(
        self, store, recorder
    ):
        """``ui.set_status`` / ``ui.set_session_state`` are presence pushes,
        not transcript content."""
        feed(
            recorder,
            (
                "client_request",
                {
                    "request_id": "r1",
                    "function": "ui.set_status",
                    "args": {"text": "thinking"},
                },
            ),
            (
                "client_request",
                {"request_id": "r2", "function": "ui.set_session_state", "args": {}},
            ),
        )
        assert store.count_items(THREAD) == 0

    def test_compaction_checkpoint_moves_the_thread_floor(self, store, recorder):
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        item = recorder.record_compaction_checkpoint(
            THREAD, summary="earlier work", kind="context_summary", version=1
        )
        thread = store.get_thread(THREAD)
        assert thread.compaction == {
            "checkpoint_item_id": item.item_id,
            "checkpoint_seq": item.seq,
            "compacted_below_seq": item.seq,
        }
        assert only(store, ItemKind.COMPACTION)[0].content["summary"] == "earlier work"

    def test_mid_run_user_messages_are_recorded(self, store, recorder):
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        recorder.record_user_message(THREAD, run_id=RUN, content="also do this")
        assert [i.content["text"] for i in only(store, ItemKind.USER_MESSAGE)] == [
            "hi",
            "also do this",
        ]

    def test_message_output_records_each_assistant_message(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("message_output", {"run_id": RUN, "content": "first"}),
            ("message_output", {"run_id": RUN, "content": "second"}),
        )
        assert [i.content["text"] for i in only(store, ItemKind.AGENT_MESSAGE)] == [
            "first",
            "second",
        ]

    def test_unhandled_notifications_are_ignored(self, store, recorder):
        feed(
            recorder,
            ("queue_changed", {"depth": 1, "items": []}),
            ("session_variables_changed", {"changed": {}, "variables": {}}),
            ("client_request_resolved", {"request_id": "r1"}),
        )
        assert store.count_items(THREAD) == 0


# ---------------------------------------------------------------------
# Review decisions (workflow completion + guardian approvals)
# ---------------------------------------------------------------------


class TestReviewDecisions:
    """Review verdicts are part of the record: a client reloading a session
    must see the same guardian/workflow decisions it saw live."""

    def test_workflow_review_appends_a_terminal_item_under_its_turn(
        self, store, recorder
    ):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "build it"}),
            (
                "plan_completion_reviewed",
                {
                    "task_id": "1",
                    "subject": "Wire the API",
                    "outcome": "reject",
                    "reviewer": "guardian",
                    "rationale": "exit criteria unmet",
                    "run_id": RUN,
                },
            ),
        )
        [review] = only(store, ItemKind.WORKFLOW_REVIEW)
        assert review.turn_id == RUN
        assert review.status is ItemStatus.COMPLETED
        assert review.role == "assistant"
        assert review.content == {
            "task_id": "1",
            "subject": "Wire the API",
            "outcome": "reject",
            "reviewer": "guardian",
            "rationale": "exit criteria unmet",
        }

    def test_repeated_reviews_of_one_task_stay_distinct_in_order(self, store, recorder):
        """The same task is legitimately reviewed more than once (rejected,
        then accepted). The transcript is a record: both verdicts stay."""
        base = {
            "task_id": "1",
            "subject": "Wire the API",
            "reviewer": "guardian",
            "run_id": RUN,
        }
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "build it"}),
            (
                "plan_completion_reviewed",
                {**base, "outcome": "reject", "rationale": "not yet"},
            ),
            (
                "plan_completion_reviewed",
                {**base, "outcome": "accept_verified", "rationale": "done"},
            ),
        )
        reviews = only(store, ItemKind.WORKFLOW_REVIEW)
        assert [r.content["outcome"] for r in reviews] == [
            "reject",
            "accept_verified",
        ]
        assert reviews[0].item_id != reviews[1].item_id
        assert reviews[0].seq < reviews[1].seq

    def test_workflow_review_drops_non_string_fields(self, store, recorder):
        feed(
            recorder,
            (
                "plan_completion_reviewed",
                {
                    "task_id": "1",
                    "subject": None,
                    "outcome": "accept_unverified",
                    "reviewer": "none",
                    "rationale": 123,
                },
            ),
        )
        [review] = only(store, ItemKind.WORKFLOW_REVIEW)
        assert review.content == {
            "task_id": "1",
            "outcome": "accept_unverified",
            "reviewer": "none",
        }

    def test_workflow_review_without_a_task_id_is_ignored(self, store, recorder):
        feed(
            recorder,
            ("plan_completion_reviewed", {"outcome": "reject", "reviewer": "guardian"}),
            ("plan_completion_reviewed", {"task_id": 7, "outcome": "reject"}),
        )
        assert store.count_items(THREAD) == 0

    def test_tool_approval_reviewed_merges_the_verdict_onto_the_prompt(
        self, store, recorder
    ):
        feed(
            recorder,
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "a1",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
            (
                "tool_approval_reviewed",
                {
                    "call_id": "a1",
                    "kind": "tool",
                    "tool_name": "write_file",
                    "reviewer": "guardian",
                    "outcome": "deny",
                    "rationale": "writes outside the workspace",
                    "risk_level": "high",
                    "run_id": RUN,
                },
            ),
        )
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.content["reviewer"] == "guardian"
        assert approval.content["outcome"] == "deny"
        assert approval.content["rationale"] == "writes outside the workspace"
        assert approval.content["risk_level"] == "high"
        # The review is content, not a lifecycle step: status is untouched.
        assert approval.status is ItemStatus.STARTED

    def test_mcp_reviewed_targets_the_mcp_approval_item(self, store, recorder):
        """The mcp lane reuses the ``call_id`` payload field for its
        ``request_id`` (``request_mcp_approval`` passes it through)."""
        feed(
            recorder,
            (
                "mcp_approval_requested",
                {
                    "run_id": RUN,
                    "request_id": "r1",
                    "server_label": "docs",
                    "tool_name": "search",
                    "arguments": {},
                },
            ),
            (
                "tool_approval_reviewed",
                {
                    "call_id": "r1",
                    "kind": "mcp",
                    "tool_name": "search",
                    "server_label": "docs",
                    "reviewer": "guardian",
                    "outcome": "allow",
                    "rationale": "read-only",
                },
            ),
        )
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.content["approval_kind"] == "mcp"
        assert approval.content["reviewer"] == "guardian"
        assert approval.content["outcome"] == "allow"

    def test_reviewed_without_a_prompt_still_records_the_decision(
        self, store, recorder
    ):
        """The guardian/sandbox lane never broadcasts the approval prompt
        (``_resolve_approval_without_client`` returns before it), so the
        review must stand alone or a reloading client loses the verdict."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "build it"}),
            (
                "tool_approval_reviewed",
                {
                    "call_id": "a1",
                    "kind": "tool",
                    "tool_name": "execute_bash",
                    "reviewer": "sandbox-policy",
                    "outcome": "allow",
                    "rationale": "sandboxed environment",
                    "run_id": RUN,
                },
            ),
        )
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.status is ItemStatus.COMPLETED
        assert approval.turn_id == RUN
        assert approval.content["approval_kind"] == "tool"
        assert approval.content["tool"] == "execute_bash"
        assert approval.content["call_id"] == "a1"
        assert approval.content["reviewer"] == "sandbox-policy"
        assert approval.content["outcome"] == "allow"
        assert approval.content["rationale"] == "sandboxed environment"

    def test_duplicate_reviewed_delivery_does_not_duplicate(self, store, recorder):
        payload = {
            "call_id": "a1",
            "kind": "tool",
            "tool_name": "execute_bash",
            "reviewer": "guardian",
            "outcome": "allow",
            "rationale": "safe",
        }
        feed(
            recorder,
            ("tool_approval_reviewed", payload),
            ("tool_approval_reviewed", payload),
        )
        assert len(only(store, ItemKind.APPROVAL)) == 1

    def test_resolved_stores_the_decision_when_the_payload_carries_it(
        self, store, recorder
    ):
        feed(
            recorder,
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "c1",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
            ("tool_approval_resolved", {"call_id": "c1", "decision": "approve"}),
        )
        [approval] = only(store, ItemKind.APPROVAL)
        assert approval.status is ItemStatus.COMPLETED
        assert approval.content["decision"] == "approve"

    def test_reviewed_without_a_call_id_is_ignored(self, store, recorder):
        feed(
            recorder,
            (
                "tool_approval_reviewed",
                {"kind": "tool", "reviewer": "guardian", "outcome": "allow"},
            ),
            ("tool_approval_reviewed", {"call_id": "", "kind": "tool"}),
        )
        assert store.count_items(THREAD) == 0


# ---------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------


class TestBranching:
    def _parent_conversation(self, recorder):
        feed(
            recorder,
            (
                "run_started",
                {"run_id": RUN, "prompt": "build it", "prompt_role": "user"},
            ),
            (
                "tool_called",
                {"run_id": RUN, "tool": "read_file", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_result",
                {"run_id": RUN, "tool": "read_file", "output": "src", "call_id": "c1"},
            ),
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "c2",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
        )
        recorder.record_approval_decision(
            THREAD, approval_kind="tool", resource_id="c2", decision="approve"
        )
        feed(
            recorder,
            ("tool_approval_resolved", {"call_id": "c2"}),
            ("message_output", {"run_id": RUN, "content": "done"}),
            ("run_end", {"run_id": RUN, "end_reason": "completed"}),
        )

    def test_fork_copies_the_parent_transcript(self, store, recorder):
        """REGRESSION: a forked session used to read back EMPTY.

        ``record_fork`` stamped ancestry onto a thread row it created with
        no items, and the reader refused to project a thread that already
        existed — so every fork rendered as a blank conversation.
        """
        self._parent_conversation(recorder)
        parent_items = items(store)
        assert len(parent_items) == 4

        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )

        child_items = store.list_items("sess-2", limit=100)
        assert len(child_items) == len(parent_items)
        assert [(i.seq, i.kind, i.status) for i in child_items] == [
            (i.seq, i.kind, i.status) for i in parent_items
        ]
        assert [i.content for i in child_items] == [i.content for i in parent_items]

    def test_fork_preserves_recorded_richness_not_a_projection(self, store, recorder):
        """Approvals never appear in raw history; a re-projection would lose
        them, so the copy has to be of the DOMAIN rows."""
        self._parent_conversation(recorder)
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        approvals = [
            item
            for item in store.list_items("sess-2", limit=100)
            if item.kind is ItemKind.APPROVAL
        ]
        assert len(approvals) == 1
        assert approvals[0].content["decision"] == "approve"

    def test_fork_child_items_are_independently_addressable(self, store, recorder):
        self._parent_conversation(recorder)
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        parent_ids = {i.item_id for i in items(store)}
        child = store.list_items("sess-2", limit=100)
        assert parent_ids.isdisjoint({i.item_id for i in child})
        assert child[0].source_ref["forked_from_thread_id"] == THREAD
        assert child[0].source_ref["forked_from_item_id"] in parent_ids

    def test_fork_stamps_the_parent_and_the_branch_point(self, store, recorder):
        self._parent_conversation(recorder)
        tail = store.list_items(THREAD, limit=1, descending=True)[0]
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        child = store.get_thread("sess-2")
        assert child.parent_thread_id == THREAD
        assert child.branched_from_item_id == tail.item_id
        assert child.user_id == "u1"

    def test_the_fork_can_be_written_to_without_colliding(self, store, recorder):
        """The child inherits the parent's ordinal cursor, so its next item
        continues the sequence instead of overwriting a copied row."""
        self._parent_conversation(recorder)
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        before = len(store.list_items("sess-2", limit=100))
        recorder.record(
            "sess-2",
            "run_started",
            {
                "session_id": "sess-2",
                "run_id": "run2",
                "prompt": "diverge",
                "prompt_role": "user",
            },
        )
        after = store.list_items("sess-2", limit=100)
        assert len(after) == before + 1
        seqs = [i.seq for i in after]
        assert len(seqs) == len(set(seqs))

    def test_a_fork_taken_mid_run_leaves_no_open_item_in_the_child(
        self, store, recorder
    ):
        """REGRESSION (N2): the child violated the run_end guarantee forever.

        Forking while a tool call is in flight copied it at ``started`` with
        the PARENT's turn_id. The child's own ``run_end`` skips it (wrong
        turn) and no parent ``run_end`` ever reaches the child thread, so
        the child kept a permanently non-terminal item — the "no in-flight
        state after run_end" guarantee, false for a reachable case.
        """
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "start work"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
        )
        assert store.open_items(THREAD)  # parent is genuinely mid-run

        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )

        child_open = store.open_items("sess-2")
        assert child_open == [], [(i.seq, i.kind, i.status) for i in child_open]
        [child_tool] = [
            i
            for i in store.list_items("sess-2", limit=50)
            if i.kind is ItemKind.TOOL_CALL
        ]
        assert child_tool.status is ItemStatus.CANCELLED
        assert child_tool.content["finalized_by"] == "fork"
        # The PARENT's copy is untouched: its run is still going.
        [parent_tool] = only(store, ItemKind.TOOL_CALL)
        assert parent_tool.status is ItemStatus.STARTED

    def test_a_forked_long_lived_item_stays_open_in_the_child(self, store, recorder):
        """The fork sweep honours the same exemption as run_end."""
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "start"}))
        plan = Item(
            item_id=derive_item_id(THREAD, "plan:p1"),
            thread_id=THREAD,
            turn_id=RUN,
            seq=store.allocate_seq(THREAD),
            kind=ItemKind.PLAN,
            status=ItemStatus.STARTED,
            created_at=1.0,
            updated_at=1.0,
            content={"steps": []},
            long_lived=True,
        )
        store.upsert_item(plan)
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        child_plans = [
            i for i in store.list_items("sess-2", limit=50) if i.kind is ItemKind.PLAN
        ]
        assert [p.status for p in child_plans] == [ItemStatus.STARTED]

    def test_forking_an_empty_thread_records_no_branch_item(self, store, recorder):
        recorder.record(
            THREAD, "session_forked", {"session_id": THREAD, "new_session_id": "sess-2"}
        )
        child = store.get_thread("sess-2")
        assert child.parent_thread_id == THREAD
        assert child.branched_from_item_id is None

    def test_duplicate_fork_delivery_does_not_double_the_child(self, store, recorder):
        self._parent_conversation(recorder)
        for _ in range(2):
            recorder.record(
                THREAD,
                "session_forked",
                {"session_id": THREAD, "new_session_id": "sess-2"},
            )
        assert len(store.list_items("sess-2", limit=100)) == 4


class TestIdempotentIdentity:
    """Ids must come from the payload, never from a server-side position.

    A positional key made a re-delivered event a NEW item, and made the id
    underivable by a client — which breaks merging the live stream against a
    stored page, the reason canonical ids exist at all.
    """

    def test_two_identical_messages_are_two_items(self, store, recorder):
        """The transcript is a record: repeated content is not collapsed.

        A content digest alone made a second identical message vanish. The
        occurrence ordinal keeps both, and both ids stay derivable by a
        client counting the ``message_output`` events it received."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("message_output", {"run_id": RUN, "content": "answer"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "read_file", "input": "{}", "call_id": "c1"},
            ),
            ("message_output", {"run_id": RUN, "content": "answer"}),
        )
        messages = [i for i in items(store) if i.kind is ItemKind.AGENT_MESSAGE]
        assert len(messages) == 2
        assert [m.content["text"] for m in messages] == ["answer", "answer"]
        assert messages[0].item_id != messages[1].item_id
        assert [m.item_id for m in messages] == [
            derive_item_id(
                THREAD,
                ConversationRecorder.message_natural_key(RUN, "answer", None, n),
            )
            for n in (0, 1)
        ]

    def test_message_id_when_present_keeps_identical_texts_distinct(
        self, store, recorder
    ):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("message_output", {"run_id": RUN, "content": "ok", "message_id": "msg_1"}),
            ("message_output", {"run_id": RUN, "content": "ok", "message_id": "msg_2"}),
        )
        messages = [i for i in items(store) if i.kind is ItemKind.AGENT_MESSAGE]
        assert len(messages) == 2
        assert {m.content["message_id"] for m in messages} == {"msg_1", "msg_2"}

    def test_message_ids_are_client_derivable_from_the_payload(self, store, recorder):
        """A client folding the live stream computes the same id."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("message_output", {"run_id": RUN, "content": "answer"}),
        )
        message = [i for i in items(store) if i.kind is ItemKind.AGENT_MESSAGE][0]
        assert message.item_id == derive_item_id(
            THREAD, ConversationRecorder.message_natural_key(RUN, "answer", None)
        )

    def test_an_unrelated_earlier_item_does_not_shift_a_message_id(
        self, store, recorder
    ):
        """Two conversations differing only by a mid-run user message used to
        produce different agent-message ids, so a stream-folding client could
        not derive them."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            ("message_output", {"run_id": RUN, "content": "answer"}),
        )
        plain = [i for i in items(store) if i.kind is ItemKind.AGENT_MESSAGE][0]

        other = ConversationRecorder(store)
        other.ensure_thread("sess-2", user_id="u1")
        other.record(
            "sess-2",
            "run_started",
            {"session_id": "sess-2", "run_id": RUN, "prompt": "hi"},
        )
        other.record_user_message("sess-2", run_id=RUN, content="one more thing")
        other.record(
            "sess-2",
            "message_output",
            {"session_id": "sess-2", "run_id": RUN, "content": "answer"},
        )
        shifted = [
            i
            for i in store.list_items("sess-2", limit=100)
            if i.kind is ItemKind.AGENT_MESSAGE
        ][0]
        key = ConversationRecorder.message_natural_key(RUN, "answer", None)
        assert plain.item_id == derive_item_id(THREAD, key)
        assert shifted.item_id == derive_item_id("sess-2", key)

    def test_two_identical_mid_run_user_messages_are_two_items(self, store, recorder):
        """A user typing "continue" twice must not lose one of them."""
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        recorder.record_user_message(THREAD, run_id=RUN, content="continue")
        recorder.record_user_message(THREAD, run_id=RUN, content="continue")
        users = [i for i in items(store) if i.kind is ItemKind.USER_MESSAGE]
        assert [u.content["text"] for u in users] == ["hi", "continue", "continue"]
        repeated = users[1:]
        assert repeated[0].item_id != repeated[1].item_id
        # Derivable: the client knows how many times it sent that content.
        assert [u.item_id for u in repeated] == [
            derive_item_id(
                THREAD,
                ConversationRecorder.user_message_natural_key(RUN, "continue", n),
            )
            for n in (0, 1)
        ]

    def test_two_identical_compactions_are_two_items(self, store, recorder):
        """Two compactions that happened to summarize to the same text are
        still two events in the record."""
        for _ in range(2):
            recorder.record_compaction_checkpoint(
                THREAD, summary="same summary", kind="context_summary", version=1
            )
        checkpoints = only(store, ItemKind.COMPACTION)
        assert len(checkpoints) == 2
        assert checkpoints[0].item_id != checkpoints[1].item_id

    def test_id_keyed_kinds_remain_strictly_idempotent(self, store, recorder):
        """Where the payload carries a real id, re-delivery must NOT append.

        This is the half of the contract the occurrence ordinal does not
        touch: tool calls, approvals, elicitations and provider-id'd
        messages all dedupe on their own identifier."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "read_file", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_called",
                {"run_id": RUN, "tool": "read_file", "input": "{}", "call_id": "c1"},
            ),
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "a1",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
            (
                "tool_approval_requested",
                {
                    "run_id": RUN,
                    "call_id": "a1",
                    "tool_name": "write_file",
                    "arguments": {},
                },
            ),
            (
                "elicitation_requested",
                {"elicitation_id": "e1", "kind": "confirm", "message": "?"},
            ),
            (
                "elicitation_requested",
                {"elicitation_id": "e1", "kind": "confirm", "message": "?"},
            ),
            ("message_output", {"run_id": RUN, "content": "ok", "message_id": "msg_1"}),
            ("message_output", {"run_id": RUN, "content": "ok", "message_id": "msg_1"}),
        )
        assert len(only(store, ItemKind.TOOL_CALL)) == 1
        assert len(only(store, ItemKind.APPROVAL)) == 1
        assert len(only(store, ItemKind.ELICITATION)) == 1
        assert len(only(store, ItemKind.AGENT_MESSAGE)) == 1


class TestReasoning:
    """Both producers of this surface must agree about the same
    conversation. The adapter projects reasoning from history, so the
    recorder has to record it too."""

    def test_reasoning_is_recorded_when_streamed(self, store, recorder):
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        recorder.record_reasoning(
            THREAD,
            run_id=RUN,
            item={
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "plan it"}],
            },
        )
        [reasoning] = only(store, ItemKind.REASONING)
        assert reasoning.content["summary"] == "plan it"
        assert reasoning.content["reasoning_id"] == "rs_1"
        assert reasoning.turn_id == RUN
        assert reasoning.status is ItemStatus.COMPLETED

    def test_chat_completions_reasoning_records_its_raw_text(self, store, recorder):
        """Chat-completions providers return reasoning as ``reasoning_text``
        content with no summary; that text is the record."""
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        recorder.record_reasoning(
            THREAD,
            run_id=RUN,
            item={
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "read the file"}],
            },
        )
        [reasoning] = only(store, ItemKind.REASONING)
        assert reasoning.content["summary"] == "read the file"

    def test_reasoning_is_idempotent_on_its_model_id(self, store, recorder):
        item = {"type": "reasoning", "id": "rs_1", "summary": []}
        recorder.record_reasoning(THREAD, run_id=RUN, item=item)
        recorder.record_persisted_reasoning(THREAD, run_id=RUN, item=item)
        assert len(only(store, ItemKind.REASONING)) == 1

    def test_history_write_records_only_identifiable_reasoning(self, store, recorder):
        """An id-less block is the stream path's alone: the history write
        cannot tell it from a second block with the same text."""
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        raw = {
            "type": "reasoning",
            "id": "__fake_id__",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "look"}],
        }
        recorder.record_reasoning(THREAD, run_id=RUN, item=raw)
        recorder.record_persisted_reasoning(THREAD, run_id=RUN, item=raw)
        recorder.record_persisted_reasoning(
            THREAD, run_id=RUN, item={"type": "reasoning", "id": "rs_2", "summary": []}
        )
        assert [r.content.get("reasoning_id") for r in only(store, ItemKind.REASONING)] == [
            None,
            "rs_2",
        ]


class TestReservedKinds:
    """``plan`` and ``run_diff`` are declared but unproduced.

    They exist so tkt_FqUypyxF2tzC is additive against a ``stable`` surface
    — the TypeScript ``ConversationItemKind`` union is closed, so widening
    it later would be a lockstep client break. These tests pin that the
    reservation is real (the values round-trip through storage and filter
    correctly) and that nothing produces them yet, so the "reserved" claim
    in protocol.md stays true.
    """

    def _store_one(self, store, kind):
        item = Item(
            item_id=derive_item_id(THREAD, f"reserved:{kind.value}"),
            thread_id=THREAD,
            turn_id=RUN,
            seq=store.allocate_seq(THREAD),
            kind=kind,
            status=ItemStatus.STARTED,
            created_at=1.0,
            updated_at=1.0,
            content={"reserved": True},
        )
        store.upsert_item(item)
        return item

    @pytest.mark.parametrize("kind", [ItemKind.PLAN, ItemKind.RUN_DIFF])
    def test_a_reserved_kind_round_trips_through_storage(self, store, recorder, kind):
        stored = self._store_one(store, kind)
        loaded = store.get_item(THREAD, stored.item_id)
        assert loaded.kind is kind
        assert loaded.to_dict()["kind"] == kind.value

    @pytest.mark.parametrize("kind", [ItemKind.PLAN, ItemKind.RUN_DIFF])
    def test_a_reserved_kind_is_filterable(self, store, recorder, kind):
        self._store_one(store, ItemKind.PLAN)
        self._store_one(store, ItemKind.RUN_DIFF)
        filtered = store.list_items(THREAD, limit=10, kinds=[kind])
        assert [item.kind for item in filtered] == [kind]

    def test_run_diff_is_turn_scoped_so_run_end_settles_it(self, store, recorder):
        """Unlike a plan, a run diff belongs to its turn: it must NOT need a
        long_lived exemption, and run_end must land it."""
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "edit files"}))
        item = self._store_one(store, ItemKind.RUN_DIFF)
        assert item.long_lived is False
        feed(recorder, ("run_end", {"run_id": RUN, "end_reason": "completed"}))
        assert store.get_item(THREAD, item.item_id).is_terminal

    def test_no_runtime_event_produces_a_reserved_kind(self, store, recorder):
        """The "not yet produced" half of the claim in protocol.md."""
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "task_create", "input": "{}", "call_id": "p1"},
            ),
            (
                "tool_result",
                {"run_id": RUN, "tool": "task_create", "output": "ok", "call_id": "p1"},
            ),
            (
                "tool_called",
                {"run_id": RUN, "tool": "apply_patch", "input": "{}", "call_id": "f1"},
            ),
            (
                "tool_result",
                {"run_id": RUN, "tool": "apply_patch", "output": "ok", "call_id": "f1"},
            ),
            ("message_output", {"run_id": RUN, "content": "done"}),
            ("run_end", {"run_id": RUN, "end_reason": "completed"}),
        )
        produced = {item.kind for item in items(store)}
        assert ItemKind.PLAN not in produced
        assert ItemKind.RUN_DIFF not in produced
        # The plan/file TOOL CALLS are still recorded, under their taxonomy.
        assert {i.content["tool_kind"] for i in only(store, ItemKind.TOOL_CALL)} == {
            "plan",
            "file",
        }


class TestLongLivedItems:
    """``run_end`` must not finalize an item that outlives its turn.

    Reserved ahead of durable structured plans (tkt_FqUypyxF2tzC): a plan
    accumulates steps across turns, so the default sweep would close it at
    the end of the run that created it.
    """

    def _open_plan(self, store, turn_id):
        item = Item(
            item_id=derive_item_id(THREAD, "plan:p1"),
            thread_id=THREAD,
            turn_id=turn_id,
            seq=store.allocate_seq(THREAD),
            kind=ItemKind.PLAN,
            status=ItemStatus.STARTED,
            created_at=1.0,
            updated_at=1.0,
            content={"steps": []},
            long_lived=True,
        )
        store.upsert_item(item)
        return item

    def test_a_long_lived_item_survives_run_end(self, store, recorder):
        feed(recorder, ("run_started", {"run_id": RUN, "prompt": "hi"}))
        plan = self._open_plan(store, RUN)
        feed(recorder, ("run_end", {"run_id": RUN, "end_reason": "completed"}))
        assert store.get_item(THREAD, plan.item_id).status is ItemStatus.STARTED

    def test_a_normal_item_in_the_same_turn_is_still_finalized(self, store, recorder):
        feed(
            recorder,
            ("run_started", {"run_id": RUN, "prompt": "hi"}),
            (
                "tool_called",
                {"run_id": RUN, "tool": "execute_bash", "input": "{}", "call_id": "c1"},
            ),
        )
        self._open_plan(store, RUN)
        feed(recorder, ("run_end", {"run_id": RUN, "end_reason": "completed"}))
        [tool] = only(store, ItemKind.TOOL_CALL)
        assert tool.is_terminal
        assert only(store, ItemKind.PLAN)[0].status is ItemStatus.STARTED

    def test_the_flag_survives_a_transition(self, store, recorder):
        item = self._open_plan(store, RUN)
        store.upsert_item(item.with_transition(content={"steps": ["a"]}, now=2.0))
        assert store.get_item(THREAD, item.item_id).long_lived is True

    def test_the_flag_round_trips_through_storage(self, store, recorder):
        item = self._open_plan(store, RUN)
        assert store.get_item(THREAD, item.item_id).long_lived is True
        assert store.list_items(THREAD, limit=10)[0].long_lived is True


# ---------------------------------------------------------------------
# Full conversation reconstruction
# ---------------------------------------------------------------------


def test_completed_conversation_reconstructs_from_the_store_alone(store, recorder):
    """The headline acceptance criterion. Drive a realistic multi-turn
    coding conversation, then rebuild it from persisted rows with no
    reference to the events that produced them."""
    feed(
        recorder,
        (
            "run_started",
            {"run_id": "r1", "prompt": "add a test", "prompt_role": "user"},
        ),
        (
            "tool_called",
            {
                "run_id": "r1",
                "tool": "read_file",
                "input": '{"path":"a.py"}',
                "call_id": "c1",
            },
        ),
        (
            "tool_result",
            {
                "run_id": "r1",
                "tool": "read_file",
                "output": "def f(): ...",
                "call_id": "c1",
            },
        ),
        (
            "tool_approval_requested",
            {
                "run_id": "r1",
                "call_id": "c2",
                "tool_name": "write_file",
                "arguments": {"path": "t.py"},
            },
        ),
    )
    recorder.record_approval_decision(
        THREAD, approval_kind="tool", resource_id="c2", decision="approve"
    )
    feed(
        recorder,
        ("tool_approval_resolved", {"call_id": "c2"}),
        (
            "tool_called",
            {
                "run_id": "r1",
                "tool": "write_file",
                "input": '{"path":"t.py"}',
                "call_id": "c3",
            },
        ),
        (
            "tool_result",
            {
                "run_id": "r1",
                "tool": "write_file",
                "output": "written",
                "call_id": "c3",
            },
        ),
        ("message_output", {"run_id": "r1", "content": "Added the test."}),
        (
            "run_end",
            {"run_id": "r1", "end_reason": "completed", "usage": {"input_tokens": 40}},
        ),
        (
            "run_started",
            {"run_id": "r2", "prompt": "now run it", "prompt_role": "user"},
        ),
        (
            "tool_called",
            {
                "run_id": "r2",
                "tool": "execute_bash",
                "input": '{"command":"pytest"}',
                "call_id": "c4",
            },
        ),
        (
            "tool_result",
            {
                "run_id": "r2",
                "tool": "execute_bash",
                "output": "1 passed",
                "call_id": "c4",
            },
        ),
        ("message_output", {"run_id": "r2", "content": "Green."}),
        (
            "run_end",
            {"run_id": "r2", "end_reason": "completed", "usage": {"input_tokens": 20}},
        ),
    )

    thread = store.get_thread(THREAD)
    assert thread.turn_count == 2
    assert thread.item_count == 8
    assert thread.usage == {"input_tokens": 60}

    turns = store.list_turns(THREAD, limit=10)
    assert [t.status for t in turns] == [TurnStatus.COMPLETED, TurnStatus.COMPLETED]
    assert [t.ordinal for t in turns] == [1, 2]

    all_items = items(store)
    # Dense, gap-free ordinals: nothing was dropped or double-allocated.
    assert [i.seq for i in all_items] == list(range(1, 9))
    # Every item landed in exactly one authoritative terminal state.
    assert all(i.is_terminal for i in all_items)
    assert [(i.kind.value, i.status.value) for i in all_items] == [
        ("user_message", "completed"),
        ("tool_call", "completed"),
        ("approval", "completed"),
        ("tool_call", "completed"),
        ("agent_message", "completed"),
        ("user_message", "completed"),
        ("tool_call", "completed"),
        ("agent_message", "completed"),
    ]

    # Turn windows address exactly their own items.
    turn_one_items = store.list_items(THREAD, limit=100, turn_id="r1")
    assert [i.seq for i in turn_one_items] == [1, 2, 3, 4, 5]
    assert turns[0].first_seq == 1 and turns[0].last_seq == 5


@pytest.mark.parametrize("kind,key", [("tool", "call_id"), ("mcp", "request_id")])
def test_parent_turn_end_does_not_finalize_worker_approval(store, recorder, kind, key):
    feed(
        recorder,
        ("run_started", {"run_id": RUN, "prompt": "worker message"}),
        (
            f"{kind}_approval_requested",
            {
                key: "worker-approval",
                "tool_name": "read_file",
                "worker_id": "worker",
                "worker_session_id": "child",
                "worker_run_id": "child-run",
            },
        ),
        ("run_end", {"run_id": RUN, "end_reason": "completed"}),
    )
    approval = next(item for item in items(store) if item.kind == ItemKind.APPROVAL)
    assert approval.status == ItemStatus.STARTED
    assert approval.content["worker_session_id"] == "child"
    feed(recorder, (f"{kind}_approval_resolved", {key: "worker-approval"}))
    assert store.get_item(THREAD, approval.item_id).status == ItemStatus.COMPLETED
