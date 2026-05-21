"""Extended unix_local sandbox session with PTY streaming + resize.

The SDK's :class:`agents.sandbox.sandboxes.unix_local.UnixLocalSandboxSession`
exposes ``pty_exec_start`` / ``pty_write_stdin`` / ``pty_terminate_all``,
which is enough for the agent's batched bash tool but not for human
terminals that want bytes-as-they-arrive and live winsize updates. This
subclass adds two methods:

* :meth:`pty_stream_output` — async iterator that yields PTY chunks as
  the kernel produces them. Multiple consumers per process_id are
  supported (each gets its own bounded queue).
* :meth:`pty_resize` — applies ``TIOCSWINSZ`` on the PTY primary fd.

The implementation overrides the SDK's ``_pump_pty_primary_fd`` so the
single in-process reader fans chunks out to stream consumers under the
same ``entry.output_lock`` the SDK uses, avoiding a register-race that
would let the shell's first prompt slip into ``output_chunks`` instead of
the consumer's queue. When stream consumers are present for an entry,
the pump skips the SDK's ``output_chunks`` append entirely — long-lived
terminal sessions would otherwise grow that deque without bound (no
batched ``_collect_pty_output`` caller drains it).

The companion :class:`ExtendedUnixLocalSandboxClient` wires this subclass
into :meth:`create` so the factory can swap to it transparently.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import struct
import termios
import uuid
from typing import AsyncIterator, Optional

from agents.sandbox.manifest import Manifest
from agents.sandbox.sandboxes.unix_local import (
    _PTY_READ_CHUNK_BYTES,
    UnixLocalSandboxClient,
    UnixLocalSandboxClientOptions,
    UnixLocalSandboxSession,
    UnixLocalSandboxSessionState,
    _UnixPtyProcessEntry,
)
from agents.sandbox.session import SandboxSession
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot

from omniagents.core.sandbox.pty_stream import PtyStreamingSession

__all__ = [
    "ExtendedUnixLocalSandboxClient",
    "ExtendedUnixLocalSandboxSession",
]


# Per-consumer queue size: 1024 chunks * 4 KB = 4 MB soft cap before
# overflow drops new chunks. Pathological producers (``cat /dev/urandom``)
# stop blocking the producer; the human terminal user sees a freeze rather
# than the launcher crashing. Mirrors the plan's backpressure decision.
_STREAM_QUEUE_MAX_CHUNKS = 1024


class ExtendedUnixLocalSandboxSession(UnixLocalSandboxSession, PtyStreamingSession):
    def pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        return self._pty_stream_output(process_id)

    async def _pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        entry = self._pty_processes.get(process_id)
        if entry is None:
            raise ValueError(f"unknown pty process_id {process_id}")
        q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=_STREAM_QUEUE_MAX_CHUNKS)

        async with entry.output_lock:
            while entry.output_chunks:
                chunk = entry.output_chunks.popleft()
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    break
            consumers = getattr(entry, "stream_consumers", None)
            if consumers is None:
                consumers = []
                setattr(entry, "stream_consumers", consumers)
            consumers.append(q)
            already_closed = entry.output_closed.is_set()

        if already_closed:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        try:
            while True:
                chunk = await q.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            consumers_now = getattr(entry, "stream_consumers", None)
            if consumers_now is not None:
                try:
                    consumers_now.remove(q)
                except ValueError:
                    pass

    async def pty_resize(self, process_id: int, *, cols: int, rows: int) -> None:
        entry = self._pty_processes.get(process_id)
        if entry is None or entry.primary_fd is None:
            return
        primary_fd = entry.primary_fd
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        packed = struct.pack("HHHH", rows, cols, 0, 0)

        def _ioctl() -> None:
            try:
                fcntl.ioctl(primary_fd, termios.TIOCSWINSZ, packed)
            except OSError as e:
                if e.errno not in {errno.EBADF, errno.EIO}:
                    raise

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _ioctl)

    async def _pump_pty_primary_fd(self, entry: _UnixPtyProcessEntry) -> None:
        primary_fd = entry.primary_fd
        if primary_fd is None:
            return

        loop = asyncio.get_running_loop()
        while True:
            try:
                chunk = await loop.run_in_executor(None, os.read, primary_fd, _PTY_READ_CHUNK_BYTES)
            except OSError as e:
                if e.errno in {errno.EIO, errno.EBADF}:
                    break
                raise

            if chunk == b"":
                break

            async with entry.output_lock:
                consumers = getattr(entry, "stream_consumers", None)
                if consumers:
                    for q in consumers:
                        try:
                            q.put_nowait(chunk)
                        except asyncio.QueueFull:
                            pass
                else:
                    entry.output_chunks.append(chunk)
            entry.output_notify.set()

        consumers = getattr(entry, "stream_consumers", None)
        if consumers:
            for q in consumers:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


class ExtendedUnixLocalSandboxClient(UnixLocalSandboxClient):
    async def create(
        self,
        *,
        snapshot: "SnapshotSpec | SnapshotBase | None" = None,
        manifest: Manifest | None = None,
        options: UnixLocalSandboxClientOptions | None = None,
    ) -> SandboxSession:
        # Re-implements the parent's body so we can instantiate the
        # extended session class. The SDK's UnixLocalSandboxClient calls
        # ``UnixLocalSandboxSession.from_state`` directly with no override
        # seam; subclassing here is the cleanest swap.
        import shutil
        import tempfile
        from pathlib import Path

        from agents.sandbox.sandboxes.unix_local import (
            _DEFAULT_MANIFEST_ROOT,
            _DEFAULT_WORKSPACE_PREFIX,
        )

        _ = shutil  # not used here; imports kept symmetric with parent

        resolved_options = options or UnixLocalSandboxClientOptions()
        workspace_root_owned = False
        if manifest is None or manifest.root == _DEFAULT_MANIFEST_ROOT:
            workspace_dir = tempfile.mkdtemp(prefix=_DEFAULT_WORKSPACE_PREFIX)
            workspace_root_owned = True
            if manifest is None:
                manifest = Manifest(root=workspace_dir)
            else:
                manifest = manifest.model_copy(update={"root": workspace_dir}, deep=True)
            _ = Path(workspace_dir)

        session_id = uuid.uuid4()
        snapshot_id = str(session_id)
        snapshot_instance = resolve_snapshot(snapshot, snapshot_id)
        state = UnixLocalSandboxSessionState(
            session_id=session_id,
            manifest=manifest,
            snapshot=snapshot_instance,
            workspace_root_owned=workspace_root_owned,
            exposed_ports=resolved_options.exposed_ports,
        )
        inner = ExtendedUnixLocalSandboxSession.from_state(state)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        if not isinstance(state, UnixLocalSandboxSessionState):
            raise TypeError(
                "ExtendedUnixLocalSandboxClient.resume expects a UnixLocalSandboxSessionState"
            )
        inner = ExtendedUnixLocalSandboxSession.from_state(state)
        return self._wrap_session(inner, instrumentation=self._instrumentation)
