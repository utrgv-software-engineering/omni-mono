"""PTY backends for :class:`omniagents.rpc.terminal.TerminalManager`.

Two backends:

* :class:`HostPtyBackend` — preserves the standalone-server behavior of
  spawning a shell as a subprocess of the omniagents server process via
  :func:`pty.openpty`. Used when the manager runs outside a sandboxed
  session (e.g. the standalone web UI, unit tests).
* :class:`SessionPtyBackend` — routes start/write/resize/stream/close
  through a :class:`PtyStreamingSession` (typically the unwrapped inner
  session of the SDK's :class:`SandboxSession`). Used by ``omni serve``
  so terminals run inside the user's sandbox (docker / e2b / unix_local
  in-place) with the same security model as the agent's bash tool.

Each backend operates on opaque :class:`PtyHandle` instances it created.
``TerminalManager`` stores the handle on its descriptor and never
inspects its contents.

The launcher's main process never imports this module directly — it
talks to ``terminal.create`` / ``terminal.close`` over JSON-RPC and the
``/ws/terminal`` socket. This is purely a backend-side abstraction.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import shlex
import struct
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Protocol

# pty/termios/fcntl are Unix-only. Guard the import so this module loads on
# Windows: only HostPtyBackend needs them, and it raises a clear error there.
# SessionPtyBackend (docker/unix_local/etc.) and the protocol types remain
# usable cross-platform.
try:
    import fcntl
    import pty
    import termios

    _HOST_PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows
    fcntl = pty = termios = None  # type: ignore[assignment]
    _HOST_PTY_AVAILABLE = False

from omniagents.core.sandbox.pty_stream import PtyStreamingSession


__all__ = [
    "PtyBackend",
    "HostPtyBackend",
    "SessionPtyBackend",
    "PtyHandle",
    "PtyOpenError",
]


class PtyOpenError(RuntimeError):
    """Raised when a backend can't start a new PTY."""


class PtyHandle(Protocol):
    """Opaque per-backend handle. Each backend defines its own concrete type."""

    shell: str
    cwd: Optional[str]
    cols: int
    rows: int


class PtyBackend(Protocol):
    """Capability surface a TerminalManager needs from its PTY provider."""

    async def start(
        self,
        *,
        shell: str,
        cwd: Optional[str],
        cols: int,
        rows: int,
        user: Optional[str] = None,
    ) -> PtyHandle: ...

    async def write(self, handle: PtyHandle, data: bytes) -> None: ...

    async def resize(self, handle: PtyHandle, cols: int, rows: int) -> None: ...

    def stream(self, handle: PtyHandle) -> AsyncIterator[bytes]: ...

    async def close(self, handle: PtyHandle) -> None: ...

    async def exit_code(self, handle: PtyHandle) -> Optional[int]: ...


# ---------------------------------------------------------------------------
# HostPtyBackend — spawns a shell as a child of the omniagents process
# ---------------------------------------------------------------------------


@dataclass
class _HostHandle:
    shell: str
    cwd: Optional[str]
    cols: int
    rows: int
    master_fd: int
    process: asyncio.subprocess.Process
    closed: bool = False
    fd_closed: bool = False
    exit_code_value: Optional[int] = None


class HostPtyBackend:
    """Backend that uses :func:`pty.openpty` directly on the host.

    Preserves the behavior of the pre-refactor :class:`TerminalManager`:
    the shell runs as a subprocess of the omniagents server process,
    inheriting whatever user/cwd the server has. Used when no sandbox
    session is attached.
    """

    async def start(
        self,
        *,
        shell: str,
        cwd: Optional[str],
        cols: int,
        rows: int,
        user: Optional[str] = None,
    ) -> _HostHandle:
        if not _HOST_PTY_AVAILABLE:
            raise PtyOpenError(
                "Host interactive terminals are not supported on this platform "
                "(no pty/termios). Use a sandbox profile (docker) for terminals here."
            )
        _ = user  # not enforceable from a host-backed PTY without setuid setup
        cols = max(10, int(cols))
        rows = max(5, int(rows))
        master_fd, slave_fd = pty.openpty()
        try:
            _set_winsize(slave_fd, cols, rows)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            cmd = _build_shell_command(shell)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd if cwd and cwd.strip() else None,
                env=env,
                start_new_session=True,
            )
        except Exception as e:
            with _suppress():
                os.close(master_fd)
            with _suppress():
                os.close(slave_fd)
            raise PtyOpenError(str(e)) from e
        else:
            with _suppress():
                os.close(slave_fd)

        return _HostHandle(
            shell=shell,
            cwd=cwd if cwd and cwd.strip() else None,
            cols=cols,
            rows=rows,
            master_fd=master_fd,
            process=process,
        )

    async def write(self, handle: _HostHandle, data: bytes) -> None:
        if handle.closed:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, handle.master_fd, data)

    async def resize(self, handle: _HostHandle, cols: int, rows: int) -> None:
        if handle.closed:
            return
        cols = max(10, int(cols))
        rows = max(5, int(rows))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _set_winsize, handle.master_fd, cols, rows)
        handle.cols = cols
        handle.rows = rows

    def stream(self, handle: _HostHandle) -> AsyncIterator[bytes]:
        return self._stream(handle)

    async def _stream(self, handle: _HostHandle) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    chunk = await loop.run_in_executor(None, os.read, handle.master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                handle.exit_code_value = await handle.process.wait()
            except Exception:
                pass
            handle.closed = True
            if not handle.fd_closed:
                handle.fd_closed = True
                with _suppress():
                    os.close(handle.master_fd)

    async def close(self, handle: _HostHandle) -> None:
        handle.closed = True
        try:
            handle.process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=2)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                handle.process.kill()
            except ProcessLookupError:
                pass
        except Exception:
            pass
        if not handle.fd_closed:
            handle.fd_closed = True
            with _suppress():
                os.close(handle.master_fd)

    async def exit_code(self, handle: _HostHandle) -> Optional[int]:
        return handle.exit_code_value


# ---------------------------------------------------------------------------
# SessionPtyBackend — spawns the shell inside a SandboxSession
# ---------------------------------------------------------------------------


@dataclass
class _SessionHandle:
    shell: str
    cwd: Optional[str]
    cols: int
    rows: int
    process_id: int
    exit_code_value: Optional[int] = None
    closed: bool = False


class SessionPtyBackend:
    """Backend that uses :meth:`PtyStreamingSession.pty_exec_start` etc.

    The shell runs inside the sandbox at the same isolation level as the
    agent's bash tool (run_as user, manifest root, network policy). The
    SDK's ``pty_write_stdin`` returns up to 250 ms of buffered output as
    a side-effect of every write — undesirable for an interactive
    terminal where the streaming path is the *authoritative* output
    channel. :meth:`write` fire-and-forgets via ``asyncio.create_task``
    so keystroke latency is dominated by network/loopback round-trip,
    not by the SDK's batched yield-time.
    """

    def __init__(self, session: PtyStreamingSession) -> None:
        self._session = session

    async def start(
        self,
        *,
        shell: str,
        cwd: Optional[str],
        cols: int,
        rows: int,
        user: Optional[str] = None,
    ) -> _SessionHandle:
        cols = max(10, int(cols))
        rows = max(5, int(rows))

        command = shell
        if cwd and cwd.strip():
            # The SDK's pty_exec_start doesn't take a cwd; prepend `cd`.
            # `exec` replaces the cd shell so signals still hit the real shell.
            # `cd ... || exit 1` keeps the failure exit code stable across
            # shells (dash exits 2 from cd failure, bash exits 1) — without
            # this, the launcher's "shell exited (code N)" surface lies
            # depending on which /bin/sh implementation the image ships.
            command = f"cd {shlex.quote(cwd)} || exit 1; exec {shell}"

        try:
            update = await self._session.pty_exec_start(  # type: ignore[attr-defined]
                command,
                tty=True,
                shell=True,
                user=user,
                yield_time_s=0.0,
            )
        except Exception as e:
            raise PtyOpenError(str(e)) from e

        process_id = update.process_id
        if process_id is None:
            # The shell exited inside the initial yield window. Surface
            # the exit code so the caller can report it.
            raise PtyOpenError(
                f"shell exited immediately (code {update.exit_code}): {shell!r}"
            )

        # Apply initial winsize. Shells may have already drawn at the
        # default 80x24; SIGWINCH causes a reprint at the right size.
        try:
            await self._session.pty_resize(process_id, cols=cols, rows=rows)
        except Exception:
            pass

        return _SessionHandle(
            shell=shell,
            cwd=cwd if cwd and cwd.strip() else None,
            cols=cols,
            rows=rows,
            process_id=process_id,
        )

    async def write(self, handle: _SessionHandle, data: bytes) -> None:
        if handle.closed:
            return
        text = data.decode("utf-8", "surrogateescape")
        if not text:
            return

        async def _send() -> None:
            try:
                await self._session_pty_write_stdin(handle.process_id, text)
            except Exception:
                pass

        # Fire-and-forget: SDK pty_write_stdin internally waits up to
        # 250 ms after each write to gather output, which would dominate
        # keystroke latency. The streaming path delivers output.
        asyncio.create_task(_send())

    async def _session_pty_write_stdin(self, process_id: int, chars: str) -> None:
        # Indirection point that lets tests intercept the SDK call.
        await self._session.pty_write_stdin(  # type: ignore[attr-defined]
            session_id=process_id,
            chars=chars,
            yield_time_s=0.0,
        )

    async def resize(self, handle: _SessionHandle, cols: int, rows: int) -> None:
        if handle.closed:
            return
        cols = max(10, int(cols))
        rows = max(5, int(rows))
        try:
            await self._session.pty_resize(handle.process_id, cols=cols, rows=rows)
        except Exception:
            pass
        handle.cols = cols
        handle.rows = rows

    def stream(self, handle: _SessionHandle) -> AsyncIterator[bytes]:
        return self._stream(handle)

    async def _stream(self, handle: _SessionHandle) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._session.pty_stream_output(handle.process_id):
                yield chunk
        finally:
            handle.closed = True

    async def close(self, handle: _SessionHandle) -> None:
        if handle.closed:
            return
        handle.closed = True
        # Polite shutdown: Ctrl-C, then Ctrl-D. The shell exits and the
        # SDK's _watch_process_exit + _finalize_pty_update remove the
        # entry from _pty_processes. We don't fall back to
        # pty_terminate_all — that would kill every PTY in the session,
        # including the agent's bash tool processes.
        for ch in ("\x03", "\x04"):
            try:
                await self._session_pty_write_stdin(handle.process_id, ch)
            except Exception:
                pass

    async def exit_code(self, handle: _SessionHandle) -> Optional[int]:
        return handle.exit_code_value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def _build_shell_command(shell: str) -> list[str]:
    parts = shlex.split(shell)
    if not parts:
        parts = [shell]
    name = os.path.basename(parts[0])
    if name in {"bash", "zsh", "sh", "fish"}:
        if "-i" not in parts:
            parts.append("-i")
    return parts


class _suppress:
    def __enter__(self) -> "_suppress":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True


def encode_output(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_input(data: str) -> bytes:
    return base64.b64decode(data)


def new_terminal_id() -> str:
    return secrets.token_hex(8)


def new_terminal_token() -> str:
    return secrets.token_urlsafe(24)
