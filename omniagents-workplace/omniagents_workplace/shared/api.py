"""Authenticated Shared file management. Upload chunks are never visible to the sandbox."""

import asyncio
import hashlib
import mimetypes
import os
import re
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from ..accounts import me
from .files import clean, open_file, directory, snapshot


class FileSpec(BaseModel):
    path: str
    size: int = Field(ge=0)


class Upload(BaseModel):
    files: list[FileSpec]
    replace: bool = False
    folders: list[str] = []


class Operation(BaseModel):
    action: str
    path: str
    destination: str | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    folder: str = ""
    instruction: str
    patterns: list[str] = ["*"]
    events: list[str] = ["shared.files_uploaded"]
    recursive: bool = True
    include_agent: bool = False
    enabled: bool = False


def shared_router(w):
    router = APIRouter(prefix="/workplace/api/shared")
    shared = w.shared

    def actor(request):
        account = me(request)
        return {"id": account.id, "name": account.name}

    def admin(request):
        account = me(request)
        if not account.admin:
            raise HTTPException(403, "Only admins manage workflows")
        return account

    @router.get("/files")
    def listing(request: Request, path: str = ""):
        me(request)
        return shared.listing(path)

    @router.post("/files")
    def operation(body: Operation, request: Request):
        if body.action not in ("mkdir", "move", "delete"):
            raise HTTPException(422, "Unknown file operation")
        return shared.operation(
            body.action, body.path, actor(request), body.destination
        )

    @router.post("/uploads")
    def begin(body: Upload, request: Request):
        person = actor(request)
        with shared.lock:
            reserved = sum(
                sum(f["size"] for f in u["files"])
                for _, u in shared.store.docs("shared.upload")
                if u["status"] == "uploading"
            )
            if (
                sum(f.size for f in body.files) + reserved
                > shutil.disk_usage(shared.root).free - 64 * 1024 * 1024
            ):
                raise HTTPException(507, "Not enough space for this upload")
            value = shared.start_upload(
                [f.model_dump() for f in body.files], person, body.replace, body.folders
            )
        return {"id": value["id"]}

    @router.put("/uploads/{upload_id}/{index}")
    async def chunk(upload_id: str, index: int, request: Request, offset: int = 0):
        person = actor(request)
        with shared.lock:
            upload = shared.upload(upload_id, person)
            if upload["status"] != "uploading" or not 0 <= index < len(upload["files"]):
                raise HTTPException(409, "This upload is not writable")
            slot = (upload_id, index)
            if slot in shared.uploading:
                raise HTTPException(409, "This file is already uploading")
            f = upload["files"][index]
            path = shared.staging / upload_id / str(index)
            existing = path.stat().st_size if path.exists() else 0
            if offset != existing:
                raise HTTPException(409, f"Resume at offset {existing}")
            shared.uploading.add(slot)
        try:
            received = 0
            with path.open("ab") as stream:
                async for data in request.stream():
                    received += len(data)
                    if received > 16 * 1024 * 1024 or offset + received > f["size"]:
                        raise HTTPException(
                            413,
                            "Upload chunks must be at most 16 MiB and fit the declared file size",
                        )
                    await asyncio.to_thread(stream.write, data)
                await asyncio.to_thread(stream.flush)
                await asyncio.to_thread(os.fsync, stream.fileno())
            size = offset + received
            with shared.lock:
                # Reload: another file in this batch may have finished concurrently.
                value = shared.upload(upload_id, person)
                value["files"][index]["ready"] = size == f["size"]
                shared.store.put("shared.upload", upload_id, value)
            return {"offset": size, "ready": size == f["size"]}
        except BaseException:
            with path.open("r+b") as stream:
                stream.truncate(offset)
            raise
        finally:
            with shared.lock:
                shared.uploading.discard(slot)

    @router.post("/uploads/{upload_id}/commit")
    def commit(upload_id: str, request: Request):
        return {"status": shared.finish_upload(upload_id, actor(request))["status"]}

    @router.delete("/uploads/{upload_id}")
    def abort(upload_id: str, request: Request):
        with shared.lock:
            value = shared.upload(upload_id, actor(request))
            if value["status"] != "uploading" or any(
                s[0] == upload_id for s in shared.uploading
            ):
                raise HTTPException(409, "This upload is busy")
            shutil.rmtree(shared.staging / upload_id, ignore_errors=True)
            shared.store.delete("shared.upload", upload_id)
        return {"ok": True}

    @router.get("/content")
    def content(request: Request, path: str, preview: bool = False):
        me(request)
        path = clean(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            stream = open_file(shared.root, path)
        except IsADirectoryError:
            raise HTTPException(409, "Download a folder as ZIP") from None
        except FileNotFoundError:
            raise HTTPException(404, "No such file") from None
        pdf = preview and mime == "application/pdf" and stream.read(5) == b"%PDF-"
        stream.seek(0)
        if preview and mime == "application/pdf" and not pdf:
            mime = "text/plain; charset=utf-8"
        safe = mime in (
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/avif",
            "application/pdf",
            "audio/mpeg",
            "audio/wav",
            "video/mp4",
            "video/webm",
        )
        if preview and not safe:
            mime = "text/plain; charset=utf-8"
        headers = {
            "Content-Disposition": ("inline" if preview else "attachment")
            + "; filename*=UTF-8''"
            + quote(Path(path).name),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": ("" if pdf else "sandbox; ")
            + "default-src 'none'; frame-ancestors 'self'",
            "Cache-Control": "no-store",
        }

        total = os.fstat(stream.fileno()).st_size
        start, end, status = 0, total - 1, 200
        headers["Accept-Ranges"] = "bytes"
        if request.headers.get("range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers["range"])
            try:
                if not match or not any(match.groups()):
                    raise ValueError()
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), total - 1) if last else total - 1
                else:
                    count = int(last)
                    if count <= 0:
                        raise ValueError()
                    start = max(0, total - count)
                if start > end or start >= total:
                    raise ValueError()
            except ValueError:
                stream.close()
                raise HTTPException(
                    416,
                    "Invalid byte range",
                    headers={"Content-Range": f"bytes */{total}"},
                ) from None
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        length = max(0, end - start + 1)
        headers["Content-Length"] = str(length)
        stream.seek(start)

        def chunks():
            remaining = length
            try:
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                stream.close()

        return StreamingResponse(
            chunks(), status_code=status, media_type=mime, headers=headers
        )

    @router.get("/download-folder")
    def archive(request: Request, path: str = ""):
        me(request)
        path = clean(path, root=True)
        with directory(shared.root, path):
            pass
        output = tempfile.TemporaryFile(dir=shared.staging)
        try:
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                entries = snapshot(shared.root)
                prefix = path + "/" if path else ""
                for name, entry in entries.items():
                    if not name.startswith(prefix):
                        continue
                    relative = name[len(prefix) :]
                    if not relative:
                        continue
                    if entry["kind"] == "folder":
                        archive.writestr(relative + "/", "")
                    else:
                        with open_file(shared.root, name) as source, archive.open(
                            relative, "w", force_zip64=True
                        ) as target:
                            shutil.copyfileobj(source, target, 1024 * 1024)
            output.seek(0)
        except BaseException:
            output.close()
            raise

        def chunks():
            try:
                while data := output.read(1024 * 1024):
                    yield data
            finally:
                output.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote((Path(path).name or "Shared") + ".zip")
            },
        )

    @router.get("/workflows")
    def workflows(request: Request):
        me(request)
        return {"workflows": shared.rules()}

    @router.post("/workflows")
    def create_rule(body: Rule, request: Request):
        admin(request)
        return shared.rule(body.model_dump())

    @router.put("/workflows/{rule_id}")
    def edit_rule(rule_id: str, body: Rule, request: Request):
        admin(request)
        return shared.rule(body.model_dump(), rule_id)

    @router.delete("/workflows/{rule_id}")
    def delete_rule(rule_id: str, request: Request):
        admin(request)
        with shared.lock:
            shared.store.delete("shared.rule", rule_id)
        return {"ok": True}

    @router.get("/events")
    def events(request: Request, before: float | None = None):
        me(request)
        items = [
            e
            for _, e in shared.store.docs("shared.event")
            if before is None or e["at"] < before
        ]
        items.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
        with shared.store._lock:
            rows = shared.store._db.execute(
                "SELECT delivery,attempts,delivered,error FROM factory_events WHERE provider='shared' ORDER BY id DESC LIMIT 100"
            ).fetchall()
        return {
            "events": items[:100],
            "deliveries": [
                dict(zip(("delivery", "attempts", "delivered", "error"), r))
                for r in rows
            ],
        }

    return router
