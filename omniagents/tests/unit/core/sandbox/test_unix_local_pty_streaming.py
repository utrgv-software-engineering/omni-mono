"""End-to-end test of pty_stream_output + pty_resize on unix_local.

Uses the real ExtendedUnixLocalSandboxSession against a temp workspace
— no docker required. Verifies:

* A long-running interactive shell delivers output via streaming.
* :meth:`pty_resize` propagates into the shell (``stty size`` reports
  the new kernel PTY winsize).
* Cancelling the iterator unregisters the consumer cleanly so further
  consumers still work.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from omniagents.core.sandbox.factory import build_sandbox_client
from omniagents.core.sandbox.pty_stream import coerce_pty_streaming

TIMEOUT = 10.0


async def _run_in_shell(inner, pid: int, cmd: str, *, settle_s: float = 0.4) -> str:
    chunks: list[bytes] = []

    async def collect():
        async for chunk in inner.pty_stream_output(pid):
            chunks.append(chunk)

    task = asyncio.create_task(collect())
    try:
        await asyncio.sleep(0.05)
        await inner.pty_write_stdin(session_id=pid, chars=cmd + "\n", yield_time_s=0.0)
        await asyncio.sleep(settle_s)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return b"".join(chunks).decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_stream_output_delivers_shell_output():
    async with asyncio.timeout(TIMEOUT):
        client, _ = build_sandbox_client({"client": {"type": "unix_local"}})
        sess = await client.create()
        try:
            await sess.start()
            inner = coerce_pty_streaming(sess)
            assert inner is not None

            update = await inner.pty_exec_start(
                "bash", "--norc", "-i", tty=True, shell=False, yield_time_s=0.0
            )
            pid = update.process_id
            assert pid is not None

            text = await _run_in_shell(inner, pid, "echo SENTINEL_$((1+2))")
            assert "SENTINEL_3" in text

            await inner.pty_terminate_all()
        finally:
            try:
                await sess.shutdown()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_pty_resize_propagates_to_shell():
    async with asyncio.timeout(TIMEOUT):
        client, _ = build_sandbox_client({"client": {"type": "unix_local"}})
        sess = await client.create()
        try:
            await sess.start()
            inner = coerce_pty_streaming(sess)
            assert inner is not None

            update = await inner.pty_exec_start(
                "bash", "--norc", "-i", tty=True, shell=False, yield_time_s=0.0
            )
            pid = update.process_id
            assert pid is not None

            await inner.pty_resize(pid, cols=132, rows=43)
            # `stty size` reads the kernel's PTY winsize directly via
            # ioctl — unlike `tput` it needs no $TERM/terminfo, so the
            # assert holds in TERM-less environments (CI) too. Output is
            # "<rows> <cols>".
            text = await _run_in_shell(inner, pid, "stty size")
            assert "132" in text
            assert "43" in text

            await inner.pty_terminate_all()
        finally:
            try:
                await sess.shutdown()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_stream_output_unregisters_on_cancel():
    """Cancelling one consumer must not break a subsequent consumer."""
    async with asyncio.timeout(TIMEOUT):
        client, _ = build_sandbox_client({"client": {"type": "unix_local"}})
        sess = await client.create()
        try:
            await sess.start()
            inner = coerce_pty_streaming(sess)
            assert inner is not None

            update = await inner.pty_exec_start(
                "bash", "--norc", "-i", tty=True, shell=False, yield_time_s=0.0
            )
            pid = update.process_id
            assert pid is not None

            # First consumer: cancel after a beat.
            first_chunks: list[bytes] = []

            async def collect_first():
                async for chunk in inner.pty_stream_output(pid):
                    first_chunks.append(chunk)

            task1 = asyncio.create_task(collect_first())
            await asyncio.sleep(0.1)
            task1.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass

            # Entry should have unregistered the queue. Check it explicitly.
            entry = inner._pty_processes.get(pid)
            assert entry is not None
            consumers = getattr(entry, "stream_consumers", [])
            assert consumers == []

            # Second consumer should work normally.
            text = await _run_in_shell(inner, pid, "echo SECOND_PASS")
            assert "SECOND_PASS" in text

            await inner.pty_terminate_all()
        finally:
            try:
                await sess.shutdown()
            except Exception:
                pass
