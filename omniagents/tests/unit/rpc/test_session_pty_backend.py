"""End-to-end test of SessionPtyBackend against a real unix_local session.

Goes through the full TerminalManager → SessionPtyBackend → SDK chain
to confirm the layering works without mocks.
"""

from __future__ import annotations

import asyncio

import pytest

from omniagents.core.sandbox.factory import build_sandbox_client
from omniagents.core.sandbox.pty_stream import coerce_pty_streaming
from omniagents.rpc.terminal import TerminalManager
from omniagents.rpc.terminal_backends import SessionPtyBackend

TIMEOUT = 10.0


async def _read_until(
    manager, descriptor, needle: str, *, deadline_s: float = 3.0
) -> str:
    """Drain the descriptor's output_queue until *needle* appears or deadline expires."""
    chunks: list[bytes] = []
    deadline = asyncio.get_event_loop().time() + deadline_s
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(
                manager.read_output(descriptor), timeout=remaining
            )
        except asyncio.TimeoutError:
            break
        if chunk is None:
            break
        chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if needle in text:
            return text
    return b"".join(chunks).decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_session_pty_backend_round_trip():
    async with asyncio.timeout(TIMEOUT):
        client, _ = build_sandbox_client({"client": {"type": "unix_local"}})
        sess = await client.create()
        try:
            await sess.start()
            inner = coerce_pty_streaming(sess)
            assert inner is not None

            backend = SessionPtyBackend(inner)
            manager = TerminalManager(backend=backend, default_shell="bash --norc -i")

            record = await manager.create_terminal("session-1", cols=100, rows=30)
            descriptor = await manager.authorize(
                "session-1", record["terminal_id"], record["token"]
            )

            await manager.write_input(descriptor, b"echo SESSION_PASS\n")
            text = await _read_until(manager, descriptor, "SESSION_PASS")
            assert "SESSION_PASS" in text

            # Resize and verify
            await manager.resize(descriptor, 132, 43)
            await asyncio.sleep(0.05)  # coalesce window
            await manager.write_input(descriptor, b"tput cols\n")
            text = await _read_until(manager, descriptor, "132")
            assert "132" in text

            await manager.cleanup_session("session-1")
        finally:
            try:
                await sess.shutdown()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_session_pty_backend_cleanup_closes_streams():
    async with asyncio.timeout(TIMEOUT):
        client, _ = build_sandbox_client({"client": {"type": "unix_local"}})
        sess = await client.create()
        try:
            await sess.start()
            inner = coerce_pty_streaming(sess)
            assert inner is not None

            backend = SessionPtyBackend(inner)
            manager = TerminalManager(backend=backend, default_shell="bash --norc -i")

            record = await manager.create_terminal("session-2", cols=80, rows=24)
            descriptor = manager._terminals["session-2"][record["terminal_id"]]

            await manager.cleanup_session("session-2")
            # The output_queue eventually receives the None sentinel.
            sentinel = await asyncio.wait_for(
                descriptor.output_queue.get(), timeout=2.0
            )
            # The reader loop may have emitted real chunks before the sentinel
            # (e.g. shell prompt). Drain until we find None.
            tries = 50
            while sentinel is not None and tries > 0:
                tries -= 1
                try:
                    sentinel = await asyncio.wait_for(
                        descriptor.output_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    break
            assert sentinel is None
            assert descriptor.closed is True
        finally:
            try:
                await sess.shutdown()
            except Exception:
                pass
