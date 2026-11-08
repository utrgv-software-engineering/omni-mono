import json

import httpx
import pytest
from agents.tool_context import ToolContext

from omniagents.builtin.tools import teams_notifications as tools


@pytest.mark.asyncio
async def test_factory_notification_transport(monkeypatch):
    monkeypatch.setenv(
        "FACTORY_TEAMS_NOTIFY_URL", "http://workplace:7300/factory/notifications/teams"
    )
    monkeypatch.setenv("FACTORY_SETTINGS_TOKEN", "factory-test-token")
    monkeypatch.setenv("HTTP_PROXY", "http://wrong-proxy:3128")
    requests = []
    options = {}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "accepted", "duplicate": False})

    real = httpx.AsyncClient

    def client(**kwargs):
        options.update(kwargs)
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    body = {
        "event_id": "pr:42",
        "title": "Ready",
        "text": "Review complete",
    }
    ctx = ToolContext(
        context={}, tool_name="teams_notify", tool_call_id="call-1", tool_arguments="{}"
    )
    result = await tools.teams_notify.on_invoke_tool(ctx, json.dumps(body))
    assert result["status"] == "accepted"
    assert json.loads(requests[0].content) == body
    assert requests[0].headers["authorization"] == "Bearer factory-test-token"
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    schema = tools.teams_notify.params_json_schema
    assert set(schema["properties"]) == set(body)
