"""The in-browser sandbox: bashkit as an SDK sandbox session.

Every other sandbox backend needs something to provision — a container, a
process, a remote VM. None of that exists in a browser tab, which is why
in-browser agents have had no sandbox and their shell tools degraded to
"unavailable".

bashkit is a bash interpreter compiled to WebAssembly, carrying its own
filesystem and its own utilities. That is enough to satisfy the sandbox
contract without anything to provision:

============================  ==========================================
sandbox concept               bashkit
============================  ==========================================
provisioning / manifest       ``Bash(files=...)``, ``apply_manifest``
capability: filesystem        ``bash.fs()``
capability: shell             ``bash.execute_sync``
resource limits               ``max_commands``, ``max_memory``,
                              ``timeout_seconds``, ``readonly_filesystem``
snapshot / restore            ``snapshot()`` / ``restore_snapshot()``
============================  ==========================================

Isolation is stronger than the process backends give you: the interpreter
has no syscalls into a host, so there is no host to escape to. The flip
side is that only the utilities bashkit itself implements exist; there are
no real binaries to exec.

Two limits worth stating precisely, because both are narrower than they
look:

``git`` and ``curl`` are supplied by this package rather than by bashkit,
via ``git=True`` / ``network={"allow": [...]}``; see
:mod:`omniagents.core.sandbox.bashkit_builtins` for why bashkit's own
versions are unusable here (one is a simulation, the other cannot compile
to wasm).

Because this is a real :class:`BaseSandboxSession`,
:class:`~omniagents.workspace.sandbox.SandboxWorkspace` wraps it like any
other, and agent file/shell tools work in a browser with no special casing.
"""

from __future__ import annotations

import io
import os
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

from agents.sandbox.files import EntryKind, FileEntry, Permissions
from agents.sandbox.manifest import Manifest
from agents.sandbox.session import BaseSandboxSession, SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.types import ExecResult, User

from .bashkit_builtins import DEFAULT_AUTHOR

_DIR_TYPES = {"directory", "dir"}

class BashkitSandboxSession(BaseSandboxSession):
    """Sandbox session backed by a bashkit interpreter.

    Args:
        root: manifest root inside the VFS.
        bash: an existing ``bashkit.Bash`` to adopt, when the interpreter
            must be shared with other code on the page. Omit to build one.
        session_id: reused as the snapshot id; useful when persisting.
        **bash_kwargs: forwarded to ``Bash()`` (``files``, ``env``,
            ``max_memory``, ``readonly_filesystem``, ...).
    """

    def __init__(
        self,
        *,
        root: str = "/workspace",
        bash: Any = None,
        session_id: str = "bashkit",
        git: Any = False,
        network: Any = None,
        real_python: bool = True,
        **bash_kwargs: Any,
    ) -> None:
        if bash is None:
            from bashkit import Bash, FileSystem

            # The workspace is an ordinary directory on the interpreter's own
            # filesystem, mounted into the shell. One filesystem: what the
            # editor writes, the shell lists; what the shell writes, Python
            # reads. No copying, and so no second source of truth to drift.
            os.makedirs(root, exist_ok=True)
            for path, content in (bash_kwargs.pop("files", None) or {}).items():
                target = path if path.startswith("/") else os.path.join(root, path)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w") as handle:
                    handle.write(content)

            builtins = dict(bash_kwargs.pop("custom_builtins", None) or {})
            if real_python:
                from .bashkit_builtins import make_python_builtin

                # Shadows bashkit's embedded Monty, so `python3` in the shell
                # is the interpreter Run uses - same version, same packages.
                runner = make_python_builtin(root=root)
                builtins["python3"] = runner
                builtins["python"] = runner
            if git:
                from .bashkit_builtins import make_git_builtin

                git_spec = {} if git is True else dict(git)
                builtins["git"] = make_git_builtin(
                    root=root,
                    proxy_base=git_spec.get("proxy_base"),
                    author=git_spec.get("author", DEFAULT_AUTHOR),
                )
            if network:
                from .bashkit_builtins import make_http_builtins

                spec = {} if network is True else dict(network)
                builtins.update(
                    make_http_builtins(
                        allow=spec.get("allow"),
                        allow_all=bool(spec.get("allow_all", network is True)),
                        timeout=float(spec.get("timeout", 30.0)),
                        root=root,
                    )
                )
            if builtins:
                bash_kwargs["custom_builtins"] = builtins
            bash_kwargs.setdefault("python", True)
            bash_kwargs.setdefault("cwd", root)
            bash = Bash(**bash_kwargs)
            bash.mount(root, FileSystem.real(root, writable=True))
        elif git or network:
            raise TypeError(
                "git=/network= build custom builtins at construction, so they "
                "cannot be applied to an existing Bash; pass the kwargs "
                "instead of bash="
            )
        elif bash_kwargs:
            raise TypeError(
                "bash_kwargs are only used when constructing a Bash; "
                "configure the instance you pass in instead"
            )

        self.bash = bash
        self.state = SandboxSessionState(
            type="bashkit",
            snapshot=NoopSnapshot(id=session_id),
            manifest=Manifest(root=root),
        )

    # ------------------------------ lifecycle ------------------------------ #

    async def running(self) -> bool:
        return True

    def describe(self) -> str:
        return f"bashkit sandbox at {self.state.manifest.root}"

    @property
    def supports_pty(self) -> bool:
        # bashkit runs commands to completion; there is no terminal to attach.
        return False

    async def _after_start(self) -> None:
        # Real backends start their shell already in the manifest root, and
        # SandboxWorkspace depends on it: its exec() passes relative paths
        # through untouched, so `cat notes.txt` has to resolve there. bashkit
        # starts in /home/user, so move once - its cwd persists across
        # execute calls, exactly like a shell session.
        await super()._after_start()
        root = str(self.state.manifest.root)
        self.bash.execute_sync(f"mkdir -p {shlex.quote(root)} && cd {shlex.quote(root)}")

    def snapshot_workspace(self) -> bytes:
        """Freeze the workspace directory.

        ``bash.snapshot()`` captures the interpreter's own VFS and shell
        state, which no longer holds the files: they live on the real
        filesystem now. This captures those, so a caller wanting to restore a
        session takes both.
        """
        import io as _io
        import tarfile

        buffer = _io.BytesIO()
        root = str(self.state.manifest.root)
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            archive.add(root, arcname=".")
        return buffer.getvalue()

    def restore_workspace(self, data: bytes) -> None:
        """Put the workspace back as ``snapshot_workspace`` found it."""
        import io as _io
        import shutil
        import tarfile

        root = str(self.state.manifest.root)
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        with tarfile.open(fileobj=_io.BytesIO(data), mode="r") as archive:
            archive.extractall(root)

    async def hydrate_workspace(self, *args: Any, **kwargs: Any) -> None:
        # Nothing to fetch: the workspace *is* the interpreter's filesystem,
        # and it is already in memory by the time the session exists.
        return None

    async def persist_workspace(self, *args: Any, **kwargs: Any) -> None:
        # Callers that want durability across page loads use
        # ``bash.snapshot()`` and store the bytes themselves - there is no
        # host filesystem here to persist onto.
        return None

    # -------------------------------- exec --------------------------------- #

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: Optional[float] = None,
    ) -> ExecResult:
        # The base class wraps commands as `sh -lc '<script>'`. Run the inner
        # script, not the wrapper: bashkit *is* the shell, and `sh -lc` starts
        # a nested one whose `cd` and `export` are discarded when it exits. A
        # terminal has to remember where it is between commands, so the script
        # has to reach the persistent interpreter directly.
        parts = [str(part) for part in command]
        if len(parts) == 3 and parts[0] in ("sh", "bash") and parts[1] in ("-c", "-lc"):
            script = parts[2]
        else:
            # Otherwise rebuild a faithful command line. shlex.join, not a
            # space join, or the script's own quoting is lost and something
            # else runs.
            script = shlex.join(parts)

        # `timeout` is not enforceable per call: bashkit's budget
        # (timeout_seconds, max_commands, max_loop_iterations) is fixed when
        # the interpreter is built. Set it there.
        _ = timeout

        result = await self._run(script)
        return ExecResult(
            stdout=result.stdout.encode(),
            stderr=result.stderr.encode(),
            exit_code=result.exit_code,
        )

    async def _run(self, script: str):
        """Run one script through bashkit, async where the build has it."""
        run_async = getattr(self.bash, "execute", None)
        if run_async is not None:
            return await run_async(script)
        # The wasm build ships only the sync entry point. Blocking costs
        # nothing meaningful: bashkit is in-memory, and Pyodide has no threads
        # for an async variant to yield to.
        return self.bash.execute_sync(script)

    # ------------------------------ file I/O ------------------------------- #
    #
    # Plain file operations, not bash.fs(): the workspace is a real directory
    # mounted into the shell, and the interpreter's own VFS handle does not
    # see through that mount - writes through it would land underneath and be
    # invisible to every command.

    async def read(
        self, path: Path, *, user: str | User | None = None
    ) -> io.IOBase:
        with open(str(path), "rb") as handle:
            return io.BytesIO(handle.read())

    async def write(
        self, path: Path, data: io.IOBase, *, user: str | User | None = None
    ) -> None:
        target = str(path)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = data.read()
        with open(target, "wb") as handle:
            handle.write(payload if isinstance(payload, bytes) else str(payload).encode())

    async def ls(
        self, path: Path | str, *, user: str | User | None = None
    ) -> Sequence[FileEntry]:
        base = str(path)
        entries: list[FileEntry] = []
        for name in os.listdir(base):
            full = os.path.join(base, name)
            info = os.lstat(full)
            if os.path.islink(full):
                kind = EntryKind.SYMLINK
            elif os.path.isdir(full):
                kind = EntryKind.DIRECTORY
            elif os.path.isfile(full):
                kind = EntryKind.FILE
            else:
                kind = EntryKind.OTHER
            entries.append(
                FileEntry(
                    path=full,
                    permissions=Permissions.from_mode(info.st_mode & 0o777),
                    owner="user",
                    group="user",
                    size=info.st_size,
                    kind=kind,
                )
            )
        entries.sort(key=lambda e: e.path)
        return entries
