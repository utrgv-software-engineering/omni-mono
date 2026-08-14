"""Composite host Workspace backed by explicitly declared source mounts.

The logical root is a launcher-managed directory. Each visible mount name maps
to one user-selected host directory without widening the boundary to a common
ancestor. Filesystem and process operations address ``<mountName>/...`` while
executing directly against the real host files.
"""

from __future__ import annotations

import asyncio
import posixpath
from datetime import datetime
from pathlib import Path
from typing import Any, List, Literal, Mapping, Optional, Sequence

from ._entries import make_entry, sort_entries
from .host import HostWorkspace
from .types import FileEntry, GrepMatch, WorkspaceEscapeError


class GitMetadataHostWorkspace(HostWorkspace):
    """Direct host tree with explicitly authorized external Git metadata.

    ``confine`` is passed through: under the host (no-sandbox) target the
    tree is unconfined like any other host workspace, and the authorized
    metadata paths then matter only to ``contains_path`` / ``io_path`` —
    the boundary questions ``git_*`` and ``resolve_confined`` still ask.
    """

    def __init__(
        self,
        root: str,
        sources: Sequence[Mapping[str, Any]],
        *,
        confine: bool = True,
    ) -> None:
        super().__init__(root=root, confine=confine)
        self._git_metadata = _git_metadata_paths(sources)

    def _contains_metadata(self, absolute_path: str) -> bool:
        resolved = Path(absolute_path).expanduser().resolve()
        return any(_is_relative_to(resolved, root) for root in self._git_metadata)

    def resolve(self, path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute() and self._contains_metadata(str(candidate)):
            return str(candidate.expanduser().resolve())
        return super().resolve(path)

    def contains_path(self, absolute_path: str) -> bool:
        return super().contains_path(absolute_path) or self._contains_metadata(
            absolute_path
        )

    def io_path(self, absolute_path: str) -> Optional[str]:
        relative = super().io_path(absolute_path)
        if relative is not None:
            return relative
        if self._contains_metadata(absolute_path):
            return str(Path(absolute_path).expanduser().resolve())
        return None


class MountedHostWorkspace(HostWorkspace):
    """A confined logical Host workspace with explicit directory mounts."""

    supports_direct_host_scan = False

    def __init__(self, root: str, sources: Sequence[Mapping[str, Any]]) -> None:
        # Confined unconditionally, and not because of any sandboxing: this
        # root is a *logical* tree of symlinks this class synthesizes, and
        # paths are meaningful only as mount-relative names. There is no
        # "the whole machine" reading of a path here to opt into — the
        # mount table is the address space. ``resolve`` is overridden below
        # to route through it.
        super().__init__(root=root, confine=True)
        self._visible_mounts: dict[str, Path] = {}
        self._internal_mounts: dict[str, Path] = {}

        root_path = Path(self.root)
        requested_mounts = {
            self._mount_name(source.get("mountName"))
            for source in sources
            if source.get("kind") in {"local", "local-git"}
        }
        # The root is framework-managed and persists across launches. Remove
        # only stale top-level links from an earlier materialization; ordinary
        # files/directories (including framework artifacts) are never touched.
        for entry in root_path.iterdir():
            if entry.is_symlink() and entry.name not in requested_mounts:
                entry.unlink()

        for source in sources:
            if source.get("kind") not in {"local", "local-git"}:
                continue
            mount_name = self._mount_name(source.get("mountName"))
            target = Path(str(source.get("path") or "")).expanduser().resolve()
            if not target.is_dir():
                raise ValueError(
                    f"host source {mount_name!r} is not a directory: {target}"
                )
            self._visible_mounts[mount_name] = target
            link = root_path / mount_name
            if link.exists() or link.is_symlink():
                if link.is_symlink() and link.resolve() != target:
                    link.unlink()
                    link.symlink_to(target, target_is_directory=True)
                elif not link.is_symlink() and link.resolve() != target:
                    # A real directory that IS the target needs no link: the
                    # source lives physically at its mount point (e.g. a
                    # host-materialized remote checkout under this root).
                    raise ValueError(
                        f"host workspace mount {mount_name!r} already exists differently"
                    )
            else:
                link.symlink_to(target, target_is_directory=True)

            for field, suffix in (
                ("gitDir", "git-dir"),
                ("gitCommonDir", "git-common-dir"),
            ):
                raw = source.get(field)
                if isinstance(raw, str) and raw.strip():
                    metadata = Path(raw).expanduser().resolve()
                    if metadata.exists():
                        self._internal_mounts[f".omni-git/{mount_name}/{suffix}"] = (
                            metadata
                        )

    @staticmethod
    def _mount_name(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("host source mountName must be a non-empty string")
        name = value.strip()
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"invalid host source mountName: {name!r}")
        return name

    @property
    def mount_names(self) -> tuple[str, ...]:
        return tuple(self._visible_mounts)

    def resolve(self, path: str) -> str:
        if not path or path == ".":
            return self.root
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if self.contains_path(str(resolved)):
                return str(resolved)
            raise WorkspaceEscapeError(
                f"Path {path!r} resolves outside mounted workspace root {self.root!r}"
            )

        normalized = posixpath.normpath(path.replace("\\", "/"))
        if normalized == ".." or normalized.startswith("../"):
            raise WorkspaceEscapeError(f"Path {path!r} escapes the mounted workspace")
        for logical, target in sorted(
            self._visible_mounts.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if normalized != logical and not normalized.startswith(logical + "/"):
                continue
            remainder = normalized[len(logical) :].lstrip("/")
            resolved = (target / remainder).resolve() if remainder else target
            try:
                resolved.relative_to(target)
            except ValueError:
                raise WorkspaceEscapeError(
                    f"Path {path!r} escapes mounted source {logical!r}"
                ) from None
            return str(resolved)

        # Framework-owned root entries (for example .omni-artifacts) remain
        # ordinary files under the managed logical root.
        return super().resolve(normalized)

    def relative_path(self, absolute_path: str) -> Optional[str]:
        resolved = Path(absolute_path).expanduser().resolve()
        root_relative = super().relative_path(str(resolved))
        if root_relative is not None:
            return root_relative

        def candidates(mounts: Mapping[str, Path]) -> list[tuple[int, str]]:
            matches: list[tuple[int, str]] = []
            for logical, target in mounts.items():
                try:
                    remainder = resolved.relative_to(target)
                except ValueError:
                    continue
                rendered = (
                    logical
                    if not remainder.parts
                    else f"{logical}/{remainder.as_posix()}"
                )
                matches.append((len(target.parts), rendered))
            return matches

        matches = candidates(self._visible_mounts)
        return max(matches, key=lambda item: item[0])[1] if matches else None

    def contains_path(self, absolute_path: str) -> bool:
        if self.relative_path(absolute_path) is not None:
            return True
        resolved = Path(absolute_path).expanduser().resolve()
        return any(
            _is_relative_to(resolved, target)
            for target in self._internal_mounts.values()
        )

    def io_path(self, absolute_path: str) -> Optional[str]:
        visible = self.relative_path(absolute_path)
        if visible is not None:
            return visible
        resolved = Path(absolute_path).expanduser().resolve()
        if any(
            _is_relative_to(resolved, target)
            for target in self._internal_mounts.values()
        ):
            return str(resolved)
        return None

    def logical_path(self, path: str) -> str:
        absolute = self.resolve(path)
        relative = self.relative_path(absolute)
        if relative is None:
            raise WorkspaceEscapeError(
                f"Path {path!r} has no visible path in mounted workspace {self.root!r}"
            )
        if relative == ".":
            return self.root
        return str(Path(self.root) / Path(*relative.split("/")))

    def _display_path(self, path: Path) -> str:
        relative = self.relative_path(str(path))
        return relative if relative is not None else path.as_posix()

    async def list_dir(
        self,
        path: str = ".",
        *,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        sort_by: Literal["name", "mtime", "size"] = "name",
    ) -> List[FileEntry]:
        entries = await super().list_dir(
            path,
            include_hidden=include_hidden,
            respect_gitignore=respect_gitignore,
            sort_by=sort_by,
        )
        if path not in {"", ".", self.root}:
            return entries

        by_name = {entry.name: entry for entry in entries}
        for name, target in self._visible_mounts.items():
            stat = await asyncio.to_thread(target.stat)
            by_name[name] = make_entry(
                name=name,
                path=name,
                is_dir=True,
                size=None,
                mtime=datetime.fromtimestamp(stat.st_mtime),
                is_symlink=False,
            )
        return sort_entries(list(by_name.values()), sort_by)

    async def glob(
        self,
        pattern: str,
        *,
        root: str = ".",
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> List[str]:
        roots = [root]
        if root in {"", ".", self.root}:
            roots.extend(self._visible_mounts)
        results: set[str] = set()
        for current in roots:
            results.update(
                await super().glob(
                    pattern,
                    root=current,
                    recursive=recursive,
                    include_hidden=include_hidden,
                )
            )
        return sorted(results)

    async def grep(
        self,
        pattern: str,
        *,
        root: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> List[GrepMatch]:
        roots = [root]
        if root in {"", ".", self.root}:
            roots.extend(self._visible_mounts)
        results: list[GrepMatch] = []
        for current in roots:
            results.extend(
                await super().grep(
                    pattern,
                    root=current,
                    file_pattern=file_pattern,
                    case_sensitive=case_sensitive,
                    context_lines=context_lines,
                )
            )
        return results

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        normalized = posixpath.normpath(path.replace("\\", "/"))
        if normalized in self._visible_mounts:
            raise PermissionError("a mounted source root cannot be removed")
        await super().remove(path, recursive=recursive)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_metadata_paths(sources: Sequence[Mapping[str, Any]]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for source in sources:
        for field in ("gitDir", "gitCommonDir"):
            raw = source.get(field)
            if isinstance(raw, str) and raw.strip():
                resolved = Path(raw).expanduser().resolve()
                if resolved.exists() and resolved not in paths:
                    paths.append(resolved)
    return tuple(paths)
