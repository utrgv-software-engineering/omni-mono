import pytest
from starlette.datastructures import QueryParams

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from agents import Agent


async def stub_build_agent(settings, mcp, app):
    return Agent(
        name="unit_agent",
        tools=[],
        instructions="Unit",
        model="gpt-3.5-turbo",
    )


def test_build_app_requires_spec():
    with pytest.raises(ValueError):
        server_app.build_app()


def test_operator_upstream_query_params_override_client_values():
    class Socket:
        query_params = QueryParams(
            "model=client&client_hint=enabled&token=do-not-forward"
        )

    url = server_app._compose_remote_ws_url(
        "wss://upstream.example/ws?model=operator&api-version=2026-08-01&ticket=x",
        suffix="/realtime",
        websocket=Socket(),
    )

    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    assert parsed.path == "/ws/realtime"
    assert parse_qs(parsed.query) == {
        "model": ["operator"],
        "api-version": ["2026-08-01"],
        "client_hint": ["enabled"],
    }


def test_register_server_functions_failure(capsys):
    def failing_registrar(service):
        raise RuntimeError("boom")

    spec = AgentSpec(
        name="UnitAgent",
        welcome_text="Unit",
        get_agent_instructions=lambda: "Unit",
        build_agent=stub_build_agent,
        register_server_functions=failing_registrar,
    )

    app = server_app.build_app(spec=spec)
    captured = capsys.readouterr()
    assert "Warning: register_server_functions failed" in captured.out
    websocket_routes = [
        route for route in app.router.routes if getattr(route, "path", None) == "/ws"
    ]
    assert websocket_routes
