"""Descriptor-relative file operations. Never traverse sandbox-created symlinks."""

import contextlib
import hashlib
import os
from pathlib import PurePosixPath
import stat

from fastapi import HTTPException


def clean(path, *, root=False):
    if not isinstance(path, str) or "\\" in path or "\x00" in path:
        raise HTTPException(422, "Invalid path")
    if root and path == "":
        return ""
    if (
        not path
        or path.startswith("/")
        or any(p in ("", ".", "..") or p.startswith(".omni-") for p in path.split("/"))
    ):
        raise HTTPException(422, "Use a relative file or folder path")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise HTTPException(422, "Control characters are not allowed in paths")
    return str(PurePosixPath(path))


@contextlib.contextmanager
def directory(root, path="", *, create=False):
    clean(path, root=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.split("/") if path else []:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError as exc:
        raise HTTPException(
            409, "The path is missing, unsafe, or changed; refresh and retry"
        ) from exc
    finally:
        os.close(fd)


@contextlib.contextmanager
def parent(root, path, *, create=False):
    path = clean(path)
    folder, _, name = path.rpartition("/")
    with directory(root, folder, create=create) as fd:
        yield fd, name


def open_file(root, path):
    with parent(root, path) as (fd, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(handle)
            raise HTTPException(409, "Only ordinary files can be read")
        return os.fdopen(handle, "rb")


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def snapshot(root, previous=None):
    previous = previous or {}
    result = {}
    with directory(root) as root_fd:

        def walk(fd, prefix):
            for name in sorted(os.listdir(fd)):
                if name.startswith(".omni-"):
                    continue
                path = prefix + name
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd,
                        )
                        try:
                            info = os.fstat(child)
                            result[path] = dict(
                                path=path,
                                kind="folder",
                                size=0,
                                inode=[info.st_dev, info.st_ino],
                                revision="folder",
                                modified=info.st_mtime,
                                signature=signature(info),
                            )
                            walk(child, path + "/")
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        sig = signature(info)
                        old = previous.get(path)
                        if old and old["signature"] == sig:
                            result[path] = old
                            continue
                        f = os.open(
                            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                        )
                        with os.fdopen(f, "rb") as stream:
                            before = os.fstat(stream.fileno())
                            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                                continue
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                            after = os.fstat(stream.fileno())
                            if signature(before) != signature(after):
                                if old:
                                    result[path] = old
                                continue
                            result[path] = dict(
                                path=path,
                                kind="file",
                                size=after.st_size,
                                inode=[after.st_dev, after.st_ino],
                                revision=digest,
                                modified=after.st_mtime,
                                signature=signature(after),
                            )
                except (FileNotFoundError, NotADirectoryError):
                    continue  # Atomic sandbox renames may race the scan; next scan reconciles.

        walk(root_fd, "")
    return result
