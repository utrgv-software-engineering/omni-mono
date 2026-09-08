import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.rpc import JsonRpcClient, RpcError
from omniagents.rpc.generated.gui_v2 import GUI_PROTOCOL_VERSION
from omniagents.rpc.transport.inmemory import InMemoryTransport
from omniagents.rpc.version import supports_gui_version
from scripts.protocol.check_gui_protocol_compatibility import (
    _read_protocol,
    compare_protocols,
)
from scripts.protocol.generate_gui_protocol import ROOT, render


@pytest.mark.parametrize(
    "server",
    [
        None,
        2,
        "",
        "1.0.0",
        "3.0.0",
        "2.0.0-dev",
        "02.0.0",
        "2.0.0\n",
        "2.0.9007199254740992",
    ],
)
def test_rejects_incompatible_or_malformed_server_versions(server):
    assert not supports_gui_version(server, GUI_PROTOCOL_VERSION)


@pytest.mark.parametrize("server", ["2.0.0", "2.0.1", "2.1.0"])
def test_accepts_compatible_server_version(server):
    assert supports_gui_version(server, GUI_PROTOCOL_VERSION)
    assert not supports_gui_version("2.0.0", "2.0.1")


def test_oversized_version_is_a_mismatch_not_an_integer_conversion_error():
    assert not supports_gui_version("2.0." + "9" * 5000, GUI_PROTOCOL_VERSION)


def test_v2_generation_is_separate_and_checked_in():
    outputs = render(major=2)
    for path, content in outputs.items():
        assert "baseline" not in path.parts
        assert "gui-v1" not in str(path) and "gui_v1" not in str(path)
        assert path.read_bytes() == content, str(path)
    document, schema, manifest, _ = _read_protocol(ROOT / "protocol/openrpc/v2")
    assert manifest["protocol_version"] == GUI_PROTOCOL_VERSION
    assert document["info"]["version"] == GUI_PROTOCOL_VERSION
    assert "/v2/" in schema["$id"]
    assert "environment_selection" in schema["$defs"]["start_run"]["required"]


def test_generated_typescript_supports_strict_indexed_access():
    ui = ROOT / "omniagents/backends/web/ui"
    compiler = ui / "node_modules/.bin/tsc"
    if not compiler.exists():
        pytest.skip("Install web UI dependencies to verify generated TypeScript")
    result = subprocess.run(
        [
            str(compiler),
            "--noEmit",
            "--strict",
            "--noUncheckedIndexedAccess",
            "--skipLibCheck",
            "--target",
            "esnext",
            "--moduleResolution",
            "bundler",
            "--module",
            "esnext",
            "src/protocol/generated/gui-v2.ts",
        ],
        cwd=ui,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_v1_candidate_generation_remains_unchanged():
    # Historical candidate evidence is preserved, not certified release-safe.
    # Released v1 is independently pinned by its immutable baseline digest.
    for path, content in render(major=1).items():
        assert path.read_bytes() == content, str(path)


def test_v2_is_an_explicit_break_from_released_v1_not_a_repaired_baseline():
    old, old_schema, _, _ = _read_protocol(ROOT / "protocol/openrpc/baseline/1.0.0")
    new, new_schema, _, _ = _read_protocol(ROOT / "protocol/openrpc/v2")
    report = compare_protocols(old, old_schema, new, new_schema)
    assert report.baseline_version == "1.0.0"
    assert report.candidate_version == "2.0.0"
    assert report.classification == "breaking"
    assert (
        "start_run.params.environment_selection added as required" in report.violations
    )
    assert (
        "start_run.params.content added oneOf constraint to unconstrained input"
        in report.violations
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,code",
    [
        (None, -32012),
        ({}, -32012),
        ({"protocol_version": "1.0.0"}, -32012),
        ({"protocol_version": "3.0.0"}, -32012),
        ({"protocol_version": "2.0.0"}, -32602),
    ],
)
async def test_python_client_rejects_false_success_without_initialized(result, code):
    client_transport, server_transport = InMemoryTransport.create_pair()
    await server_transport.connect()
    client_transport.send_json = AsyncMock(wraps=client_transport.send_json)

    async def reply():
        request = await server_transport.receive_json()
        assert request["params"]["protocol_version"] == "2.0.0"
        await server_transport.send_json(
            {"jsonrpc": "2.0", "method": "message_output", "params": {}}
        )
        await server_transport.send_json(
            {"jsonrpc": "2.0", "id": request["id"], "result": result}
        )

    responder = asyncio.create_task(reply())
    events = SimpleNamespace(message_output=AsyncMock())
    client = JsonRpcClient(client_transport, methods=events)
    try:
        with pytest.raises(RpcError) as error:
            await client.__aenter__()
        assert error.value.code == code
        assert client.initialize_result is None
        assert client._recv_task is None
        events.message_output.assert_not_awaited()
        assert [
            call.args[0]["method"]
            for call in client_transport.send_json.await_args_list
        ] == ["initialize"]
        await responder
    finally:
        responder.cancel()
        await server_transport.disconnect()


@pytest.mark.asyncio
async def test_python_client_accepts_newer_minor_and_additive_response_fields():
    client_transport, server_transport = InMemoryTransport.create_pair()
    await server_transport.connect()
    result = {
        "protocol_version": "2.1.0",
        "identity": {"name": "server", "version": "1", "future": True},
        "platform": {"os": "linux", "arch": "x86_64"},
        "agent_host": {"agent_host_id": "host"},
        "capabilities": {
            **{
                key: False
                for key in (
                    "realtime",
                    "mcp_apps",
                    "client_functions",
                    "approvals",
                    "artifacts",
                    "replay",
                    "terminal",
                )
            },
            "experimental_operations": [],
            "disabled_notifications": [],
            "future": True,
        },
        "future": True,
    }

    async def reply():
        request = await server_transport.receive_json()
        await server_transport.send_json(
            {"jsonrpc": "2.0", "id": request["id"], "result": result}
        )
        assert (await server_transport.receive_json())["method"] == "initialized"

    responder = asyncio.create_task(reply())
    try:
        async with JsonRpcClient(client_transport) as client:
            assert client.initialize_result.protocol_version == "2.1.0"
            await responder
    finally:
        responder.cancel()
        await server_transport.disconnect()
