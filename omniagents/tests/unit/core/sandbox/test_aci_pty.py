"""Unit tests for the ACI PTY batching/exit semantics (no Azure needed).

A fake exec channel feeds frames (and optionally hangs to simulate a
still-running process). Validates that ``_collect_pty_output`` drains to the
deadline and ``_finalize_pty`` retires exited processes (exit_code set,
process_id cleared) while keeping running ones live.
"""

from __future__ import annotations

import pytest

pytest.importorskip("azure.identity")
pytest.importorskip("azure.mgmt.containerinstance")

import asyncio  # noqa: E402

from agents.sandbox.manifest import Manifest  # noqa: E402
from agents.sandbox.snapshot import resolve_snapshot  # noqa: E402

from omniagents.core.sandbox.aci import (  # noqa: E402
    AciSandboxSession,
    AciSandboxSessionState,
    _AciPtyEntry,
)
from omniagents.core.sandbox.pty_stream import coerce_pty_streaming  # noqa: E402


class FakePtyChan:
    def __init__(self, frames, *, hang: bool = False) -> None:
        self._frames = list(frames)
        self._hang = hang
        self.written: list[str] = []
        self.closed = False

    async def iter_output(self):
        for f in self._frames:
            yield f if isinstance(f, bytes) else f.encode()
            await asyncio.sleep(0)
        if self._hang:
            await asyncio.Event().wait()  # never completes → process "still running"

    async def write(self, data: str) -> None:
        self.written.append(data)

    async def close(self) -> None:
        self.closed = True


def _session() -> AciSandboxSession:
    state = AciSandboxSessionState(
        type="aci",
        snapshot=resolve_snapshot(None, "x"),
        manifest=Manifest(),
        container_name="c",
    )
    return AciSandboxSession(manager=None, name="c", state=state)  # type: ignore[arg-type]


def test_collect_then_finalize_retires_exited_process():
    async def go():
        sess = _session()
        entry = _AciPtyEntry(FakePtyChan([b"hello\n", b"world\n"]))
        sess._pty_processes[1234] = entry
        sess._reserved_pty_process_ids.add(1234)
        out, _ = await sess._collect_pty_output(entry, 1000, None)
        upd = await sess._finalize_pty(1234, entry, out, None)
        return out, upd

    out, upd = asyncio.run(go())
    assert out == b"hello\nworld\n"
    assert upd.exit_code == 0  # channel closed => command ended
    assert upd.process_id is None  # exited => retired from the registry


def test_running_process_keeps_session_live():
    async def go():
        sess = _session()
        entry = _AciPtyEntry(FakePtyChan([b"$ "], hang=True))
        sess._pty_processes[42] = entry
        sess._reserved_pty_process_ids.add(42)
        out, _ = await sess._collect_pty_output(entry, 150, None)  # short window
        upd = await sess._finalize_pty(42, entry, out, None)
        await entry.terminate()  # cleanup the hung reader
        return out, upd

    out, upd = asyncio.run(go())
    assert out == b"$ "
    assert upd.exit_code is None  # still running
    assert upd.process_id == 42  # kept live


def test_token_truncation_passthrough():
    async def go():
        sess = _session()
        entry = _AciPtyEntry(FakePtyChan([b"abcdef" * 100]))
        out, tokens = await sess._collect_pty_output(entry, 500, 5)  # cap to 5 tokens
        return out, tokens

    out, tokens = asyncio.run(go())
    assert isinstance(out, bytes)
    assert tokens is not None and tokens > 0  # original token count reported


def test_aci_session_exposes_streaming_protocol():
    sess = _session()
    assert coerce_pty_streaming(sess) is sess


def test_stream_output_drains_existing_and_live_chunks():
    async def go():
        sess = _session()
        entry = _AciPtyEntry(FakePtyChan([b"first\n", b"second\n"]))
        sess._pty_processes[99] = entry
        sess._reserved_pty_process_ids.add(99)
        chunks: list[bytes] = []
        async for chunk in sess.pty_stream_output(99):
            chunks.append(chunk)
        return b"".join(chunks)

    assert asyncio.run(go()) == b"first\nsecond\n"


def test_pty_resize_is_supported_noop():
    async def go():
        sess = _session()
        await sess.pty_resize(123, cols=132, rows=43)

    asyncio.run(go())
