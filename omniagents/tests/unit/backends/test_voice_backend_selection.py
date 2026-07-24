import pytest

from agents import Agent

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.voice_pipeline_service import VoicePipelineService


async def stub_build_agent(settings, mcp, app):
    return Agent(
        name="unit_agent",
        tools=[],
        instructions="Unit",
        model="gpt-3.5-turbo",
    )


def test_build_app_registers_pipeline_voice_backend():
    spec = AgentSpec(
        name="UnitAgent",
        welcome_text="Unit",
        get_agent_instructions=lambda: "Unit",
        build_agent=stub_build_agent,
        voice_backend="pipeline",
    )

    app = server_app.build_app(spec=spec)
    assert isinstance(app.state.realtime_service, VoicePipelineService)

    websocket_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/ws/realtime"
    ]
    assert websocket_routes
