"""End-to-end test for the host_bridge sandbox provider.

Spins up the real host-side handler (``serve_host_connection`` → a real
``ExtendedUnixLocalSandboxSession``) on a loopback WebSocket and drives it with
the cloud-side ``HostBridgeSandboxClient``/session. Verifies that exec, file
IO, PTY, and exposed-port resolution all forward correctly and that the host
operates on a real local workspace directory.
"""

from __future__ import annotations

import asyncio
import io

import pytest
import websockets

from agents.sandbox import Manifest
from omniagents.core.sandbox.factory import build_sandbox_client
from omniagents.core.sandbox.host_bridge import (
    HostBridgeSandboxClient,
    HostBridgeSandboxClientOptions,
    serve_host_connection,
)
from omniagents.core.sandbox.pty_stream import coerce_pty_streaming


async def _start_host_server() -> tuple[object, int]:
    server = await websockets.serve(
        serve_host_connection, "127.0.0.1", 0, max_size=None
    )
    port = next(iter(server.sockets)).getsockname()[1]
    return server, port


def test_factory_resolves_host_bridge() -> None:
    profile = {
        "client": {"type": "host_bridge", "endpoint": "ws://example/x"},
        "options": {"exposed_ports": [8080]},
    }
    client, options = build_sandbox_client(profile)
    assert type(client).__name__ == "HostBridgeSandboxClient"
    assert options.exposed_ports == (8080,)


@pytest.mark.asyncio
async def test_host_bridge_exec_fileio_pty_ports(tmp_path) -> None:
    server, port = await _start_host_server()
    endpoint = f"ws://127.0.0.1:{port}"
    try:
        client = HostBridgeSandboxClient(endpoint=endpoint)
        options = HostBridgeSandboxClientOptions(
            endpoint=endpoint, exposed_ports=(8080,)
        )
        manifest = Manifest.model_validate({"root": str(tmp_path)})
        session = await client.create(manifest=manifest, options=options)
        await session.start()

        # exec: cloud preps `sh -lc`, host runs it once
        res = await session.exec("echo", "hello-from-host")
        assert res.exit_code == 0
        assert res.stdout.decode().strip() == "hello-from-host"

        # write + read roundtrip against the host's real workspace
        await session.write(str(tmp_path / "f.txt"), io.BytesIO(b"payload-123"))
        back = (await session.read(str(tmp_path / "f.txt"))).read()
        assert back == b"payload-123"

        # exec sees the file the write op created (same workspace on the host)
        cat = await session.exec("cat", "f.txt")
        assert cat.stdout.decode().strip() == "payload-123"

        # PTY
        update = await session.pty_exec_start("printf", "pty-out", yield_time_s=1.0)
        assert b"pty-out" in update.output

        streaming = coerce_pty_streaming(session)
        assert streaming is not None
        update = await streaming.pty_exec_start("cat", tty=True, yield_time_s=0.0)
        assert update.process_id is not None
        chunks: list[bytes] = []

        async def collect() -> None:
            async for chunk in streaming.pty_stream_output(update.process_id):
                chunks.append(chunk)
                if b"bridge-stream" in b"".join(chunks):
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)
        await streaming.pty_write_stdin(
            session_id=update.process_id,
            chars="bridge-stream\n",
            yield_time_s=0.0,
        )
        await asyncio.wait_for(task, timeout=3)
        assert b"bridge-stream" in b"".join(chunks)
        await streaming.pty_resize(update.process_id, cols=100, rows=30)
        await streaming.pty_terminate_all()

        # exposed port resolves (configured via options.exposed_ports)
        endpoint_info = await session.resolve_exposed_port(8080)
        assert endpoint_info.port == 8080

        await session.aclose()
    finally:
        server.close()
        await server.wait_closed()
