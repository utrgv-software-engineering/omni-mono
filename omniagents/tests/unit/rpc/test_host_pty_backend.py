"""HostPtyBackend against real shells: closing a terminal ends its commands."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid

import pytest

from omniagents.rpc.terminal import TerminalManager
from omniagents.rpc.terminal_backends import HostPtyBackend

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="host PTYs are POSIX-only"
)

TIMEOUT = 10.0


def _running(marker: str) -> bool:
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, check=False)
    return found.returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", " &"], ids=["foreground", "background"])
async def test_closing_a_terminal_ends_its_commands(suffix: str):
    # Closing the terminal is a hangup, like closing a terminal window: an
    # interactive shell ignores SIGTERM, and a SIGKILLed one cannot pass the
    # hangup on to its jobs.
    marker = f"sleep 4242.{uuid.uuid4().int % 10_000:04d}"
    manager = TerminalManager(backend=HostPtyBackend(), default_shell="bash --norc -i")
    try:
        async with asyncio.timeout(TIMEOUT):
            record = await manager.create_terminal("session-1", cols=80, rows=24)
            descriptor = await manager.authorize(
                "session-1", record["terminal_id"], record["token"]
            )
            await manager.write_input(descriptor, f"{marker}{suffix}\n".encode())
            while not _running(marker):
                await asyncio.sleep(0.05)

            await manager.close_terminal("session-1", record["terminal_id"])
            while _running(marker):
                await asyncio.sleep(0.05)
    finally:
        subprocess.run(["pkill", "-f", marker], check=False)
