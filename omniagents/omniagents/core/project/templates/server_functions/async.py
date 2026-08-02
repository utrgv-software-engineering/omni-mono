from omniagents import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

@server_function(
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
)
async def {sanitized}(value: int = 1) -> dict:
    return {"ok": True, "value": value}
