import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agents.run_context import RunContextWrapper
from agents.tool_context import ToolContext

from omniagents.builtin.tools.teams_notifications import teams_notify


@pytest.mark.asyncio
async def test_notification_carries_registered_conversation(monkeypatch):
    requests = []
    registrations = []
    token = "a" * 64

    def register(agent, session, event):
        registrations.append((agent, session, event))
        return token

    service = SimpleNamespace(
        _power_automate=SimpleNamespace(
            source=SimpleNamespace(register_notification=register)
        ),
        _power_automate_agent="improver",
    )
    monkeypatch.setattr(
        "omniagents.core.agents.service.current_service", lambda: service
    )
    monkeypatch.setattr(
        "omniagents.core.session.helpers.current_session_id", lambda: "session-one"
    )
    monkeypatch.setenv(
        "FACTORY_TEAMS_NOTIFY_URL",
        "https://workplace.invalid/factory/notifications/teams",
    )
    monkeypatch.setenv("FACTORY_SETTINGS_TOKEN", "test-token")
    real = httpx.AsyncClient

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "accepted"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler)),
    )
    context = ToolContext.from_agent_context(
        RunContextWrapper(context=None),
        tool_call_id="test-call",
        tool_name="teams_notify",
        tool_arguments="{}",
    )
    result = await teams_notify.on_invoke_tool(
        context,
        json.dumps({"event_id": "review-42", "title": "Review", "text": "Question"}),
    )
    assert result["status"] == "accepted"
    assert registrations == [("improver", "session-one", "review-42")]
    assert requests == [
        {
            "event_id": "review-42",
            "title": "Review",
            "text": f"Question\n\n[omni-conversation:{token}]",
        }
    ]
