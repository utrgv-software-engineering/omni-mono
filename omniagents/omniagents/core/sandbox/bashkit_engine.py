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
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

from agents.sandbox.files import EntryKind, FileEntry, Permissions
from agents.sandbox.manifest import Manifest
from agents.sandbox.session import BaseSandboxSession, SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.types import ExecResult, User

from .bashkit_builtins import _mirror_in, _mirror_out

_DIR_TYPES = {"directory", "dir"}

# Shell words that open a block. While one is open the script cannot be split.
_BLOCK_OPENERS = {"do", "then", "case", "{"}
_BLOCK_CLOSERS = {"done", "fi", "esac", "}"}


def _split_top_level(script: str):
    """Split a script into ``(separator, statement)`` pairs at safe points.

    Only needed when a mirroring builtin is enabled: those builtins read the
    workspace through a snapshot taken before the command, so a script like::

        echo hello > f.txt
        git add f.txt

    would have ``git`` looking at a mirror made before ``echo`` ran. Running
    the statements as separate calls lets the session re-sync between them.
    bashkit keeps shell state (cwd, env, functions) across calls, so this
    stays faithful.

    Splits on newline, ``;`` and ``&&`` at depth zero only. Anything with a
    heredoc, an open block, quotes or a subshell is returned whole — a wrong
    split would change the program's meaning, which is far worse than a stale
    read. Returns ``None`` when the script must not be split.
    """
    if "<<" in script:  # heredoc bodies are not tokenizable this way
        return None

    parts = []
    current = ""
    separator = ";"
    depth = 0
    word = ""
    single = double = escaped = False
    index = 0

    def close_word():
        nonlocal word, depth
        if word in _BLOCK_OPENERS:
            depth += 1
        elif word in _BLOCK_CLOSERS:
            depth -= 1
        word = ""

    while index < len(script):
        char = script[index]
        if escaped:
            current += char
            escaped = False
            index += 1
            continue
        if char == "\\":
            current += char
            escaped = True
            index += 1
            continue
        if single:
            current += char
            single = char != "'"
            index += 1
            continue
        if double:
            current += char
            double = char != '"'
            index += 1
            continue
        if char == "'":
            single = True
            current += char
            index += 1
            continue
        if char == '"':
            double = True
            current += char
            index += 1
            continue
        if char in "()":
            return None  # subshells and function bodies: leave alone
        if char.isspace() or char in ";&|":
            close_word()
        if char in "\n;" or script[index : index + 2] == "&&":
            is_and = script[index : index + 2] == "&&"
            if depth == 0:
                statement = current.strip()
                if statement:
                    parts.append((separator, statement))
                    separator = "&&" if is_and else ";"
                current = ""
                index += 2 if is_and else 1
                continue
        if script[index : index + 2] in ("||", "|&") or char == "|":
            # Pipelines and || chains stay in one piece.
            current += char
            index += 1
            continue
        current += char
        if not char.isspace() and char not in ";&|":
            word += char
        index += 1

    if single or double or depth != 0:
        return None
    statement = current.strip()
    if statement:
        parts.append((separator, statement))
    return parts or None


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
        git: bool = False,
        network: Any = None,
        **bash_kwargs: Any,
    ) -> None:
        # Where builtins that need an ordinary filesystem (git, curl -o) see
        # the workspace. Set before they are constructed, since they capture it.
        self._mirror = f"/tmp/omniagents-mirror/{session_id}"
        self._mirrored = bool(git or network)

        if bash is None:
            from bashkit import Bash

            builtins = dict(bash_kwargs.pop("custom_builtins", None) or {})
            if git:
                from .bashkit_builtins import make_git_builtin

                builtins["git"] = make_git_builtin(root=root, mirror=self._mirror)
            if network:
                from .bashkit_builtins import make_http_builtins

                spec = {} if network is True else dict(network)
                builtins.update(
                    make_http_builtins(
                        allow=spec.get("allow"),
                        allow_all=bool(spec.get("allow_all", network is True)),
                        timeout=float(spec.get("timeout", 30.0)),
                        root=root,
                        mirror=self._mirror,
                    )
                )
            if builtins:
                bash_kwargs["custom_builtins"] = builtins
            bash_kwargs.setdefault("python", True)
            bash = Bash(**bash_kwargs)
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

        if not self._mirrored:
            result = await self._run(script)
            return ExecResult(
                stdout=result.stdout.encode(),
                stderr=result.stderr.encode(),
                exit_code=result.exit_code,
            )

        # A builtin cannot touch the VFS itself (ctx.fs ops panic on wasm -
        # no threads), so the workspace is mirrored to a real directory around
        # each statement, here, where bash.fs() is safe. Statement-by-statement
        # rather than once per script: otherwise `echo x > f` followed by
        # `git add f` would have git reading a mirror taken before the echo.
        #
        # Split the script the caller wrote, not the `sh -lc '<script>'`
        # wrapper the base class adds - the wrapper makes the whole program a
        # single quoted argument, so splitting it yields one statement and the
        # re-sync never happens.
        parts = [str(part) for part in command]
        inner = (
            parts[2]
            if len(parts) == 3
            and parts[0] in ("sh", "bash")
            and parts[1] in ("-c", "-lc")
            else script
        )
        statements = _split_top_level(inner) or [(";", inner)]

        out, err, code = "", "", 0
        for separator, statement in statements:
            if separator == "&&" and code != 0:
                break
            baseline = _mirror_in(
                self.bash.fs(), str(self.state.manifest.root), self._mirror
            )
            result = await self._run(statement)
            _mirror_out(
                self.bash.fs(), str(self.state.manifest.root), self._mirror, baseline
            )
            out += result.stdout
            err += result.stderr
            code = result.exit_code

        return ExecResult(stdout=out.encode(), stderr=err.encode(), exit_code=code)

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
