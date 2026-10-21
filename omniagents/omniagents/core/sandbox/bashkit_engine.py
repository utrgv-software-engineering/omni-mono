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

import fnmatch
import io
import os
import re
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

#: git subcommands that reach the network (dulwich remote ops). Everything
#: else is local object-store work and keeps working when the offline
#: toggle blocks egress.
_GIT_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote"})


class BashkitSandboxSession(BaseSandboxSession):
    """Sandbox session backed by a bashkit interpreter.

    Args:
        root: manifest root inside the VFS.
        host_dir: real host directory backing ``root``. Defaults to ``root``
            itself (the historical identity mapping, used in-browser where
            the "host" filesystem is the surrounding Emscripten FS). When it
            differs, the shell presents ``root`` (e.g. ``/workspace``) while
            bytes live in ``host_dir`` — the host path never appears inside
            the sandbox, matching the docker container's namespace shape.
            The host-side builtins (git/curl) receive translated host paths
            transparently.
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
        host_dir: str | None = None,
        bash: Any = None,
        session_id: str = "bashkit",
        git: Any = False,
        network: Any = None,
        real_python: bool = True,
        **bash_kwargs: Any,
    ) -> None:
        # Live offline toggle (sandbox.set_network). The network-capable
        # builtins (curl/wget, git's remote subcommands) consult this flag
        # per invocation, so flipping it gates a running session without
        # rebuilding builtins. Construction-time policy (the allowlist)
        # stays inside the builtins themselves.
        self._network_blocked = False
        # None = identity (vfs path IS the host path) — the historical and
        # in-browser behavior. Set = split namespace; every host-side file
        # operation goes through _host_path()/_virtual_path().
        self._host_dir = os.path.realpath(host_dir) if host_dir else None
        self._vfs_root = root
        backing = self._host_dir or root
        if bash is None:
            from bashkit import Bash, FileSystem

            # The workspace is an ordinary directory on the interpreter's own
            # filesystem, mounted into the shell. One filesystem: what the
            # editor writes, the shell lists; what the shell writes, Python
            # reads. No copying, and so no second source of truth to drift.
            os.makedirs(backing, exist_ok=True)
            for path, content in (bash_kwargs.pop("files", None) or {}).items():
                target = (
                    self._host_path(path)
                    if path.startswith("/")
                    else os.path.join(backing, path)
                )
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w") as handle:
                    handle.write(content)

            builtins = dict(bash_kwargs.pop("custom_builtins", None) or {})
            if real_python:
                from .bashkit_builtins import make_python_builtin

                # Shadows bashkit's embedded Monty, so `python3` in the shell
                # is the interpreter Run uses - same version, same packages.
                runner = make_python_builtin(root=backing)
                builtins["python3"] = self._host_cwd_builtin(runner)
                builtins["python"] = self._host_cwd_builtin(runner)
            if git:
                from .bashkit_builtins import make_git_builtin

                git_spec = {} if git is True else dict(git)
                builtins["git"] = self._network_gated(
                    "git",
                    self._host_cwd_builtin(
                        make_git_builtin(
                            root=backing,
                            proxy_base=git_spec.get("proxy_base"),
                            author=git_spec.get("author", DEFAULT_AUTHOR),
                        )
                    ),
                    git_subcommands=True,
                )
            if network:
                from .bashkit_builtins import make_http_builtins

                spec = {} if network is True else dict(network)
                builtins.update(
                    {
                        name: self._network_gated(name, self._host_cwd_builtin(fn))
                        for name, fn in make_http_builtins(
                            allow=spec.get("allow"),
                            allow_all=bool(spec.get("allow_all", network is True)),
                            timeout=float(spec.get("timeout", 30.0)),
                            root=backing,
                        ).items()
                    }
                )
            if builtins:
                bash_kwargs["custom_builtins"] = builtins
            bash_kwargs.setdefault("python", True)
            bash_kwargs.setdefault("cwd", root)
            bash = Bash(**bash_kwargs)
            bash.mount(root, FileSystem.real(backing, writable=True))
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

    # --------------------------- path namespace ---------------------------- #

    def _host_path(self, path: Any) -> str:
        """VFS path → real host path. Identity when there is no split."""
        text = str(path)
        if self._host_dir is None:
            return text
        vfs_root = PurePosixPath(self._vfs_root)
        candidate = PurePosixPath(text)
        if candidate == vfs_root:
            return self._host_dir
        try:
            relative = candidate.relative_to(vfs_root)
        except ValueError:
            # Outside the VFS root: nothing to translate. Confinement
            # checks reject these; plain file ops fail on the host path.
            return text
        return os.path.join(self._host_dir, *relative.parts)

    def _virtual_path(self, path: Any) -> str:
        """Real host path → VFS path, for output the sandbox user sees."""
        text = str(path)
        if self._host_dir is None:
            return text
        relative = os.path.relpath(os.path.realpath(text), self._host_dir)
        if relative == ".":
            return self._vfs_root
        if relative.startswith(".."):
            return text
        return str(PurePosixPath(self._vfs_root, *relative.split(os.sep)))

    def _network_gated(
        self, name: str, fn: Any, *, git_subcommands: bool = False
    ) -> Any:
        """Wrap a network-capable builtin behind the live offline toggle.

        The flag is read per invocation, so ``sandbox.set_network`` gates a
        running session. With ``git_subcommands`` only the remote
        operations are refused — local git (status/commit/log) keeps
        working offline, exactly like the docker iptables block.
        """
        session = self

        def gated(ctx: Any):
            if session._network_blocked:
                argv = [str(a) for a in getattr(ctx, "argv", ())]
                if not git_subcommands or (
                    argv and argv[0] in _GIT_NETWORK_SUBCOMMANDS
                ):
                    from bashkit import BuiltinResult

                    return BuiltinResult(
                        stderr=f"{name}: network is disabled (sandbox offline)\n",
                        exit_code=1,
                    )
            return fn(ctx)

        return gated

    def _host_cwd_builtin(self, fn: Any) -> Any:
        """Wrap a host-side builtin (git/curl/python) for the split namespace.

        Two translations, both no-ops without a split: ``ctx.cwd`` (a VFS
        path from bashkit) becomes a host path before the builtin touches
        the filesystem, and any host path the builtin echoes back
        (``Initialized empty Git repository in …``, curl's saved-to
        messages, tracebacks) is rewritten to the VFS path — the host
        layout must never leak into sandbox output.
        """
        if self._host_dir is None:
            return fn

        session = self

        def wrapped(ctx: Any):
            class _Ctx:
                def __getattr__(self, name: str) -> Any:
                    return getattr(ctx, name)

                @property
                def cwd(self) -> str:
                    return session._host_path(getattr(ctx, "cwd", "") or "")

            return session._virtualize_result(fn(_Ctx()))

        return wrapped

    def _virtualize_result(self, result: Any) -> Any:
        """Rewrite host paths in a BuiltinResult's output to VFS paths."""
        host_dir = self._host_dir
        if host_dir is None:
            return result
        try:
            from bashkit import BuiltinResult

            def fix(value: Any) -> Any:
                if isinstance(value, str):
                    return value.replace(host_dir, self._vfs_root)
                return value

            return BuiltinResult(
                stdout=fix(getattr(result, "stdout", "") or ""),
                stderr=fix(getattr(result, "stderr", "") or ""),
                exit_code=int(getattr(result, "exit_code", 0) or 0),
            )
        except Exception:
            return result

    # ------------------------------ lifecycle ------------------------------ #

    async def running(self) -> bool:
        return True

    def describe(self) -> str:
        return f"bashkit sandbox at {self.state.manifest.root}"

    def supports_pty(self) -> bool:
        # bashkit runs commands to completion; there is no terminal to attach.
        # Deliberately a plain method, not a property: the base class and every
        # sibling backend declare it as one, and SandboxWorkspace calls it as
        # one (`self._session.supports_pty()`), so a property would raise
        # TypeError: 'bool' object is not callable.
        return False

    async def _after_start(self) -> None:
        # Real backends start their shell already in the manifest root, and
        # SandboxWorkspace depends on it: its exec() passes relative paths
        # through untouched, so `cat notes.txt` has to resolve there. bashkit
        # starts in /home/user, so move once - its cwd persists across
        # execute calls, exactly like a shell session.
        await super()._after_start()
        root = str(self.state.manifest.root)
        self.bash.execute_sync(
            f"mkdir -p {shlex.quote(root)} && cd {shlex.quote(root)}"
        )

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
        root = self._host_dir or str(self.state.manifest.root)
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            archive.add(root, arcname=".")
        return buffer.getvalue()

    def restore_workspace(self, data: bytes) -> None:
        """Put the workspace back as ``snapshot_workspace`` found it."""
        import io as _io
        import shutil
        import tarfile
        import tempfile

        root = self._host_dir or str(self.state.manifest.root)
        parent = os.path.dirname(root) or "."
        os.makedirs(parent, exist_ok=True)

        # Extract into a disposable sibling first.  ``data`` rejects archive
        # members that escape through ``..`` or links, strips leading slashes,
        # and refuses device files.  Staging also means a rejected/corrupt
        # snapshot cannot destroy the workspace it was supposed to replace.
        with tempfile.TemporaryDirectory(
            prefix=f".{os.path.basename(root)}.restore-", dir=parent
        ) as staging:
            with tarfile.open(fileobj=_io.BytesIO(data), mode="r") as archive:
                archive.extractall(staging, filter="data")

            # Swap only after extraction has succeeded. Rename the previous
            # workspace out of the way first so a failed final rename can put
            # it back intact instead of leaving a partially deleted tree.
            backup = tempfile.mkdtemp(
                prefix=f".{os.path.basename(root)}.backup-", dir=parent
            )
            os.rmdir(backup)
            had_workspace = os.path.lexists(root)
            try:
                if had_workspace:
                    os.replace(root, backup)
                os.replace(staging, root)
            except Exception:
                if had_workspace and not os.path.lexists(root):
                    os.replace(backup, root)
                raise
            else:
                if had_workspace:
                    shutil.rmtree(backup, ignore_errors=True)

    async def hydrate_workspace(self, *args: Any, **kwargs: Any) -> None:
        # Nothing to fetch: the workspace *is* the interpreter's filesystem,
        # and it is already in memory by the time the session exists.
        return None

    async def persist_workspace(self, *args: Any, **kwargs: Any) -> io.IOBase:
        # Callers that want durability across page loads use
        # ``bash.snapshot()``/``snapshot_workspace()`` and store the bytes
        # themselves. Nothing is persisted through this path, but the base
        # class promises a readable byte stream and ``hydrate_workspace`` on
        # other backends feeds the result straight to ``tarfile.open`` — so
        # hand back a valid *empty* archive rather than None, which would
        # explode at the first read.
        import tarfile

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w"):
            pass
        buffer.seek(0)
        return buffer

    # -------------------------------- exec --------------------------------- #

    def _prepare_exec_command(self, *command, shell, user):
        # bashkit has no user identities: everything runs as the single wasm
        # interpreter, and there is no sudo. The SDK base wraps `user=` in
        # ``sudo -u <user> --`` — under bashkit that exits 127 (serve's init
        # commands and credential setup pass ``user="root"``). Drop the
        # identity instead: it is meaningless here, not a privilege change.
        return super()._prepare_exec_command(*command, shell=shell, user=None)

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
        # Bashkit's built-in find/grep walk its private VFS and do not cross a
        # ``FileSystem.real`` mount. SandboxWorkspace deliberately invokes
        # these two as argv (shell=False), which lets the session provide the
        # same operations against the mounted workspace without intercepting
        # arbitrary shell scripts.
        if len(parts) >= 4 and parts[0] == "find" and "-name" in parts:
            return self._find_mounted_files(parts)
        if len(parts) >= 4 and parts[0] == "grep" and "-e" in parts:
            return self._grep_mounted_files(parts)
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
        """Run one script through bashkit's in-memory interpreter."""
        # The native package exposes ``execute`` as an asyncio Future, but its
        # current bridge never resolves under CPython.  The sync entry point is
        # also the portable API exposed by the wasm build.  It does no host I/O
        # and Pyodide has no worker thread to yield to, so calling it directly
        # is both deterministic in CI and appropriate in the browser.
        return self.bash.execute_sync(script)

    def _find_mounted_files(self, command: Sequence[str]) -> ExecResult:
        """Implement SandboxWorkspace's ``find`` argv over the real mount."""
        root = self._confined_mounted_root(command[1])
        if root is None:
            return ExecResult(
                stdout=b"", stderr=b"find: path outside workspace\n", exit_code=1
            )
        pattern = command[command.index("-name") + 1]
        recursive = "-maxdepth" not in command
        exclude_hidden = "-not" in command and "*/.*" in command
        matches: list[str] = []

        for current, directories, files in os.walk(root):
            if exclude_hidden:
                directories[:] = [
                    name for name in directories if not name.startswith(".")
                ]
                files = [name for name in files if not name.startswith(".")]
            for name in [*directories, *files]:
                if fnmatch.fnmatchcase(name, pattern):
                    matches.append(self._virtual_path(os.path.join(current, name)))
            if not recursive:
                directories[:] = []

        stdout = "".join(f"{path}\n" for path in sorted(matches)).encode()
        return ExecResult(stdout=stdout, stderr=b"", exit_code=0)

    def _grep_mounted_files(self, command: Sequence[str]) -> ExecResult:
        """Implement SandboxWorkspace's recursive ``grep`` over the mount."""
        root = self._confined_mounted_root(command[-1])
        if root is None:
            return ExecResult(
                stdout=b"", stderr=b"grep: path outside workspace\n", exit_code=2
            )
        pattern = command[command.index("-e") + 1]
        include = "*"
        if "--include" in command:
            include = command[command.index("--include") + 1]
        # ``context_lines`` is reserved by the Workspace contract; Host- and
        # PyodideWorkspace intentionally ignore it because GrepMatch has no
        # context field. Accept ``-C`` here while returning the same match-only
        # result rather than leaking grep's context framing into the parser.
        flags = 0 if "-i" not in command else re.IGNORECASE
        try:
            expression = re.compile(pattern, flags)
        except re.error as error:
            return ExecResult(
                stdout=b"",
                stderr=f"grep: {error}\n".encode(),
                exit_code=2,
            )

        matches: list[str] = []
        for current, _, files in os.walk(root):
            for name in files:
                if not fnmatch.fnmatchcase(name, include):
                    continue
                path = os.path.join(current, name)
                if os.path.islink(path):
                    continue
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        virtual = self._virtual_path(path)
                        for line_number, line in enumerate(handle, 1):
                            if expression.search(line):
                                content = line.rstrip("\r\n")
                                matches.append(f"{virtual}:{line_number}:{content}\n")
                except OSError:
                    continue

        return ExecResult(
            stdout="".join(matches).encode(),
            stderr=b"",
            exit_code=0 if matches else 1,
        )

    def _confined_mounted_root(self, candidate: str) -> Optional[str]:
        """Return a real path only when it remains under the workspace."""
        workspace = os.path.realpath(self._host_dir or str(self.state.manifest.root))
        requested = os.path.realpath(self._host_path(candidate))
        try:
            if os.path.commonpath((workspace, requested)) != workspace:
                return None
        except ValueError:
            return None
        return requested

    # ------------------------------ file I/O ------------------------------- #
    #
    # Plain file operations, not bash.fs(): the workspace is a real directory
    # mounted into the shell, and the interpreter's own VFS handle does not
    # see through that mount - writes through it would land underneath and be
    # invisible to every command.

    async def read(self, path: Path, *, user: str | User | None = None) -> io.IOBase:
        with open(self._host_path(path), "rb") as handle:
            return io.BytesIO(handle.read())

    async def write(
        self, path: Path, data: io.IOBase, *, user: str | User | None = None
    ) -> None:
        target = self._host_path(path)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = data.read()
        with open(target, "wb") as handle:
            handle.write(
                payload if isinstance(payload, bytes) else str(payload).encode()
            )

    async def ls(
        self, path: Path | str, *, user: str | User | None = None
    ) -> list[FileEntry]:
        base = self._host_path(path)
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
                    # VFS path, not the host path — callers (and the agent's
                    # file tools) must never see through the mount.
                    path=self._virtual_path(full),
                    permissions=Permissions.from_mode(info.st_mode & 0o777),
                    owner="user",
                    group="user",
                    size=info.st_size,
                    kind=kind,
                )
            )
        entries.sort(key=lambda e: e.path)
        return entries
