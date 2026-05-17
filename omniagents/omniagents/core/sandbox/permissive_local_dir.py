"""``PermissiveLocalDir`` — seed a sandbox workspace from a *host*
directory that may or may not be a git repo, using ``.gitignore``
semantics to decide what's in scope and silently skipping symlinks.

Why this exists alongside the SDK's ``LocalDir`` and our own
:class:`~omniagents.core.sandbox.local_git_archive.LocalGitArchive`:

* The SDK's ``LocalDir`` is hardened for *untrusted* file trees: it
  refuses every symlink and ignores ``.gitignore`` (knows nothing about
  git). For a single-user launcher loading the user's *own* folder
  this is overkill — every stray ``node_modules`` link aborts the seed.
* ``LocalGitArchive`` is the right tool when the source is a git repo,
  but requires ``.git/`` to exist on the host. Many user folders aren't
  git repos and the user has explicitly signaled "this is just a
  folder, don't impose git on me."

``PermissiveLocalDir`` does the pragmatic thing for that case:

1. Walk the source tree.
2. At each directory, read any ``.gitignore`` present and add its
   patterns to the cumulative ignore set (anchored at that directory,
   matching gitignore semantics).
3. Skip every symlink silently — no following (would escape ``src``),
   no including (would extract as a dangling link in the container).
4. Stream regular files into a tarball; ship one tar to the container
   and extract.

Threat-model note: this entry trusts the source tree. It is appropriate
for a single-user launcher loading the user's own files, or for a
multi-tenant deployment where each tenant has an isolated process and
their source paths cannot be controlled by another tenant. Not safe
for a shared-process service archiving paths that arbitrary network
clients could swap mid-walk (TOCTOU). For that use case, use the
SDK's ``LocalDir`` (or wrap in git and use ``LocalGitArchive``).
"""

from __future__ import annotations

import asyncio
import io
import os
import stat
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pathspec
from agents.sandbox.entries.base import BaseEntry
from agents.sandbox.materialization import MaterializedFile

if TYPE_CHECKING:
    from agents.sandbox.session.base_sandbox_session import BaseSandboxSession


__all__ = ["PermissiveLocalDir", "PermissiveLocalDirError"]


_STAGING_TAR_NAME = ".omni_permissive_local_dir.seed.tar"
_GITIGNORE_NAME = ".gitignore"


class PermissiveLocalDirError(Exception):
    """Raised when :class:`PermissiveLocalDir` cannot materialize the
    workspace. Reasons: source path doesn't exist or isn't a directory,
    in-container tar extraction failed.
    """

    def __init__(
        self,
        message: str,
        *,
        src: Path | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.src = src
        self.stderr = stderr


class PermissiveLocalDir(BaseEntry):
    """Manifest entry: seed a host directory into the sandbox using
    gitignore-aware enumeration with silent symlink skipping. See
    module docstring for the threat-model rationale.

    Fields:

    * ``src`` — host path to the source directory. Must exist when
      :meth:`apply` runs; relative paths are resolved against
      ``base_dir``.
    """

    type: Literal["permissive_local_dir"] = "permissive_local_dir"
    is_dir: bool = True
    src: Path

    def model_post_init(self, context: object, /) -> None:
        _ = context
        self.permissions.directory = True

    async def apply(
        self,
        session: "BaseSandboxSession",
        dest: Path,
        base_dir: Path,
    ) -> list[MaterializedFile]:
        src_abs = self.src if self.src.is_absolute() else (base_dir / self.src)
        try:
            src_resolved = src_abs.resolve(strict=True)
        except (FileNotFoundError, OSError) as e:
            raise PermissiveLocalDirError(
                f"permissive_local_dir: src path does not exist: {src_abs}",
                src=src_abs,
            ) from e

        if not src_resolved.is_dir():
            raise PermissiveLocalDirError(
                f"permissive_local_dir: src must be a directory: {src_resolved}",
                src=src_resolved,
            )

        # Host-mode short-circuit: when running against ``unix_local`` and
        # the destination path overlaps the source on the host filesystem
        # (src == dest, src inside dest, or dest inside src), the seed is
        # either redundant (workspace already contains the files) or
        # recursive (would tar src into a subdir of itself, the next boot
        # picks up the subdir, geometric explosion → OOM). The agent
        # already sees the host workspace in unix_local mode, so we
        # no-op the materialization. Permissions still applied so the
        # entry's declared metadata wins.
        from agents.sandbox.sandboxes.unix_local import (  # noqa: PLC0415
            UnixLocalSandboxSession,
        )

        if isinstance(session, UnixLocalSandboxSession) and _host_paths_overlap(
            src_resolved, Path(dest)
        ):
            await session.mkdir(dest, parents=True)
            await self._apply_metadata(session, dest)
            return []

        archive_bytes = await asyncio.to_thread(_build_dir_tar, src_resolved)

        await session.mkdir(dest, parents=True)
        staging_path = dest / _STAGING_TAR_NAME
        await session.write(staging_path, io.BytesIO(archive_bytes))

        try:
            extract = await session.exec(
                "tar", "xf", str(staging_path), "-C", str(dest), shell=False
            )
            if not extract.ok():
                raise PermissiveLocalDirError(
                    "permissive_local_dir: tar extract failed",
                    src=src_resolved,
                    stderr=extract.stderr.decode("utf-8", errors="replace"),
                )
        finally:
            try:
                await session.exec("rm", "-f", str(staging_path), shell=False)
            except Exception:
                pass

        await self._apply_metadata(session, dest)
        return []


def _host_paths_overlap(a: Path, b: Path) -> bool:
    """Return True if two host paths overlap — equal, or one contains
    the other after symlink resolution.

    *b* may not exist on disk yet (it's the seed destination that
    ``session.mkdir`` will create). ``resolve(strict=False)`` returns
    the absolute path with the existing prefix's symlinks resolved and
    the non-existent suffix left intact — sufficient for the overlap
    check the caller cares about.
    """
    a_norm = a.resolve(strict=False)
    b_norm = b.resolve(strict=False)
    if a_norm == b_norm:
        return True
    try:
        a_norm.relative_to(b_norm)
        return True
    except ValueError:
        pass
    try:
        b_norm.relative_to(a_norm)
        return True
    except ValueError:
        pass
    return False


def _build_dir_tar(src: Path) -> bytes:
    """Walk *src*, respect any ``.gitignore`` encountered, silently
    skip symlinks, stream regular files into an in-memory tarball.

    Gitignore semantics are scoped: a ``.gitignore`` at ``a/b/`` applies
    to paths under ``a/b/`` only. We carry a stack of ``(anchor_rel,
    PathSpec)`` down the walk and check each candidate against every
    spec in scope.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Stack entries: (anchor_rel_posix, PathSpec). Empty anchor =
        # the source root itself.
        spec_stack: list[tuple[str, pathspec.PathSpec]] = []
        _walk(src, Path("."), tar, spec_stack)
    return buf.getvalue()


def _walk(
    src_root: Path,
    rel_dir: Path,
    tar: tarfile.TarFile,
    spec_stack: list[tuple[str, pathspec.PathSpec]],
) -> None:
    abs_dir = src_root / rel_dir
    try:
        entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
    except (FileNotFoundError, PermissionError):
        return

    # Pick up a .gitignore in this dir, if any, before processing files
    # — patterns from the local .gitignore can affect siblings.
    anchor_rel = "" if rel_dir == Path(".") else rel_dir.as_posix()
    pushed_local = False
    gitignore_path = abs_dir / _GITIGNORE_NAME
    if gitignore_path.is_file():
        try:
            lines = gitignore_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            spec = pathspec.PathSpec.from_lines("gitignore", lines)
            spec_stack.append((anchor_rel, spec))
            pushed_local = True
        except OSError:
            pass

    try:
        for entry in entries:
            rel_path = rel_dir / entry.name if rel_dir != Path(".") else Path(entry.name)
            rel_posix = rel_path.as_posix()

            try:
                st = entry.stat(follow_symlinks=False)
            except (FileNotFoundError, OSError):
                continue

            if stat.S_ISLNK(st.st_mode):
                continue  # silent skip — see module docstring

            is_dir = stat.S_ISDIR(st.st_mode)

            if _is_ignored(rel_posix, is_dir, spec_stack):
                continue

            if is_dir:
                # Emit a directory entry for tar legibility, then recurse.
                tar.addfile(_dir_tarinfo(rel_posix))
                _walk(src_root, rel_path, tar, spec_stack)
                continue

            if stat.S_ISREG(st.st_mode):
                try:
                    with open(entry.path, "rb") as f:
                        info = tarfile.TarInfo(name=rel_posix)
                        info.size = st.st_size
                        info.mode = st.st_mode & 0o777
                        info.mtime = int(st.st_mtime)
                        tar.addfile(info, f)
                except (FileNotFoundError, PermissionError):
                    continue
            # Other types (sockets, fifos, devices): skip.
    finally:
        if pushed_local:
            spec_stack.pop()


def _is_ignored(
    rel_posix: str,
    is_dir: bool,
    spec_stack: list[tuple[str, pathspec.PathSpec]],
) -> bool:
    """Apply every PathSpec in scope. A spec at ``anchor_rel`` only
    receives paths beneath that anchor, re-rooted at the anchor. We
    pass directories with a trailing ``/`` so directory-only patterns
    (``foo/``) match correctly.
    """
    candidate = rel_posix + ("/" if is_dir else "")
    for anchor_rel, spec in spec_stack:
        if anchor_rel:
            prefix = anchor_rel + "/"
            if not rel_posix.startswith(prefix):
                continue
            scoped = rel_posix[len(prefix):] + ("/" if is_dir else "")
        else:
            scoped = candidate
        if spec.match_file(scoped):
            return True
    return False


def _dir_tarinfo(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name + "/")
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info
