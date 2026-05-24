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

# Default ceiling on the total size of files seeded from a host directory.
# Guards against accidentally picking a huge folder (a home dir, an
# un-gitignored ``node_modules``, a dataset): the tar is built in memory and,
# on the ACI heredoc path, base64-streamed in memory too, so an unbounded seed
# can OOM. Generous enough for normal repos (typically <100 MiB); override per
# entry via ``max_bytes`` or globally via ``OMNI_MAX_SEED_BYTES`` (set <=0 to
# disable). Bounded kinds (git-tracked ``LocalGitArchive``, ``GitRepo`` clone)
# don't need this — only the permissive local-dir walk is unbounded by git.
_DEFAULT_MAX_SEED_BYTES = 1 * 1024**3  # 1 GiB


def _resolve_max_seed_bytes(explicit: int | None) -> int | None:
    """Resolve the effective seed-size cap. ``None`` (unset) falls back to
    ``OMNI_MAX_SEED_BYTES`` then the default; a value <= 0 (explicit or env)
    means unlimited and returns ``None``."""
    if explicit is not None:
        return explicit if explicit > 0 else None
    env = os.getenv("OMNI_MAX_SEED_BYTES")
    if env is not None:
        try:
            value = int(env)
        except ValueError:
            value = _DEFAULT_MAX_SEED_BYTES
        return value if value > 0 else None
    return _DEFAULT_MAX_SEED_BYTES


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


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
    # Cap on total seeded bytes. None → OMNI_MAX_SEED_BYTES / default; <=0 →
    # unlimited. Enforced early during the walk so a giant folder aborts
    # before the in-memory tar is built.
    max_bytes: int | None = None

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

        # Host-mode short-circuit: unix_local sessions never relocate files.
        # The agent operates directly on the user's host filesystem, so the
        # source dir is already "in place" — there is no destination to
        # materialize into. Skip *everything* (no mkdir, no metadata, no
        # extract) so host mode is structurally incapable of writing to or
        # relocating any host path through this entry. Source descriptors
        # still flow through the manifest as inert records and are surfaced
        # to the agent via ``agent_workspace_state.get_sources()``.
        from agents.sandbox.sandboxes.unix_local import (  # noqa: PLC0415
            UnixLocalSandboxSession,
        )

        if isinstance(session, UnixLocalSandboxSession):
            return []

        max_bytes = _resolve_max_seed_bytes(self.max_bytes)
        archive_bytes = await asyncio.to_thread(
            _build_dir_tar, src_resolved, max_bytes
        )

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


def _build_dir_tar(src: Path, max_bytes: int | None = None) -> bytes:
    """Walk *src*, respect any ``.gitignore`` encountered, silently
    skip symlinks, stream regular files into an in-memory tarball.

    Gitignore semantics are scoped: a ``.gitignore`` at ``a/b/`` applies
    to paths under ``a/b/`` only. We carry a stack of ``(anchor_rel,
    PathSpec)`` down the walk and check each candidate against every
    spec in scope.

    When *max_bytes* is set, the cumulative size of included (post-gitignore)
    regular files is checked as the walk proceeds and the build aborts —
    before reading the offending file or finishing the tar — once it is
    exceeded, raising :class:`PermissiveLocalDirError`.
    """
    buf = io.BytesIO()
    total = [0]  # mutable running byte total, shared across the recursion
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Stack entries: (anchor_rel_posix, PathSpec). Empty anchor =
        # the source root itself.
        spec_stack: list[tuple[str, pathspec.PathSpec]] = []
        _walk(src, Path("."), tar, spec_stack, max_bytes=max_bytes, total=total)
    return buf.getvalue()


def _walk(
    src_root: Path,
    rel_dir: Path,
    tar: tarfile.TarFile,
    spec_stack: list[tuple[str, pathspec.PathSpec]],
    *,
    max_bytes: int | None,
    total: list[int],
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
                _walk(
                    src_root,
                    rel_path,
                    tar,
                    spec_stack,
                    max_bytes=max_bytes,
                    total=total,
                )
                continue

            if stat.S_ISREG(st.st_mode):
                # Enforce the seed-size cap early: count this file before
                # reading it so a giant directory aborts mid-walk instead of
                # building the whole in-memory tar (and OOM-ing the heredoc
                # upload on remote backends).
                total[0] += st.st_size
                if max_bytes is not None and total[0] > max_bytes:
                    raise PermissiveLocalDirError(
                        f"permissive_local_dir: source exceeds the "
                        f"{_human_bytes(max_bytes)} seed limit "
                        f"(reached {_human_bytes(total[0])} at {rel_posix!r}). "
                        f"Add a .gitignore to exclude large files, use the "
                        f"folder as a git repo so only tracked files are seeded, "
                        f"or raise OMNI_MAX_SEED_BYTES (set <=0 to disable).",
                        src=src_root,
                    )
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
