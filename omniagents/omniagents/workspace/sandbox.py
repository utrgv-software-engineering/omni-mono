"""Sandbox-side Workspace implementation.

Wraps an SDK ``BaseSandboxSession``: file I/O goes through ``session.read`` /
``session.write`` / ``session.ls`` / ``session.rm`` / ``session.mkdir``;
shell execution through ``session.exec`` and ``session.pty_exec_start``;
discovery (glob/grep) shells out to ``find`` and ``grep`` inside the
sandbox.

Path semantics: sandbox sessions accept paths relative to the manifest
root or absolute paths inside the container. We pass strings through and
let the session do its own validation; the host-side workspace's
``WorkspaceEscapeError`` semantics are enforced by the session's own
path-policy machinery.
"""

from __future__ import annotations

import io
import shlex
import time
import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceFeatureUnavailable,
)


class SandboxWorkspace(Workspace):
    """Workspace backed by an SDK sandbox session.

    *session* is an instance of ``agents.sandbox.session.BaseSandboxSession``
    (or any subclass). The sandbox runtime is responsible for starting and
    stopping the session; SandboxWorkspace assumes it's already running.
    """

    def __init__(
        self,
        session: Any,
        *,
        root: str = "/workspace",
        scratch_root: str = "/tmp/omniagents/scratch",
        default_user: Optional[str] = None,
    ) -> None:
        self._session = session
        self.root = root
        # Scratch dir lives outside the manifest mount on purpose: anything
        # tool-internal (worktrees, background-spawn logs) should never
        # collide with the user's workspace files. The path is reachable
        # via ``session.exec`` (mkdir/git/etc.); file I/O via
        # ``session.read``/``session.write`` may be limited by the
        # sandbox path policy, so callers that need byte-level access
        # there should prefer ``exec``-based approaches (cat, tee, etc.).
        self.scratch_root = scratch_root
        # Default identity for ``exec`` / ``spawn`` / ``read`` / ``write``
        # calls that don't pass an explicit ``user=``. Without this, the
        # SDK's docker session runs commands as root, which lets agent
        # code (e.g. the bash tool) tamper with in-container security
        # measures like ``iptables`` network filtering. Set this to the
        # remapped non-privileged user (e.g. ``"user"`` or ``"1000:1000"``)
        # to constrain the agent to its uid mapping.
        self._default_user = default_user

    # ----------------------------- helpers ----------------------------- #

    def resolve(self, path: str) -> str:
        # Sandbox paths are interpreted relative to the manifest root by
        # the session itself. We just normalize: relative paths become
        # ``<root>/<path>``; absolute paths stay as-is. The session enforces
        # the actual sandbox/host boundary via its WorkspacePathPolicy.
        if not path or path == ".":
            return self.root
        p = PurePosixPath(path)
        if p.is_absolute():
            return str(p)
        return str(PurePosixPath(self.root) / p)

    # ----------------------------- file I/O ---------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        data = await self.read_bytes(path)
        return data.decode(encoding, errors="replace")

    async def read_bytes(self, path: str) -> bytes:
        absolute = self.resolve(path)
        stream = await self._session.read(
            PurePosixPath(absolute), user=self._default_user
        )
        try:
            return stream.read()
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        append: bool = False,
        create_dirs: bool = True,
    ) -> None:
        await self.write_bytes(
            path,
            content.encode(encoding),
            append=append,
            create_dirs=create_dirs,
        )

    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> None:
        absolute = self.resolve(path)
        if append:
            # session.write is whole-file replace; emulate append by
            # reading + concat. Race-prone if another writer is active,
            # but sandbox sessions are typically single-tenant.
            try:
                existing = await self.read_bytes(path)
            except FileNotFoundError:
                existing = b""
            data = existing + data
        if create_dirs:
            parent = str(PurePosixPath(absolute).parent)
            if parent and parent != self.root:
                try:
                    await self._session.mkdir(
                        PurePosixPath(parent), parents=True, user=self._default_user
                    )
                except Exception:
                    # mkdir may complain on existing dirs depending on backend;
                    # write itself will fail loudly if the parent really doesn't exist.
                    pass
        await self._session.write(
            PurePosixPath(absolute), io.BytesIO(data), user=self._default_user
        )

    async def exists(self, path: str) -> bool:
        absolute = self.resolve(path)
        result = await self._session.exec(
            "sh",
            "-lc",
            f"test -e {shlex.quote(absolute)}",
            shell=False,
            user=self._default_user,
        )
        return result.exit_code == 0

    async def is_file(self, path: str) -> bool:
        absolute = self.resolve(path)
        result = await self._session.exec(
            "sh",
            "-lc",
            f"test -f {shlex.quote(absolute)}",
            shell=False,
            user=self._default_user,
        )
        return result.exit_code == 0

    async def is_dir(self, path: str) -> bool:
        absolute = self.resolve(path)
        result = await self._session.exec(
            "sh",
            "-lc",
            f"test -d {shlex.quote(absolute)}",
            shell=False,
            user=self._default_user,
        )
        return result.exit_code == 0

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> None:
        absolute = self.resolve(path)
        try:
            await self._session.mkdir(
                PurePosixPath(absolute), parents=parents, user=self._default_user
            )
        except Exception:
            if not exist_ok:
                raise
            # Best-effort idempotency — mkdir on existing dir varies by backend

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        absolute = self.resolve(path)
        await self._session.rm(
            PurePosixPath(absolute), recursive=recursive, user=self._default_user
        )

    # ---------------------------- discovery ---------------------------- #

    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        # `respect_gitignore` is documented but not enforced — same fallback
        # as HostWorkspace when no git binary is available. SDK's
        # session.ls returns its own FileEntry shape; we translate to ours.
        _ = respect_gitignore
        absolute = self.resolve(path)
        sdk_entries = await self._session.ls(
            PurePosixPath(absolute), user=self._default_user
        )
        out: List[FileEntry] = []
        for sdk_entry in sdk_entries:
            name = getattr(sdk_entry, "name", None) or getattr(sdk_entry, "path", "")
            if not include_hidden and isinstance(name, str) and name.startswith("."):
                continue
            entry_path = getattr(sdk_entry, "path", name)
            is_dir = bool(getattr(sdk_entry, "is_dir", False))
            size = getattr(sdk_entry, "size", None)
            mtime_raw = getattr(sdk_entry, "mtime", None)
            mtime = (
                mtime_raw
                if isinstance(mtime_raw, datetime)
                else (
                    datetime.fromtimestamp(mtime_raw)
                    if isinstance(mtime_raw, (int, float))
                    else None
                )
            )
            out.append(
                FileEntry(
                    name=str(name),
                    path=str(entry_path),
                    is_dir=is_dir,
                    size=size,
                    mtime=mtime,
                )
            )
        keyfn = {
            "name": lambda e: e.name,
            "mtime": lambda e: (e.mtime or datetime.min, e.name),
            "size": lambda e: (-(e.size or 0), e.name),
        }[sort_by]
        out.sort(key=keyfn)
        return out

    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]:
        absolute = self.resolve(root)
        # Use `find` rather than session.ls + recursion: it's atomic and
        # consistent with how shell-based workflows already use it.
        depth_arg = [] if recursive else ["-maxdepth", "1"]
        hidden_filter = [] if include_hidden else ["-not", "-path", "*/.*"]
        cmd = ["find", absolute, *depth_arg, "-name", pattern, *hidden_filter]
        result = await self._session.exec(*cmd, shell=False, user=self._default_user)
        if result.exit_code != 0:
            return []
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        # Trim back to paths relative to root
        out: List[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith(self.root + "/"):
                out.append(line[len(self.root) + 1 :])
            else:
                out.append(line)
        out.sort()
        return out

    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]:
        absolute = self.resolve(root)
        cmd = ["grep", "-rn"]
        if not case_sensitive:
            cmd.append("-i")
        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])
        if file_pattern != "*":
            cmd.extend(["--include", file_pattern])
        cmd.extend(["-e", pattern, absolute])
        result = await self._session.exec(*cmd, shell=False, user=self._default_user)
        # grep returns 1 on no matches — treat as empty rather than error
        if result.exit_code not in (0, 1):
            return []
        out: List[GrepMatch] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            # Format: <path>:<line_number>:<line_content>
            try:
                path_part, lineno, content = line.split(":", 2)
            except ValueError:
                continue
            try:
                lineno_int = int(lineno)
            except ValueError:
                continue
            if path_part.startswith(self.root + "/"):
                path_part = path_part[len(self.root) + 1 :]
            out.append(GrepMatch(path=path_part, line_number=lineno_int, line=content))
        return out

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
        # `cwd` is currently a no-op for sandbox sessions — the SDK treats
        # the workspace root as the working directory. To run from a
        # different cwd, prefix with `cd <dir> &&` in the command itself.
        # `env` similarly isn't a session-level concept — env vars are part
        # of the manifest. Accept both for API compatibility but warn if
        # someone passes them.
        _ = env
        effective_user = user if user is not None else self._default_user
        if cwd:
            # Prefix shell commands with cd; for non-shell commands, this
            # would change the semantics, so refuse.
            if not shell:
                raise WorkspaceFeatureUnavailable(
                    "SandboxWorkspace.exec(cwd=..., shell=False) isn't "
                    "supported. Use shell=True or include the cd in the "
                    "command list."
                )
            cwd_resolved = self.resolve(cwd)
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            command = f"cd {shlex.quote(cwd_resolved)} && {cmd_str}"

        start = time.perf_counter()
        if shell:
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            result = await self._session.exec(
                cmd_str, shell=True, user=effective_user, timeout=timeout
            )
        else:
            args = [command] if isinstance(command, str) else list(command)
            result = await self._session.exec(
                *args, shell=False, user=effective_user, timeout=timeout
            )
        wall = time.perf_counter() - start

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        exit_code = result.exit_code if result.exit_code is not None else -1
        truncated = False
        if max_output_bytes is not None:
            if len(result.stdout) > max_output_bytes:
                stdout = result.stdout[:max_output_bytes].decode(
                    "utf-8", errors="replace"
                )
                truncated = True
            if len(result.stderr) > max_output_bytes:
                stderr = result.stderr[:max_output_bytes].decode(
                    "utf-8", errors="replace"
                )
                truncated = True
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
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
        """Start a long-running command inside the sandbox.

        Both ``pty=False`` and ``pty=True`` are supported. Under the hood
        we always use ``session.pty_exec_start`` because that's the only
        sandbox API that returns a tracked process handle (one-shot
        ``session.exec`` blocks until completion). With ``pty=False`` we
        ask the session not to allocate a TTY — the caller gets a
        ProcessHandle whose ``logs()``/``terminate()``/``status()`` shape
        matches the host backend, but stdin still works through
        ``pty_write_stdin``.

        Raises :class:`WorkspaceFeatureUnavailable` if the session
        backend can't track long-running processes (``supports_pty()``
        returned ``False``).
        """
        if not self._session.supports_pty():
            raise WorkspaceFeatureUnavailable(
                "This sandbox session doesn't support tracked background "
                "processes (supports_pty() returned False). Use exec() for "
                "one-shot calls."
            )
        _ = env  # not session-level
        effective_user = user if user is not None else self._default_user
        if cwd:
            cwd_resolved = self.resolve(cwd)
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            command = f"cd {shlex.quote(cwd_resolved)} && {cmd_str}"

        if shell:
            cmd_str = command if isinstance(command, str) else shlex.join(command)
            update = await self._session.pty_exec_start(
                cmd_str, shell=True, tty=pty, user=effective_user, yield_time_s=0.0
            )
        else:
            args = [command] if isinstance(command, str) else list(command)
            update = await self._session.pty_exec_start(
                *args, shell=False, tty=pty, user=effective_user, yield_time_s=0.0
            )
        return _SandboxProcessHandle(session=self._session, update=update)


class _SandboxProcessHandle(ProcessHandle):
    """ProcessHandle backed by a sandbox PTY session."""

    def __init__(self, session: Any, update: Any) -> None:
        self.process_id = uuid.uuid4().hex[:12]
        self._session = session
        self._session_id: int = update.process_id
        self._buffer = bytearray(getattr(update, "output", b""))
        self._exit_code: Optional[int] = update.exit_code
        self._start = time.perf_counter()

    async def status(
        self,
    ) -> Literal["running", "completed", "killed", "failed"]:
        if self._exit_code is None:
            return "running"
        if self._exit_code == 0:
            return "completed"
        if self._exit_code < 0:
            return "killed"
        return "failed"

    async def logs(self, *, tail_lines: Optional[int] = None) -> str:
        text = self._buffer.decode("utf-8", errors="replace")
        if tail_lines is not None:
            lines = text.splitlines()
            text = "\n".join(lines[-tail_lines:])
        return text

    async def write_stdin(self, data: str) -> None:
        update = await self._session.pty_write_stdin(
            session_id=self._session_id, chars=data, yield_time_s=0.0
        )
        if getattr(update, "output", None):
            self._buffer.extend(update.output)
        self._exit_code = update.exit_code

    async def wait(self, timeout: Optional[float] = None) -> ExecResult:
        # Sandbox PTY doesn't have a clean "wait until done" — poll via
        # zero-stdin reads which return when output is available or process
        # exits. For now: do a single best-effort read; callers that need
        # strict wait semantics should poll.
        update = await self._session.pty_write_stdin(
            session_id=self._session_id,
            chars="",
            yield_time_s=timeout or 0.0,
        )
        if getattr(update, "output", None):
            self._buffer.extend(update.output)
        self._exit_code = update.exit_code
        return ExecResult(
            stdout=self._buffer.decode("utf-8", errors="replace"),
            stderr="",
            exit_code=self._exit_code if self._exit_code is not None else -1,
            wall_time_seconds=time.perf_counter() - self._start,
        )

    async def terminate(self, *, force_after: float = 5.0) -> None:
        # Send Ctrl-C, then ask the session to terminate the PTY entry.
        try:
            await self._session.pty_write_stdin(
                session_id=self._session_id, chars="\x03", yield_time_s=0.0
            )
        except Exception:
            pass
        # SDK's pty_terminate_all kills every PTY in the session — too
        # broad for a single-process terminate. Best-effort with Ctrl-C
        # only for now; a finer-grained terminate is a follow-up if we
        # ship multi-job sandbox runs.
        _ = force_after
