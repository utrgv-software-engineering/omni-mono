"""Persistent Shared directory, operation journal, change feed and folder workflows."""

import asyncio
import fnmatch
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from fastapi import HTTPException
from .files import clean, directory, parent, snapshot

log = logging.getLogger(__name__)
EVENTS = {
    "shared.files_uploaded",
    "shared.file_updated",
    "shared.entry_moved",
    "shared.entry_deleted",
}


class Shared:
    def __init__(self, workplace):
        self.w = workplace
        self.store = workplace.store
        self.root = workplace.data_dir / "shared"
        self.staging = workplace.data_dir / "shared-staging"
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.observed = None
        self.task = None
        self.uploading = set()
        self.stopping = asyncio.Event()
        self.error = None

    def doc(self, kind, key):
        return dict(self.store.docs("shared." + kind)).get(key)

    def entries(self):
        return dict(self.store.docs("shared.entry"))

    def rules(self):
        return [v for _, v in self.store.docs("shared.rule")]

    def matches(self, rule, event):
        if not rule["enabled"]:
            return []
        if event["origin"] != "person" and not rule["include_agent"]:
            return []
        folder = rule["folder"]

        def matches_path(path):
            if folder and not path.startswith(folder + "/"):
                return False
            relative = path[len(folder) + 1 :] if folder else path
            return (
                bool(relative)
                and (rule["recursive"] or "/" not in relative)
                and any(
                    fnmatch.fnmatchcase(relative.rsplit("/", 1)[-1].lower(), p.lower())
                    for p in rule["patterns"]
                )
            )

        return [
            e
            for e in event["entries"]
            if e.get("change", event["event"]) in rule["events"]
            and not (
                e["kind"] == "folder" and event["event"] == "shared.files_uploaded"
            )
            and (
                matches_path(e["path"])
                or (e.get("old_path") and matches_path(e["old_path"]))
            )
        ]

    def event(self, event, entries, actor, origin, *, identity=None):
        event = dict(
            id=identity or uuid.uuid4().hex,
            event=event,
            entries=entries,
            actor=actor,
            origin=origin,
            at=time.time(),
            sandbox_root="/workspace/Shared",
        )
        self.store.put("shared.event", event["id"], event)
        for rule in self.rules():
            matched = self.matches(rule, event)
            if not matched:
                continue
            payload = {**event, "entries": matched, "workflow": rule}
            self.w.event_gateway.enqueue(
                "workplace-shared",
                "shared",
                event["id"] + ":" + rule["id"],
                json.dumps(payload).encode(),
                {},
            )
        return event

    def baseline(self, entries):
        old = self.entries()
        for path in old.keys() - entries.keys():
            self.store.delete("shared.entry", path)
        for path, entry in entries.items():
            if old.get(path) != entry:
                self.store.put("shared.entry", path, entry)
        self.observed = entries

    def baseline_affected(self, now, paths, descendants=False):
        # Do not swallow unrelated sandbox edits that raced a human operation.
        old = self.entries()

        def affected(path):
            return (
                path in paths
                or any(p.startswith(path + "/") for p in paths)
                or (descendants and any(path.startswith(p + "/") for p in paths))
            )

        for path in set(old) | set(now):
            if affected(path):
                if path in now:
                    old[path] = now[path]
                else:
                    old.pop(path, None)
        self.baseline(old)

    @staticmethod
    def public(entry):
        return {k: v for k, v in entry.items() if k not in ("signature", "inode")}

    def reconcile(self, *, stable=False):
        with self.lock:
            old = self.entries()
            now = snapshot(self.root, old)
            if stable and self.observed != now:
                self.observed = now
                return
            if not self.store.meta("shared.initialized", False):
                with self.store.transaction():
                    self.baseline(now)
                    self.store.set_meta("shared.initialized", True)
                return
            added = set(now) - set(old)
            removed = set(old) - set(now)
            moved = []
            for source in sorted(removed, key=lambda p: (p.count("/"), p)):
                if source not in removed:
                    continue
                target = next(
                    (
                        p
                        for p in sorted(added)
                        if now[p]["inode"] == old[source]["inode"]
                        and now[p]["kind"] == old[source]["kind"]
                    ),
                    None,
                )
                if target is None:
                    continue
                # Include affected descendants so folder rules can match file patterns.
                # One folder move represents its descendants, not a cascade of uploads.
                for child in list(removed):
                    dest = target + child[len(source) :]
                    if (
                        (child == source or child.startswith(source + "/"))
                        and dest in added
                        and old[child]["inode"] == now[dest]["inode"]
                    ):
                        moved.append({**self.public(now[dest]), "old_path": child})
                        removed.remove(child)
                        added.remove(dest)
            updated = [
                self.public(now[p])
                for p in old.keys() & now.keys()
                if old[p]["revision"] != now[p]["revision"]
                or old[p]["kind"] != now[p]["kind"]
            ]
            with self.store.transaction():
                for kind, entries in [
                    ("shared.entry_moved", moved),
                    (
                        "shared.files_uploaded",
                        [self.public(now[p]) for p in sorted(added)],
                    ),
                    ("shared.file_updated", updated),
                    (
                        "shared.entry_deleted",
                        [self.public(old[p]) for p in sorted(removed)],
                    ),
                ]:
                    if entries:
                        self.event(
                            kind,
                            entries,
                            {"id": "sandbox", "name": "Agent / sandbox"},
                            "agent",
                        )
                self.baseline(now)

    def listing(self, folder=""):
        folder = clean(folder, root=True)
        with self.lock:
            # Listing sees current bytes immediately; event detection still waits for stability.
            entries = snapshot(self.root, self.entries())
            with directory(self.root, folder):
                pass
            prefix = folder + "/" if folder else ""
            return dict(
                path=folder,
                entries=[
                    self.public(e)
                    for p, e in entries.items()
                    if p.startswith(prefix) and "/" not in p[len(prefix) :]
                ],
                error=self.error,
            )

    def rule(self, data, rule_id=None):
        folder = clean(data["folder"], root=True)
        with directory(self.root, folder):
            pass
        if not data["name"].strip() or not data["instruction"].strip():
            raise HTTPException(422, "Name and instructions are required")
        if not data["events"] or not set(data["events"]) <= EVENTS:
            raise HTTPException(422, "Choose supported file events")
        patterns = [p.strip() for p in data["patterns"] if p.strip()]
        if not patterns or any("/" in p or "\\" in p for p in patterns):
            raise HTTPException(422, "Use filename patterns such as *.pdf")
        with self.lock, self.store.transaction():
            previous = self.doc("rule", rule_id) if rule_id else None
            if rule_id and previous is None:
                raise HTTPException(404, "No such workflow")
            value = {
                **data,
                "id": rule_id or uuid.uuid4().hex,
                "folder": folder,
                "patterns": patterns,
                "name": data["name"].strip(),
                "revision": (previous or {}).get("revision", 0) + 1,
            }
            self.store.put("shared.rule", value["id"], value)
            return value

    def start_upload(self, files, actor, replace=False, folders=None):
        folders = sorted(set(clean(p) for p in (folders or [])))
        if not files and not folders:
            raise HTTPException(422, "Choose files")
        paths = [clean(f["path"]) for f in files]
        if len(paths) != len(set(paths)):
            raise HTTPException(409, "Duplicate upload paths")
        if any(
            any(p.startswith(other + "/") for other in paths if p != other)
            for p in paths
        ):
            raise HTTPException(409, "A file cannot contain another file")
        with self.lock:
            self.reconcile()
            old = self.entries()
            for folder in folders:
                if folder in old and old[folder]["kind"] != "folder":
                    raise HTTPException(409, f"{folder} already exists as a file")
            if any(
                folder == path or folder.startswith(path + "/")
                for folder in folders
                for path in paths
            ):
                raise HTTPException(409, "A file cannot contain a folder")
            for path in paths:
                if path in old and (not replace or old[path]["kind"] != "file"):
                    raise HTTPException(409, f"{path} already exists")
            upload = dict(
                id=uuid.uuid4().hex,
                actor=actor,
                folders=folders,
                files=[
                    {**f, "path": p, "previous": old.get(p), "ready": False}
                    for f, p in zip(files, paths)
                ],
                at=time.time(),
                status="uploading",
            )
            (self.staging / upload["id"]).mkdir(mode=0o700)
            self.store.put("shared.upload", upload["id"], upload)
            return upload

    def upload(self, upload_id, actor):
        value = self.doc("upload", upload_id)
        if not value or value["actor"]["id"] != actor["id"]:
            raise HTTPException(404, "No such upload")
        return value

    def finish_upload(self, upload_id, actor):
        with self.lock:
            value = self.upload(upload_id, actor)
            if value["status"] == "complete":
                return value
            if any(slot[0] == upload_id for slot in self.uploading):
                raise HTTPException(409, "Files are still uploading")
            if any(not f["ready"] for f in value["files"]):
                raise HTTPException(409, "Files are still uploading")
            self.reconcile()
            current = self.entries()
            for f in value["files"]:
                if current.get(f["path"]) != f["previous"]:
                    raise HTTPException(
                        409, f"{f['path']} changed during upload; start again"
                    )
            value["status"] = "committing"
            self.store.put("shared.upload", upload_id, value)
            return self.commit_upload(value)

    def commit_upload(self, value):
        with self.lock:
            for folder in value.get("folders", []):
                with directory(self.root, folder, create=True):
                    pass
            for i, f in enumerate(value["files"]):
                source = self.staging / value["id"] / str(i)
                if source.exists():
                    with parent(self.root, f["path"], create=True) as (fd, name):
                        os.replace(source, name, dst_dir_fd=fd)
                        os.fsync(fd)
                # The source disappears only after atomic installation; resume after a crash.
            now = snapshot(self.root, self.entries())
            with self.store.transaction():
                items = [
                    {
                        **self.public(now[f["path"]]),
                        "change": (
                            "shared.file_updated"
                            if f["previous"] is not None
                            else "shared.files_uploaded"
                        ),
                    }
                    for f in value["files"]
                ]
                items += [
                    self.public(now[p])
                    for p in value.get("folders", [])
                    if p not in self.entries()
                ]
                # One uploaded batch is one workflow invocation, including replacements.
                self.event(
                    (
                        "shared.file_updated"
                        if items
                        and all(e.get("change") == "shared.file_updated" for e in items)
                        else "shared.files_uploaded"
                    ),
                    items,
                    value["actor"],
                    "person",
                    identity=value["id"],
                )
                self.baseline_affected(
                    now, [f["path"] for f in value["files"]] + value.get("folders", [])
                )
                value["status"] = "complete"
                self.store.put("shared.upload", value["id"], value)
            shutil.rmtree(self.staging / value["id"], ignore_errors=True)
            return value

    def operation(self, action, path, actor, destination=None):
        path = clean(path)
        with self.lock:
            if action not in {"mkdir", "move", "delete"}:
                raise HTTPException(422, "Unknown file operation")
            with parent(self.root, path):
                pass
            self.reconcile()
            now = self.entries()
            if action != "mkdir" and path not in now:
                raise HTTPException(404, "No such file or folder")
            if action == "mkdir" and path in now:
                raise HTTPException(409, "That path already exists")
            if action == "move":
                destination = clean(destination)
                if destination in now or destination.startswith(path + "/"):
                    raise HTTPException(409, "Choose a different, unused destination")
                with parent(self.root, destination):
                    pass
            value = dict(
                id=uuid.uuid4().hex,
                action=action,
                path=path,
                destination=destination,
                actor=actor,
                entries=[
                    self.public(e)
                    for p, e in now.items()
                    if p == path or p.startswith(path + "/")
                ],
                status="pending",
            )
            self.store.put("shared.operation", value["id"], value)
            return self.apply_operation(value)

    def apply_operation(self, op):
        with self.lock:
            path = op["path"]
            with parent(self.root, path) as (fd, name):
                if op["action"] == "mkdir":
                    try:
                        os.mkdir(name, dir_fd=fd)
                    except FileExistsError:
                        with directory(self.root, path):
                            pass
                elif op["action"] == "move":
                    with parent(self.root, op["destination"]) as (dst, new_name):
                        try:
                            os.rename(name, new_name, src_dir_fd=fd, dst_dir_fd=dst)
                        except FileNotFoundError:
                            os.stat(new_name, dir_fd=dst, follow_symlinks=False)
                        os.fsync(dst)
                else:
                    try:
                        os.rename(
                            name, self.staging / ("delete-" + op["id"]), src_dir_fd=fd
                        )
                    except FileNotFoundError:
                        pass
                os.fsync(fd)
            now = snapshot(self.root, self.entries())
            if op["action"] == "mkdir":
                kind, items = "shared.files_uploaded", [self.public(now[path])]
            elif op["action"] == "move":
                kind = "shared.entry_moved"
                items = [
                    {
                        **self.public(now[op["destination"] + e["path"][len(path) :]]),
                        "old_path": e["path"],
                    }
                    for e in op["entries"]
                ]
            else:
                kind, items = "shared.entry_deleted", op["entries"]
            with self.store.transaction():
                self.event(kind, items, op["actor"], "person", identity=op["id"])
                self.baseline_affected(
                    now,
                    [path] + ([op["destination"]] if op["destination"] else []),
                    descendants=True,
                )
                op["status"] = "complete"
                self.store.put("shared.operation", op["id"], op)
            trash = self.staging / ("delete-" + op["id"])
            if trash.is_dir():
                shutil.rmtree(trash)
            elif trash.exists() or trash.is_symlink():
                trash.unlink()
            return {"ok": True}

    def maintain_once(self):
        with self.lock:
            failed = False
            for _, value in self.store.docs("shared.upload"):
                if value["status"] == "committing":
                    try:
                        self.commit_upload(value)
                    except Exception:
                        failed = True
                        log.exception("Shared upload recovery failed: %s", value["id"])
                elif (
                    value["status"] == "uploading"
                    and time.time() - value["at"] > 86400
                    and not any(slot[0] == value["id"] for slot in self.uploading)
                ):
                    shutil.rmtree(self.staging / value["id"], ignore_errors=True)
                    self.store.delete("shared.upload", value["id"])
            for _, op in self.store.docs("shared.operation"):
                if op["status"] == "pending":
                    try:
                        self.apply_operation(op)
                    except Exception:
                        failed = True
                        log.exception("Shared operation recovery failed: %s", op["id"])
            self.reconcile(stable=True)
            self.error = (
                "A Shared operation could not be recovered; retrying. Other file changes are still tracked."
                if failed
                else None
            )

    async def run(self):
        while not self.stopping.is_set():
            try:
                await asyncio.to_thread(self.maintain_once)
            except Exception:
                self.error = "Shared change tracking needs attention; retrying. Files remain available."
                log.exception("Shared reconciliation failed")
            try:
                await asyncio.wait_for(self.stopping.wait(), 2)
            except asyncio.TimeoutError:
                pass

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.stopping.set()
            await self.task
