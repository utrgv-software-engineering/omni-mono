import asyncio
import json
import pytest
from agents.tool import ToolContext
from agents.usage import Usage


@pytest.fixture
def invoke_tool():
    def _invoke(tool, *, workspace_root=None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        context_data = {}
        if workspace_root is not None:
            context_data['workspace_root'] = workspace_root
        ctx = ToolContext(context=context_data, usage=Usage(), tool_name=tool.name, tool_call_id='test-call')
        return asyncio.run(tool.on_invoke_tool(ctx, json.dumps(payload)))

    return _invoke
