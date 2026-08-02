from __future__ import annotations

from agents.tool import function_tool

from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@function_tool
def echo_text_tool(text: str) -> str:
    """Echo text for verification."""
    return text


@function_tool
def add_integers_tool(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def create_server_rpc_agent_spec() -> AgentSpec:
    holder: dict[str, AgentSpec] = {}

    async def build_agent(settings, mcp, _):
        spec = holder["spec"]
        return await _default_build_agent(settings, mcp, spec)

    spec = AgentSpec(
        name="Server RPC OpenAI",
        welcome_text="Server mode test",
        get_agent_instructions=lambda: (
            "You are a strict test agent. When asked to add two integers, you must call the "
            "add_integers_tool with JSON arguments containing keys 'a' and 'b'. After receiving the tool "
            "result, reply with the single word 'ack' followed by the numeric result."
        ),
        build_agent=build_agent,
        available_tools=[echo_text_tool, add_integers_tool],
        model_name="gpt-4o-mini",
        model_settings={"temperature": 0},
        max_turns=4,
    )

    def register(service):
        async def get_variables(service_instance, session, args):
            # The generated GUI protocol requires server_call results to be
            # dicts (ServerCallResult is RootModel[dict]).
            if session is None:
                return {}
            return getattr(session, "variables", None) or {}

        async def trigger_status(service_instance, session, args):
            if session is None:
                return {"ok": False}
            text = args.get("text", "") if isinstance(args, dict) else ""
            run_id = args.get("run_id") if isinstance(args, dict) else None
            show_spinner = (
                args.get("show_spinner", True) if isinstance(args, dict) else True
            )
            await service_instance.set_client_status(
                text,
                session=session,
                run_id=run_id,
                show_spinner=show_spinner,
            )
            return {"ok": True}

        service.register_server_function(
            "session.get_variables",
            get_variables,
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
        )
        service.register_server_function(
            "test.trigger_status",
            trigger_status,
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
        )

    spec.register_server_functions = register
    holder["spec"] = spec
    return spec
