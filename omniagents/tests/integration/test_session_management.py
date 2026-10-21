from types import SimpleNamespace
import pytest
import asyncio

from omniagents.rpc.agents import remote
from omniagents.rpc.agents.remote import RemoteAgent


class DummyClient:
    def __init__(self, transport, methods, **kwargs):
        self.other = self
        self._transport = transport
        self._methods = methods

    @classmethod
    def from_url(cls, url, methods, **kwargs):
        # Mimic the factory method (real signature also takes client_info,
        # capabilities, token, and policy keyword arguments).
        transport = SimpleNamespace(url=url)
        return cls(transport, methods)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def on_disconnect(self, callback):
        # RemoteAgent registers a disconnect hook right after connecting.
        self._disconnect_callback = callback

    async def list_sessions(self):
        return SimpleNamespace(
            result=[{"id": "s1", "archived": False}, {"id": "s2", "archived": True}]
        )

    async def archive_session(self, session_id: str):
        self.archived = session_id
        return SimpleNamespace(result=True)


def test_list_sessions(monkeypatch):
    monkeypatch.setattr(remote, "JsonRpcClient", DummyClient)
    proxy = RemoteAgent("ws://dummy")
    sessions = asyncio.run(proxy.list_sessions())
    assert sessions == [{"id": "s1", "archived": False}, {"id": "s2", "archived": True}]

    res = asyncio.run(proxy.archive_session("s1"))
    assert res is True
    assert proxy._client.archived == "s1"
