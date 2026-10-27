"""``omniagents-workplace backup``: a workplace's state, copied while it runs.

The database is copied with SQLite's online backup (a consistent snapshot,
whatever is being written), and each repository refs first, then its objects:
git only adds objects, and writes them before the refs that point at them, so
every ref copied has its objects, whatever is pushed meanwhile. Git's
temporary files (a push in progress) are left out. It goes into
``<to>/workplace-<UTC time>/``, named so only once it is whole; the oldest
backups past ``keep`` are removed.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

__all__ = ["backup", "restore"]

#: What a push or a gc has in progress: not part of a repository yet.
_TRANSIENT = shutil.ignore_patterns(
    "tmp_objdir-*", "incoming-*", "tmp_pack_*", "*.lock"
)


def _copy(src: str, dst: str) -> None:
    try:
        shutil.copy2(src, dst)
    except FileNotFoundError:
        pass  # gone while we copied: a temporary file


def _copy_repositories(git: Path, to: Path) -> None:
    for repo in sorted(git.glob("*/*.git")):
        target = to / repo.relative_to(git)
        target.mkdir(parents=True, mode=0o700)
        for entry in sorted(repo.iterdir(), key=lambda e: e.name == "objects"):
            if entry.name == "objects":
                continue
            if entry.is_dir():
                shutil.copytree(
                    entry,
                    target / entry.name,
                    symlinks=True,
                    ignore=_TRANSIENT,
                    copy_function=_copy,
                )
            else:
                _copy(str(entry), str(target / entry.name))
        # After the refs: every object they name is already there.
        if (repo / "objects").is_dir():
            shutil.copytree(
                repo / "objects",
                target / "objects",
                symlinks=True,
                ignore=_TRANSIENT,
                copy_function=_copy,
            )


def backup(data: Path, to: Path, *, keep: int = 14) -> Path:
    data, to = Path(data), Path(to)
    source = data / "workplace.db"
    if not source.is_file():
        raise SystemExit(f"No workplace in {data}")
    now = time.time()
    target = to / (
        time.strftime("workplace-%Y%m%dT%H%M%S", time.gmtime(now))
        + f"{int(now % 1 * 1000):03d}Z"
    )
    partial = target.with_name(target.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(str(source)) as live, sqlite3.connect(
        str(partial / "workplace.db")
    ) as copy:
        live.backup(copy)
    if (data / "git").is_dir():
        _copy_repositories(data / "git", partial / "git")
    if (data / "library").is_dir():
        shutil.copytree(data / "library", partial / "library", symlinks=True)
    if (data / "github-connection.key").is_file():
        shutil.copy2(data / "github-connection.key", partial / "github-connection.key")
    partial.rename(target)
    kept = sorted(
        p
        for p in to.glob("workplace-*")
        if p.is_dir() and not p.name.endswith(".partial")
    )
    for old in kept[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
    return target


def restore(source: Path, data: Path) -> Path:
    """Replace ``data``'s database and repositories with a backup's (``source``,
    or the latest in a directory of them); the backup restored."""
    source, data = Path(source), Path(data)
    if not (source / "workplace.db").is_file():
        # Only whole backups: one still being made, or that failed, is .partial.
        found = sorted(
            p
            for p in source.glob("workplace-*")
            if not p.name.endswith(".partial") and (p / "workplace.db").is_file()
        )
        if not found:
            raise SystemExit(f"No backup in {source}")
        source = found[-1]
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("workplace.db", "workplace.db-wal", "workplace.db-shm"):
        (data / name).unlink(missing_ok=True)
    shutil.rmtree(data / "git", ignore_errors=True)
    shutil.copy2(source / "workplace.db", data / "workplace.db")
    # A copy is a workplace of its own: it never takes the original's job containers for its own.
    with sqlite3.connect(str(data / "workplace.db")) as db:
        db.execute("DELETE FROM meta WHERE key = 'workplace.instance'")
        # Restores also power staging replay: never copy a live GitHub identity
        # or an enabled improvement loop into that environment. Reconnect explicitly.
        db.execute(
            "DELETE FROM meta WHERE key LIKE 'repository.github.%' OR key = 'repository.settings'"
        )
    (data / "github-connection.key").unlink(missing_ok=True)
    if (source / "git").is_dir():
        shutil.copytree(source / "git", data / "git", symlinks=True)
    shutil.rmtree(data / "library", ignore_errors=True)
    if (source / "library").is_dir():
        shutil.copytree(source / "library", data / "library", symlinks=True)
    return source
