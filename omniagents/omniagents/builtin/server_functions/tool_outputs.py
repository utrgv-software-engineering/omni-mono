"""Authorized thread-scoped access to retained tool text, independent of sandboxes."""

import asyncio
from omniagents import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.core.tools.output_budget import read_output


@server_function(
    name_override="tool_outputs.read",
    description="Read or search retained tool text",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
    params_schema={
        "type": "object",
        "properties": {
            "reference": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
            "query": {"type": ["string", "null"]},
        },
        "required": ["reference"],
        "additionalProperties": False,
    },
)
async def read_tool_output(
    reference: str, offset: int = 0, limit: int = 8000, query: str | None = None
) -> dict:
    raise RuntimeError("Thread context is required")


async def _invoke(service, session, args=None):
    if session is None:
        raise ValueError("Thread context is required")
    manager = service._session_manager
    scope = (manager._project_slug, manager._agent_slug)
    return await asyncio.to_thread(read_output, scope, session.id, **(args or {}))


read_tool_output._server_function_on_invoke = _invoke
