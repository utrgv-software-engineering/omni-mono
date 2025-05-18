from types import SimpleNamespace
import pytest
import asyncio

from omniagents import remote_proxy
from omniagents.remote_proxy import RemoteAgentProxy

class DummyClient:
    def __init__(self, url, methods):
        self.other = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def list_sessions(self):
        return SimpleNamespace(result=[{"id": "s1", "archived": False}, {"id": "s2", "archived": True}])

    async def archive_session(self, session_id: str):
        self.archived = session_id
        return SimpleNamespace(result=True)

def test_list_sessions(monkeypatch):
    monkeypatch.setattr(remote_proxy, "JsonRpcClient", DummyClient)
    proxy = RemoteAgentProxy("ws://dummy")
    sessions = asyncio.run(proxy.list_sessions())
    assert sessions == [{"id": "s1", "archived": False}, {"id": "s2", "archived": True}]

    res = asyncio.run(proxy.archive_session("s1"))
    assert res is True
    assert proxy._client.archived == "s1"
