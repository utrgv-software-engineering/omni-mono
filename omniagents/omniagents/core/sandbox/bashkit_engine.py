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
has no syscalls into a host, so there is no host to escape to. What it
cannot do is run real binaries — no ``git``, no network — because there is
no operating system underneath, only the utilities bashkit implements.

Because this is a real :class:`BaseSandboxSession`,
:class:`~omniagents.workspace.sandbox.SandboxWorkspace` wraps it like any
other, and agent file/shell tools work in a browser with no special casing.
"""

from __future__ import annotations

import io
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

from agents.sandbox.files import EntryKind, FileEntry, Permissions
from agents.sandbox.manifest import Manifest
from agents.sandbox.session import BaseSandboxSession, SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.types import ExecResult, User

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
        # The base class hands us argv, often the ``sh -lc <script>`` form.
        # shlex.join rebuilds a faithful command line, and bashkit - being a
        # shell - runs it, ``sh -lc`` wrapper included. Joining on plain
        # spaces instead would drop the script's own quoting and silently
        # execute the wrong thing.
        script = shlex.join(str(part) for part in command)

        # `timeout` is not enforceable per call: bashkit's budget
        # (timeout_seconds, max_commands, max_loop_iterations) is fixed when
        # the interpreter is built. Set it there.
        _ = timeout

        run_async = getattr(self.bash, "execute", None)
        if run_async is not None:
            result = await run_async(script)
        else:
            # The wasm build ships only the sync entry point. Blocking costs
            # nothing meaningful: bashkit is in-memory, and Pyodide has no
            # threads for an async variant to yield to.
            result = self.bash.execute_sync(script)

        return ExecResult(
            stdout=result.stdout.encode(),
            stderr=result.stderr.encode(),
            exit_code=result.exit_code,
        )

    # ------------------------------ file I/O ------------------------------- #

    async def read(
        self, path: Path, *, user: str | User | None = None
    ) -> io.IOBase:
        return io.BytesIO(self.bash.fs().read_file(str(path)))

    async def write(
        self, path: Path, data: io.IOBase, *, user: str | User | None = None
    ) -> None:
        fs = self.bash.fs()
        target = PurePosixPath(str(path))
        parent = str(target.parent)
        if parent and parent != "." and not fs.exists(parent):
            fs.mkdir(parent, True)
        payload = data.read()
        fs.write_file(
            str(path), payload if isinstance(payload, bytes) else str(payload).encode()
        )

    async def ls(
        self, path: Path | str, *, user: str | User | None = None
    ) -> Sequence[FileEntry]:
        # Overridden rather than inherited: the base parses `ls -la` output,
        # and bashkit's columns are not GNU's (no link count, owner or
        # group), so the parse yields nothing. Reading the directory
        # structurally avoids the text round-trip entirely.
        fs = self.bash.fs()
        base = str(path)
        entries: list[FileEntry] = []
        for entry in fs.read_dir(base):
            name = entry.get("name")
            if not name:
                continue
            metadata = entry.get("metadata") or {}
            file_type = metadata.get("file_type")
            if file_type in _DIR_TYPES:
                kind = EntryKind.DIRECTORY
            elif file_type == "symlink":
                kind = EntryKind.SYMLINK
            elif file_type == "file":
                kind = EntryKind.FILE
            else:
                kind = EntryKind.OTHER
            entries.append(
                FileEntry(
                    path=str(PurePosixPath(base) / name),
                    permissions=Permissions.from_mode(
                        metadata.get("mode", 0o644) & 0o777
                    ),
                    # bashkit has no user or group model; report the single
                    # implicit identity rather than inventing one.
                    owner="user",
                    group="user",
                    size=metadata.get("size", 0) or 0,
                    kind=kind,
                )
            )
        entries.sort(key=lambda e: e.path)
        return entries
