"""Pyodide-backed Workspace.

Implements the :class:`Workspace` ABC against a long-lived Node subprocess
running ``pyodide_worker/worker.mjs``. The worker owns a single pyodide
interpreter and serves newline-delimited JSON-RPC over stdio; this class
translates Workspace method calls into ops.

Why a subprocess and not a binding library: pyodide is wasm and must run in
a JS host. The only Python-side option is to shell out to one — Node here
because it's the only JS host that exposes the host filesystem via NODEFS.

What works:
    * All file I/O (read/write/list/stat/mkdir/remove) — direct ``FS.*`` calls
      on the worker, base64'd over stdio.
    * ``glob``/``grep`` — composed in Python via ``exec_python``, reusing the
      same ``re``/``fnmatch`` semantics as ``HostWorkspace``.
    * ``run_python`` (extra primitive; not on the ABC) — code-interpreter
      style execution against the same interpreter, with stdout/stderr
      capture. Globals persist across calls; use ``reset_interpreter()`` to
      drop them.

What doesn't:
    * ``exec`` / ``spawn`` — there is no shell. Both raise
      :class:`WorkspaceFeatureUnavailable` so callers (e.g. ``bash_tool``)
      can degrade gracefully instead of hanging.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import itertools
import json
import os
import posixpath
import shutil
import sys
from datetime import datetime
from importlib import resources as importlib_resources
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Sequence

from ._entries import log_skipped_entry, make_entry, sort_entries
from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceError,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

# --------------------------------------------------------------------------- #
# Worker client                                                               #
# --------------------------------------------------------------------------- #


class PyodideWorkerError(WorkspaceError):
    """Raised when the worker reports an error or exits unexpectedly."""

    def __init__(
        self, message: str, *, op: Optional[str] = None, errno: Optional[int] = None
    ) -> None:
        super().__init__(message)
        self.op = op
        self.errno = errno


class _PyodideWorker:
    """Async client owning one ``worker.mjs`` subprocess.

    One request → one response; ops are serialized in submission order on
    both sides (the worker chains its dispatch through a single Promise
    chain). Bytes payloads are base64 to keep stdio text-clean.
    """

    def __init__(
        self,
        *,
        host_workspace_dir: Optional[str],
        node_path: str = "node",
        worker_script: Optional[str] = None,
        packages: Sequence[str] = (),
        snapshot_path: Optional[str] = None,
    ) -> None:
        self._host_workspace_dir = host_workspace_dir
        self._node_path = node_path
        self._worker_script = worker_script or _default_worker_script()
        self._packages = tuple(packages)
        self._snapshot_path = snapshot_path

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._pending: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._id_counter = itertools.count(1)
        self._write_lock = asyncio.Lock()
        self._ready: Optional[asyncio.Future[Dict[str, Any]]] = None
        self._closed = False

    # ---- lifecycle ------------------------------------------------------- #

    async def start(self) -> None:
        if self._proc is not None:
            return
        if not Path(self._worker_script).is_file():
            raise PyodideWorkerError(
                f"worker script not found: {self._worker_script!r}. "
                "Did you `npm install` in omniagents/workspace/pyodide_worker?"
            )

        args = [self._node_path, self._worker_script]
        if self._host_workspace_dir:
            args.append(f"--workspace={self._host_workspace_dir}")
        if self._packages:
            args.append(f"--packages={','.join(self._packages)}")
        if self._snapshot_path:
            args.append(f"--snapshot={self._snapshot_path}")

        # cwd = the worker script dir so `import { loadPyodide } from "pyodide"`
        # resolves against the colocated node_modules.
        cwd = str(Path(self._worker_script).parent)
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,  # let worker logs surface directly
            cwd=cwd,
        )

        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="pyodide-worker-reader"
        )

        # Wait for the worker's one-line "ready" handshake.
        ready_payload = await asyncio.wait_for(self._ready, timeout=120.0)
        if not ready_payload.get("ok"):
            raise PyodideWorkerError(
                f"worker failed to bootstrap: {ready_payload.get('error', {})}"
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        finally:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Fail any still-pending futures so callers don't hang.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PyodideWorkerError("worker shut down"))
            self._pending.clear()

    # ---- I/O loop -------------------------------------------------------- #

    async def _reader_loop(self) -> None:
        assert self._proc is not None
        stdout = self._proc.stdout
        assert stdout is not None
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    # Log and ignore; worker stderr will have details.
                    continue
                msg_id = msg.get("id")
                if (
                    msg.get("ready")
                    and self._ready is not None
                    and not self._ready.done()
                ):
                    self._ready.set_result(msg)
                    continue
                if msg_id is None:
                    # Unsolicited; nothing to dispatch.
                    continue
                fut = self._pending.pop(int(msg_id), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        finally:
            # Wake the readiness handshake if the worker died before signalling.
            if self._ready is not None and not self._ready.done():
                self._ready.set_result(
                    {"ok": False, "error": {"message": "worker exited before ready"}}
                )

    # ---- request/response ----------------------------------------------- #

    async def call(
        self,
        op: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self._proc is None:
            raise PyodideWorkerError("worker not started")
        if self._proc.returncode is not None:
            raise PyodideWorkerError(f"worker exited with code {self._proc.returncode}")

        loop = asyncio.get_running_loop()
        req_id = next(self._id_counter)
        fut: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        payload = (
            json.dumps(
                {"id": req_id, "op": op, "params": params or {}}, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )
        async with self._write_lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(payload)
            await self._proc.stdin.drain()

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise PyodideWorkerError(
                err.get("message", "unknown worker error"),
                op=op,
                errno=err.get("errno"),
            )
        return resp.get("result", {})


def _default_worker_script() -> str:
    """Find ``worker.mjs`` whether running from source or an installed wheel."""
    try:
        path = (
            importlib_resources.files("omniagents.workspace.pyodide_worker")
            / "worker.mjs"
        )
        return str(path)
    except Exception:
        # Fallback: alongside this file at source layout.
        return str(Path(__file__).parent / "pyodide_worker" / "worker.mjs")


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


_NOT_FOUND_ERRNOS = {errno.ENOENT}


def _is_not_found(err: PyodideWorkerError) -> bool:
    if err.errno in _NOT_FOUND_ERRNOS:
        return True
    msg = str(err).lower()
    return "no such file" in msg or "enoent" in msg


_NOT_A_DIRECTORY_ERRNOS = {errno.ENOTDIR}


def _is_not_a_directory(err: PyodideWorkerError) -> bool:
    if err.errno in _NOT_A_DIRECTORY_ERRNOS:
        return True
    msg = str(err).lower()
    return "not a directory" in msg or "enotdir" in msg


_PERMISSION_ERRNOS = {errno.EACCES, errno.EPERM}


def _is_permission_denied(err: PyodideWorkerError) -> bool:
    """Whether the worker refused an operation for permission reasons.

    Emscripten's ``FS.ErrnoError`` carries a real ``errno`` and the worker
    forwards it, so this backend can honor the ``PermissionError`` arm of
    the :meth:`Workspace.list_dir` contract rather than being the one
    substrate that cannot.
    """
    if err.errno in _PERMISSION_ERRNOS:
        return True
    msg = str(err).lower()
    return "permission denied" in msg or "eacces" in msg or "eperm" in msg


# Pure-Python composite ops, executed inside the worker via exec_python.
# Returning structured JSON keeps the protocol uniform.
_GLOB_CODE = """
import json, fnmatch, os
from pathlib import Path
_root = Path(__params__["root"])
_pattern = __params__["pattern"]
_recursive = __params__["recursive"]
_include_hidden = __params__["include_hidden"]
_workspace_root = __params__["workspace_root"]
if not _root.is_dir():
    _out = []
else:
    _iter = _root.rglob(_pattern) if _recursive else _root.glob(_pattern)
    _out = []
    for _p in _iter:
        _rel = _p.relative_to(_workspace_root).as_posix()
        if not _include_hidden and any(part.startswith(".") for part in Path(_rel).parts):
            continue
        _out.append(_rel)
    _out.sort()
print(json.dumps(_out))
"""

_GREP_CODE = """
import json, re, os
from fnmatch import fnmatch
from pathlib import Path
_root = Path(__params__["root"])
_workspace_root = __params__["workspace_root"]
_pattern = __params__["pattern"]
_file_pattern = __params__["file_pattern"]
_case = __params__["case_sensitive"]
_flags = 0 if _case else re.IGNORECASE
_regex = re.compile(_pattern, _flags)
_out = []
if _root.is_dir():
    for _p in _root.rglob("*"):
        if not _p.is_file():
            continue
        if not fnmatch(_p.name, _file_pattern):
            continue
        _rel = _p.relative_to(_workspace_root).as_posix()
        try:
            _text = _p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _i, _line in enumerate(_text.splitlines(), start=1):
            _m = _regex.search(_line)
            if _m:
                _out.append({"path": _rel, "line_number": _i, "line": _line, "column": _m.start() + 1})
print(json.dumps(_out))
"""


# --------------------------------------------------------------------------- #
# Workspace implementation                                                    #
# --------------------------------------------------------------------------- #


class PyodideWorkspace(Workspace):
    """Workspace backed by a pyodide interpreter running inside Node.

    The workspace root inside the pyodide FS is always ``/workspace``.  When
    *host_workspace_dir* is set, that directory is NODEFS-mounted so file
    changes round-trip to the host disk automatically.  When omitted, the
    workspace is MEMFS-only (ephemeral, lost on subprocess exit).

    Args:
        host_workspace_dir: Host directory to NODEFS-mount as ``/workspace``.
            ``None`` → ephemeral MEMFS root.
        node_path: ``node`` executable to spawn. Defaults to whatever is on PATH.
        packages: Pyodide-built packages to pre-load (e.g. ``["numpy"]``).
        snapshot_path: Optional path to a pyodide heap snapshot for fast
            warm starts. Pyodide builds without ``makeMemorySnapshot`` will
            ignore writes silently.
        scratch_root: Override for the framework scratch dir. Defaults to
            ``$OMNIAGENTS_SCRATCH`` or ``~/.omniagents/scratch``.
    """

    def __init__(
        self,
        *,
        host_workspace_dir: Optional[str] = None,
        node_path: str = "node",
        packages: Sequence[str] = (),
        snapshot_path: Optional[str] = None,
        scratch_root: Optional[str] = None,
        worker: Optional[_PyodideWorker] = None,
    ) -> None:
        # Pyodide FS root is fixed — that's where the worker mounts NODEFS
        # (or just creates the MEMFS dir). Keep it stable so tool path
        # arithmetic doesn't depend on the host dir.
        self.root = "/workspace"

        scratch = (
            scratch_root
            or os.environ.get("OMNIAGENTS_SCRATCH")
            or os.path.join(os.path.expanduser("~"), ".omniagents", "scratch")
        )
        self.scratch_root = str(Path(scratch).expanduser().resolve())

        self._host_workspace_dir = (
            str(Path(host_workspace_dir).expanduser().resolve())
            if host_workspace_dir
            else None
        )
        self._worker = worker or _PyodideWorker(
            host_workspace_dir=self._host_workspace_dir,
            node_path=node_path,
            packages=packages,
            snapshot_path=snapshot_path,
        )
        self._started = False
        self._start_lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------- #

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self._worker.start()
            self._started = True

    async def aclose(self) -> None:
        await self._worker.close()
        self._started = False

    async def __aenter__(self) -> "PyodideWorkspace":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    # ---- path resolution ------------------------------------------------- #

    def resolve(self, path: str) -> str:
        if not path or path == ".":
            return self.root
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            full = PurePosixPath(os.path.normpath(str(candidate)))
        else:
            full = PurePosixPath(
                os.path.normpath(str(PurePosixPath(self.root) / candidate))
            )
        # Defense in depth — disallow escape.
        try:
            full.relative_to(self.root)
        except ValueError:
            raise WorkspaceEscapeError(
                f"Path {path!r} resolves to {full!r}, outside workspace root {self.root!r}"
            ) from None
        return str(full)

    # ---- file I/O -------------------------------------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        data = await self.read_bytes(path)
        return data.decode(encoding, errors="replace")

    async def read_bytes(self, path: str) -> bytes:
        await self._ensure_started()
        absolute = self.resolve(path)
        try:
            result = await self._worker.call("fs_read", {"path": absolute})
        except PyodideWorkerError as e:
            if _is_not_found(e):
                raise FileNotFoundError(absolute) from e
            raise
        return base64.b64decode(result["bytes_b64"])

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
        await self._ensure_started()
        absolute = self.resolve(path)
        params = {
            "path": absolute,
            "bytes_b64": base64.b64encode(data).decode("ascii"),
            "create_dirs": create_dirs,
        }
        op = "fs_append" if append else "fs_write"
        await self._worker.call(op, params)

    async def exists(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        await self._ensure_started()
        result = await self._worker.call("fs_exists", {"path": absolute})
        return bool(result.get("exists"))

    async def is_file(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        await self._ensure_started()
        try:
            result = await self._worker.call("fs_lstat", {"path": absolute})
        except PyodideWorkerError as e:
            if _is_not_found(e):
                return False
            raise
        return bool(result.get("is_file"))

    async def is_dir(self, path: str) -> bool:
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            return False
        await self._ensure_started()
        try:
            result = await self._worker.call("fs_lstat", {"path": absolute})
        except PyodideWorkerError as e:
            if _is_not_found(e):
                return False
            raise
        return bool(result.get("is_dir"))

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> None:
        await self._ensure_started()
        absolute = self.resolve(path)
        try:
            await self._worker.call("fs_mkdir", {"path": absolute, "parents": parents})
        except PyodideWorkerError as e:
            if exist_ok and ("EEXIST" in str(e) or "exists" in str(e).lower()):
                return
            raise

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        await self._ensure_started()
        absolute = self.resolve(path)
        try:
            stat = await self._worker.call("fs_lstat", {"path": absolute})
        except PyodideWorkerError as e:
            if _is_not_found(e):
                return  # idempotent — matches HostWorkspace
            raise
        if stat.get("is_dir"):
            if recursive:
                await self._worker.call("fs_rmtree", {"path": absolute})
            else:
                await self._worker.call("fs_rmdir", {"path": absolute})
        else:
            await self._worker.call("fs_unlink", {"path": absolute})

    # ---- discovery ------------------------------------------------------- #

    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        _ = respect_gitignore  # parity with HostWorkspace — no git in pyodide
        await self._ensure_started()
        absolute = self.resolve(path)
        try:
            result = await self._worker.call("fs_readdir", {"path": absolute})
        except PyodideWorkerError as e:
            # OSError-compatible, like every other backend: an empty list
            # would make "missing directory" indistinguishable from "empty
            # directory".
            if _is_not_found(e):
                raise FileNotFoundError(absolute) from e
            if _is_not_a_directory(e):
                raise NotADirectoryError(absolute) from e
            if _is_permission_denied(e):
                raise PermissionError(absolute) from e
            raise OSError(f"{absolute}: {e}") from e

        out: List[FileEntry] = []
        for entry in result.get("entries", []):
            name = str(entry.get("name", ""))
            if not include_hidden and name.startswith("."):
                continue
            child_abs = (
                absolute.rstrip("/") + "/" + name if absolute != "/" else "/" + name
            )
            mtime_raw = entry.get("mtime")
            mtime = (
                datetime.fromtimestamp(mtime_raw)
                if isinstance(mtime_raw, (int, float)) and mtime_raw > 0
                else None
            )
            # The worker stats with ``FS.lstat``, so ``is_dir`` already
            # describes the entry itself rather than a symlink target —
            # exactly the shared contract.
            try:
                out.append(
                    make_entry(
                        name=name,
                        path=posixpath.relpath(child_abs, self.root),
                        is_dir=bool(entry.get("is_dir")),
                        size=entry.get("size"),
                        mtime=mtime,
                        is_symlink=bool(entry.get("is_symlink")),
                    )
                )
            except ValueError as error:
                log_skipped_entry("PyodideWorkspace", absolute, error)
                continue
        return sort_entries(out, sort_by)

    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]:
        await self._ensure_started()
        absolute = self.resolve(root)
        params = {
            "root": absolute,
            "pattern": pattern,
            "recursive": recursive,
            "include_hidden": include_hidden,
            "workspace_root": self.root,
        }
        # The worker exposes a single exec_python that compiles the snippet —
        # we inject params as a Python dict named ``__params__`` via a small
        # prelude. Cheaper than a per-op JSON parse on the JS side.
        code = _wrap_with_params(_GLOB_CODE, params)
        result = await self._worker.call(
            "exec_python", {"code": code, "capture_stdout": True}
        )
        if result.get("exit_code", 0) != 0:
            raise PyodideWorkerError(f"glob failed: {result.get('stderr', '').strip()}")
        return list(json.loads(result.get("stdout", "[]").strip() or "[]"))

    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]:
        _ = context_lines  # reserved — same as HostWorkspace
        await self._ensure_started()
        absolute = self.resolve(root)
        params = {
            "root": absolute,
            "pattern": pattern,
            "file_pattern": file_pattern,
            "case_sensitive": case_sensitive,
            "workspace_root": self.root,
        }
        code = _wrap_with_params(_GREP_CODE, params)
        result = await self._worker.call(
            "exec_python", {"code": code, "capture_stdout": True}
        )
        if result.get("exit_code", 0) != 0:
            raise PyodideWorkerError(f"grep failed: {result.get('stderr', '').strip()}")
        raw = json.loads(result.get("stdout", "[]").strip() or "[]")
        return [
            GrepMatch(
                path=item["path"],
                line_number=int(item["line_number"]),
                line=item["line"],
                column=item.get("column"),
            )
            for item in raw
        ]

    # ---- capabilities --------------------------------------------------- #

    @property
    def supports_shell(self) -> bool:  # noqa: D401 — overrides base
        return False

    # ---- shell execution (intentionally unsupported) -------------------- #

    async def exec(  # noqa: A001
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
        _ = (command, cwd, timeout, env, shell, user, max_output_bytes)
        raise WorkspaceFeatureUnavailable(
            "PyodideWorkspace has no shell — use run_python(code) for Python "
            "execution, or pair this workspace with a sandbox backend if you "
            "need bash/git/native binaries."
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
        _ = (command, cwd, env, shell, user, pty)
        raise WorkspaceFeatureUnavailable(
            "PyodideWorkspace cannot spawn background processes — there is no "
            "fork/exec in pyodide. Use run_python for one-shot Python execution."
        )

    # ---- Python execution (extra primitive; not on the ABC) ------------- #

    async def run_python(
        self,
        code: str,
        *,
        load_imports: bool = True,
        timeout_s: Optional[float] = None,
        capture_stdout: bool = True,
    ) -> ExecResult:
        """Execute Python code inside the worker's pyodide interpreter.

        Globals persist across calls — write multi-step analyses without
        re-loading data. ``load_imports=True`` calls
        ``loadPackagesFromImports`` first so the model can ``import numpy``
        without ceremony.

        Returns an ``ExecResult`` with stdout/stderr/exit_code populated and
        ``wall_time_seconds`` measured by the Python side.
        """
        await self._ensure_started()
        import time as _time

        start = _time.perf_counter()
        result = await self._worker.call(
            "exec_python",
            {
                "code": code,
                "capture_stdout": capture_stdout,
                "load_imports": load_imports,
                "timeout_s": timeout_s,
            },
            timeout=(timeout_s + 5.0) if timeout_s else None,
        )
        wall = _time.perf_counter() - start
        return ExecResult(
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=int(result.get("exit_code", 0)),
            wall_time_seconds=wall,
            truncated=False,
        )

    async def reset_interpreter(self) -> None:
        """Drop Python-side globals without restarting the wasm heap.

        Cheap. Use between unrelated tasks so leftover variables don't shadow
        new code.
        """
        await self._ensure_started()
        await self._worker.call(
            "exec_python",
            {
                "code": "for _k in list(globals()):\n    if not _k.startswith('_'): del globals()[_k]\n",
                "capture_stdout": False,
            },
        )


def _wrap_with_params(code: str, params: Dict[str, Any]) -> str:
    """Inject *params* as a module-level dict named ``__params__``.

    Composite ops (glob, grep) read parameters this way instead of via JSON
    args because exec_python's only argument is a code string — wrapping is
    simpler than threading another protocol field.
    """
    payload = json.dumps(params)
    return f"import json as _json\n__params__ = _json.loads({payload!r})\n{code}"


# --------------------------------------------------------------------------- #
# Discovery helper                                                            #
# --------------------------------------------------------------------------- #


def pyodide_worker_available(node_path: str = "node") -> bool:
    """Return True if a Node executable is on PATH (worker prerequisite)."""
    return shutil.which(node_path) is not None
