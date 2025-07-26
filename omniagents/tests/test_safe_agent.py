import asyncio

from omniagents.core.agents.safe import SafeAgent
from agents.tool import FunctionTool


async def dummy_tool(ctx, params_json: str):
    return 'ok'

def test_safe_agent_wrap(monkeypatch):
    calls = []

    async def approval_cb(name, args):
        calls.append((name, args))
        return True, False, None

    tool = FunctionTool('danger', description='', params_json_schema={}, on_invoke_tool=dummy_tool)
    sa = SafeAgent(
        name='a',
        tools=[tool],
        instructions='',
        model='gpt',
        approval_callback=approval_cb,
        skip_approvals=False,
    )
    wrapped = sa.wrap_tool_with_approval(tool)
    asyncio.run(wrapped.on_invoke_tool(None, '{}'))
    assert calls == [('danger', '')]


def test_safe_agent_safe_tool(monkeypatch):
    async def approval_cb(name, args):
        return False, False, None

    tool = FunctionTool('safe', description='', params_json_schema={}, on_invoke_tool=dummy_tool)
    sa = SafeAgent(
        name='a',
        tools=[tool],
        instructions='',
        model='gpt',
        approval_callback=approval_cb,
        safe_tool_names=['safe'],
        skip_approvals=False,
    )
    wrapped = sa.wrap_tool_with_approval(tool)
    assert wrapped is tool
