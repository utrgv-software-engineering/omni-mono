"""Per-session terminal lifecycle manager.

Thin orchestrator over a :class:`PtyBackend`: tracks descriptors per
(session_id, terminal_id), enforces a per-terminal attach-once auth
token, and pumps bytes from the backend's stream into a queue the
WebSocket route in :mod:`omniagents.backends.server.app` drains.

The manager has no opinion on WHERE the shell runs — that's the
backend's job. :class:`HostPtyBackend` runs the shell as a subprocess
of the omniagents server (the historical default, preserved for the
standalone web UI). :class:`SessionPtyBackend` routes through a
:class:`PtyStreamingSession` so the shell lives inside the user's
sandbox (`omni serve` wires this up).
"""

from __future__ import annotations

import asyncio
from asyncio import Queue
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from omniagents.rpc.terminal_backends import (
    HostPtyBackend,
    PtyBackend,
    PtyHandle,
    PtyOpenError,
    decode_input,
    encode_output,
    new_terminal_id,
    new_terminal_token,
)


class TerminalError(Exception):
    pass


class TerminalAuthorizationError(TerminalError):
    pass


class TerminalNotFoundError(TerminalError):
    pass


@dataclass
class TerminalDescriptor:
    session_id: str
    terminal_id: str
    token: str
    backend: PtyBackend
    handle: PtyHandle
    created_at: datetime
    cols: int
    rows: int
    shell: str
    cwd: Optional[str]
    output_queue: "Queue[Optional[bytes]]"
    reader_task: Optional[asyncio.Task]
    exit_code: Optional[int] = None
    consumer_attached: bool = False
    closed: bool = False


class TerminalManager:
    """Track and authorize per-session interactive terminals."""

    def __init__(
        self,
        *,
        backend: Optional[PtyBackend] = None,
        default_shell: Optional[str] = None,
        default_cwd: Optional[str] = None,
        default_user: Optional[str] = None,
    ) -> None:
        self._backend: PtyBackend = backend if backend is not None else HostPtyBackend()
        self._default_shell = default_shell
        self._default_cwd = default_cwd
        self._default_user = default_user
        self._terminals: Dict[str, Dict[str, TerminalDescriptor]] = {}
        self._lock = asyncio.Lock()
        # Per-terminal coalescing for resize calls — xterm.js can fire
        # multiple resize events per frame during a window drag, and
        # backend resizes are non-trivial (ioctl or docker exec_resize).
        # Each entry holds the latest pending (cols, rows); the worker
        # task collapses bursts to a single trailing call.
        self._resize_pending: Dict[str, tuple[int, int]] = {}
        self._resize_tasks: Dict[str, asyncio.Task] = {}

    @property
    def backend(self) -> PtyBackend:
        return self._backend

    async def create_terminal(
        self,
        session_id: str,
        *,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            raise TerminalError("session_id required")
        resolved_shell = self._resolve_shell(shell)
        resolved_cwd = self._resolve_cwd(cwd)
        resolved_user = user if user and user.strip() else self._default_user
        width = cols if cols and cols > 0 else 80
        height = rows if rows and rows > 0 else 24

        try:
            handle = await self._backend.start(
                shell=resolved_shell,
                cwd=resolved_cwd,
                cols=width,
                rows=height,
                user=resolved_user,
            )
        except PtyOpenError as e:
            raise TerminalError(str(e)) from e

        token = new_terminal_token()
        terminal_id = new_terminal_id()
        descriptor = TerminalDescriptor(
            session_id=session_id,
            terminal_id=terminal_id,
            token=token,
            backend=self._backend,
            handle=handle,
            created_at=datetime.now(timezone.utc),
            cols=width,
            rows=height,
            shell=resolved_shell,
            cwd=resolved_cwd,
            output_queue=Queue(),
            reader_task=None,
        )
        descriptor.reader_task = asyncio.create_task(self._reader_loop(descriptor))
        async with self._lock:
            bucket = self._terminals.setdefault(session_id, {})
            bucket[terminal_id] = descriptor
        return {
            "terminal_id": terminal_id,
            "token": token,
            "shell": resolved_shell,
            "cwd": resolved_cwd,
            "created_at": descriptor.created_at.isoformat(),
            "cols": width,
            "rows": height,
        }

    async def authorize(
        self, session_id: str, terminal_id: str, token: str
    ) -> TerminalDescriptor:
        async with self._lock:
            terminal = self._terminals.get(session_id, {}).get(terminal_id)
            if terminal is None:
                raise TerminalNotFoundError("terminal not found")
            if terminal.token != token:
                raise TerminalAuthorizationError("invalid terminal token")
            if terminal.closed:
                raise TerminalNotFoundError("terminal closed")
            if terminal.consumer_attached:
                raise TerminalAuthorizationError("terminal already attached")
            terminal.consumer_attached = True
            return terminal

    async def close_terminal(self, session_id: str, terminal_id: str) -> None:
        terminal = await self._pop_terminal(session_id, terminal_id)
        if terminal is None:
            return
        await self._shutdown_terminal(terminal)

    async def cleanup_session(self, session_id: str) -> None:
        async with self._lock:
            terminals = list(self._terminals.pop(session_id, {}).values())
        for terminal in terminals:
            await self._shutdown_terminal(terminal)

    async def write_input(self, terminal: TerminalDescriptor, data: bytes) -> None:
        if terminal.closed or not data:
            return
        await terminal.backend.write(terminal.handle, data)

    async def resize(self, terminal: TerminalDescriptor, cols: int, rows: int) -> None:
        if terminal.closed:
            return
        cols = max(10, int(cols))
        rows = max(5, int(rows))
        terminal.cols = cols
        terminal.rows = rows

        key = f"{terminal.session_id}:{terminal.terminal_id}"
        self._resize_pending[key] = (cols, rows)
        existing = self._resize_tasks.get(key)
        if existing is not None and not existing.done():
            return  # the running task will pick up the latest pending value
        self._resize_tasks[key] = asyncio.create_task(self._apply_resize_worker(key, terminal))

    async def _apply_resize_worker(
        self, key: str, terminal: TerminalDescriptor
    ) -> None:
        try:
            # 16 ms coalescing window — collapses xterm's resize-storm
            # during a window drag into a single trailing backend call.
            await asyncio.sleep(0.016)
            while True:
                pending = self._resize_pending.pop(key, None)
                if pending is None:
                    return
                cols, rows = pending
                if terminal.closed:
                    return
                try:
                    await terminal.backend.resize(terminal.handle, cols, rows)
                except Exception:
                    pass
                # If another resize landed during the await, loop and
                # apply it too; otherwise exit.
                if key not in self._resize_pending:
                    return
        finally:
            self._resize_tasks.pop(key, None)

    async def read_output(self, terminal: TerminalDescriptor) -> Optional[bytes]:
        return await terminal.output_queue.get()

    async def _pop_terminal(
        self, session_id: str, terminal_id: str
    ) -> Optional[TerminalDescriptor]:
        async with self._lock:
            bucket = self._terminals.get(session_id)
            if not bucket:
                return None
            terminal = bucket.pop(terminal_id, None)
            if terminal is None:
                return None
            if not bucket:
                self._terminals.pop(session_id, None)
            terminal.closed = True
            return terminal

    async def _shutdown_terminal(self, terminal: TerminalDescriptor) -> None:
        terminal.closed = True
        if terminal.reader_task and not terminal.reader_task.done():
            terminal.reader_task.cancel()
            try:
                await terminal.reader_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        try:
            await terminal.backend.close(terminal.handle)
        except Exception:
            pass
        try:
            terminal.exit_code = await terminal.backend.exit_code(terminal.handle)
        except Exception:
            pass
        await terminal.output_queue.put(None)

    async def _reader_loop(self, terminal: TerminalDescriptor) -> None:
        try:
            async for chunk in terminal.backend.stream(terminal.handle):
                if not chunk:
                    continue
                await terminal.output_queue.put(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            try:
                terminal.exit_code = await terminal.backend.exit_code(terminal.handle)
            except Exception:
                pass
            terminal.closed = True
            await terminal.output_queue.put(None)
            await self._discard_terminal(terminal)

    async def _discard_terminal(self, terminal: TerminalDescriptor) -> None:
        async with self._lock:
            bucket = self._terminals.get(terminal.session_id)
            if not bucket:
                return
            bucket.pop(terminal.terminal_id, None)
            if not bucket:
                self._terminals.pop(terminal.session_id, None)

    def _resolve_shell(self, shell: Optional[str]) -> str:
        if shell and shell.strip():
            return shell.strip()
        if self._default_shell and self._default_shell.strip():
            return self._default_shell.strip()
        import os

        env_shell = os.environ.get("SHELL")
        if env_shell and env_shell.strip():
            return env_shell.strip()
        return "/bin/bash"

    def _resolve_cwd(self, cwd: Optional[str]) -> Optional[str]:
        if cwd and cwd.strip():
            return cwd
        if self._default_cwd and self._default_cwd.strip():
            return self._default_cwd
        return None

    @staticmethod
    def encode_output(data: bytes) -> str:
        return encode_output(data)

    @staticmethod
    def decode_input(data: str) -> bytes:
        return decode_input(data)


__all__ = [
    "TerminalManager",
    "TerminalDescriptor",
    "TerminalError",
    "TerminalAuthorizationError",
    "TerminalNotFoundError",
]
