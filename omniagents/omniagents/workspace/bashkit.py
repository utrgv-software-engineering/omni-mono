"""Workspace backed by bashkit — a real shell where there was none.

:class:`~omniagents.workspace.in_pyodide.InPyodideWorkspace` gives a browser
tab file I/O but no ``exec``: its docstring says there is no shell in a
browser, which was true when it was written. bashkit is a bash interpreter
compiled to WebAssembly, so that premise no longer holds. This backend is
the consequence — the full ABC, ``supports_shell = True``, and
``execute_bash`` working in a tab.

**bashkit's VFS is the workspace.** That is the whole design. bashkit
carries its own filesystem, written in Rust inside the wasm module, and it
is *not* the Emscripten filesystem that ``pathlib`` sees. Splitting the two
— file methods on ``pathlib``, ``exec`` on bashkit — produces a workspace
whose own tools cannot see each other's effects::

    await ws.write_text("data.csv", rows)   # Emscripten FS
    await ws.exec("wc -l data.csv")         # bashkit VFS: No such file

So every method here, file I/O included, goes through the same
:meth:`Bash.fs` handle the shell runs against. There is exactly one
filesystem and no syncing.

Moving data between this workspace and Pyodide's own filesystem is
therefore an explicit copy at the edges — starter files in, results out —
not something the workspace does implicitly:

    ws.bash.write_file("/workspace/main.py", Path("main.py").read_text())
    Path("out.txt").write_text(ws.bash.fs().read_file("/workspace/out.txt").decode())

A zero-copy view is not available: ``FileSystem.real()`` is compiled out of
the wasm build, a browser having no host filesystem to expose.

What you get beyond a shell: bashkit is provisioned (``files=``), resource
limited (``max_commands``, ``max_memory``, ``timeout_seconds``,
``readonly_filesystem``) and snapshottable (``snapshot()`` /
``restore_snapshot()``), so this doubles as the in-browser sandbox — with
isolation stronger than a subprocess, since there is no host to escape to.

Not provided: ``spawn`` (bashkit runs commands to completion; there is no
process model to hold a handle on), ``git``, and the network.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import shlex
import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

# bashkit's stat/read_dir report directories as "directory" (not "dir") and
# symlinks as "symlink". Both spellings are accepted so a rename upstream
# degrades to a miss rather than silently classifying every dir as a file.
_DIR_TYPES = {"directory", "dir"}

_NO_SPAWN = (
    "bashkit runs commands to completion and exposes no process handle, so "
    "background processes are unavailable. Use exec() instead."
)


class BashkitWorkspace(Workspace):
    """Workspace whose filesystem and shell are both a bashkit interpreter.

    Args:
        bash: an existing ``bashkit.Bash`` to adopt. Pass one when the
            shell must be shared with other code — a lesson page where the
            student's own commands and the agent's tools operate on the
            same files. Omit it and one is created.
        root: workspace root inside the VFS. Created if missing.
        scratch_root: framework-owned scratch dir, kept outside ``root``.
        confine: when True (default) paths outside ``root`` are rejected,
            matching ``HostWorkspace``.
        **bash_kwargs: forwarded to ``Bash()`` when constructing one
            (``python``, ``files``, ``env``, ``max_memory``, ...).
    """

    def __init__(
        self,
        bash: Any = None,
        *,
        root: str = "/workspace",
        scratch_root: Optional[str] = None,
        confine: bool = True,
        **bash_kwargs: Any,
    ) -> None:
        if bash is None:
            from bashkit import Bash

            bash_kwargs.setdefault("python", True)
            bash = Bash(**bash_kwargs)
        elif bash_kwargs:
            raise TypeError(
                "bash_kwargs are only used when constructing a Bash; "
                "configure the instance you pass in instead"
            )

        self.bash = bash
        self.root = posixpath.normpath(root)
        self.confine = confine
        self.scratch_root = posixpath.normpath(
            scratch_root or "/tmp/omniagents/scratch"
        )

        fs = self._fs()
        for directory in (self.root, self.scratch_root):
            if not fs.exists(directory):
                fs.mkdir(directory, True)

    # ----------------------------- helpers ----------------------------- #

    def _fs(self):
        # Documented as a live handle: every operation takes the interpreter
        # lock, so it stays correct across reset() and shell writes.
        return self.bash.fs()

    def resolve(self, path: str) -> str:
        if not path or path == ".":
            return self.root
        absolute = path if posixpath.isabs(path) else posixpath.join(self.root, path)
        normalized = posixpath.normpath(absolute)
        if self.confine:
            if normalized != self.root and not normalized.startswith(self.root + "/"):
                raise WorkspaceEscapeError(
                    f"{path!r} resolves outside the workspace root {self.root!r}"
                )
        return normalized

    def _display_path(self, absolute: str) -> str:
        if absolute == self.root:
            return "."
        prefix = self.root.rstrip("/") + "/"
        return absolute[len(prefix):] if absolute.startswith(prefix) else absolute

    def _file_type(self, path: str) -> Optional[str]:
        fs = self._fs()
        if not fs.exists(path):
            return None
        try:
            return fs.stat(path).get("file_type")
        except Exception:
            return None

    @staticmethod
    def _as_datetime(value: Any) -> Optional[datetime]:
        """bashkit stat times: epoch seconds, or something already datetime-ish."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value)
            except (OverflowError, OSError, ValueError):
                return None
        return None

    def _walk(self, start: str) -> List[tuple]:
        """``(absolute_path, is_dir)`` for everything under ``start``.

        Directories are included because ``Path.rglob`` yields them and
        ``glob`` must match the other backends; ``grep`` filters them out.
        """
        fs = self._fs()
        found: List[tuple] = []
        pending = [start]
        while pending:
            current = pending.pop()
            try:
                entries = fs.read_dir(current)
            except Exception:
                continue
            for entry in entries:
                name = entry.get("name")
                if not name:
                    continue
                absolute = posixpath.join(current, name)
                metadata = entry.get("metadata") or {}
                is_dir = metadata.get("file_type") in _DIR_TYPES
                found.append((absolute, is_dir))
                if is_dir:
                    pending.append(absolute)
        return found

    # ----------------------------- file I/O ----------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return self._fs().read_file(self.resolve(path)).decode(encoding)

    async def read_bytes(self, path: str) -> bytes:
        return self._fs().read_file(self.resolve(path))

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
            path, content.encode(encoding), append=append, create_dirs=create_dirs
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
        fs = self._fs()
        if create_dirs:
            parent = posixpath.dirname(absolute)
            if parent and not fs.exists(parent):
                fs.mkdir(parent, True)
        if append and fs.exists(absolute):
            fs.append_file(absolute, data)
        else:
            fs.write_file(absolute, data)

    async def exists(self, path: str) -> bool:
        return self._fs().exists(self.resolve(path))

    async def is_file(self, path: str) -> bool:
        return self._file_type(self.resolve(path)) == "file"

    async def is_dir(self, path: str) -> bool:
        return self._file_type(self.resolve(path)) in _DIR_TYPES

    async def mkdir(
        self, path: str, *, parents: bool = True, exist_ok: bool = True
    ) -> None:
        absolute = self.resolve(path)
        fs = self._fs()
        if fs.exists(absolute):
            if exist_ok:
                return
            raise FileExistsError(absolute)
        fs.mkdir(absolute, parents)

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        self._fs().remove(self.resolve(path), recursive)

    # ---------------------------- discovery ----------------------------- #

    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        # respect_gitignore is accepted for parity with the other backends;
        # bashkit has no git, so it is a no-op here.
        _ = respect_gitignore
        absolute = self.resolve(path)
        if self._file_type(absolute) not in _DIR_TYPES:
            return []

        entries: List[FileEntry] = []
        for entry in self._fs().read_dir(absolute):
            name = entry.get("name")
            if not name or (not include_hidden and name.startswith(".")):
                continue
            metadata = entry.get("metadata") or {}
            is_dir = metadata.get("file_type") in _DIR_TYPES
            entries.append(
                FileEntry(
                    name=name,
                    path=self._display_path(posixpath.join(absolute, name)),
                    is_dir=is_dir,
                    size=None if is_dir else metadata.get("size"),
                    mtime=self._as_datetime(metadata.get("modified")),
                    is_symlink=metadata.get("file_type") == "symlink",
                )
            )
        keyfn = {
            "name": lambda e: e.name,
            "mtime": lambda e: (e.mtime or datetime.min, e.name),
            "size": lambda e: (-(e.size or 0), e.name),
        }[sort_by]
        entries.sort(key=keyfn)
        return entries

    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]:
        # Matched in Python rather than by the shell so the semantics are the
        # ones every other backend has (Path.glob/rglob), not bash's.
        absolute = self.resolve(root)
        if self._file_type(absolute) not in _DIR_TYPES:
            return []

        results: List[str] = []
        for candidate, _is_dir in self._walk(absolute):
            relative = candidate[len(absolute.rstrip("/")) + 1:]
            if not include_hidden and any(
                part.startswith(".") for part in relative.split("/")
            ):
                continue
            if recursive:
                matched = fnmatch.fnmatch(
                    relative, pattern
                ) or fnmatch.fnmatch(posixpath.basename(relative), pattern)
            else:
                matched = "/" not in relative and fnmatch.fnmatch(relative, pattern)
            if matched:
                results.append(self._display_path(candidate))
        results.sort()
        return results

    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]:
        # context_lines is reserved on GrepMatch across backends.
        _ = context_lines
        absolute = self.resolve(root)
        if self._file_type(absolute) not in _DIR_TYPES:
            return []

        compiled = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        fs = self._fs()
        matches: List[GrepMatch] = []
        for candidate, is_dir in sorted(self._walk(absolute)):
            if is_dir:
                continue
            if not fnmatch.fnmatch(posixpath.basename(candidate), file_pattern):
                continue
            try:
                text = fs.read_file(candidate).decode("utf-8", errors="replace")
            except Exception:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                found = compiled.search(line)
                if found:
                    matches.append(
                        GrepMatch(
                            path=self._display_path(candidate),
                            line_number=number,
                            line=line,
                            column=found.start(),
                        )
                    )
        return matches

    # ------------------------- shell execution -------------------------- #

    @property
    def supports_shell(self) -> bool:
        return True

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
        # `user` is meaningless here - a bashkit interpreter is single-user
        # and there is no host identity to drop to.
        _ = shell, user
        script = command if isinstance(command, str) else shlex.join(command)

        prelude = []
        if env:
            prelude += [f"export {k}={shlex.quote(v)}" for k, v in env.items()]
        if cwd:
            prelude.append(f"cd {shlex.quote(self.resolve(cwd))}")
        elif self.root:
            prelude.append(f"cd {shlex.quote(self.root)}")
        full = "\n".join(prelude + [script])

        # `timeout` cannot be honored per call: bashkit's budget
        # (timeout_seconds, max_commands, max_loop_iterations) is fixed when
        # the interpreter is built. Wrapping in asyncio.wait_for would return
        # control while the interpreter kept running, which is worse than
        # being honest. Set the budget on the Bash instance instead.
        _ = timeout

        started = time.monotonic()
        # The wasm build ships execute_sync but not the async execute (async
        # needs a runtime that Emscripten does not give it), so the browser
        # path has to take the sync call. Blocking is not a concern there:
        # bashkit is in-memory, and Pyodide has no threads for an async
        # variant to yield to anyway.
        run_async = getattr(self.bash, "execute", None)
        if run_async is not None:
            result = await run_async(full)
        else:
            result = self.bash.execute_sync(full)
        elapsed = time.monotonic() - started

        stdout, stderr = result.stdout, result.stderr
        truncated = False
        if max_output_bytes is not None:
            if len(stdout.encode()) > max_output_bytes:
                stdout = stdout.encode()[:max_output_bytes].decode(errors="ignore")
                truncated = True
            if len(stderr.encode()) > max_output_bytes:
                stderr = stderr.encode()[:max_output_bytes].decode(errors="ignore")
                truncated = True

        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            wall_time_seconds=elapsed,
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
        raise WorkspaceFeatureUnavailable(_NO_SPAWN)
