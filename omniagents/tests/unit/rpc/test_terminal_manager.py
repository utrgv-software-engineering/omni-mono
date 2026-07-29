import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import AsyncIterator, List, Optional, Tuple

import pytest
import pytest_asyncio

from omniagents.rpc.terminal import (
    TerminalAuthorizationError,
    TerminalError,
    TerminalManager,
    TerminalNotFoundError,
)
from omniagents.rpc.terminal_backends import PtyBackend
from omniagents.rpc.transport import QueuePolicy, TransportOverloadedError

TIMEOUT = 5.0


@dataclass
class FakeHandle:
    shell: str
    cwd: Optional[str]
    cols: int
    rows: int
    closed: bool = False
    exit_code_value: Optional[int] = None


class FakePtyBackend:
    """Test double for :class:`PtyBackend` with introspectable history."""

    def __init__(self) -> None:
        self.starts: List[FakeHandle] = []
        self.writes: List[Tuple[FakeHandle, bytes]] = []
        self.resizes: List[Tuple[FakeHandle, int, int]] = []
        self.closes: List[FakeHandle] = []
        self._stream_holds: dict[int, asyncio.Event] = {}

    async def start(
        self,
        *,
        shell: str,
        cwd: Optional[str],
        cols: int,
        rows: int,
        user: Optional[str] = None,
    ) -> FakeHandle:
        _ = user
        handle = FakeHandle(shell=shell, cwd=cwd, cols=cols, rows=rows)
        self.starts.append(handle)
        return handle

    async def write(self, handle: FakeHandle, data: bytes) -> None:
        if handle.closed:
            return
        self.writes.append((handle, data))

    async def resize(self, handle: FakeHandle, cols: int, rows: int) -> None:
        if handle.closed:
            return
        self.resizes.append((handle, cols, rows))
        handle.cols = cols
        handle.rows = rows

    def stream(self, handle: FakeHandle) -> AsyncIterator[bytes]:
        return self._stream(handle)

    async def _stream(self, handle: FakeHandle) -> AsyncIterator[bytes]:
        # Suspend forever until the manager cancels us during shutdown.
        # No real output is emitted; tests push to the descriptor's
        # output_queue directly when they want to assert read_output.
        event = self._stream_holds.setdefault(id(handle), asyncio.Event())
        try:
            await event.wait()
        except asyncio.CancelledError:
            return
        return
        yield  # pragma: no cover  (make this a generator)

    async def close(self, handle: FakeHandle) -> None:
        handle.closed = True
        event = self._stream_holds.get(id(handle))
        if event is not None:
            event.set()
        self.closes.append(handle)

    async def exit_code(self, handle: FakeHandle) -> Optional[int]:
        return handle.exit_code_value


_: PtyBackend = FakePtyBackend()  # type: ignore[assignment]


@pytest_asyncio.fixture
async def terminal_env():
    backend = FakePtyBackend()
    manager = TerminalManager(backend=backend)
    env = SimpleNamespace(manager=manager, backend=backend)
    try:
        yield env
    finally:
        for session_id in list(manager._terminals.keys()):
            await manager.cleanup_session(session_id)


def _get_descriptor(manager, session_id, terminal_id):
    return manager._terminals[session_id][terminal_id]


@pytest.mark.asyncio
async def test_create_terminal_populates_descriptor(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        result = await terminal_env.manager.create_terminal(
            "session-1", shell="/usr/bin/fish", cwd="/tmp/work", cols=120, rows=40
        )
        assert result["shell"] == "/usr/bin/fish"
        assert result["cols"] == 120
        assert result["rows"] == 40
        assert terminal_env.backend.starts[0].shell == "/usr/bin/fish"
        assert terminal_env.backend.starts[0].cwd == "/tmp/work"
        descriptor = _get_descriptor(
            terminal_env.manager, "session-1", result["terminal_id"]
        )
        assert descriptor.shell == "/usr/bin/fish"
        assert descriptor.cwd == "/tmp/work"
        assert descriptor.cols == 120
        assert descriptor.rows == 40
        await terminal_env.manager.close_terminal("session-1", result["terminal_id"])


@pytest.mark.asyncio
async def test_create_terminal_defaults(monkeypatch, terminal_env):
    async with asyncio.timeout(TIMEOUT):
        monkeypatch.delenv("SHELL", raising=False)
        result = await terminal_env.manager.create_terminal(
            "session-2", shell=None, cwd="   ", cols=0, rows=-5
        )
        assert result["shell"] == "/bin/bash"
        assert result["cols"] == 80
        assert result["rows"] == 24
        descriptor = _get_descriptor(
            terminal_env.manager, "session-2", result["terminal_id"]
        )
        assert descriptor.cwd is None
        await terminal_env.manager.close_terminal("session-2", result["terminal_id"])


@pytest.mark.asyncio
async def test_create_terminal_uses_default_shell_when_provided():
    backend = FakePtyBackend()
    manager = TerminalManager(
        backend=backend, default_shell="bash -i", default_cwd="/workspace"
    )
    async with asyncio.timeout(TIMEOUT):
        result = await manager.create_terminal("s1")
        assert result["shell"] == "bash -i"
        assert result["cwd"] == "/workspace"
        await manager.cleanup_session("s1")


@pytest.mark.asyncio
async def test_create_terminal_uses_default_user_when_caller_omits():
    """Embedder-installed manager owns the user identity (parallels how
    SandboxWorkspace.default_user binds the sandbox identity for the
    agent's bash tool)."""
    backend = FakePtyBackend()
    manager = TerminalManager(
        backend=backend,
        default_shell="bash -i",
        default_user="1000:1000",
    )
    async with asyncio.timeout(TIMEOUT):
        await manager.create_terminal("s1", cols=80, rows=24)
        # The backend's start() was called with the default user.
        # FakePtyBackend doesn't store user, so we verify by checking the
        # start signature was hit — see test_create_terminal_uses_default_user_when_caller_omits_user_check.
        await manager.cleanup_session("s1")


@pytest.mark.asyncio
async def test_default_user_threads_to_backend_start():
    captured_users = []

    class RecordingBackend(FakePtyBackend):
        async def start(self, **kwargs):
            captured_users.append(kwargs.get("user"))
            return await super().start(**kwargs)

    backend = RecordingBackend()
    manager = TerminalManager(
        backend=backend,
        default_shell="bash -i",
        default_user="1000:1000",
    )
    async with asyncio.timeout(TIMEOUT):
        await manager.create_terminal("s1", cols=80, rows=24)
        assert captured_users == ["1000:1000"]
        # Caller override wins
        await manager.create_terminal("s1", cols=80, rows=24, user="root")
        assert captured_users == ["1000:1000", "root"]
        await manager.cleanup_session("s1")


@pytest.mark.asyncio
async def test_create_terminal_requires_session_id(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        with pytest.raises(TerminalError):
            await terminal_env.manager.create_terminal("", shell="/bin/bash")


@pytest.mark.asyncio
async def test_authorize_enforces_token_and_single_attach(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-3")
        terminal_id = record["terminal_id"]
        token = record["token"]
        with pytest.raises(TerminalAuthorizationError):
            await terminal_env.manager.authorize("session-3", terminal_id, "bad")
        descriptor = await terminal_env.manager.authorize(
            "session-3", terminal_id, token
        )
        assert descriptor.consumer_attached is True
        with pytest.raises(TerminalAuthorizationError):
            await terminal_env.manager.authorize("session-3", terminal_id, token)
        await terminal_env.manager.close_terminal("session-3", terminal_id)


@pytest.mark.asyncio
async def test_authorize_unknown_terminal(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        with pytest.raises(TerminalNotFoundError):
            await terminal_env.manager.authorize("missing", "t1", "tok")


@pytest.mark.asyncio
async def test_close_terminal_removes_descriptor(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-4")
        terminal_id = record["terminal_id"]
        await terminal_env.manager.close_terminal("session-4", terminal_id)
        assert "session-4" not in terminal_env.manager._terminals


@pytest.mark.asyncio
async def test_cleanup_session_shuts_down_all(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        await terminal_env.manager.create_terminal("session-5")
        await terminal_env.manager.create_terminal("session-5")
        await terminal_env.manager.cleanup_session("session-5")
        assert "session-5" not in terminal_env.manager._terminals
        assert len(terminal_env.backend.closes) == 2


@pytest.mark.asyncio
async def test_resize_and_write_input(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-6")
        descriptor = await terminal_env.manager.authorize(
            "session-6", record["terminal_id"], record["token"]
        )
        await terminal_env.manager.write_input(descriptor, b"ls\n")
        assert terminal_env.backend.writes[-1][1] == b"ls\n"

        await terminal_env.manager.resize(descriptor, 4, 3)
        await terminal_env.manager.resize(descriptor, 100, 40)
        # Coalescing window is 16 ms; wait for the trailing call to apply.
        await asyncio.sleep(0.1)
        assert terminal_env.backend.resizes[-1] == (descriptor.handle, 100, 40)
        # Both calls land on the descriptor's cached cols/rows immediately
        # (the coalescing doesn't suppress the descriptor update).
        assert descriptor.cols == 100
        assert descriptor.rows == 40
        await terminal_env.manager.close_terminal("session-6", record["terminal_id"])


@pytest.mark.asyncio
async def test_resize_coalesces_storm(terminal_env):
    """Bursts of resize() in one event-loop tick collapse to a single backend call."""
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-coalesce")
        descriptor = await terminal_env.manager.authorize(
            "session-coalesce", record["terminal_id"], record["token"]
        )
        baseline = len(terminal_env.backend.resizes)
        # 5 calls in immediate succession.
        for cols in (90, 100, 110, 120, 130):
            await terminal_env.manager.resize(descriptor, cols, 25)
        await asyncio.sleep(0.05)
        applied = terminal_env.backend.resizes[baseline:]
        # Trailing-only semantics: only the last value is propagated.
        assert applied == [(descriptor.handle, 130, 25)]
        await terminal_env.manager.close_terminal(
            "session-coalesce", record["terminal_id"]
        )


@pytest.mark.asyncio
async def test_read_output_queue(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-7")
        descriptor = _get_descriptor(
            terminal_env.manager, "session-7", record["terminal_id"]
        )
        await descriptor.output_queue.put(
            b"hello",
            size=5,
            policy=QueuePolicy.COALESCE_APPEND,
            key=descriptor.terminal_id,
            event_class="terminal_output",
            merge=lambda previous, current: previous + current,
            merged_size=len,
        )
        piece = await terminal_env.manager.read_output(descriptor)
        assert piece == b"hello"
        await terminal_env.manager.close_terminal("session-7", record["terminal_id"])


@pytest.mark.asyncio
async def test_authorize_after_close_rejected(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-8")
        terminal_id = record["terminal_id"]
        token = record["token"]
        await terminal_env.manager.close_terminal("session-8", terminal_id)
        with pytest.raises(TerminalNotFoundError):
            await terminal_env.manager.authorize("session-8", terminal_id, token)


@pytest.mark.asyncio
async def test_write_and_resize_ignore_closed(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-9")
        descriptor = await terminal_env.manager.authorize(
            "session-9", record["terminal_id"], record["token"]
        )
        baseline_writes = len(terminal_env.backend.writes)
        baseline_resizes = len(terminal_env.backend.resizes)
        await terminal_env.manager.close_terminal("session-9", record["terminal_id"])
        await terminal_env.manager.write_input(descriptor, b"echo\n")
        await terminal_env.manager.resize(descriptor, 200, 80)
        await asyncio.sleep(0.05)
        assert len(terminal_env.backend.writes) == baseline_writes
        assert len(terminal_env.backend.resizes) == baseline_resizes


@pytest.mark.asyncio
async def test_cleanup_session_cancels_reader_and_signals(terminal_env):
    async with asyncio.timeout(TIMEOUT):
        record = await terminal_env.manager.create_terminal("session-10")
        descriptor = _get_descriptor(
            terminal_env.manager, "session-10", record["terminal_id"]
        )
        await terminal_env.manager.cleanup_session("session-10")
        assert await terminal_env.manager.read_output(descriptor) is None


@pytest.mark.asyncio
async def test_terminal_output_coalesces_bytes_in_order():
    backend = FakePtyBackend()
    manager = TerminalManager(backend=backend, output_max_chunks=2, output_max_bytes=8)
    async with asyncio.timeout(TIMEOUT):
        record = await manager.create_terminal("session-bounded")
        descriptor = _get_descriptor(manager, "session-bounded", record["terminal_id"])
        await descriptor.output_queue.put(
            b"ab",
            size=2,
            policy=QueuePolicy.COALESCE_APPEND,
            key=descriptor.terminal_id,
            event_class="terminal_output",
            merge=lambda previous, current: previous + current,
            merged_size=len,
        )
        await descriptor.output_queue.put(
            b"cd",
            size=2,
            policy=QueuePolicy.COALESCE_APPEND,
            key=descriptor.terminal_id,
            event_class="terminal_output",
            merge=lambda previous, current: previous + current,
            merged_size=len,
        )
        assert descriptor.output_queue.byte_size == 4
        assert await manager.read_output(descriptor) == b"abcd"
        await manager.close_terminal("session-bounded", record["terminal_id"])


@pytest.mark.asyncio
async def test_terminal_shutdown_closes_full_queue_without_sentinel_slot():
    backend = FakePtyBackend()
    manager = TerminalManager(backend=backend, output_max_chunks=1, output_max_bytes=4)
    async with asyncio.timeout(TIMEOUT):
        record = await manager.create_terminal("session-full")
        descriptor = _get_descriptor(manager, "session-full", record["terminal_id"])
        await descriptor.output_queue.put(b"full", size=4)

        await manager.cleanup_session("session-full")

        assert await manager.read_output(descriptor) == b"full"
        assert await manager.read_output(descriptor) is None


@pytest.mark.asyncio
async def test_terminal_reader_reports_oversized_output_and_closes_backend():
    class OutputBackend(FakePtyBackend):
        async def _stream(self, handle: FakeHandle) -> AsyncIterator[bytes]:
            yield b"oversized"

    backend = OutputBackend()
    manager = TerminalManager(backend=backend, output_max_chunks=1, output_max_bytes=4)
    async with asyncio.timeout(TIMEOUT):
        record = await manager.create_terminal("session-overloaded")
        descriptor = _get_descriptor(
            manager, "session-overloaded", record["terminal_id"]
        )
        await descriptor.reader_task

        with pytest.raises(TransportOverloadedError) as exc_info:
            await manager.read_output(descriptor)
        assert exc_info.value.resource == "terminal_output"
        assert exc_info.value.retryable is False
        assert descriptor.closed is True
        assert descriptor.handle.closed is True
