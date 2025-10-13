import asyncio
import base64
import os
import pty
import secrets
import shlex
import struct
import termios
from asyncio import Queue
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import fcntl


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
    master_fd: int
    process: asyncio.subprocess.Process
    created_at: datetime
    cols: int
    rows: int
    shell: str
    cwd: Optional[str]
    output_queue: Queue[Optional[bytes]]
    reader_task: Optional[asyncio.Task]
    exit_code: Optional[int] = None
    consumer_attached: bool = False
    closed: bool = False


class TerminalManager:
    def __init__(self) -> None:
        self._terminals: Dict[str, Dict[str, TerminalDescriptor]] = {}
        self._lock = asyncio.Lock()

    async def create_terminal(
        self,
        session_id: str,
        *,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            raise TerminalError("session_id required")
        resolved_shell = self._resolve_shell(shell)
        resolved_cwd = cwd if cwd and cwd.strip() else None
        width = cols if cols and cols > 0 else 80
        height = rows if rows and rows > 0 else 24
        master_fd, slave_fd = pty.openpty()
        try:
            self._set_winsize(slave_fd, width, height)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            cmd = self._build_command(resolved_shell)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=resolved_cwd,
                env=env,
                start_new_session=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except Exception:
                pass
        token = secrets.token_urlsafe(24)
        terminal_id = secrets.token_hex(8)
        descriptor = TerminalDescriptor(
            session_id=session_id,
            terminal_id=terminal_id,
            token=token,
            master_fd=master_fd,
            process=process,
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

    async def authorize(self, session_id: str, terminal_id: str, token: str) -> TerminalDescriptor:
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
            terminals = self._terminals.pop(session_id, {}).values()
        for terminal in list(terminals):
            await self._shutdown_terminal(terminal)

    async def write_input(self, terminal: TerminalDescriptor, data: bytes) -> None:
        if terminal.closed:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, terminal.master_fd, data)

    async def resize(self, terminal: TerminalDescriptor, cols: int, rows: int) -> None:
        if terminal.closed:
            return
        cols = max(10, cols)
        rows = max(5, rows)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._set_winsize, terminal.master_fd, cols, rows)
        terminal.cols = cols
        terminal.rows = rows

    async def read_output(self, terminal: TerminalDescriptor) -> Optional[bytes]:
        return await terminal.output_queue.get()

    async def _pop_terminal(self, session_id: str, terminal_id: str) -> Optional[TerminalDescriptor]:
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
        if terminal.reader_task:
            terminal.reader_task.cancel()
            try:
                await terminal.reader_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        try:
            terminal.process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(terminal.process.wait(), timeout=2)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                terminal.process.kill()
            except ProcessLookupError:
                pass
        except Exception:
            pass
        try:
            os.close(terminal.master_fd)
        except Exception:
            pass
        await terminal.output_queue.put(None)

    async def _reader_loop(self, terminal: TerminalDescriptor) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, terminal.master_fd, 4096)
                if not data:
                    break
                await terminal.output_queue.put(data)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            try:
                code = await terminal.process.wait()
                terminal.exit_code = code
            except Exception:
                pass
            terminal.closed = True
            await terminal.output_queue.put(None)
            await self._discard_terminal(terminal)
            try:
                os.close(terminal.master_fd)
            except Exception:
                pass

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
        env_shell = os.environ.get("SHELL")
        if env_shell and env_shell.strip():
            return env_shell.strip()
        return "/bin/bash"

    def _build_command(self, shell: str) -> list[str]:
        parts = shlex.split(shell)
        if not parts:
            parts = [shell]
        name = os.path.basename(parts[0])
        if name in {"bash", "zsh", "sh", "fish"}:
            if "-i" not in parts:
                parts.append("-i")
        return parts

    def _set_winsize(self, fd: int, cols: int, rows: int) -> None:
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)

    def encode_output(self, data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    def decode_input(self, data: str) -> bytes:
        return base64.b64decode(data)


__all__ = [
    "TerminalManager",
    "TerminalDescriptor",
    "TerminalError",
    "TerminalAuthorizationError",
    "TerminalNotFoundError",
]
