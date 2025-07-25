import asyncio
import importlib
from fastapi.testclient import TestClient
from omniagents.server import app as server_app
from omniagents.core.specs import AgentSpec
from agents import Agent
from omniagents.core import runner_bridge


async def dummy_build_agent(settings, mcp, app):
    return Agent(name='a', tools=[], instructions='', model='gpt')


async def fake_stream_agent(agent, prompt, sess, channel, max_turns=20):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message
    append_message(sess.id, user_msg)
    await channel.async_call("run_end", {"run_id": asyncio.current_task().get_name()})


def test_rpc_methods(monkeypatch):
    spec = AgentSpec(
        name='A',
        header_title='A',
        page_title='A',
        welcome_text='hi',
        client_storage_prefix='a.',
        get_agent_instructions=lambda: 'instr',
        build_agent=dummy_build_agent,
    )

    monkeypatch.setattr(runner_bridge, 'stream_agent', fake_stream_agent)
    app = server_app.build_app(spec=spec)
    client = TestClient(app)

    with client.websocket_connect('/ws') as ws:
        ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'start_run', 'params': {'prompt': 'hello'}})
        resp = ws.receive_json()
        session_id = resp['result']['session_id']
        ws.receive_json()  # run_end

        ws.send_json({'jsonrpc': '2.0', 'id': 2, 'method': 'get_session_history', 'params': {'session_id': session_id}})
        hist_resp = ws.receive_json()
        hist = hist_resp['result']
        assert hist[0]['content'] == 'hello'

        ws.send_json({'jsonrpc': '2.0', 'id': 3, 'method': 'list_sessions', 'params': {}})
        list_resp = ws.receive_json()
        sess_meta = list_resp['result'][0]
        assert sess_meta['id'] == session_id
        assert sess_meta['archived'] is False

        ws.send_json({'jsonrpc': '2.0', 'id': 4, 'method': 'archive_session', 'params': {'session_id': session_id}})
        arch_resp = ws.receive_json()
        assert arch_resp['result'] is True

        ws.send_json({'jsonrpc': '2.0', 'id': 5, 'method': 'list_sessions', 'params': {}})
        list_resp2 = ws.receive_json()
        sess_meta2 = list_resp2['result'][0]
        assert sess_meta2['archived'] is True

    # Reload modules to ensure archive state persists
    import omniagents.core.session.manager as sessions_mod
    importlib.reload(sessions_mod)
    importlib.reload(server_app)
    app2 = server_app.build_app(spec=spec)
    client2 = TestClient(app2)
    with client2.websocket_connect('/ws') as ws2:
        ws2.send_json({'jsonrpc': '2.0', 'id': 6, 'method': 'list_sessions', 'params': {}})
        list_resp3 = ws2.receive_json()
        sess_meta3 = list_resp3['result'][0]
        assert sess_meta3['archived'] is True

