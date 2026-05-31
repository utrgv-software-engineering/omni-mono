"""``omni sandbox-host`` — turn this machine into a remote sandbox backend.

This is the host (laptop) half of "computer-as-sandbox". The agent runs
elsewhere (the cloud ``omni serve``); this process exposes the sandbox surface
— ``exec`` / file IO / PTYs / exposed ports — over a WebSocket so a cloud
``HostBridgeSandboxSession`` can drive *this* machine's filesystem.

The wire protocol + per-connection session handling live in
``omniagents.core.sandbox.host_bridge.serve_host_connection`` (co-located with
the cloud client so the protocol is defined once). This module is just the CLI:
bind a loopback WebSocket, hand each connection to that handler, and print
``{"exec_port": N}`` on stdout so the launcher can register the port with its
cloud relay. One long-lived server serves many sessions (one per connection).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Optional

import websockets

from omniagents.core.sandbox.host_bridge import serve_host_connection


async def _run(host: str, port: int, workspace: Optional[str]) -> None:
    async def handler(ws: Any) -> None:
        await serve_host_connection(ws, workspace_root=workspace)

    async with websockets.serve(handler, host, port, max_size=None) as server:
        bound = next(iter(server.sockets)).getsockname()
        sys.stdout.write(json.dumps({"exec_port": bound[1], "host": host}) + "\n")
        sys.stdout.flush()
        await asyncio.Future()  # run forever


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="omni sandbox-host")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind (0 = auto; the chosen port is printed as JSON).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Local workspace dir to root every session at (overrides the "
        "cloud-sent manifest root, which reflects the cloud's paths).",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args.host, args.port, args.workspace))
    except KeyboardInterrupt:
        return 0
    return 0
