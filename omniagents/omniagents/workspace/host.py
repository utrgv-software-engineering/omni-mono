"""Host-side Workspace implementation.

Wraps ``pathlib`` and ``asyncio.create_subprocess_*`` so synchronous stdlib
work runs off the event loop via ``asyncio.to_thread``. It is selected only by
an explicit host ExecutionEnvironment lease.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from ._entries import log_skipped_entry, make_entry, sort_entries
from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)


class HostWorkspace(Workspace):
    """Workspace backed by the local filesystem rooted at ``root``."""

    supports_direct_host_scan = True

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        scratch_root: Optional[str] = None,
        confine: bool = True,
    ) -> None:
        # Direct construction may omit root for low-level/tests convenience.
        # Runtime routing never constructs an implicit HostWorkspace.
        resolved = Path(root or os.getcwd()).resolve()
        if not resolved.is_dir():
            # Allow non-existent root; tools may create files in fresh dirs.
            # But the path must be absolute and parent-traversable.
            resolved.mkdir(parents=True, exist_ok=True)
        self.root = str(resolved)

        # When False, ``resolve`` still anchors relative paths at ``root``
        # (so cwd ergonomics are unchanged) but does NOT reject absolute
        # paths outside it. This is the "no sandbox" host target: the agent
        # may read/write anywhere the process can. Default True keeps the
        # workspace-root boundary every existing caller relies on.
        self.confine = confine

        # Scratch directory — deliberately outside ``root`` so tool-internal
        # state (worker worktrees, spawn logs, etc.) never mixes with the
        # user's workspace files. Honors ``$OMNIAGENTS_SCRATCH`` for users
        # who want a non-default location.
        scratch = (
            scratch_root
            or os.environ.get("OMNIAGENTS_SCRATCH")
            or os.path.join(os.path.expanduser("~"), ".omniagents", "scratch")
        )
        self.scratch_root = str(Path(scratch).expanduser().resolve())

    # ----------------------------- helpers ----------------------------- #

    def _display_path(self, p: Path) -> str:
        """Path shown in discovery results: relative to root when inside it,
        else the absolute posix path (possible only when unconfined)."""
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()

    def resolve(self, path: str) -> str:
        if not path or path == ".":
            return self.root
        # Path() resolution differs for absolute vs relative; we want all
        # paths interpreted relative to root unless they're already inside
        # root. Absolute paths outside root → escape error.
        candidate = Path(path)
        if candidate.is_absolute():
            full = candidate.resolve()
        else:
            full = (Path(self.root) / candidate).resolve()
        # Defense in depth — make sure we're still inside root. Skipped when
        # the workspace is unconfined (the "host" no-sandbox target).
        if self.confine:
            try:
                full.relative_to(self.root)
            except ValueError:
                raise WorkspaceEscapeError(
                    f"Path {path!r} resolves to {full!r}, outside workspace root {self.root!r}"
                ) from None
        return str(full)

    # ----------------------------- file I/O ---------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        absolute = self.resolve(path)
        return await asyncio.to_thread(Path(absolute).read_text, encoding=encoding)

    async def read_bytes(self, path: str) -> bytes:
        absolute = self.resolve(path)
        return await asyncio.to_thread(Path(absolute).read_bytes)

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        append: bool = False,
        create_dirs: bool = True,
    ) -> None:
        absolute = Path(self.resolve(path))
        if create_dirs:
            await asyncio.to_thread(absolute.parent.mkdir, parents=True, exist_ok=True)
        mode = "a" if append else "w"

        def _write() -> None:
            with absolute.open(mode, encoding=encoding) as f:
                f.write(content)

        await asyncio.to_thread(_write)

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> None:
        absolute = Path(self.resolve(path))
        if create_dirs:
            await asyncio.to_thread(absolute.parent.mkdir, parents=True, exist_ok=True)
        mode = "ab" if append else "wb"

        def _write() -> None:
            with absolute.open(mode) as f:
                f.write(data)

        await asyncio.to_thread(_write)

    async def exists(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        return await asyncio.to_thread(Path(absolute).exists)

    async def is_file(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        return await asyncio.to_thread(Path(absolute).is_file)

    async def is_dir(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        return await asyncio.to_thread(Path(absolute).is_dir)

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> None:
        absolute = self.resolve(path)
        await asyncio.to_thread(
            Path(absolute).mkdir, parents=parents, exist_ok=exist_ok
        )

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        absolute = Path(self.resolve(path))

        def _rm() -> None:
            if not absolute.exists():
                return
            if absolute.is_dir():
                if recursive:
                    shutil.rmtree(absolute)
                else:
                    absolute.rmdir()
            else:
                absolute.unlink()

        await asyncio.to_thread(_rm)

    # ---------------------------- discovery ---------------------------- #

    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        # `respect_gitignore` is honored only when git is available; we don't
        # vendor a gitignore parser here. For sandbox parity, both backends
        # ship the same default-on flag with the same fallback behavior.
        absolute = Path(self.resolve(path))

        def _list() -> List[FileEntry]:
            # ``iterdir`` raises the OSError subclass the contract promises
            # (FileNotFoundError / NotADirectoryError / PermissionError) —
            # deliberately not swallowed into an empty listing, so callers
            # can tell "empty directory" from "no such directory" and the
            # sandbox backend has host behavior to mirror.
            out: List[FileEntry] = []
            for entry in absolute.iterdir():
                if not include_hidden and entry.name.startswith("."):
                    continue
                try:
                    # ``lstat``: an entry is described by itself, never by
                    # a symlink target, so a dangling link still lists.
                    stat = entry.lstat()
                    is_dir = entry.is_dir() and not entry.is_symlink()
                    is_symlink = entry.is_symlink()
                except OSError:
                    # One unreadable entry never aborts the listing.
                    continue
                try:
                    out.append(
                        make_entry(
                            name=entry.name,
                            path=self._display_path(entry),
                            is_dir=is_dir,
                            size=stat.st_size,
                            mtime=datetime.fromtimestamp(stat.st_mtime),
                            is_symlink=is_symlink,
                            # Unconfined host workspaces may legitimately
                            # list outside the root, where no relative
                            # spelling exists.
                            allow_absolute=not self.confine,
                        )
                    )
                except ValueError as error:
                    log_skipped_entry("HostWorkspace", str(absolute), error)
                    continue
            return sort_entries(out, sort_by)

        return await asyncio.to_thread(_list)

    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]:
        absolute = Path(self.resolve(root))

        def _glob() -> List[str]:
            if not absolute.is_dir():
                return []
            iterator = absolute.rglob(pattern) if recursive else absolute.glob(pattern)
            results: List[str] = []
            for p in iterator:
                if not include_hidden and any(
                    part.startswith(".") for part in p.relative_to(absolute).parts
                ):
                    continue
                results.append(self._display_path(p))
            results.sort()
            return results

        return await asyncio.to_thread(_glob)

    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]:
        # Pure-Python implementation. Sandbox impl can shell out to `rg`
        # via session.exec for performance but the surface is identical.
        # context_lines isn't surfaced in GrepMatch yet — flag is reserved
        # for future use.
        _ = context_lines
        import re

        absolute = Path(self.resolve(root))
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        def _grep() -> List[GrepMatch]:
            if not absolute.is_dir():
                return []
            out: List[GrepMatch] = []
            for path in absolute.rglob("*"):
                if not path.is_file():
                    continue
                if not fnmatch(path.name, file_pattern):
                    continue
                rel = self._display_path(path)
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    m = regex.search(line)
                    if m:
                        out.append(
                            GrepMatch(
                                path=rel,
                                line_number=i,
                                line=line,
                                column=m.start() + 1,
                            )
                        )
            return out

        return await asyncio.to_thread(_grep)

    # ------------------------- shell execution ------------------------- #

    async def exec(
        self,
        command: CommandLike,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = 30.0,
        env: Optional[Dict[str, str]] = None,
        shell: bool = True,
        user: Optional[str] = None,
        max_output_bytes: Optional[int] = None,
    ) -> ExecResult:
        # `user` is a sandbox-only concern (run_as / sudo) — host-side we
        # don't change identity. Documented as ignored host-side.
        _ = user
        cwd_resolved = self.resolve(cwd) if cwd else self.root
        merged_env = {**os.environ, **(env or {})} if env else None

        start = time.perf_counter()
        if shell:
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_resolved,
                env=merged_env,
            )
        else:
            args = [command] if isinstance(command, str) else list(command)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_resolved,
                env=merged_env,
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode if proc.returncode is not None else -1
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=2.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()
            exit_code = -1
            wall = time.perf_counter() - start
            return ExecResult(
                stdout=_safe_decode(stdout_b)
                + f"\nCommand timed out after {timeout:.3f}s.",
                stderr=_safe_decode(stderr_b),
                exit_code=exit_code,
                wall_time_seconds=wall,
                truncated=False,
            )

        wall = time.perf_counter() - start
        stdout_str = _safe_decode(stdout_b)
        stderr_str = _safe_decode(stderr_b)
        truncated = False
        if max_output_bytes is not None:
            if len(stdout_b) > max_output_bytes:
                stdout_str = _safe_decode(stdout_b[:max_output_bytes])
                truncated = True
            if len(stderr_b) > max_output_bytes:
                stderr_str = _safe_decode(stderr_b[:max_output_bytes])
                truncated = True
        return ExecResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code,
            wall_time_seconds=wall,
            truncated=truncated,
        )

    async def spawn(
        self,
        command: CommandLike,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: bool = True,
        user: Optional[str] = None,
        pty: bool = False,
    ) -> ProcessHandle:
        _ = user
        if pty:
            # PTY spawning host-side requires a separate implementation
            # (pty.fork or pexpect) — defer to a follow-up rather than
            # silently degrading.
            raise WorkspaceFeatureUnavailable(
                "HostWorkspace.spawn(pty=True) is not implemented yet. "
                "Use exec() for one-shot calls or open a feature request."
            )
        cwd_resolved = self.resolve(cwd) if cwd else self.root
        merged_env = {**os.environ, **(env or {})} if env else None

        if shell:
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd_resolved,
                env=merged_env,
            )
        else:
            args = [command] if isinstance(command, str) else list(command)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd_resolved,
                env=merged_env,
            )
        return _HostProcessHandle(proc=proc)


class _HostProcessHandle(ProcessHandle):
    """ProcessHandle backed by an asyncio subprocess on the host."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.process_id = uuid.uuid4().hex[:12]
        self.pid = proc.pid
        self._proc = proc
        self._start = time.perf_counter()
        self._stdout_buf = bytearray()
        self._stderr_buf = bytearray()
        self._reader_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        async def _pump(stream, buf):
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)

        await asyncio.gather(
            _pump(self._proc.stdout, self._stdout_buf),
            _pump(self._proc.stderr, self._stderr_buf),
        )

    async def status(
        self,
    ) -> Literal["running", "completed", "killed", "failed"]:
        rc = self._proc.returncode
        if rc is None:
            return "running"
        if rc == 0:
            return "completed"
        if rc < 0:
            return "killed"
        return "failed"

    async def logs(self, *, tail_lines: Optional[int] = None) -> str:
        text = _safe_decode(bytes(self._stdout_buf)) + _safe_decode(
            bytes(self._stderr_buf)
        )
        if tail_lines is not None:
            lines = text.splitlines()
            text = "\n".join(lines[-tail_lines:])
        return text

    async def write_stdin(self, data: str) -> None:
        if self._proc.stdin is None:
            raise WorkspaceFeatureUnavailable("Process was spawned without stdin pipe.")
        self._proc.stdin.write(data.encode("utf-8"))
        await self._proc.stdin.drain()

    async def wait(self, timeout: Optional[float] = None) -> ExecResult:
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(
                stdout=_safe_decode(bytes(self._stdout_buf)),
                stderr=_safe_decode(bytes(self._stderr_buf)),
                exit_code=-1,
                wall_time_seconds=time.perf_counter() - self._start,
                truncated=False,
            )
        await self._reader_task
        return ExecResult(
            stdout=_safe_decode(bytes(self._stdout_buf)),
            stderr=_safe_decode(bytes(self._stderr_buf)),
            exit_code=(
                self._proc.returncode if self._proc.returncode is not None else -1
            ),
            wall_time_seconds=time.perf_counter() - self._start,
            truncated=False,
        )

    async def terminate(self, *, force_after: float = 5.0) -> None:
        if self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=force_after)
        except asyncio.TimeoutError:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            await self._proc.wait()


def _safe_decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
