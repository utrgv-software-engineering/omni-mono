import inspect
from omniagents.server import app as server_app


def test_list_sessions_return_annotation():
    # Build a minimal AgentSpec just to create the app
    from omniagents.core.specs import AgentSpec
    from agents import Agent

    async def dummy_build_agent(settings, mcp, app):
        return Agent(name='a', tools=[], instructions='', model='gpt')

    spec = AgentSpec(
        name='A',
        header_title='A',
        page_title='A',
        welcome_text='hi',
        client_storage_prefix='a.',
        get_agent_instructions=lambda: 'instr',
        build_agent=dummy_build_agent,
    )

    app = server_app.build_app(spec=spec)

    # Find the websocket route and extract the AgentService instance from closure
    handler = next(r.endpoint for r in app.router.routes if r.path == '/ws')
    endpoint = handler.__closure__[0].cell_contents
    service = endpoint.methods
    sig = inspect.signature(service.list_sessions)
    assert sig.return_annotation != inspect.Signature.empty
    assert sig.return_annotation in (list[dict], list, 'list[dict]')
