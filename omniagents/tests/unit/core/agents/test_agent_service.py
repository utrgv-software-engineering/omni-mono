import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


@pytest.mark.asyncio
async def test_archive_session_triggers_terminal_cleanup(temp_history_db):
    spec = AgentSpec(
        name="name",
        header_title="header",
        page_title="page",
        welcome_text="welcome",
        client_storage_prefix="client",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )

    service = AgentService(spec)
    session = service._session_manager.get_or_create()
    calls = []

    class DummyTerminalManager:
        async def cleanup_session(self, session_id):
            calls.append(session_id)

    service.terminal_manager = DummyTerminalManager()

    result = await service.archive_session(session.id)

    assert result is True
    assert calls == [session.id]
