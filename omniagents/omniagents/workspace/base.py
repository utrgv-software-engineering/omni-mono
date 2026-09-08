"""Workspace abstract base + ProcessHandle protocol.

The workspace abstraction lets tool authors write one body that works
identically against a host filesystem or a sandbox manifest. ``HostWorkspace``
wraps the local filesystem; ``SandboxWorkspace`` wraps an SDK
``BaseSandboxSession``. Both implement :class:`Workspace` so tool bodies
just call ``await workspace.read_text(...)`` etc. without caring which
backend is active.

The active backend is selected by a contextvar (see
:mod:`omniagents.workspace`); the SDK's sandbox runtime sets it to a
``SandboxWorkspace`` for the duration of a sandbox run, and host-mode is
the default.
"""

from __future__ import annotations

import abc
import posixpath
from typing import Dict, List, Literal, Optional, Sequence, Union

from .types import ExecResult, FileEntry, GrepMatch, WorkspaceEscapeError

CommandLike = Union[str, Sequence[str]]


class ProcessHandle(abc.ABC):
    """Background-process handle returned by ``Workspace.spawn``.

    The same shape works whether the process runs on the host or inside a
    sandbox session, so ``omni-code``'s bash-jobs tracker can hold a list
    of handles and not care about the backend.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from ._ownership import guard_operation

        method = cls.__dict__.get("write_stdin")
        if method is not None and not getattr(method, "__isabstractmethod__", False):
            setattr(cls, "write_stdin", guard_operation(method))

    process_id: str
    """An opaque identifier unique within this process. Always set."""

    pid: Optional[int] = None
    """Underlying OS pid, when the backend exposes one (host always; some
    sandbox backends may set this if the session surfaces it). ``None``
    otherwise — callers must not rely on it for cross-process operations."""

    @abc.abstractmethod
    async def status(
        self,
    ) -> Literal["running", "completed", "killed", "failed"]: ...

    @abc.abstractmethod
    async def logs(self, *, tail_lines: Optional[int] = None) -> str: ...

    @abc.abstractmethod
    async def write_stdin(self, data: str) -> None: ...

    @abc.abstractmethod
    async def wait(self, timeout: Optional[float] = None) -> ExecResult: ...

    @abc.abstractmethod
    async def terminate(self, *, force_after: float = 5.0) -> None: ...


class Workspace(abc.ABC):
    """Async filesystem + shell interface used by sandbox-aware tools.

    Path semantics: relative paths are resolved against ``self.root``.
    Confinement depends on the backend: plain host workspaces allow paths
    outside the root, while mounted and sandbox workspaces enforce their
    address spaces. Call :meth:`resolve_confined` when the caller requires
    a workspace boundary regardless of backend.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from ._ownership import guard_operation

        for name in ("write_text", "write_bytes", "mkdir", "remove", "exec", "spawn"):
            method = cls.__dict__.get(name)
            if method is not None and not getattr(
                method, "__isabstractmethod__", False
            ):
                setattr(cls, name, guard_operation(method))

    root: str
    """Filesystem-absolute path of the workspace root in whichever backend
    this workspace operates against. On host: the project workspace dir;
    in sandbox: the manifest root (often ``/workspace``)."""

    scratch_root: str
    """Framework-owned scratch directory for tool-internal state — worktrees,
    spawn log files, anything that must persist across a tool call but
    should *not* mix with the user's workspace files. Deliberately outside
    :attr:`root` so glob/grep/git on the workspace don't trip over it.

    * Host: typically ``~/.omniagents/scratch`` (or
      ``$OMNIAGENTS_SCRATCH``).
    * Sandbox: a writable container path outside the manifest mount (e.g.
      ``/tmp/omniagents/scratch``). The path is reachable via
      :meth:`exec`; tool code that only needs to ``mkdir``/``rm``/run
      commands there works without further setup.

    Tools that need their own subtree should ``mkdir`` a named subdir
    under ``scratch_root`` rather than writing directly to it."""

    supports_direct_host_scan: bool = False
    """Whether the workspace is one ordinary host tree that may be scanned
    directly below :attr:`root`.

    RPC implementations use this capability instead of guessing from a
    concrete class. Composite host workspaces intentionally leave it false:
    their logical directory tree is supplied by the Workspace adapter even
    though individual operations ultimately target host files.
    """

    def relative_path(self, absolute_path: str) -> Optional[str]:
        """Return an environment-local path for an authorized absolute path.

        Ordinary workspaces are one physical tree, so containment is a simple
        root-prefix check. Composite workspaces override this to map explicitly
        mounted host sources back to their logical mount paths. Callers such as
        the Git RPC must ask the Workspace rather than reimplementing physical
        path containment against ``root``.
        """

        candidate = posixpath.normpath(str(absolute_path)).rstrip("/")
        root = posixpath.normpath(str(self.root)).rstrip("/")
        if candidate == root:
            return "."
        prefix = root + "/"
        return candidate[len(prefix) :] if candidate.startswith(prefix) else None

    def contains_path(self, absolute_path: str) -> bool:
        """Whether *absolute_path* belongs to this Workspace boundary."""

        return self.relative_path(absolute_path) is not None

    @property
    def enforces_confinement(self) -> bool:
        """Whether :meth:`resolve` itself refuses paths outside the root.

        True everywhere except :class:`~omniagents.workspace.host.HostWorkspace`
        and its direct-tree subclasses, where the agent's file tools
        deliberately reach the whole machine, exactly as the shell already
        does. This is a property of the class, not a setting: there is no
        "host but fenced" mode to configure. Callers that need the boundary
        regardless use :meth:`resolve_confined`.
        """

        return True

    def resolve_confined(self, path: str) -> str:
        """:meth:`resolve`, with the workspace-root boundary always enforced.

        The wire surfaces (``fs_*``, ``git_*``) promise every path they touch
        stays inside the root: their canonicalizers already reject absolute
        and ``..`` spellings, so the only way out is a symlink *inside* the
        root pointing outside it. A confined workspace catches that in
        ``resolve``; an unconfined one does not, and would otherwise serve a
        file outside the root to a client that was promised it could not
        happen. Confinement is therefore the *caller's* policy, not a
        property of the workspace: the agent's tools want the machine, the
        wire surfaces want the root.

        Containment is asked of the workspace (:meth:`contains_path`) rather
        than recomputed here, so a composite workspace's authorized mounts —
        and a :class:`GitMetadataHostWorkspace`'s external Git metadata —
        stay reachable. Confined workspaces pay nothing: ``resolve`` already
        refused, so the extra check is skipped entirely.
        """

        absolute = self.resolve(path)
        if self.enforces_confinement:
            return absolute
        if not self.contains_path(absolute):
            raise WorkspaceEscapeError(
                f"Path {path!r} resolves to {absolute!r}, outside workspace "
                f"root {self.root!r}"
            )
        return absolute

    def io_path(self, absolute_path: str) -> Optional[str]:
        """Return a spelling accepted by Workspace I/O for an authorized path.

        Ordinary and sandbox workspaces use their environment-relative path.
        A composite host workspace may retain an authorized absolute spelling
        for framework-internal metadata that must not become a visible mount.
        """

        return self.relative_path(absolute_path)

    def logical_path(self, path: str) -> str:
        """Return the environment-visible absolute spelling of *path*.

        :meth:`resolve` answers the physical path used for I/O.  Those are the
        same thing for ordinary host and sandbox workspaces, but not for a
        composite workspace whose logical mount is backed by a host directory.
        RPC responses and process ``cwd`` values must use this method instead
        of leaking the physical backing path to clients.
        """

        return self.resolve(path)

    # ----------------------------- file I/O ----------------------------- #

    @abc.abstractmethod
    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str: ...

    @abc.abstractmethod
    async def read_bytes(self, path: str) -> bytes: ...

    @abc.abstractmethod
    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        append: bool = False,
        create_dirs: bool = True,
    ) -> None: ...

    @abc.abstractmethod
    async def write_bytes(
        self,
        path: str,
        data: bytes,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> None: ...

    @abc.abstractmethod
    async def exists(self, path: str) -> bool: ...

    @abc.abstractmethod
    async def is_file(self, path: str) -> bool: ...

    @abc.abstractmethod
    async def is_dir(self, path: str) -> bool: ...

    @abc.abstractmethod
    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> None: ...

    @abc.abstractmethod
    async def remove(self, path: str, *, recursive: bool = False) -> None: ...

    # ---------------------------- discovery ----------------------------- #

    @abc.abstractmethod
    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        """List one directory as substrate-independent
        :class:`~omniagents.workspace.types.FileEntry` values.

        Every backend returns entries whose ``name`` is a bare basename
        and whose ``path`` is a **workspace-relative POSIX path** — see
        :class:`FileEntry` for the full contract. ``include_hidden``
        filters on the basename, so a dotfile is a dotfile on every
        substrate.

        Failures are ``OSError``-compatible on every backend, never the
        substrate's own transport exception:

        * :class:`FileNotFoundError` — *path* does not exist;
        * :class:`NotADirectoryError` — *path* exists but is not a
          directory;
        * :class:`PermissionError` — *path* cannot be read;
        * plain :class:`OSError` for anything else the substrate refused.

        A single entry the backend cannot describe is skipped, not
        raised: one bad entry must never abort a whole listing.
        """
        ...

    @abc.abstractmethod
    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]: ...

    @abc.abstractmethod
    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]: ...

    # ------------------------- shell execution -------------------------- #

    @abc.abstractmethod
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
    ) -> ExecResult: ...

    @abc.abstractmethod
    async def spawn(
        self,
        command: CommandLike,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: bool = True,
        user: Optional[str] = None,
        pty: bool = False,
    ) -> ProcessHandle: ...

    # --------------------------- capabilities --------------------------- #

    @property
    def supports_shell(self) -> bool:
        """Whether :meth:`exec` and :meth:`spawn` can run arbitrary commands.

        Default ``True`` — host and standard sandbox backends always do.
        Backends without a real shell (e.g. :class:`PyodideWorkspace`)
        override to ``False`` so tools like ``execute_bash`` can degrade
        gracefully with a clear message instead of hanging or surfacing a
        generic exception.
        """
        return True

    # --------------------------- path helpers --------------------------- #

    @abc.abstractmethod
    def resolve(self, path: str) -> str:
        """Resolve *path* against ``self.root`` into an absolute path.

        Backends with :attr:`enforces_confinement` reject paths outside their
        address space with :class:`WorkspaceEscapeError`. Plain host paths
        are not confined; use :meth:`resolve_confined` to require a boundary.
        """

    def relative_to_workspace(self, path: str) -> str:
        """Inverse of :meth:`resolve` — return *path* relative to ``root``."""
        import os.path

        absolute = self.resolve(path)
        return os.path.relpath(absolute, self.root)
