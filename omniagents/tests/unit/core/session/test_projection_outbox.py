import pytest


@pytest.fixture
def projection_db():
    from omniagents.core.session import history_db

    return history_db


class TestProjectionOutbox:
    @pytest.mark.parametrize("kind", ["queued_message", "user_message"])
    @pytest.mark.parametrize("boundary", ["before_domain", "before_ack"])
    def test_injected_message_recovery_preserves_identity_and_order(
        self, projection_db, monkeypatch, kind, boundary
    ):
        from omniagents.core.session.manager import Session
        from omniagents.core.conversation.recorder import (
            ConversationRecorder,
            recorder_for_session,
        )

        session = Session("p", "a")
        recorder = recorder_for_session(session)
        recorder.ensure_thread(session.id)
        intent = {
            "version": 1,
            "kind": kind,
            "queue_id": "q",
            "natural_key": "user:run:text:0",
            "run_id": "run",
        }

        def fail(*args, **kwargs):
            raise OSError("interrupted")

        with monkeypatch.context() as patcher:
            if boundary == "before_domain":
                patcher.setattr(ConversationRecorder, "_append_item", fail)
            else:
                patcher.setattr(projection_db, "acknowledge_projection", fail)
            session.append_message(
                {"role": "user", "content": "accepted"}, projection=intent
            )
        assert (
            len(
                projection_db.pending_projections(
                    session.id, project_slug="p", agent_slug="a"
                )
            )
            == 1
        )
        recorder.record_compaction_checkpoint(
            session.id, run_id="run", summary="later", kind="context_summary", version=1
        )
        recorder.recover_pending_messages(session.id)
        items = recorder.store.list_items(session.id, limit=10)
        assert [item.kind.value for item in items] == ["user_message", "compaction"]
        assert items[0].content["text"] == "accepted"
        assert items[0].turn_id == (None if kind == "queued_message" else "run")
        assert (
            projection_db.pending_projections(
                session.id, project_slug="p", agent_slug="a"
            )
            == []
        )

    def test_response_is_not_accepted_without_a_recovery_owner(
        self, projection_db, monkeypatch
    ):
        from omniagents.core.session import manager

        session = manager.Session("p", "a")
        monkeypatch.setattr(
            manager, "_materialize_conversation_thread", lambda session: None
        )
        with pytest.raises(RuntimeError, match="canonical thread unavailable"):
            session.append_message(
                {
                    "role": "assistant",
                    "type": "message",
                    "status": "completed",
                    "id": "msg_no_owner",
                    "content": "answer",
                }
            )
        assert session.history == []
        assert (
            projection_db.load_history(session.id, project_slug="p", agent_slug="a")
            == []
        )

    @pytest.mark.asyncio
    async def test_full_fork_inherits_context_but_historical_branch_does_not(
        self, projection_db
    ):
        from omniagents.core.session.manager import SessionManager, Session

        manager = SessionManager("p", "a")
        parent = manager.get_or_create()
        first = {"role": "user", "content": "first"}
        await parent.add_items([first, {"role": "user", "content": "later"}])
        compacted = [{"role": "user", "content": "latest compacted context"}]
        await parent.replace_sdk_items(compacted)
        child = manager.fork_session(parent)
        assert await Session("p", "a", child.id).get_items() == compacted
        branch = manager.fork_session(parent, history=[first])
        assert await Session("p", "a", branch.id).get_items() == [first]

    @pytest.mark.asyncio
    async def test_sdk_clear_pop_and_append_survive_reload_without_erasing_transcript(
        self, projection_db
    ):
        from omniagents.core.session.manager import Session

        session = Session("p", "a")
        first = {"role": "user", "content": "first"}
        second = {"role": "user", "content": "second"}
        await session.add_items([first, second])
        assert await session.get_items(limit=0) == []
        assert await session.pop_item() == second
        reloaded = Session("p", "a", session.id)
        assert await reloaded.get_items() == [first]
        assert reloaded.history == [first, second]
        await reloaded.clear_session()
        reloaded = Session("p", "a", session.id)
        assert await reloaded.get_items() == []
        await reloaded.add_items([{"role": "user", "content": "new"}])
        again = Session("p", "a", session.id)
        assert await again.get_items() == [{"role": "user", "content": "new"}]
        assert len(again.history) == 3

    @pytest.mark.asyncio
    async def test_sdk_compaction_replacement_is_atomic_and_not_transcript_replay(
        self, projection_db, monkeypatch
    ):
        from agents.memory import OpenAIResponsesCompactionSession
        from omniagents.core.session.manager import Session
        from omniagents.core.session.compaction import _atomic_compaction_session_cls
        import omniagents.core.session.manager as manager

        session = Session("p", "a")
        original = [{"role": "user", "content": "old context"}]
        await session.add_items(original)
        compacted = [{"role": "user", "content": "compact context"}]
        wrapped = _atomic_compaction_session_cls(OpenAIResponsesCompactionSession)(
            session.id, session
        )

        def fail(*args, **kwargs):
            raise OSError("context write failed")

        with monkeypatch.context() as patcher:
            patcher.setattr(manager, "replace_sdk_context", fail)
            with pytest.raises(OSError):
                await wrapped._replace_underlying_session_items(
                    output_items=compacted, previous_items=original
                )
        assert await session.get_items() == original
        assert await Session("p", "a", session.id).get_items() == original
        await wrapped._replace_underlying_session_items(
            output_items=compacted, previous_items=original
        )
        assert await Session("p", "a", session.id).get_items() == compacted
        assert session.history == original
        assert (
            projection_db.load_history(session.id, project_slug="p", agent_slug="a")
            == original
        )

    def test_intent_failure_rolls_back_history_too(self, projection_db):
        original_context = [{"role": "user", "content": "keep"}]
        projection_db.replace_sdk_context(
            "rollback", original_context, project_slug="p", agent_slug="a"
        )
        with pytest.raises(TypeError):
            projection_db.append_message(
                "rollback",
                {"role": "assistant", "content": "not committed"},
                project_slug="p",
                agent_slug="a",
                projection={"unserializable": {1}},
            )
        assert (
            projection_db.load_history("rollback", project_slug="p", agent_slug="a")
            == []
        )
        assert (
            projection_db.pending_projections(
                "rollback", project_slug="p", agent_slug="a"
            )
            == []
        )
        assert (
            projection_db.load_sdk_context("rollback", project_slug="p", agent_slug="a")
            == original_context
        )

    def test_acknowledgement_cannot_cross_sessions_or_shards(self, projection_db):
        projection_db.append_message(
            "owner",
            {"role": "assistant", "content": "durable"},
            project_slug="p",
            agent_slug="a",
            projection={"version": 1},
        )
        pending = projection_db.pending_projections(
            "owner", project_slug="p", agent_slug="a"
        )
        assert len(pending) == 1
        projection_db.acknowledge_projection(
            "other", pending[0]["row_id"], project_slug="p", agent_slug="a"
        )
        assert (
            projection_db.pending_projections("owner", project_slug="p", agent_slug="a")
            == pending
        )
        projection_db.acknowledge_projection(
            "owner", pending[0]["row_id"], project_slug="p", agent_slug="a"
        )
        assert (
            projection_db.pending_projections("owner", project_slug="p", agent_slug="a")
            == []
        )

    @pytest.mark.parametrize("boundary", ["before_domain", "before_ack"])
    def test_read_recovers_an_interrupted_write_once(
        self, projection_db, monkeypatch, boundary
    ):
        from omniagents.core.session.manager import Session
        from omniagents.core.conversation.recorder import (
            ConversationRecorder,
            recorder_for_session,
        )
        from omniagents.core.conversation.service import ConversationService

        session = Session("p", "a")
        session.active_run_id = "run"
        recorder = recorder_for_session(session)
        recorder.ensure_thread(session.id)
        recorder.open_turn(session.id, "run", prompt="question", prompt_role="user")

        def fail(*args, **kwargs):
            raise OSError("injected interruption")

        with monkeypatch.context() as patcher:
            if boundary == "before_domain":
                patcher.setattr(ConversationRecorder, "record_persisted_message", fail)
            else:
                patcher.setattr(projection_db, "acknowledge_projection", fail)
            session.append_message(
                {
                    "role": "assistant",
                    "type": "message",
                    "status": "completed",
                    "id": "msg_saved",
                    "content": "answer",
                }
            )
        assert (
            len(
                projection_db.pending_projections(
                    session.id, project_slug="p", agent_slug="a"
                )
            )
            == 1
        )
        service = ConversationService(project_slug="p", agent_slug="a")
        service.ensure_thread(session.id)
        service.ensure_thread(session.id)
        assert (
            projection_db.pending_projections(
                session.id, project_slug="p", agent_slug="a"
            )
            == []
        )
        assert recorder.store.count_items(session.id) == 2
        assert recorder.store.get_thread(session.id).item_count == 2
        assert recorder.store.get_turn(session.id, "run").item_count == 2
