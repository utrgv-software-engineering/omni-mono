"""Library metadata in SQLite; agent-editable files in a persistent directory."""

import html
import os
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

MAX_FILE = 2 * 1024 * 1024


class Library:
    def __init__(self, store, root: Path, workspace_root="/workspace/Library"):
        self.store, self.root, self.workspace_root = store, Path(root), workspace_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def get(self, artifact_id):
        found = next(
            (d for key, d in self.store.docs("library.artifact") if key == artifact_id),
            None,
        )
        if found is None:
            raise HTTPException(404, "No such artifact")
        return found

    def list(self):
        return [self.summary(d) for _, d in self.store.docs("library.artifact")]

    def summary(self, item):
        # Direct file-tool edits are visible without an extra publish operation.
        updated = item["updated_at"]
        try:
            updated = max(updated, self.file_stat(item, item["main"]).st_mtime)
        except HTTPException:
            pass
        return {
            **item,
            "updated_at": updated,
            "folder": f"{self.workspace_root}/{item['id']}",
        }

    def create(self, title, kind, owner):
        title = title.strip()
        if not title or len(title) > 120 or kind not in ("page", "document"):
            raise HTTPException(
                422, "Use a title of 1–120 characters and type page or document"
            )
        with self.lock:
            artifact_id = uuid.uuid4().hex
            folder = self.root / artifact_id
            folder.mkdir()
            main = "index.html" if kind == "page" else "index.md"
            content = (
                f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
                "<style>body{font:16px system-ui;margin:0;padding:24px;color:#17202a;background:#fff}</style></head>"
                f"<body><h1>{html.escape(title)}</h1></body></html>"
                if kind == "page"
                else f"# {title}\n"
            )
            (folder / main).write_text(content)
            now = time.time()
            item = dict(
                id=artifact_id,
                title=title,
                kind=kind,
                owner_id=owner,
                main=main,
                created_at=now,
                updated_at=now,
            )
            self.store.put("library.artifact", artifact_id, item)
            return self.summary(item)

    def rename(self, item, title):
        title = title.strip()
        if not title or len(title) > 120:
            raise HTTPException(422, "Use a title of 1–120 characters")
        with self.lock:
            item = {**self.get(item["id"]), "title": title, "updated_at": time.time()}
            self.store.put("library.artifact", item["id"], item)
            return self.summary(item)

    def delete(self, item):
        with self.lock:
            folder = self.root / item["id"]
            if folder.is_symlink():
                folder.unlink()
            elif folder.exists():
                shutil.rmtree(folder)
            self.store.delete("library.artifact", item["id"])

    def _open(self, item, path):
        """Walk using no-follow directory descriptors, including the artifact root.

        The agent can edit files concurrently; resolve()+read() is not sufficient
        to prevent a symlink swap from reading outside the artifact.
        """
        parts = PurePosixPath(path).parts
        if (
            not parts
            or path.startswith("/")
            or "\\" in path
            or any(p in ("..", ".") or p.startswith(".") for p in parts)
        ):
            raise HTTPException(404, "No such file")
        directory = None
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in (item["id"], *parts[:-1]):
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                os.close(fd)
                raise HTTPException(
                    413, "Artifact files must be regular files up to 2 MiB"
                )
            return fd
        except (OSError, ValueError):
            raise HTTPException(404, "No such file")
        finally:
            if directory is not None:
                os.close(directory)

    def file_stat(self, item, path):
        fd = self._open(item, path)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)

    def read(self, item, path):
        with os.fdopen(self._open(item, path), "rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise HTTPException(413, "Artifact file exceeds 2 MiB")
        return data
