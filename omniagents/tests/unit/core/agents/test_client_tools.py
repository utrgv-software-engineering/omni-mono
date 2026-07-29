"""Tests for the client tools architecture in AgentService.

1. _sf_session_ensure extracts client_tools from variables
2. _build_client_tools creates FunctionTool proxies with correct schemas
3. _build_client_tools invoker sends client_request and returns response
4. _build_agent_for_session injects client tools into agent
"""

import asyncio
import json

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


def make_spec(**overrides):
    defaults = dict(
        name="test-agent",
        welcome_text="hi",
        get_agent_instructions=lambda: "be helpful",
        build_agent=None,
    )
    defaults.update(overrides)
    return AgentSpec(**defaults)


CLIENT_TOOLS_DEFS = [
    {
        "name": "get_ticket",
        "description": "Get the current ticket state.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "move_ticket",
        "description": "Move ticket to column.",
        "parameters": {
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Target column"},
            },
            "required": ["column"],
        },
    },
    {
        "name": "escalate",
        "description": "Escalate to human.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What you need"},
            },
            "required": ["message"],
        },
    },
]


# ---------------------------------------------------------------------------
# 1. _sf_session_ensure extracts client_tools from variables
# ---------------------------------------------------------------------------


class TestSessionEnsureClientTools:
    def test_extracts_client_tools_from_variables(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()

        variables = {
            "additional_instructions": "do stuff",
            "client_tools": CLIENT_TOOLS_DEFS,
        }

        result = service._sf_session_ensure(session, {"variables": variables})

        # client_tools should be popped from variables and stored on session
        assert hasattr(session, "_client_tools")
        assert session._client_tools == CLIENT_TOOLS_DEFS
        # Should be removed from the variables dict
        assert "client_tools" not in session.variables

    def test_preserves_other_variables(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()

        variables = {
            "additional_instructions": "do stuff",
            "client_tools": CLIENT_TOOLS_DEFS,
            "workspace_root": "/tmp/test",
        }

        service._sf_session_ensure(session, {"variables": variables})

        assert session.variables.get("additional_instructions") == "do stuff"

    def test_no_client_tools_is_fine(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()

        service._sf_session_ensure(session, {"variables": {"foo": "bar"}})

        assert not hasattr(session, "_client_tools") or not session._client_tools

    def test_non_list_client_tools_ignored(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()

        service._sf_session_ensure(
            session, {"variables": {"client_tools": "not a list"}}
        )

        assert not hasattr(session, "_client_tools") or not session._client_tools


# ---------------------------------------------------------------------------
# 2. _build_client_tools creates FunctionTool proxies
# ---------------------------------------------------------------------------


class TestBuildClientTools:
    def test_returns_empty_list_when_no_defs(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        # No _client_tools attribute
        assert service._build_client_tools(session) == []

    def test_returns_empty_list_for_empty_defs(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = []
        assert service._build_client_tools(session) == []

    def test_creates_function_tools_with_correct_metadata(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = CLIENT_TOOLS_DEFS

        tools = service._build_client_tools(session)

        assert len(tools) == 3
        names = [t.name for t in tools]
        assert names == ["get_ticket", "move_ticket", "escalate"]

        # Check descriptions
        assert tools[0].description == "Get the current ticket state."
        assert tools[1].description == "Move ticket to column."
        assert tools[2].description == "Escalate to human."

    def test_creates_function_tools_with_correct_schemas(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = CLIENT_TOOLS_DEFS

        tools = service._build_client_tools(session)

        # get_ticket: no params
        assert tools[0].params_json_schema == {"type": "object", "properties": {}}

        # move_ticket: column param required
        schema = tools[1].params_json_schema
        assert "column" in schema["properties"]
        assert schema["required"] == ["column"]

        # escalate: message param required
        schema = tools[2].params_json_schema
        assert "message" in schema["properties"]
        assert schema["required"] == ["message"]

    def test_skips_defs_without_name(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [
            {"description": "no name"},
            {
                "name": "valid",
                "description": "has name",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

        tools = service._build_client_tools(session)
        assert len(tools) == 1
        assert tools[0].name == "valid"

    def test_tools_have_strict_json_schema_false(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = CLIENT_TOOLS_DEFS

        tools = service._build_client_tools(session)
        for t in tools:
            assert t.strict_json_schema is False

    @pytest.mark.asyncio
    async def test_safe_client_tool_declares_no_approval_predicate(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [
            {
                "name": "speak",
                "description": "Speak aloud.",
                "parameters": {"type": "object", "properties": {}},
                "safe": True,
            }
        ]

        [tool] = service._build_client_tools(session)

        assert callable(tool.needs_approval)
        assert await tool.needs_approval(None, {}, "call_1") is False


# ---------------------------------------------------------------------------
# 3. Invoker sends client_request and returns response
# ---------------------------------------------------------------------------


class TestClientToolInvoker:
    @pytest.mark.asyncio
    async def test_invoker_calls_request_client_function(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[0]]  # get_ticket only

        # Stub channel so request_client_function can deliver
        class StubChannel:
            def __init__(self):
                self.calls = []

            async def async_call(self, method, args):
                self.calls.append((method, args))
                # Simulate async response from launcher
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        service.client_response(
                            request_id=(
                                args["args"]["request_id"]
                                if "request_id" in args.get("args", {})
                                else args["request_id"]
                            ),
                            ok=True,
                            result={"id": "ticket-1", "title": "Test Ticket"},
                            error=None,
                        )
                    )
                )
                return True

        channel = StubChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)
        assert len(tools) == 1

        # Invoke the tool
        result_json = await tools[0].on_invoke_tool(None, "{}")

        result = json.loads(result_json)
        assert result["id"] == "ticket-1"
        assert result["title"] == "Test Ticket"

    @pytest.mark.asyncio
    async def test_invoker_passes_arguments_correctly(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[1]]  # move_ticket

        captured_payloads = []

        class StubChannel:
            async def async_call(self, method, args):
                captured_payloads.append(args)
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        service.client_response(
                            request_id=args["request_id"],
                            ok=True,
                            result={"ok": True, "column": "Done"},
                            error=None,
                        )
                    )
                )
                return True

        channel = StubChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)
        result_json = await tools[0].on_invoke_tool(None, '{"column": "Done"}')

        result = json.loads(result_json)
        assert result["ok"] is True
        assert result["column"] == "Done"

        # Verify the payload sent to the channel (the resolution also
        # broadcasts a client_request_resolved dismissal; filter it out).
        requests = [
            payload
            for payload in captured_payloads
            if payload.get("function") == "tool.call"
        ]
        assert len(requests) == 1
        payload = requests[0]
        assert payload["function"] == "tool.call"
        assert payload["args"]["tool"] == "move_ticket"
        assert payload["args"]["arguments"] == {"column": "Done"}

    @pytest.mark.asyncio
    async def test_invoker_returns_error_on_timeout(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[0]]

        # Channel that never responds
        class SilentChannel:
            async def async_call(self, method, args):
                return True

        channel = SilentChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)

        # Monkey-patch the timeout to be very short for testing
        original_build = service._build_client_tools

        # Instead, just call with a short timeout by patching request_client_function
        original_rcf = service.request_client_function

        async def short_timeout_rcf(function, args, **kwargs):
            kwargs["timeout_ms"] = 10  # 10ms timeout
            return await original_rcf(function, args, **kwargs)

        service.request_client_function = short_timeout_rcf

        # Rebuild tools so they use the patched method
        tools = service._build_client_tools(session)
        result_json = await tools[0].on_invoke_tool(None, "{}")

        result = json.loads(result_json)
        assert "error" in result
        assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invoker_returns_error_on_failed_response(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[2]]  # escalate

        class StubChannel:
            async def async_call(self, method, args):
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        service.client_response(
                            request_id=args["request_id"],
                            ok=False,
                            result=None,
                            error={"message": "Ticket not found"},
                        )
                    )
                )
                return True

        channel = StubChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)
        result_json = await tools[0].on_invoke_tool(None, '{"message": "help"}')

        result = json.loads(result_json)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invoker_handles_invalid_json_params(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[0]]

        class StubChannel:
            async def async_call(self, method, args):
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        service.client_response(
                            request_id=args["request_id"],
                            ok=True,
                            result={"id": "ticket-1"},
                            error=None,
                        )
                    )
                )
                return True

        channel = StubChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)
        # Pass invalid JSON — should not crash, args should be {}
        result_json = await tools[0].on_invoke_tool(None, "not valid json")

        result = json.loads(result_json)
        assert result["id"] == "ticket-1"

    @pytest.mark.asyncio
    async def test_invoker_handles_empty_params(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[0]]

        class StubChannel:
            async def async_call(self, method, args):
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        service.client_response(
                            request_id=args["request_id"],
                            ok=True,
                            result={"id": "ticket-1"},
                            error=None,
                        )
                    )
                )
                return True

        channel = StubChannel()
        service.channel = channel
        session.channels = [channel]

        tools = service._build_client_tools(session)
        # Pass empty string — should not crash
        result_json = await tools[0].on_invoke_tool(None, "")

        result = json.loads(result_json)
        assert result["id"] == "ticket-1"


# ---------------------------------------------------------------------------
# 4. _build_agent_for_session injects client tools
# ---------------------------------------------------------------------------


class TestBuildAgentInjectsClientTools:
    @pytest.mark.asyncio
    async def test_client_tools_appended_to_agent_tools(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        session._client_tools = CLIENT_TOOLS_DEFS

        agent = await service._build_agent_for_session(session, None)

        tool_names = [t.name for t in agent.tools]
        assert "get_ticket" in tool_names
        assert "move_ticket" in tool_names
        assert "escalate" in tool_names

    @pytest.mark.asyncio
    async def test_no_client_tools_leaves_agent_unchanged(self):
        service = AgentService(make_spec())
        session = service._session_manager.get_or_create()
        # No _client_tools set

        agent = await service._build_agent_for_session(session, None)

        # Should still have any default tools but not our client tools
        tool_names = [t.name for t in agent.tools]
        assert "get_ticket" not in tool_names
        assert "move_ticket" not in tool_names
        assert "escalate" not in tool_names

    @pytest.mark.asyncio
    async def test_client_tools_dont_replace_existing_tools(self):
        from agents.tool import FunctionTool

        existing_tool = FunctionTool(
            name="existing_tool",
            description="already here",
            params_json_schema={"type": "object", "properties": {}},
            on_invoke_tool=lambda ctx, params: "{}",
        )

        async def custom_build(settings, primary_server, app):
            from agents import Agent

            return Agent(name="custom", tools=[existing_tool])

        service = AgentService(make_spec(build_agent=custom_build))
        session = service._session_manager.get_or_create()
        session._client_tools = [CLIENT_TOOLS_DEFS[0]]  # just get_ticket

        agent = await service._build_agent_for_session(session, None)

        tool_names = [t.name for t in agent.tools]
        assert "existing_tool" in tool_names
        assert "get_ticket" in tool_names
        assert len(agent.tools) == 2
