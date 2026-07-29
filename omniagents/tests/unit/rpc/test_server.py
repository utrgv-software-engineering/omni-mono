import asyncio

import pytest
from unittest.mock import AsyncMock

from omniagents.rpc.base import RpcMethodsBase
from omniagents.rpc.generated.gui_v1.models import (
    CLIENT_REQUEST_MODELS,
    SERVER_NOTIFICATION_MODELS,
)
from omniagents.rpc.server import GUI_RPC_METHODS, JsonRpcChannel, JsonRpcEndpoint
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

    async def no_params(self):
        return "ok"

    async def start_run(self, prompt):
        self.calls.append(("start_run", prompt))
        return {"run_id": "run-1", "session_id": "session-1"}

    async def fork_session(self, session_id, new_session_id=None):
        self.calls.append(("fork_session", session_id, new_session_id))
        return {"session_id": session_id, "new_session_id": new_session_id or "new"}

    async def _private(self):
        return "hidden"


DUMMY_RPC_METHODS = {
    "echo",
    "notify",
    "needs_argument",
    "boom",
    "no_params",
    "start_run",
    "fork_session",
}


def dummy_endpoint(methods=None):
    return JsonRpcEndpoint(methods or DummyMethods(), allowed_methods=DUMMY_RPC_METHODS)


class TransportStub:
    def __init__(self):
        self.send_json = AsyncMock()


def gui_endpoint(methods=None, **capabilities):
    return JsonRpcEndpoint(
        methods or DummyMethods(),
        allowed_methods=DUMMY_RPC_METHODS,
        gui_handshake=True,
        server_capabilities=capabilities,
    )


def gui_channel(transport=None):
    return JsonRpcChannel(transport or TransportStub(), enforce_initialization=True)


def initialize_message(call_id=1, **overrides):
    params = {
        "protocol_version": "1.0.0",
        "identity": {"name": "test-client", "version": "2.0.0"},
        "platform": {"os": "linux", "arch": "x86_64"},
        "capabilities": {
            "realtime": True,
            "mcp_apps": True,
            "client_functions": True,
            "approvals": True,
            "artifacts": True,
            "replay": True,
            "terminal": True,
            "experimental_operations": [],
            "disabled_notifications": [],
        },
    }
    params.update(overrides)
    return {"jsonrpc": "2.0", "id": call_id, "method": "initialize", "params": params}


def test_gui_rpc_registry_matches_generated_request_models():
    assert GUI_RPC_METHODS == frozenset(CLIENT_REQUEST_MODELS)


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
async def test_channel_validates_known_notification_payloads():
    transport = TransportStub()
    channel = JsonRpcChannel(transport, notification_models=SERVER_NOTIFICATION_MODELS)
    with pytest.raises(ValueError):
        await channel.async_call("run_started", {"run_id": 1})
    transport.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_preserves_unknown_extension_notifications():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    await channel.async_call("extension_event", {"value": 1})
    payload = transport.send_json.await_args.args[0]
    assert payload["method"] == "extension_event"


@pytest.mark.asyncio
async def test_endpoint_handles_parse_error():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = dummy_endpoint()
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
    endpoint = dummy_endpoint()
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
    endpoint = dummy_endpoint(methods)
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
    endpoint = dummy_endpoint()
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
    endpoint = dummy_endpoint()
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
    endpoint = dummy_endpoint()
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
    endpoint = dummy_endpoint(methods)
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 5, "method": "echo", "args": {"value": "ok"}},
    )
    transport.send_json.assert_awaited_once()
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == "ok"
    assert methods.calls == ["ok"]


@pytest.mark.asyncio
async def test_endpoint_prefers_params_over_args_alias():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    methods = DummyMethods()
    endpoint = dummy_endpoint(methods)
    await endpoint._process_message(
        channel,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "echo",
            "params": {"value": "params"},
            "args": {"value": "args"},
        },
    )
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == "params"
    assert methods.calls == ["params"]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [None, {}])
async def test_endpoint_accepts_omitted_or_null_params(params):
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = dummy_endpoint()
    message = {"jsonrpc": "2.0", "id": 7, "method": "no_params"}
    if params is None:
        message["params"] = None
    await endpoint._process_message(channel, message)
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == "ok"


@pytest.mark.asyncio
async def test_endpoint_rejects_unregistered_callable():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = dummy_endpoint()
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 8, "method": "_private", "params": {}},
    )
    payload = transport.send_json.await_args.args[0]
    assert payload["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": {"method": "_private"},
    }


@pytest.mark.asyncio
async def test_endpoint_ignores_unknown_notification_then_dispatches_request():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = dummy_endpoint()
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "method": "future_event", "params": {}},
    )
    transport.send_json.assert_not_awaited()
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 9, "method": "echo", "params": {"value": 9}},
    )
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == 9


@pytest.mark.asyncio
async def test_endpoint_rejects_invalid_generated_request_params():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    endpoint = dummy_endpoint()
    await endpoint._process_message(
        channel,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "start_run",
            "params": {"prompt": 1},
        },
    )
    payload = transport.send_json.await_args.args[0]
    assert payload["error"]["code"] == -32602
    assert payload["error"]["data"]["method"] == "start_run"


@pytest.mark.asyncio
async def test_endpoint_ignores_unknown_fields_for_stable_requests():
    transport = TransportStub()
    channel = JsonRpcChannel(transport)
    methods = DummyMethods()
    endpoint = dummy_endpoint(methods)
    await endpoint._process_message(
        channel,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "start_run",
            "params": {"prompt": "hello", "future_optional": True},
        },
    )
    payload = transport.send_json.await_args.args[0]
    assert payload["result"] == {"run_id": "run-1", "session_id": "session-1"}
    assert methods.calls == [("start_run", "hello")]


@pytest.mark.asyncio
async def test_gui_endpoint_requires_initialize_then_initialized():
    transport = TransportStub()
    channel = gui_channel(transport)
    endpoint = gui_endpoint()

    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"value": 1}},
    )
    assert transport.send_json.await_args.args[0]["error"] == {
        "code": -32010,
        "message": "Connection is not initialized",
        "data": {"kind": "initialization_required", "state": "new"},
    }

    transport.send_json.reset_mock()
    await endpoint._process_message(channel, initialize_message())
    assert channel.initialization_state == "initialize_accepted"
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"value": 2}},
    )
    assert transport.send_json.await_args.args[0]["error"]["code"] == -32010

    await endpoint._process_message(
        channel, {"jsonrpc": "2.0", "method": "initialized", "params": {}}
    )
    assert channel.initialization_state == "ready"
    await endpoint._process_message(
        channel,
        {"jsonrpc": "2.0", "id": 3, "method": "echo", "params": {"value": 3}},
    )
    assert transport.send_json.await_args.args[0]["result"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2.0.0", "1.0.1", "invalid"])
async def test_initialize_rejects_incompatible_protocol_versions(version):
    transport = TransportStub()
    endpoint = gui_endpoint()
    await endpoint._process_message(
        gui_channel(transport), initialize_message(protocol_version=version)
    )
    error = transport.send_json.await_args.args[0]["error"]
    assert error["code"] == -32012
    assert error["data"] == {
        "kind": "protocol_version_mismatch",
        "client_version": version,
        "server_version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_initialize_returns_identity_and_capability_intersection():
    transport = TransportStub()
    channel = gui_channel(transport)
    endpoint = gui_endpoint(realtime=True, replay=True, terminal=False)
    await endpoint._process_message(channel, initialize_message())
    result = transport.send_json.await_args.args[0]["result"]
    assert result["protocol_version"] == "1.0.0"
    assert result["identity"]["name"] == "omniagents"
    assert result["identity"]["version"]
    assert result["platform"]["os"]
    assert result["platform"]["arch"]
    assert result["capabilities"] == {
        "realtime": True,
        "mcp_apps": False,
        "client_functions": False,
        "approvals": False,
        "artifacts": False,
        "replay": True,
        "terminal": False,
        "experimental_operations": [],
        "disabled_notifications": [],
    }
    assert channel.client_info == {"name": "test-client", "version": "2.0.0"}


@pytest.mark.asyncio
async def test_initialize_rejects_missing_identity_and_mandatory_opt_out():
    endpoint = gui_endpoint()
    identity_transport = TransportStub()
    await endpoint._process_message(
        gui_channel(identity_transport), initialize_message(identity={})
    )
    assert identity_transport.send_json.await_args.args[0]["error"]["code"] == -32602

    opt_out_transport = TransportStub()
    capabilities = initialize_message()["params"]["capabilities"]
    capabilities["disabled_notifications"] = ["run_started"]
    await endpoint._process_message(
        gui_channel(opt_out_transport), initialize_message(capabilities=capabilities)
    )
    assert opt_out_transport.send_json.await_args.args[0]["error"]["code"] == -32013
    assert (
        opt_out_transport.send_json.await_args.args[0]["error"]["data"]["kind"]
        == "mandatory_capability_disabled"
    )


@pytest.mark.asyncio
async def test_experimental_requests_require_exact_grant():
    transport = TransportStub()
    channel = gui_channel(transport)
    endpoint = gui_endpoint()
    await endpoint._process_message(channel, initialize_message())
    await endpoint._process_message(
        channel, {"jsonrpc": "2.0", "method": "initialized", "params": {}}
    )
    await endpoint._process_message(
        channel,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "fork_session",
            "params": {"session_id": "old"},
        },
    )
    assert transport.send_json.await_args.args[0]["error"] == {
        "code": -32013,
        "message": "Experimental capability was not granted",
        "data": {"kind": "capability_not_negotiated", "capability": "fork_session"},
    }

    granted_transport = TransportStub()
    granted_channel = gui_channel(granted_transport)
    capabilities = initialize_message()["params"]["capabilities"]
    capabilities["experimental_operations"] = ["fork_session", "future_method"]
    await endpoint._process_message(
        granted_channel, initialize_message(capabilities=capabilities)
    )
    assert granted_transport.send_json.await_args.args[0]["error"] == {
        "code": -32013,
        "message": "Unsupported experimental capabilities",
        "data": {
            "kind": "capability_not_negotiated",
            "unsupported_capabilities": ["future_method"],
        },
    }

    granted_transport = TransportStub()
    granted_channel = gui_channel(granted_transport)
    capabilities["experimental_operations"] = ["fork_session"]
    await endpoint._process_message(
        granted_channel, initialize_message(capabilities=capabilities)
    )
    result = granted_transport.send_json.await_args.args[0]["result"]
    assert result["capabilities"]["experimental_operations"] == ["fork_session"]
    await endpoint._process_message(
        granted_channel,
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
    )
    await endpoint._process_message(
        granted_channel,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "fork_session",
            "params": {"session_id": "old"},
        },
    )
    assert (
        granted_transport.send_json.await_args.args[0]["result"]["session_id"] == "old"
    )


@pytest.mark.asyncio
async def test_outbound_notifications_honor_state_opt_outs_and_experimental_grants():
    transport = TransportStub()
    channel = gui_channel(transport)
    await channel.async_call("run_started", {"run_id": "run", "session_id": "session"})
    transport.send_json.assert_not_awaited()

    channel.initialization_state = "ready"
    channel.disabled_notifications = frozenset({"token"})
    await channel.async_call("token", {})
    await channel.async_call("session_forked", {})
    transport.send_json.assert_not_awaited()

    channel.experimental_grants = frozenset({"session_forked"})
    await channel.async_call(
        "session_forked", {"session_id": "old", "new_session_id": "new"}
    )
    transport.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_endpoint_skips_gui_handshake_and_models():
    class GenericMethods(RpcMethodsBase):
        async def list_sessions(self):
            return {"sessions": []}

    transport = TransportStub()
    endpoint = JsonRpcEndpoint(GenericMethods())
    await endpoint._process_message(
        JsonRpcChannel(transport),
        {"jsonrpc": "2.0", "id": 1, "method": "list_sessions", "params": {}},
    )
    assert transport.send_json.await_args.args[0]["result"] == {"sessions": []}

    transport.send_json.reset_mock()
    channel = JsonRpcChannel(transport)
    await channel.async_call("run_started", {"run_id": 1})
    transport.send_json.assert_awaited_once()


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
    endpoint = JsonRpcEndpoint(methods, allowed_methods={"check_channel"})

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
