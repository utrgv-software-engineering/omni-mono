import asyncio
import json
from fastapi.testclient import TestClient
from omniagents.server import app as server_app
from omniagents.core.specs import AgentSpec
from agents import Agent
from omniagents.server import runner_bridge


async def dummy_build_agent(settings, mcp, app):
    return Agent(name='a', tools=[], instructions='', model='gpt')


async def fake_stream_agent(agent, prompt, sess, channel, max_turns=20):
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.server.history_db import append_message
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
        ws.send_json({'id': 1, 'method': 'start_run', 'args': {'prompt': 'hello'}})
        resp = ws.receive_json()
        session_id = resp['result']['session_id']
        ws.receive_json()  # run_end

        ws.send_json({'id': 2, 'method': 'get_session_history', 'args': {'session_id': session_id}})
        hist_resp = ws.receive_json()
        hist = json.loads(hist_resp['result'])
        assert hist[0]['content'] == 'hello'

        ws.send_json({'id': 3, 'method': 'list_sessions', 'args': {}})
        list_resp = ws.receive_json()
        assert session_id in list_resp['result']

