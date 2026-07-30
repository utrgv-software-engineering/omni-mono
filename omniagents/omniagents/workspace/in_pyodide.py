"""In-browser Workspace, for Python already running inside Pyodide.

Not to be confused with :mod:`omniagents.workspace.pyodide`, which is the
mirror image: that one runs on a host and *drives* a Pyodide interpreter
in a Node subprocess. This one is used when the interpreter running the
agent IS Pyodide — a browser tab, a Web Worker, a teaching platform — and
the "filesystem" is Emscripten's in-memory FS reachable through ordinary
``pathlib``.

Why :class:`~omniagents.workspace.host.HostWorkspace` cannot be used
there: every one of its methods hands the blocking stdlib call to
``asyncio.to_thread``, and Pyodide has no threads. The calls would raise
rather than run. Since MEMFS operations are memory-speed anyway, this
backend simply performs them inline — there is nothing to move off the
event loop.

What works: all file I/O, ``list_dir``, ``glob``, ``grep`` — same
semantics as ``HostWorkspace``, so the builtin filesystem tools
(``read_file``, ``write_file``, ``edit_file``, ``glob_files``,
``grep_files``) behave identically in a browser.

What doesn't: ``exec`` and ``spawn``. There is no shell in a browser tab,
so both raise :class:`WorkspaceFeatureUnavailable` and
:attr:`supports_shell` is ``False`` — which is the documented signal that
lets ``execute_bash`` degrade with a clear message instead of hanging.

Files live in the Pyodide FS, which is memory: they vanish when the page
reloads unless the host has mounted something persistent (IDBFS/NODEFS).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Literal, Optional

from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

_NO_SHELL = (
    "This workspace runs inside the browser, where there is no shell. "
    "Use the file tools instead of shell commands."
)


class InPyodideWorkspace(Workspace):
    """Workspace backed by the Pyodide (Emscripten) filesystem.

    Args:
        root: workspace root. Created if missing. Defaults to the cwd.
        scratch_root: framework-owned scratch dir, kept outside ``root``.
        confine: when True (default) absolute paths outside ``root`` are
            rejected, matching ``HostWorkspace``'s default.
    """

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        scratch_root: Optional[str] = None,
        confine: bool = True,
    ) -> None:
        resolved = Path(root or os.getcwd())
        resolved.mkdir(parents=True, exist_ok=True)
        self.root = str(resolved)
        self.confine = confine

        scratch = Path(scratch_root or os.path.join(self.root, ".omniagents-scratch"))
        scratch.mkdir(parents=True, exist_ok=True)
        self.scratch_root = str(scratch)

    # ----------------------------- helpers ----------------------------- #

    def resolve(self, path: str) -> str:
        if not path or path == ".":
            return self.root
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else Path(self.root) / candidate
        # Normalize without touching the filesystem: Pyodide's MEMFS has no
        # symlinks to chase, and Path.resolve() on a missing path is fine.
        normalized = Path(os.path.normpath(str(absolute)))
        if self.confine:
            root = Path(self.root)
            if normalized != root and root not in normalized.parents:
                raise WorkspaceEscapeError(
                    f"{path!r} resolves outside the workspace root {self.root!r}"
                )
        return str(normalized)

    def _display_path(self, p: Path) -> str:
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()

    # ----------------------------- file I/O ----------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(self.resolve(path)).read_text(encoding=encoding)

    async def read_bytes(self, path: str) -> bytes:
        return Path(self.resolve(path)).read_bytes()

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
            absolute.parent.mkdir(parents=True, exist_ok=True)
        with open(absolute, "a" if append else "w", encoding=encoding) as handle:
            handle.write(content)

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
            absolute.parent.mkdir(parents=True, exist_ok=True)
        with open(absolute, "ab" if append else "wb") as handle:
            handle.write(data)

    async def exists(self, path: str) -> bool:
        return Path(self.resolve(path)).exists()

    async def is_file(self, path: str) -> bool:
        return Path(self.resolve(path)).is_file()

    async def is_dir(self, path: str) -> bool:
        return Path(self.resolve(path)).is_dir()

    async def mkdir(
        self, path: str, *, parents: bool = True, exist_ok: bool = True
    ) -> None:
        Path(self.resolve(path)).mkdir(parents=parents, exist_ok=exist_ok)

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        import shutil

        absolute = Path(self.resolve(path))
        if absolute.is_dir():
            if recursive:
                shutil.rmtree(absolute)
            else:
                absolute.rmdir()
        else:
            absolute.unlink()

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
        # there is no git in the browser, so it is a no-op here.
        _ = respect_gitignore
        absolute = Path(self.resolve(path))
        if not absolute.is_dir():
            return []

        entries: List[FileEntry] = []
        for entry in absolute.iterdir():
            if not include_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=entry.name,
                    path=self._display_path(entry),
                    is_dir=entry.is_dir(),
                    size=stat.st_size if entry.is_file() else None,
                    mtime=datetime.fromtimestamp(stat.st_mtime),
                    is_symlink=entry.is_symlink(),
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
        absolute = Path(self.resolve(root))
        if not absolute.is_dir():
            return []
        iterator = absolute.rglob(pattern) if recursive else absolute.glob(pattern)
        results: List[str] = []
        for match in iterator:
            if not include_hidden and any(
                part.startswith(".") for part in match.relative_to(absolute).parts
            ):
                continue
            results.append(self._display_path(match))
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
        absolute = Path(self.resolve(root))
        if not absolute.is_dir():
            return []

        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags)
        matches: List[GrepMatch] = []
        for candidate in sorted(absolute.rglob("*")):
            if not candidate.is_file():
                continue
            if not fnmatch(candidate.name, file_pattern):
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
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
        return False

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
        raise WorkspaceFeatureUnavailable(_NO_SHELL)

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
        raise WorkspaceFeatureUnavailable(_NO_SHELL)
