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
import errno
import fnmatch
import sqlite3
import time
import json
import re
import tempfile
from contextlib import closing
from pathlib import Path

__all__ = ["backup", "confidential_paths", "export_keys", "restore", "upload"]

#: Encryption keys: under the strict policy never in a data backup; they are
#: escrowed apart (``export_keys``, into Key Vault) and given back on restore.
KEY_SUFFIX = ".key"


def confidential_paths(data: Path) -> list[str]:
    """What not to back up for Confidential factories: their Shared uploads
    and their sandboxes' disks (working copies of data a system of record
    holds), relative to ``data``."""
    out: list[str] = []

    def classification(db: Path):
        try:
            with closing(
                sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            ) as c:
                row = c.execute(
                    "SELECT value FROM meta WHERE key = 'factory.profile'"
                ).fetchone()
        except sqlite3.Error:
            return None
        return (json.loads(row[0]) or {}).get("classification") if row else None

    if (data / "workplace.db").is_file() and classification(
        data / "workplace.db"
    ) == "confidential":
        out += ["shared", "shared-staging"]
    factories = data / "installation" / "factories"
    if factories.is_dir():
        for factory in sorted(factories.iterdir()):
            db = factory / "workplace" / "workplace.db"
            if db.is_file() and classification(db) == "confidential":
                base = f"installation/factories/{factory.name}"
                out += [
                    f"{base}/workplace/shared",
                    f"{base}/workplace/shared-staging",
                    f"{base}/vms",
                ]
    return out


def export_keys(data: Path, to: Path) -> list[str]:
    """Copy every encryption key under ``data`` to ``to``, by relative path."""
    data, to = Path(data), Path(to)
    copied = []
    for key in sorted(data.rglob("*" + KEY_SUFFIX)):
        if key.is_file() and not key.is_symlink():
            relative = key.relative_to(data)
            target = to / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(key, target)
            target.chmod(0o600)
            copied.append(str(relative))
    return copied


#: What a push or a gc has in progress: not part of a repository yet.
_TRANSIENT = shutil.ignore_patterns(
    "tmp_objdir-*", "incoming-*", "tmp_pack_*", "*.lock"
)


def _copy(src: str, dst: str) -> None:
    try:
        if (
            shutil.disk_usage(Path(dst).parent).free
            < Path(src).stat().st_size + 1024**3
        ):
            raise OSError(
                errno.ENOSPC, "Not enough free space to finish the backup safely"
            )
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


def _copy_installation(source: Path, target: Path, *, exclude=(), relative=""):
    """Include the installation registry and each factory's independent state."""
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in source.iterdir():
        child = relative + "/" + entry.name
        if any(fnmatch.fnmatchcase(child, pattern) for pattern in exclude):
            continue
        # Docker's live storage driver is not a recoverable filesystem backup.
        # The full recovery bundle saves deployment databases/volumes separately.
        if source.name == "coolify" and entry.name == "engine":
            continue
        if entry.name.endswith((".lock", "-wal", "-shm")) or entry.name in {
            "__pycache__",
            ".venv",
        }:
            continue
        destination = target / entry.name
        if entry.is_symlink():
            destination.symlink_to(entry.readlink())
        elif (
            entry.name == "git"
            and entry.is_dir()
            and (source / "workplace.db").is_file()
        ):
            # Managed factories need the same refs-before-objects ordering as
            # the installation's legacy/root workplace during concurrent pushes.
            _copy_repositories(entry, destination)
        elif entry.is_dir():
            _copy_installation(entry, destination, exclude=exclude, relative=child)
        elif entry.suffix in {".db", ".sqlite", ".sqlite3"} and _sqlite_file(entry):
            _backup_database(entry, destination)
        else:
            _copy(str(entry), str(destination))


def _sqlite_file(path):
    with path.open("rb") as source:
        return source.read(16) == b"SQLite format 3\x00"


def _backup_database(source, destination):
    with closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    ) as live, closing(sqlite3.connect(str(destination))) as copy:
        live.backup(copy)
        # Publish a standalone file, never a WAL database that needs writable
        # sidecars merely to validate it from a read-only backup mount.
        copy.execute("PRAGMA journal_mode=DELETE")


def _backup_bytes(path: Path, *, exclude=(), relative="") -> int:
    """Conservative estimate before writing a new backup, without following links."""
    try:
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in exclude):
            return 0
        if path.is_symlink():
            return 0
        if path.is_file():
            size = path.stat().st_size
            wal = path.with_name(path.name + "-wal")
            return size + (wal.stat().st_size if wal.is_file() else 0)
        if not path.is_dir():
            return 0
        return sum(
            _backup_bytes(entry, exclude=exclude, relative=relative + "/" + entry.name)
            for entry in path.iterdir()
            if entry.name not in {".venv", "__pycache__"}
            and not entry.name.endswith((".lock", "-wal", "-shm"))
            and not (path.name == "coolify" and entry.name == "engine")
        )
    except FileNotFoundError:
        return 0


def backup(
    data: Path,
    to: Path,
    *,
    keep: int = 14,
    include: dict[str, Path] | None = None,
    exclude: tuple[str, ...] = (),
    keys: bool = True,
    confidential: bool = False,
) -> Path:
    """``keys=False``: no encryption keys in the backup. ``confidential=True``:
    no Shared uploads or sandbox disks of Confidential factories."""
    data, to = Path(data), Path(to)
    left_out = confidential_paths(data) if confidential else []
    include = include or {}
    for name, path in include.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
            raise ValueError("Backup include names must be simple directory names")
        if not Path(path).is_dir() or to.resolve().is_relative_to(Path(path).resolve()):
            raise ValueError(
                "Backup include directories must exist and must not contain the backup destination"
            )
    for pattern in exclude:
        if (
            "/" not in pattern
            or pattern.split("/", 1)[0] not in include
            or ".." in pattern.split("/")
            or "\\" in pattern
        ):
            raise ValueError(
                "Exclusions must start with an additional directory name, such as factory-home/quicksand"
            )
    source = data / "workplace.db"
    if not source.is_file():
        raise SystemExit(f"No workplace in {data}")
    to.mkdir(parents=True, exist_ok=True, mode=0o700)
    selected = [
        data / name
        for name in (
            "workplace.db",
            "git",
            "library",
            "shared",
            "shared-staging",
            "installation",
            "factory-secrets.key",
            "github-connection.key",
            "workflow-settings.key",
            "workflow-settings.enc",
        )
    ]
    required = (
        sum(_backup_bytes(path) for path in selected)
        + sum(
            _backup_bytes(Path(path), exclude=exclude, relative=name)
            for name, path in include.items()
        )
        + 1024**3
    )
    free = shutil.disk_usage(to).free
    if required > free:
        raise OSError(
            errno.ENOSPC,
            f"Backup needs approximately {required} bytes including reserve; {free} available",
        )
    now = time.time()
    target = to / (
        time.strftime("workplace-%Y%m%dT%H%M%S", time.gmtime(now))
        + f"{int(now % 1 * 1000):03d}Z"
    )
    partial = target.with_name(target.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, mode=0o700)
    try:
        _backup_database(source, partial / "workplace.db")
        if (data / "git").is_dir():
            _copy_repositories(data / "git", partial / "git")
        if (data / "library").is_dir():
            shutil.copytree(
                data / "library",
                partial / "library",
                symlinks=True,
                copy_function=_copy,
            )
        for name in ("shared", "shared-staging"):
            if name in left_out:
                continue
            if (data / name).is_dir():
                shutil.copytree(
                    data / name, partial / name, symlinks=True, copy_function=_copy
                )
        for name in (
            "factory-secrets.key",
            "github-connection.key",
            "workflow-settings.key",
            "workflow-settings.enc",
        ):
            if name.endswith(KEY_SUFFIX) and not keys:
                continue
            if (data / name).is_file():
                shutil.copy2(data / name, partial / name)
        if (data / "installation").is_dir():
            _copy_installation(
                data / "installation",
                partial / "installation",
                exclude=tuple(left_out) + (() if keys else ("*" + KEY_SUFFIX,)),
                relative="installation",
            )
        for name, path in include.items():
            _copy_installation(
                Path(path),
                partial / "additional" / name,
                exclude=exclude,
                relative=name,
            )
        (partial / "backup-metadata.json").write_text(
            json.dumps(
                {
                    "format": 2,
                    "excluded_additional_paths": list(exclude),
                    "keys": (
                        "included" if keys else "excluded; restore them from escrow"
                    ),
                    "confidential_excluded": left_out,
                    "created_at": now,
                    "additional": {
                        name: str(Path(path).resolve())
                        for name, path in include.items()
                    },
                    "deployment_engines": "excluded; use consistent database dumps and application volume backups",
                },
                indent=2,
            )
        )
        partial.rename(target)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    kept = sorted(
        p
        for p in to.glob("workplace-*")
        if p.is_dir() and not p.name.endswith(".partial")
    )
    for old in kept[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
    return target


def restore(
    source: Path,
    data: Path,
    *,
    mode: str = "staging",
    include: dict[str, Path] | None = None,
    keys: Path | None = None,
) -> Path:
    """Replace ``data``'s database and repositories with a backup's (``source``,
    or the latest in a directory of them); the backup restored."""
    if mode not in {"staging", "production"}:
        raise ValueError("Restore mode must be staging or production")
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
    if source.resolve().is_relative_to(data.resolve()) or data.resolve().is_relative_to(
        source.resolve()
    ):
        raise ValueError("The backup must be outside the restore destination")
    include = include or {}
    additional = (
        {p.name for p in (source / "additional").iterdir()}
        if (source / "additional").exists()
        else set()
    )
    if mode == "production" and additional != set(include):
        raise ValueError(
            "Production restore requires destinations for every additional directory: "
            + ", ".join(sorted(additional))
        )
    for name, destination in include.items():
        destination = Path(destination)
        for other in [source, data, *(p for n, p in include.items() if n != name)]:
            if destination.resolve().is_relative_to(Path(other).resolve()) or Path(
                other
            ).resolve().is_relative_to(destination.resolve()):
                raise ValueError(
                    "Restore destinations must not overlap each other or the backup"
                )
        if name not in additional or (
            destination.exists() and any(destination.iterdir())
        ):
            raise ValueError(
                "Additional restore destinations must be empty directories with matching backup names"
            )
    # Validate before modifying the destination. Production recovery preserves
    # integration identity; callers must stop the original installation first.
    # Validate in writable temporary storage, not in the immutable backup.
    # Legacy snapshots may retain WAL mode or sidecars; retain their committed
    # WAL data instead of ignoring it with SQLite's immutable connection option.
    with tempfile.TemporaryDirectory(prefix="workplace-restore-") as directory:
        candidate = Path(directory) / "workplace.db"
        shutil.copyfile(source / "workplace.db", candidate)
        candidate.chmod(0o600)
        if (source / "workplace.db-wal").exists():
            shutil.copyfile(
                source / "workplace.db-wal", Path(directory) / "workplace.db-wal"
            )
        with closing(sqlite3.connect(str(candidate))) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The backup database failed its integrity check")
            db.execute("PRAGMA journal_mode=DELETE")
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("workplace.db", "workplace.db-wal", "workplace.db-shm"):
            (data / name).unlink(missing_ok=True)
        shutil.copy2(candidate, data / "workplace.db")
    shutil.rmtree(data / "git", ignore_errors=True)
    shutil.rmtree(data / "installation", ignore_errors=True)
    if (source / "installation").is_dir():
        shutil.copytree(source / "installation", data / "installation", symlinks=True)
    # A copy is a workplace of its own: it never takes the original's job containers for its own.
    databases = [data / "workplace.db"]
    databases.extend((data / "installation").glob("factories/*/workplace/workplace.db"))
    for database in databases:
        if mode == "production":
            continue
        with sqlite3.connect(str(database)) as db:
            db.execute("DELETE FROM meta WHERE key = 'workplace.instance'")
            # A staging restore must not replay accepted production events.
            db.execute("DROP TABLE IF EXISTS factory_events")
            db.execute("DELETE FROM meta WHERE key = 'event_gateway.runtime_ready'")
            # Staging cannot inherit production GitHub or deployment authority.
            db.execute(
                "DELETE FROM meta WHERE key LIKE 'repository.github.%' OR key IN ('repository.settings', 'factory.secrets')"
            )
        for name in (
            "factory-secrets.key",
            "github-connection.key",
            "workflow-settings.key",
            "workflow-settings.enc",
        ):
            (database.parent / name).unlink(missing_ok=True)
    registry = data / "installation" / "installation.db"
    if mode == "staging" and registry.is_file():
        # Its records contain absolute production paths, runtime container IDs
        # and autostart settings. Keep them for inspection, never let a staging
        # host mount or restart the original production factories by default.
        _backup_database(registry, registry.with_name("installation.snapshot.db"))
        for suffix in ("", "-wal", "-shm"):
            Path(str(registry) + suffix).unlink(missing_ok=True)
    for name in (
        "factory-secrets.key",
        "github-connection.key",
        "workflow-settings.key",
        "workflow-settings.enc",
    ):
        (data / name).unlink(missing_ok=True)
        if mode == "production" and (source / name).is_file():
            shutil.copy2(source / name, data / name)
    if (source / "git").is_dir():
        shutil.copytree(source / "git", data / "git", symlinks=True)
    shutil.rmtree(data / "library", ignore_errors=True)
    if (source / "library").is_dir():
        shutil.copytree(source / "library", data / "library", symlinks=True)
    for name in ("shared", "shared-staging"):
        shutil.rmtree(data / name, ignore_errors=True)
        if (source / name).is_dir():
            shutil.copytree(source / name, data / name, symlinks=True)
    for name, destination in include.items():
        shutil.copytree(
            source / "additional" / name, destination, symlinks=True, dirs_exist_ok=True
        )
    if keys is not None and mode == "production":
        # Keys escrowed apart from the data backup (export_keys) go back in place.
        for key in sorted(Path(keys).rglob("*" + KEY_SUFFIX)):
            target = data / key.relative_to(keys)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(key, target)
            target.chmod(0o600)
    return source


def upload(backup_dir: Path, container_url: str, client=None) -> int:
    """Upload a backup directory to Azure Blob Storage, under its name.

    ``container_url`` is the container's URL with a SAS token
    (``https://<account>.blob.core.windows.net/<container>?<sas>``). Each file
    is a block blob, sent in 64 MiB blocks. The container's lifecycle rule
    deletes backups after 30 days. Returns the bytes uploaded."""
    import base64
    from urllib.parse import quote, urlsplit, urlunsplit

    import httpx

    url = urlsplit(container_url)
    if url.scheme != "https" or not url.query:
        raise ValueError("Give the container's https:// URL with a SAS token")
    backup_dir = Path(backup_dir)
    own = client is None
    client = client or httpx.Client(timeout=300)
    sent = 0
    block = 64 * 1024**2
    headers = {"x-ms-version": "2021-08-06"}
    try:
        for path in sorted(p for p in backup_dir.rglob("*") if p.is_file()):
            name = quote(f"{backup_dir.name}/{path.relative_to(backup_dir).as_posix()}")
            blob = urlunsplit(
                (
                    url.scheme,
                    url.netloc,
                    f"{url.path.rstrip('/')}/{name}",
                    url.query,
                    "",
                )
            )
            ids = []
            with path.open("rb") as source:
                while True:
                    chunk = source.read(block)
                    if not chunk and ids:
                        break
                    block_id = base64.b64encode(f"{len(ids):08d}".encode()).decode()
                    client.put(
                        blob + "&comp=block&blockid=" + quote(block_id, safe=""),
                        content=chunk,
                        headers=headers,
                    ).raise_for_status()
                    ids.append(block_id)
                    sent += len(chunk)
                    if not chunk:
                        break
            listing = "".join(f"<Latest>{i}</Latest>" for i in ids)
            client.put(
                blob + "&comp=blocklist",
                content=f'<?xml version="1.0" encoding="utf-8"?><BlockList>{listing}</BlockList>',
                headers={**headers, "Content-Type": "application/xml"},
            ).raise_for_status()
    finally:
        if own:
            client.close()
    return sent
