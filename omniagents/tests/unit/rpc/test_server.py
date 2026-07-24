import asyncio

import pytest
from unittest.mock import AsyncMock

from omniagents.rpc.base import RpcMethodsBase
from omniagents.rpc.server import JsonRpcChannel, JsonRpcEndpoint
from omniagents.rpc.transport import TransportError, TransportDecodeError


class DummyMethods(RpcMethodsBase):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def echo(self, value):
        self.calls.append(value)
        return value

    async def notify(self, value):
        self.calls.append(("notify", value))

    async def needs_argument(self, value):
        return value

    async def boom(self):
        raise RuntimeError("boom")


class TransportStub:
    def __init__(self):
        self.send_json = AsyncMock()


@pytest.mark.asyncio
async def test_channel_send_result_propagates_transport_error():
    transport = TransportStub()
    transport.send_json.side_effect = TransportError("fail")
    channel = JsonRpcChannel(transport)
    with pytest.raises(TransportError):
        await channel.send_result(1, {"value": 1})


@pytest.mark.asyncio
async def test_channel_send_error_payload():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    await channel.send_error("abc", -32601, "Method not found", {"method": "missing"})
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "abc"
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "Method not found"
    assert payload["error"]["data"] == {"method": "missing"}


@pytest.mark.asyncio
async def test_channel_async_call_payload():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    await channel.async_call("event", {"foo": "bar"})
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload == {"jsonrpc": "2.0", "method": "event", "params": {"foo": "bar"}}


@pytest.mark.asyncio
async def test_endpoint_handles_parse_error():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = JsonRpcEndpoint(DummyMethods())
    exc = TransportDecodeError("{bad", ValueError("bad"))
    await endpoint._handle_parse_error(channel, exc)
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32700
    assert payload["id"] is None


@pytest.mark.asyncio
async def test_endpoint_rejects_missing_jsonrpc():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = JsonRpcEndpoint(DummyMethods())
    await endpoint._process_message(
        channel, {"id": 1, "method": "echo", "params": {"value": 1}}
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32600
    assert payload["id"] == 1


@pytest.mark.asyncio
async def test_endpoint_notification_does_not_respond():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    methods = DummyMethods()
    endpoint = JsonRpcEndpoint(methods)
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "method": "notify", "params": {"value": 7}},
    )
    transport.send_json.assert_not_awaited()
    assert methods.calls == [("notify", 7)]


@pytest.mark.asyncio
async def test_endpoint_method_not_found():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = JsonRpcEndpoint(DummyMethods())
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 2, "method": "missing", "params": {}},
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32601
    assert payload["id"] == 2


@pytest.mark.asyncio
async def test_endpoint_invalid_params():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = JsonRpcEndpoint(DummyMethods())
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 3, "method": "needs_argument", "params": {}},
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32602
    assert payload["id"] == 3


@pytest.mark.asyncio
async def test_endpoint_internal_error_contains_method():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = JsonRpcEndpoint(DummyMethods())
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 4, "method": "boom", "params": {}},
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32603
    assert payload["error"]["data"]["method"] == "boom"


@pytest.mark.asyncio
async def test_endpoint_accepts_args_field():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    methods = DummyMethods()
    endpoint = JsonRpcEndpoint(methods)
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 5, "method": "echo", "args": {"value": "ok"}},
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == "ok"
    assert methods.calls == ["ok"]


class ChannelContextMethods(RpcMethodsBase):
    def __init__(
        self, ready_a: asyncio.Event, ready_b: asyncio.Event, go: asyncio.Event
    ):
        super().__init__()
        self._ready_a = ready_a
        self._ready_b = ready_b
        self._go = go

    async def check_channel(self, label: str):
        first = self.channel
        if label == "a":
            self._ready_a.set()
        else:
            self._ready_b.set()
        await self._go.wait()
        second = self.channel
        return first is second


@pytest.mark.asyncio
async def test_endpoint_channel_is_task_local():
    ready_a = asyncio.Event()
    ready_b = asyncio.Event()
    go = asyncio.Event()
    methods = ChannelContextMethods(ready_a, ready_b, go)
    endpoint = JsonRpcEndpoint(methods)

    transport_a = TransportStub()
    transport_b = TransportStub()
    channel_a = JsonRpcChannel(transport_a)
    channel_b = JsonRpcChannel(transport_b)

    task_a = asyncio.create_task(
        endpoint._process_message(
            channel_a,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "check_channel",
                "params": {"label": "a"},
            },
        )
    )
    task_b = asyncio.create_task(
        endpoint._process_message(
            channel_b,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "check_channel",
                "params": {"label": "b"},
            },
        )
    )

    await ready_a.wait()
    await ready_b.wait()
    go.set()
    await asyncio.gather(task_a, task_b)

    payload_a = transport_a.send_json.await_args.args[0]
    payload_b = transport_b.send_json.await_args.args[0]
    assert payload_a["result"] is True
    assert payload_b["result"] is True
