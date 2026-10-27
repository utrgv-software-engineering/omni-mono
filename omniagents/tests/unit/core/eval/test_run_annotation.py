import types

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.service import ConversationService
from omniagents.core.eval import targets


def test_a_project_run_records_its_metadata_on_its_conversation():
    conversations = ConversationService(project_slug="proj", agent_slug="agent")
    # The run recorded its conversation before the evaluation annotates it.
    ConversationRecorder(conversations.store).ensure_thread(
        "sess-1", project_legacy_history=False
    )
    conversations.update_thread(
        "sess-1", can_access=lambda _owner: True, metadata={"kept": True}
    )
    spec = types.SimpleNamespace(
        available_tools=[],
        description=None,
        name="agent",
        project_slug="proj",
        agent_slug="agent",
    )
    run = targets._ProjectAgentRun(types.SimpleNamespace(_session_id="sess-1"), spec)

    run.annotate({"omniagents": {"run_type": "evaluation"}})

    metadata = conversations.get_thread("sess-1")["metadata"]
    # Thread metadata merges: what was there stays.
    assert metadata == {"kept": True, "omniagents": {"run_type": "evaluation"}}


def test_a_run_without_a_conversation_annotates_nothing():
    spec = types.SimpleNamespace(
        available_tools=[],
        description=None,
        name="agent",
        project_slug="proj",
        agent_slug="agent",
    )
    run = targets._ProjectAgentRun(types.SimpleNamespace(_session_id=None), spec)
    run.annotate({"omniagents": {}})
