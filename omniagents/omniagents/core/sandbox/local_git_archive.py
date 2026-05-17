"""``LocalGitArchive`` — seed a sandbox workspace from a *host* git repo
using ``git ls-files``-driven enumeration and a streamed tarball.

Why this exists alongside the SDK's ``LocalDir``/``GitRepo`` entries:

* ``LocalDir`` reads the working tree file-by-file with hardened
  ``O_NOFOLLOW`` / pinned-fd traversal designed for *multi-tenant*
  privileged-copy scenarios where the source path is untrusted. It
  refuses every symlink, never respects ``.gitignore`` (because it
  doesn't know about git), and pushes every file through
  ``session.write`` — slow on real repos with ``node_modules``,
  caches, build output, etc.
* ``GitRepo`` clones a *remote* URL inside the container. Useful when
  the source is a remote, useless when the source is the user's local
  checkout that may have uncommitted state, local-only branches, or no
  remote at all.

``LocalGitArchive`` uses git as the *truth source for what to ship*
without requiring a commit:

1. Run ``git ls-files --cached --others --exclude-standard -z`` on the
   host repo. This lists tracked files + untracked-but-not-ignored
   files (same set you'd see in ``git status``, minus the ignored ones).
   Works in repos with or without an initial commit.
2. Build an in-memory tarball from those paths.
3. Ship one tar to the container; extract.

Properties:

* **Respects** ``.gitignore`` **automatically.** Anything git would
  ignore is excluded. No ``node_modules``, no ``dist/``, no IDE state.
* **Includes uncommitted work.** Working-tree edits and untracked-but-
  not-ignored files reach the agent immediately — matches user
  intuition when reopening a project they were just editing.
* **Symlinks preserved when their target stays inside the workspace;
  others silently dropped.** ``tarfile`` adds preserved entries as
  ``SYMTYPE`` with the literal ``os.readlink`` target — no following,
  no dereferencing of the target bytes. Symlinks whose target is
  absolute, or whose relative target normalizes outside the archive
  root, are skipped. The cross-tenant exfil argument (the launcher
  never reads the target's bytes) is unchanged; the stricter policy
  matches the SDK's snapshot restore validator
  (``_validate_symlink_target`` in ``agents.sandbox.util.tar_utils``,
  invoked with ``allow_external_symlink_targets=False``), so
  workspaces seeded by this entry can be re-hydrated from snapshots
  without tripping that validator. Common offender: ``.fvm/`` or
  similar dev-tool symlinks left over from another machine, whose
  absolute target doesn't exist on this host *or* the container.
* **One round trip.** Single ``session.write`` of the tar, one
  ``tar xf``. O(tracked-file bytes), not O(working-tree file count).

What it does *not* include: paths git ignores (``.gitignore``,
``.git/info/exclude``, global excludes). The agent can still see git
history via the ``.git`` directory itself — ``git ls-files`` lists
``.git`` only when explicitly tracked (it isn't), but we ship ``.git``
as a directory anyway via a separate code path so in-container git
commands work.

Residual concern worth naming: walking the working tree (vs. reading
from ``.git/objects``) means a TOCTOU window exists where a co-tenant
with write access to paths under ``src`` could swap a regular file
for a symlink mid-walk. The current implementation does not pin
file descriptors to defend against that — appropriate for the
launcher's current deployment shape (single-tenant or per-tenant
isolated processes), not safe for a clerk-style multi-tenant service
sharing read of many users' homes from one process.
"""

from __future__ import annotations

import asyncio
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from agents.sandbox.entries.base import BaseEntry
from agents.sandbox.materialization import MaterializedFile
from pydantic import Field

if TYPE_CHECKING:
    from agents.sandbox.session.base_sandbox_session import BaseSandboxSession


__all__ = ["LocalGitArchive", "LocalGitArchiveError"]


# Where inside the destination we drop the temporary tar before
# extracting it. Hidden filename so the user's tree isn't polluted if
# extraction fails before cleanup.
_STAGING_TAR_NAME = ".omni_local_git_archive.seed.tar"


class LocalGitArchiveError(Exception):
    """Raised when ``LocalGitArchive`` cannot materialize the workspace.

    Reasons: source is not a git repo, ``ref`` does not resolve, the
    ``git`` binary is missing on the host, or the in-container ``tar``
    extraction failed. ``context`` carries machine-readable details.
    """

    def __init__(
        self,
        message: str,
        *,
        src: Path | None = None,
        ref: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.src = src
        self.ref = ref
        self.stderr = stderr


class LocalGitArchive(BaseEntry):
    """A manifest entry that seeds a host git repo into the sandbox
    workspace using ``git ls-files`` to enumerate paths and a streamed
    tarball to transport them. See module docstring for the threat-
    model rationale.

    Fields:

    * ``src`` — host path to the git repo (the directory containing
      ``.git/``, not ``.git/`` itself). Must exist when
      :meth:`apply` runs; relative paths are resolved against
      ``base_dir``.
    * ``include_untracked`` — when True (default), include
      untracked-but-not-ignored files (``git ls-files --others
      --exclude-standard``). When False, only files in the index are
      shipped. The default matches "what the user sees in their
      editor right now."
    """

    type: Literal["local_git_archive"] = "local_git_archive"
    is_dir: bool = True
    src: Path
    include_untracked: bool = Field(default=True)

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
            raise LocalGitArchiveError(
                f"local_git_archive: src path does not exist: {src_abs}",
                src=src_abs,
            ) from e

        if not src_resolved.is_dir():
            raise LocalGitArchiveError(
                f"local_git_archive: src must be a directory: {src_resolved}",
                src=src_resolved,
            )
        if not (src_resolved / ".git").exists():
            raise LocalGitArchiveError(
                f"local_git_archive: not a git repo (no .git/): {src_resolved}",
                src=src_resolved,
            )

        # Host-mode short-circuit. See PermissiveLocalDir for the full
        # rationale — same overlap pathology, same in-memory tar cost.
        # When the agent already sees the host workspace (unix_local) and
        # the source overlaps the destination, materializing is at best
        # redundant and at worst recursive.
        from agents.sandbox.sandboxes.unix_local import (  # noqa: PLC0415
            UnixLocalSandboxSession,
        )

        if isinstance(session, UnixLocalSandboxSession) and _host_paths_overlap(
            src_resolved, Path(dest)
        ):
            await session.mkdir(dest, parents=True)
            await self._apply_metadata(session, dest)
            return []

        archive_bytes = await asyncio.to_thread(
            _build_workspace_tar, src_resolved, self.include_untracked
        )

        await session.mkdir(dest, parents=True)
        staging_path = dest / _STAGING_TAR_NAME
        await session.write(staging_path, io.BytesIO(archive_bytes))

        try:
            extract = await session.exec(
                "tar",
                "xf",
                str(staging_path),
                "-C",
                str(dest),
                shell=False,
            )
            if not extract.ok():
                raise LocalGitArchiveError(
                    "local_git_archive: tar extract failed",
                    src=src_resolved,
                    stderr=extract.stderr.decode("utf-8", errors="replace"),
                )
        finally:
            try:
                await session.exec("rm", "-f", str(staging_path), shell=False)
            except Exception:
                pass

        await self._apply_metadata(session, dest)
        # Receipts: empty. Computing them would require re-reading
        # each file out of the container; ``GitRepo`` makes the same
        # call, see entries/artifacts.py:692.
        return []


def _host_paths_overlap(a: Path, b: Path) -> bool:
    """Return True if two host paths overlap — equal, or one contains
    the other. *b* may not exist on disk yet (seed destination created
    by ``session.mkdir``); ``resolve(strict=False)`` handles that.
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


def _list_git_paths(src: Path, include_untracked: bool) -> list[str]:
    """Enumerate paths git would surface in ``git status``, excluding
    ignored ones. ``-z`` outputs NUL-delimited names so we don't need
    to worry about whitespace in filenames.
    """
    args = ["git", "-C", str(src), "ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    try:
        completed = subprocess.run(  # noqa: S603,S607
            args, capture_output=True, check=False
        )
    except FileNotFoundError as e:
        raise LocalGitArchiveError(
            "local_git_archive: `git` binary not found on host",
            src=src,
        ) from e

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise LocalGitArchiveError(
            f"local_git_archive: git ls-files failed (exit {completed.returncode})",
            src=src,
            stderr=stderr or None,
        )

    raw = completed.stdout
    return [name for name in raw.decode("utf-8", errors="replace").split("\x00") if name]


def _build_workspace_tar(src: Path, include_untracked: bool) -> bytes:
    """Build an in-memory tarball containing the gitignore-respecting
    contents of *src*. Symlinks are stored as symlink entries (target
    string preserved, no dereference); files are stored as regular
    entries with the file's bytes. Missing paths are silently skipped
    (race tolerance: ``git ls-files`` reports what existed at scan time
    but a path may have been removed before we get to ``tar.add``).
    """
    rel_paths = _list_git_paths(src, include_untracked)

    buf = io.BytesIO()
    seen_dirs: set[str] = set()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in rel_paths:
            abs_path = src / rel
            try:
                st = os.lstat(abs_path)
            except FileNotFoundError:
                continue
            except OSError:
                # Permission errors etc. — surfacing per-file would be
                # noisy. The agent can re-run if needed.
                continue

            # tarfile.add(recursive=False) handles regular files and
            # symlinks correctly. For symlinks we explicitly want
            # SYMTYPE behavior (don't follow); add() with default
            # dereference=False does that.
            for parent in _missing_parent_dirs(rel, seen_dirs):
                tar.addfile(_dir_tarinfo(parent))
                seen_dirs.add(parent)

            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(abs_path)
                if not _symlink_target_stays_inside(rel, target):
                    # Absolute or escaping targets are silently skipped:
                    # the SDK's snapshot restore validator would reject
                    # them on the way back in, so shipping them now just
                    # poisons the next resume. The launcher still never
                    # reads the target's bytes, so the exfil property
                    # the older behavior leaned on is unchanged.
                    continue
                info = tarfile.TarInfo(name=rel)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                info.mode = 0o777
                tar.addfile(info)
            elif stat.S_ISREG(st.st_mode):
                with open(abs_path, "rb") as f:
                    info = tarfile.TarInfo(name=rel)
                    info.size = st.st_size
                    info.mode = st.st_mode & 0o777
                    info.mtime = int(st.st_mtime)
                    tar.addfile(info, f)
            # Other file types (sockets, fifos, devices) are skipped —
            # they wouldn't be in a normal source tree and pose no
            # value for an agent's workspace.
    return buf.getvalue()


def _missing_parent_dirs(rel_path: str, seen: set[str]) -> list[str]:
    """Yield parent directory names that haven't been emitted yet.

    Tar files don't strictly need directory entries (extractors create
    them on demand), but emitting them makes the archive's structure
    legible in ``tar tvf`` and matches what ``git archive`` produces.
    """
    parts = rel_path.split("/")
    out: list[str] = []
    cur = ""
    for part in parts[:-1]:
        cur = f"{cur}/{part}" if cur else part
        if cur not in seen:
            out.append(cur)
    return out


def _dir_tarinfo(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name + "/")
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


def _symlink_target_stays_inside(rel_path: str, target: str) -> bool:
    """Return True if a symlink at ``rel_path`` with link target
    ``target`` resolves to a path that remains inside the archive root.

    Mirrors the predicate the SDK applies on snapshot restore in
    ``agents.sandbox.util.tar_utils._validate_symlink_target`` with
    ``allow_external_symlink_targets=False``: absolute targets and
    relative targets that pop past the root via ``..`` are rejected.
    Applying the same predicate at seed time keeps seed and restore
    consistent.
    """
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return False
    member_parent = PurePosixPath(rel_path).parent
    return _normalize_posix_path_without_root(member_parent / target_path) is not None


def _normalize_posix_path_without_root(path: PurePosixPath) -> tuple[str, ...] | None:
    """Collapse ``.`` and ``..`` parts; return ``None`` if normalization
    would pop past the top (i.e. the path escapes the archive root).
    Mirrors the helper of the same name in the SDK's ``tar_utils``.
    """
    normalized: list[str] = []
    for part in path.parts:
        if part in ("", ".", "/"):
            continue
        if part == "..":
            if not normalized:
                return None
            normalized.pop()
            continue
        normalized.append(part)
    return tuple(normalized)
