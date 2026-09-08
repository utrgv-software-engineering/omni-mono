"""Tests for the standardized client lifecycle policy.

Covers the core correctness deliverables of the lifecycle standardization:

- failure classification (JSON-RPC error codes, WebSocket close codes)
- deterministic backoff schedule + reconnect state machine
- pending-call rejection on permanent disconnect (no unresolved futures)
- per-call timeouts and asyncio cancellation cleanup
- structured error preservation
- RemoteAgent streaming runs failing fast on disconnect instead of hanging
- cross-language consistency of the TypeScript lifecycle twins
"""

import asyncio
import contextlib
import re
from pathlib import Path

import pytest

from omniagents.rpc import JsonRpcClient, RpcError, RpcMethodsBase
from omniagents.rpc.agents.remote import RemoteAgent, _is_permanent_connect_error
from omniagents.rpc.lifecycle import (
    DEFAULT_LIFECYCLE_POLICY,
    LifecyclePolicy,
    ReconnectController,
    RpcConnectionClosedError,
    RpcTimeoutError,
    classify_close_code,
    classify_rpc_error_code,
    reconnect_delay,
)
from omniagents.rpc.transport import TransportError
from omniagents.rpc.transport.inmemory import InMemoryTransport

ROOT = Path(__file__).resolve().parents[3]

INITIALIZE_RESULT = {
    "protocol_version": "2.0.0",
    "identity": {"name": "omniagents", "version": "0.0.0"},
    "platform": {"os": "linux", "arch": "x86_64"},
    "agent_host": {
        "agent_host_id": "test-lifecycle-host",
        "default_workspace_id": None,
        "default_environment_id": None,
    },
    "capabilities": {
        "realtime": False,
        "mcp_apps": False,
        "client_functions": True,
        "approvals": True,
        "artifacts": False,
        "replay": True,
        "terminal": False,
        "experimental_operations": [],
        "disabled_notifications": [],
    },
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassification:
    def test_permanent_rpc_error_codes(self):
        for code in (-32700, -32600, -32601, -32602, -32010, -32011, -32012, -32013):
            assert classify_rpc_error_code(code) == "permanent"

    def test_retryable_rpc_error_codes(self):
        assert classify_rpc_error_code(-32020) == "retryable"

    def test_indeterminate_rpc_error_codes(self):
        assert classify_rpc_error_code(-32603) == "indeterminate"
        assert classify_rpc_error_code(-32000) == "indeterminate"
        assert classify_rpc_error_code(1234) == "indeterminate"

    def test_auth_close_code_is_permanent(self):
        assert classify_close_code(4403) == "permanent"

    def test_feature_unavailable_close_code_is_permanent(self):
        assert classify_close_code(4404) == "permanent"

    def test_application_close_range_is_permanent(self):
        assert classify_close_code(4400) == "permanent"
        assert classify_close_code(4499) == "permanent"

    def test_transient_close_codes_are_retryable(self):
        for code in (None, 1000, 1001, 1006, 1011, 1012, 1013):
            assert classify_close_code(code) == "retryable"

    def test_protocol_violation_close_codes_are_permanent(self):
        for code in (1002, 1003, 1008):
            assert classify_close_code(code) == "permanent"


class TestConnectErrorClassification:
    def test_permanent_handshake_rpc_error(self):
        assert _is_permanent_connect_error(RpcError(-32012, "version mismatch"))

    def test_indeterminate_rpc_error_is_not_permanent(self):
        assert not _is_permanent_connect_error(RpcError(-32603, "internal"))

    def test_permanent_connection_close(self):
        assert _is_permanent_connect_error(
            RpcConnectionClosedError("auth", permanent=True)
        )
        assert not _is_permanent_connect_error(RpcConnectionClosedError("blip"))

    def test_http_auth_rejection_in_cause_chain(self):
        class _InvalidStatus(Exception):
            status_code = 403

        try:
            raise TransportError("Failed to connect") from _InvalidStatus()
        except TransportError as exc:
            assert _is_permanent_connect_error(exc)

    def test_plain_network_error_is_retryable(self):
        assert not _is_permanent_connect_error(TransportError("connection refused"))


# ---------------------------------------------------------------------------
# Backoff schedule / reconnect controller
# ---------------------------------------------------------------------------


class TestBackoff:
    def test_exponential_schedule_without_jitter(self):
        policy = LifecyclePolicy(reconnect_jitter=0.0)
        delays = [reconnect_delay(policy, n) for n in range(1, 9)]
        assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    def test_jitter_is_symmetric_and_bounded(self):
        policy = DEFAULT_LIFECYCLE_POLICY
        low = reconnect_delay(policy, 1, rng=lambda: 0.0)
        high = reconnect_delay(policy, 1, rng=lambda: 1.0)
        mid = reconnect_delay(policy, 1, rng=lambda: 0.5)
        assert low == pytest.approx(0.45)
        assert high == pytest.approx(0.55)
        assert mid == pytest.approx(0.5)

    def test_controller_retries_until_budget_exhausted(self):
        policy = LifecyclePolicy(reconnect_max_attempts=3, reconnect_jitter=0.0)
        ctrl = ReconnectController(policy)
        first = ctrl.record_failure()
        second = ctrl.record_failure()
        third = ctrl.record_failure()
        assert (first.retry, second.retry, third.retry) == (True, True, False)
        assert third.permanent
        assert first.delay == 0.5 and second.delay == 1.0

    def test_controller_stops_immediately_on_permanent(self):
        ctrl = ReconnectController(DEFAULT_LIFECYCLE_POLICY)
        decision = ctrl.record_failure(permanent=True)
        assert not decision.retry
        assert decision.permanent

    def test_controller_resets_on_success(self):
        policy = LifecyclePolicy(reconnect_max_attempts=2, reconnect_jitter=0.0)
        ctrl = ReconnectController(policy)
        assert ctrl.record_failure().retry
        ctrl.record_success()
        assert ctrl.attempts == 0
        assert ctrl.record_failure().retry


# ---------------------------------------------------------------------------
# JsonRpcClient lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_policy():
    return LifecyclePolicy(
        connect_timeout=1.0,
        rpc_timeout=0.2,
        reconnect_initial_delay=0.01,
        reconnect_max_delay=0.05,
        reconnect_jitter=0.0,
        reconnect_max_attempts=3,
    )


async def _connected_client(policy, methods=None):
    client_transport, server_transport = InMemoryTransport.create_pair()
    await server_transport.connect()
    client = JsonRpcClient(
        client_transport, methods, gui_handshake=False, policy=policy
    )
    await client.__aenter__()
    return client, server_transport


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_pending_calls_rejected_on_disconnect(self, fast_policy):
        """No pending future survives a permanent disconnect."""
        client, server = await _connected_client(fast_policy)

        task_a = asyncio.create_task(client.request("slow_a", timeout=None))
        task_b = asyncio.create_task(client.request("slow_b", timeout=None))
        # Wait until both requests are on the wire.
        await server.receive_json()
        await server.receive_json()

        await server.disconnect()

        with pytest.raises(RpcConnectionClosedError):
            await task_a
        with pytest.raises(RpcConnectionClosedError):
            await task_b
        assert client._pending == {}

    @pytest.mark.asyncio
    async def test_disconnect_callback_fires_once(self, fast_policy):
        client, server = await _connected_client(fast_policy)
        seen = []
        client.on_disconnect(seen.append)

        await server.disconnect()
        await asyncio.sleep(0.05)

        assert len(seen) == 1
        assert isinstance(seen[0], RpcConnectionClosedError)

    @pytest.mark.asyncio
    async def test_call_after_close_raises_connection_closed(self, fast_policy):
        client, server = await _connected_client(fast_policy)
        await server.disconnect()
        await asyncio.sleep(0.05)

        with pytest.raises(RpcConnectionClosedError):
            await client.call("anything")

    @pytest.mark.asyncio
    async def test_rpc_timeout(self, fast_policy):
        """A call with no response times out and leaves no pending entry."""
        client, server = await _connected_client(fast_policy)

        with pytest.raises(RpcTimeoutError) as excinfo:
            await client.call("never_answered")

        assert excinfo.value.method == "never_answered"
        assert excinfo.value.timeout == fast_policy.rpc_timeout
        assert client._pending == {}
        await client.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_explicit_timeout_overrides_policy(self, fast_policy):
        client, server = await _connected_client(fast_policy)

        start = asyncio.get_event_loop().time()
        with pytest.raises(RpcTimeoutError):
            await client.request("never_answered", timeout=0.05)
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < fast_policy.rpc_timeout
        await client.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_cancellation_cleans_pending_entry(self, fast_policy):
        """Aborting a long RPC via task cancellation leaves no orphan."""
        client, server = await _connected_client(fast_policy)

        task = asyncio.create_task(client.request("long_call", timeout=None))
        await server.receive_json()  # request hit the wire
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert client._pending == {}
        await client.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_structured_error_preserved(self, fast_policy):
        client, server = await _connected_client(fast_policy)

        task = asyncio.create_task(client.call("boom"))
        request = await server.receive_json()
        await server.send_json(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32013,
                    "message": "Capability mismatch",
                    "data": {"kind": "capability_not_negotiated", "capability": "x"},
                },
            }
        )

        with pytest.raises(RpcError) as excinfo:
            await task
        assert excinfo.value.code == -32013
        assert excinfo.value.data == {
            "kind": "capability_not_negotiated",
            "capability": "x",
        }
        await client.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_aexit_rejects_outstanding_calls(self, fast_policy):
        """Deliberate close also settles every outstanding call."""
        client, server = await _connected_client(fast_policy)

        task = asyncio.create_task(client.request("in_flight", timeout=None))
        await server.receive_json()
        await client.__aexit__(None, None, None)

        with pytest.raises(RpcConnectionClosedError) as excinfo:
            await task
        assert excinfo.value.permanent

    @pytest.mark.asyncio
    async def test_server_initiated_request_settles_on_disconnect(self, fast_policy):
        """A response call issued for a server-initiated request is
        rejected (not left hanging) when the connection drops before the
        server acknowledges it."""
        response_results = []

        class _Methods(RpcMethodsBase):
            def __init__(self, get_client):
                super().__init__()
                self._get_client = get_client

            def client_request(self, **params):
                async def _respond():
                    try:
                        await self._get_client().call(
                            "client_response",
                            request_id=params["request_id"],
                            ok=True,
                        )
                        response_results.append("ok")
                    except RpcConnectionClosedError:
                        response_results.append("closed")

                asyncio.get_event_loop().create_task(_respond())

        holder = {}
        methods = _Methods(lambda: holder["client"])
        client, server = await _connected_client(fast_policy, methods)
        holder["client"] = client

        await server.send_json(
            {
                "jsonrpc": "2.0",
                "method": "client_request",
                "params": {"request_id": "req-1", "function": "ui.echo", "args": {}},
            }
        )
        # The client's response call reaches the wire, then the link dies.
        await server.receive_json()
        await server.disconnect()
        await asyncio.sleep(0.05)

        assert response_results == ["closed"]
        assert client._pending == {}


# ---------------------------------------------------------------------------
# RemoteAgent — active runs
# ---------------------------------------------------------------------------


async def _serve_handshake(server):
    """Answer the GUI initialize handshake on the paired transport."""
    request = await server.receive_json()
    assert request["method"] == "initialize"
    await server.send_json(
        {"jsonrpc": "2.0", "id": request["id"], "result": INITIALIZE_RESULT}
    )
    initialized = await server.receive_json()
    assert initialized["method"] == "initialized"


class TestRemoteAgentLifecycle:
    @pytest.mark.asyncio
    async def test_active_run_fails_fast_on_disconnect(self, fast_policy):
        """A streaming run raises instead of hanging when the link dies."""
        client_transport, server = InMemoryTransport.create_pair()
        await server.connect()
        agent = RemoteAgent(transport=client_transport, policy=fast_policy)

        async def _server_side():
            await _serve_handshake(server)
            request = await server.receive_json()
            assert request["method"] == "start_run"
            await server.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"run_id": "run-1", "session_id": "sess-1"},
                }
            )
            # Stream one token, then drop the connection mid-run.
            await server.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "token",
                    "params": {"delta": "hel", "run_id": "run-1"},
                }
            )
            await server.disconnect()

        server_task = asyncio.create_task(_server_side())
        result = agent.run_streamed([{"role": "user", "content": "hi"}])

        events = []
        with pytest.raises(RpcConnectionClosedError):
            async for event in result.stream_events():
                events.append(event)
        await server_task
        assert agent._current_run_id is None

    @pytest.mark.asyncio
    async def test_start_run_timeout_during_active_session(self, fast_policy):
        """An unanswered start_run hits the RPC deadline instead of hanging."""
        client_transport, server = InMemoryTransport.create_pair()
        await server.connect()
        agent = RemoteAgent(transport=client_transport, policy=fast_policy)

        async def _server_side():
            await _serve_handshake(server)
            await server.receive_json()  # start_run — never answered

        server_task = asyncio.create_task(_server_side())
        result = agent.run_streamed([{"role": "user", "content": "hi"}])

        with pytest.raises(RpcTimeoutError):
            async for _ in result.stream_events():
                pass
        await server_task

    @pytest.mark.asyncio
    async def test_permanent_connect_error_not_retried(self, fast_policy):
        """A permanent handshake rejection surfaces immediately (attempt 1)."""
        client_transport, server = InMemoryTransport.create_pair()
        await server.connect()
        agent = RemoteAgent(transport=client_transport, policy=fast_policy)

        async def _server_side():
            request = await server.receive_json()
            await server.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {
                        "code": -32012,
                        "message": "Protocol version mismatch",
                        "data": {
                            "kind": "protocol_version_mismatch",
                            "client_version": "1.0.0",
                            "server_version": "2.0.0",
                        },
                    },
                }
            )

        server_task = asyncio.create_task(_server_side())
        with pytest.raises(RpcError) as excinfo:
            await agent._ensure_client()
        assert excinfo.value.code == -32012
        await server_task
        server_task = None
        # In-memory pair cannot reconnect, so a retryable path would raise
        # a different error after exhausting the budget; the single-shot
        # RpcError proves classification short-circuited the retries.

    @pytest.mark.asyncio
    async def test_stale_disconnect_sentinel_is_ignored(self, fast_policy):
        """Sentinels from a previous connection generation don't kill runs."""
        client_transport, server = InMemoryTransport.create_pair()
        await server.connect()
        agent = RemoteAgent(transport=client_transport, policy=fast_policy)
        agent._client_generation = 2  # a newer connection is active
        agent._event_queue.put_nowait(
            {
                "method": "__connection_closed__",
                "params": {"generation": 1, "message": "old", "permanent": False},
            }
        )
        agent._event_queue.put_nowait({"method": "run_end", "params": {"run_id": None}})

        async def _server_side():
            await _serve_handshake(server)
            request = await server.receive_json()
            await server.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"run_id": None, "session_id": "sess-1"},
                }
            )

        server_task = asyncio.create_task(_server_side())
        result = agent.run_streamed([{"role": "user", "content": "hi"}])
        events = [event async for event in result.stream_events()]
        assert events == []  # stale sentinel skipped, run_end consumed
        await server_task


# ---------------------------------------------------------------------------
# Cross-language consistency
# ---------------------------------------------------------------------------

WEB_LIFECYCLE = ROOT / "omniagents/backends/web/ui/src/rpc/lifecycle.ts"
INK_LIFECYCLE = ROOT / "omniagents/backends/ink/tui/src/rpc/lifecycle.ts"


class TestTypescriptTwins:
    def test_web_and_ink_lifecycle_modules_are_identical(self):
        assert WEB_LIFECYCLE.read_bytes() == INK_LIFECYCLE.read_bytes()

    def test_typescript_defaults_match_python_policy(self):
        source = WEB_LIFECYCLE.read_text()
        match = re.search(
            r"DEFAULT_LIFECYCLE_POLICY: LifecyclePolicy = \{(.*?)\}",
            source,
            re.DOTALL,
        )
        assert match, "DEFAULT_LIFECYCLE_POLICY literal not found"
        ts = {
            key: float(value)
            for key, value in re.findall(r"(\w+): ([\d.]+)", match.group(1))
        }
        policy = DEFAULT_LIFECYCLE_POLICY
        assert ts["connectTimeoutMs"] == policy.connect_timeout * 1000
        assert ts["rpcTimeoutMs"] == policy.rpc_timeout * 1000
        assert ts["pingIntervalMs"] == policy.ping_interval * 1000
        assert ts["pingTimeoutMs"] == policy.ping_timeout * 1000
        assert ts["reconnectInitialDelayMs"] == policy.reconnect_initial_delay * 1000
        assert ts["reconnectMultiplier"] == policy.reconnect_multiplier
        assert ts["reconnectMaxDelayMs"] == policy.reconnect_max_delay * 1000
        assert ts["reconnectJitter"] == policy.reconnect_jitter
        assert ts["reconnectMaxAttempts"] == policy.reconnect_max_attempts

    def test_typescript_error_code_sets_match_python(self):
        from omniagents.rpc.lifecycle import (
            PERMANENT_CLOSE_CODES,
            PERMANENT_RPC_ERROR_CODES,
            RETRYABLE_RPC_ERROR_CODES,
        )

        source = WEB_LIFECYCLE.read_text()

        def ts_set(name: str) -> frozenset[int]:
            match = re.search(
                rf"{name}: ReadonlySet<number> = new Set\(\[(.*?)\]\)",
                source,
                re.DOTALL,
            )
            assert match, f"{name} literal not found"
            return frozenset(
                int(code) for code in re.findall(r"(-?\d+),", match.group(1))
            )

        assert ts_set("PERMANENT_RPC_ERROR_CODES") == PERMANENT_RPC_ERROR_CODES
        assert ts_set("RETRYABLE_RPC_ERROR_CODES") == RETRYABLE_RPC_ERROR_CODES
        assert ts_set("PERMANENT_CLOSE_CODES") == PERMANENT_CLOSE_CODES
