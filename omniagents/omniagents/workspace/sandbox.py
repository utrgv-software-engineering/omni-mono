"""Sandbox-side Workspace implementation.

Wraps an SDK ``BaseSandboxSession``: file I/O goes through ``session.read`` /
``session.write`` / ``session.ls`` / ``session.rm`` / ``session.mkdir``;
shell execution through ``session.exec`` and ``session.pty_exec_start``;
discovery (glob/grep) shells out to ``find`` and ``grep`` inside the
sandbox.

Path semantics: paths are resolved against the manifest root exactly as
they are on the host. :meth:`SandboxWorkspace.resolve` normalizes ``..``
lexically and raises :class:`WorkspaceEscapeError` for anything that
would step outside :attr:`root`, so the two substrates refuse the same
inputs with the same typed error. Symlink escapes cannot be caught
lexically; those are refused by the SDK session's own path policy, whose
rejection is translated back into :class:`WorkspaceEscapeError` here
instead of leaking an SDK transport exception to callers.
"""

from __future__ import annotations

import io
import posixpath
import shlex
import time
import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from ._entries import entry_basename, log_skipped_entry, make_entry, sort_entries
from .base import CommandLike, ProcessHandle, Workspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

#: The SDK's stable ``ErrorCode`` values (``SandboxError.error_code``),
#: mapped onto the workspace layer's error types. ``invalid_manifest_path``
#: is how the session's path policy refuses an absolute path, a ``..``
#: escape, or a symlink whose target resolves outside every allowed root;
#: mapping it to :class:`WorkspaceEscapeError` is what lets the ``fs_*`` /
#: ``git_*`` surfaces answer with the same typed wire error on both
#: substrates instead of an untyped transport failure.
_SDK_ERROR_CODES: Dict[str, type] = {
    "invalid_manifest_path": WorkspaceEscapeError,
    "workspace_read_not_found": FileNotFoundError,
    "workspace_root_not_found": FileNotFoundError,
    "apply_patch_file_not_found": FileNotFoundError,
}

#: ``errno``-style markers in a container's own error text, for failures
#: the SDK reports as a generic non-zero exec (``ls`` failing, say) whose
#: only detail is the command's stderr.
_OS_ERROR_MARKERS = (
    ("no such file or directory", FileNotFoundError),
    ("file not found", FileNotFoundError),
    ("not a directory", NotADirectoryError),
    ("permission denied", PermissionError),
    ("access denied", PermissionError),
)

#: Prose fallbacks for the path-policy rejection, in case an SDK version
#: raises it without a machine-readable code.
_ESCAPE_MARKERS = (
    "manifest path must be relative",
    "manifest path must not escape root",
    "invalid_manifest_path",
)


def _session_error_text(error: BaseException) -> str:
    """Everything a session error can tell us, lowercased.

    SDK errors carry the container's stdout/stderr in a ``context`` mapping
    rather than in ``str(error)``, so a message-only match would miss the
    ``ls`` failure we need to classify.
    """
    parts = [str(error), type(error).__name__]
    context = getattr(error, "context", None)
    if isinstance(context, dict):
        parts.extend(str(value) for value in context.values())
    result = getattr(error, "result", None)
    for stream in ("stdout", "stderr"):
        raw = getattr(result, stream, None)
        if isinstance(raw, bytes):
            parts.append(raw.decode("utf-8", errors="replace"))
        elif isinstance(raw, str):
            parts.append(raw)
    return " ".join(parts).lower()


def _flag(entry: Any, attribute: str) -> bool:
    """Read one boolean-ish attribute off a session's directory entry.

    The SDK's ``FileEntry.is_dir`` is a **method**, so the obvious
    ``bool(getattr(entry, "is_dir", False))`` evaluated a bound method and
    was ``True`` for every entry — files, symlinks and FIFOs all reported
    as directories. Call it when it's callable; never trust its truthiness.
    """
    value = getattr(entry, attribute, False)
    if callable(value):
        try:
            value = value()
        except Exception:  # pragma: no cover - defensive
            return False
    return bool(value)


def _entry_kind(entry: Any) -> "tuple[bool, bool]":
    """``(is_dir, is_symlink)`` for one session entry, lstat semantics.

    The SDK's ``kind`` enum is authoritative when present: an entry is
    described by *itself*, so a symlink is a symlink whatever it points at
    (and ``ls -la`` cannot tell us what that is without another round
    trip). Sessions without ``kind`` fall back to boolean/callable
    attributes.
    """
    kind = getattr(entry, "kind", None)
    kind_value = str(getattr(kind, "value", kind) or "")
    if kind_value:
        return kind_value == "directory", kind_value == "symlink"
    return _flag(entry, "is_dir"), _flag(entry, "is_symlink")


def _entry_mtime(entry: Any) -> Optional[datetime]:
    raw = getattr(entry, "mtime", None)
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)) and raw > 0:
        try:
            return datetime.fromtimestamp(raw)
        except (OverflowError, OSError, ValueError):  # pragma: no cover
            return None
    return None


def _drop_ambiguous(entries: List[FileEntry], directory: str) -> List[FileEntry]:
    """Remove entries whose path the substrate reported more than once.

    The SDK's ``ls`` parser splits the ``ls -la`` line on whitespace, so a
    filename with a *leading* space loses it: a directory holding both
    ``" lead.txt"`` and ``"lead.txt"`` comes back as two entries both
    called ``lead.txt``, with different sizes. There is no way to tell
    from that output which row belongs to which file, and guessing gets it
    wrong half the time — ``fs_stat`` picked the first match and reported
    the 20-byte padded file's size for the 4-byte real one.

    Neither row is trustworthy, so both are dropped and logged rather than
    reported under a name that does not round-trip. This is a limitation
    of the SDK's text-parsing listing, not of the workspace layer; the
    honest answer here is "this listing cannot describe those files".
    """
    seen: Dict[str, int] = {}
    for entry in entries:
        seen[entry.path] = seen.get(entry.path, 0) + 1
    duplicated = {path for path, count in seen.items() if count > 1}
    if not duplicated:
        return entries
    for path in sorted(duplicated):
        log_skipped_entry(
            "SandboxWorkspace",
            directory,
            ValueError(
                f"the substrate listing reported {path!r} {seen[path]} times; "
                "the entries cannot be told apart, so none is reported"
            ),
        )
    return [entry for entry in entries if entry.path not in duplicated]


def _translate_session_error(error: BaseException, path: str) -> BaseException:
    """Map one SDK session failure onto the workspace layer's error types.

    Returns the exception to raise; the caller chains the original as
    ``__cause__`` so nothing about the substrate failure is lost.
    """
    if isinstance(error, (WorkspaceEscapeError, OSError)):
        return error
    code = getattr(error, "error_code", None)
    mapped = _SDK_ERROR_CODES.get(str(code)) if code is not None else None
    if mapped is WorkspaceEscapeError:
        return WorkspaceEscapeError(
            f"Path {path!r} was refused by the sandbox path policy: {error}"
        )
    if mapped is not None:
        return mapped(f"{path}: {error}")
    text = _session_error_text(error)
    if any(marker in text for marker in _ESCAPE_MARKERS):
        return WorkspaceEscapeError(
            f"Path {path!r} was refused by the sandbox path policy: {error}"
        )
    for marker, os_error in _OS_ERROR_MARKERS:
        if marker in text:
            return os_error(f"{path}: {error}")
    return OSError(f"{path}: {error}")


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
        root: Optional[str] = None,
        scratch_root: str = "/tmp/omniagents/scratch",
        default_user: Optional[str] = None,
    ) -> None:
        self._session = session
        # Follow the session's manifest root unless told otherwise. Hardcoding
        # "/workspace" silently disagreed with any sandbox rooted elsewhere,
        # and every path then resolved somewhere the session never looks.
        if root is None:
            try:
                root = str(session.state.manifest.root)
            except AttributeError:
                root = "/workspace"
        # Normalized once so every later comparison is against one spelling:
        # an unnormalized ``root="/workspace/"`` made ``resolve(".")`` answer
        # ``/workspace/`` while ``resolve("a.txt")`` answered
        # ``/workspace/a.txt``, and the two disagree as dict keys, in
        # ``posixpath.relpath`` and in the ``$root|$root/*`` shell guard.
        self.root = posixpath.normpath(root) if root else "/"
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

    @property
    def session(self) -> Any:
        """The explicitly bound sandbox session for environment adapters.

        Server functions must reach it through an EnvironmentLease's workspace,
        never through process-global serve state.
        """

        return self._session

    # ----------------------------- helpers ----------------------------- #

    def resolve(self, path: str) -> str:
        """Absolute container path for *path*, confined to :attr:`root`.

        Relative paths anchor at the root; absolute paths are accepted
        only when they are already inside it. ``..`` is normalized
        lexically *before* the check, so ``sub/../../etc`` is refused
        rather than handed to the container as
        ``/workspace/sub/../../etc``. This mirrors
        :meth:`HostWorkspace.resolve` and :meth:`PyodideWorkspace.resolve`
        — one typed :class:`WorkspaceEscapeError` on every substrate.

        Lexical normalization cannot see through symlinks (resolving one
        would need a container round trip, and this method is sync). A
        symlink whose target leaves the root is refused by the SDK
        session's path policy on the actual read/write/list call, and that
        rejection is translated back into ``WorkspaceEscapeError`` too.
        """
        root = PurePosixPath(self.root)
        if not path or path == ".":
            return self.root
        candidate = PurePosixPath(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        # posixpath.normpath collapses "." and ".." without touching disk.
        full = PurePosixPath(posixpath.normpath(str(candidate)))
        if full != root and root not in full.parents:
            raise WorkspaceEscapeError(
                f"Path {path!r} resolves to {str(full)!r}, outside workspace "
                f"root {self.root!r}"
            )
        return str(full)

    # ----------------------------- file I/O ---------------------------- #

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        data = await self.read_bytes(path)
        return data.decode(encoding, errors="replace")

    async def read_bytes(self, path: str) -> bytes:
        absolute = self.resolve(path)
        try:
            stream = await self._session.read(
                PurePosixPath(absolute), user=self._default_user
            )
        except Exception as error:
            raise _translate_session_error(error, absolute) from error
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
        try:
            await self._session.write(
                PurePosixPath(absolute), io.BytesIO(data), user=self._default_user
            )
        except Exception as error:
            raise _translate_session_error(error, absolute) from error

    async def _test(self, flag: str, path: str) -> bool:
        """``test -<flag>`` on *path*, refused when it leaves the root.

        ``HostWorkspace``'s predicates resolve symlinks and answer
        ``False`` for anything outside the root; a bare ``test`` in the
        container follows symlinks silently, so a link pointing at
        ``/etc`` would answer ``True`` there and ``False`` on the host for
        the same call. The shell prefix realpaths the argument and refuses
        it when the target escapes — a refusal can only ever turn a
        ``True`` into a ``False``, never the reverse.

        ``readlink -f`` exists in coreutils and busybox. If an image
        somehow lacks it the probe answers ``False`` rather than falling
        back to a lexical check: the lexical path of a symlink is inside
        the root by construction, so a fallback would let ``test -f``
        follow the link and answer for a file outside the workspace —
        exactly what this guard exists to prevent. Failing closed costs a
        false negative on an image with no ``readlink``; failing open
        would cost the guarantee.
        """
        try:
            absolute = self.resolve(path)
        except WorkspaceEscapeError:
            # Mirrors HostWorkspace: a path outside the workspace does not
            # "exist" as far as this workspace is concerned, rather than
            # raising at a predicate call site.
            return False
        target = shlex.quote(absolute)
        root = shlex.quote(self.root)
        script = (
            f"real=$(readlink -f -- {target} 2>/dev/null) || exit 1; "
            f'case "$real" in {root}|{root}/*) ;; *) exit 1 ;; esac; '
            f"test -{flag} {target}"
        )
        result = await self._session.exec(
            "sh", "-lc", script, shell=False, user=self._default_user
        )
        return result.exit_code == 0

    async def exists(self, path: str) -> bool:
        return await self._test("e", path)

    async def is_file(self, path: str) -> bool:
        return await self._test("f", path)

    async def is_dir(self, path: str) -> bool:
        return await self._test("d", path)

    async def is_exec_dir(self, path: str) -> bool:
        # Shell cwd may be outside the file-tool root (for example /tmp).
        # Probe through the same session/user as exec, without file-policy fencing.
        absolute = posixpath.normpath(posixpath.join(self.root, path))
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
        """List one container directory in the shared ``FileEntry`` shape.

        The SDK's own ``FileEntry`` is a different type with different
        semantics, and translating it naively is what made this backend
        diverge from every other one:

        * it has **no** ``name`` field, so falling back to ``path`` put the
          *absolute container path* in ``name`` — hidden-file filtering
          never matched, and every consumer that joined ``name`` or
          ``path`` onto a workspace-relative base built a broken path;
        * ``is_dir`` is a **method**, so ``bool(getattr(entry, "is_dir"))``
          was ``True`` for every entry — files, symlinks and FIFOs all
          reported as directories;
        * ``kind`` already distinguishes symlinks, but ``is_symlink`` was
          left at its default ``False``, so callers that refuse to descend
          into symlinks (this module's own scanners among them) descended
          anyway.

        Everything now goes through ``EntryKind`` and ``make_entry``.
        """
        # `respect_gitignore` is documented but not enforced — same fallback
        # as HostWorkspace when no git binary is available.
        _ = respect_gitignore
        absolute = self.resolve(path)
        try:
            sdk_entries = await self._session.ls(
                PurePosixPath(absolute), user=self._default_user
            )
        except Exception as error:
            # Never leak the container's transport error: callers get the
            # same OSError family the host backend raises.
            raise _translate_session_error(error, absolute) from error

        out: List[FileEntry] = []
        for sdk_entry in sdk_entries:
            raw_path = str(getattr(sdk_entry, "path", "") or "")
            if not raw_path:
                continue
            # The SDK always reports absolute container paths; a duck-typed
            # session reporting bare names is anchored at the directory we
            # just listed rather than mis-read as workspace-relative.
            child = (
                posixpath.normpath(raw_path)
                if raw_path.startswith("/")
                else posixpath.normpath(posixpath.join(absolute, raw_path))
            )
            # ``ls -la`` on a *file* echoes the argument back as the only
            # entry: that is the container telling us the target is not a
            # directory. The host backend raises NotADirectoryError for
            # the same call, so this one does too.
            if child == absolute:
                raise NotADirectoryError(f"{absolute}: not a directory")
            name = entry_basename(child)
            if not include_hidden and name.startswith("."):
                continue
            is_dir, is_symlink = _entry_kind(sdk_entry)
            try:
                out.append(
                    make_entry(
                        name=name,
                        path=posixpath.relpath(child, self.root),
                        is_dir=is_dir,
                        size=getattr(sdk_entry, "size", None),
                        # The SDK's own listing carries no timestamp, so this
                        # is ``None`` on a real container rather than
                        # fabricated; a substrate that does expose one is
                        # passed through.
                        mtime=_entry_mtime(sdk_entry),
                        is_symlink=is_symlink,
                    )
                )
            except ValueError as error:
                # One entry the substrate described in a way that cannot be
                # expressed as a workspace-relative path is skipped, never
                # raised — a single bad entry must not abort the listing —
                # but never silently: see log_skipped_entry.
                log_skipped_entry("SandboxWorkspace", absolute, error)
                continue
        return sort_entries(_drop_ambiguous(out, absolute), sort_by)

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

    def _result(self) -> ExecResult:
        return ExecResult(
            stdout=self._buffer.decode("utf-8", errors="replace"),
            stderr="",
            exit_code=self._exit_code if self._exit_code is not None else -1,
            wall_time_seconds=time.perf_counter() - self._start,
        )

    @staticmethod
    def _is_terminal_session_error(error: Exception) -> bool:
        if not isinstance(error, (KeyError, ValueError)):
            return False
        message = str(error).lower()
        return any(
            marker in message
            for marker in ("unknown pty", "not found", "no such process")
        )

    async def wait(self, timeout: Optional[float] = None) -> ExecResult:
        # Sandbox PTY doesn't have a clean "wait until done" — poll via
        # zero-stdin reads which return when output is available or process
        # exits. For now: do a single best-effort read; callers that need
        # strict wait semantics should poll.
        if self._exit_code is not None:
            return self._result()
        try:
            update = await self._session.pty_write_stdin(
                session_id=self._session_id,
                chars="",
                yield_time_s=timeout or 0.0,
            )
        except Exception as error:
            if not self._is_terminal_session_error(error):
                raise
            self._exit_code = 1
            return self._result()
        if getattr(update, "output", None):
            self._buffer.extend(update.output)
        self._exit_code = update.exit_code
        return self._result()

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
