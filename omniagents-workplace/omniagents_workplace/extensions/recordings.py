"""Recorder staging is durable; only finalization publishes a Shared event."""

import base64
import os
import re
import shutil
import time
import uuid
from fastapi import HTTPException


class Recordings:
    def __init__(self, workplace):
        self.shared = workplace.shared
        self.store = workplace.store
        self.root = self.shared.staging / "recordings"
        self.root.mkdir(exist_ok=True, mode=0o700)

    def get(self, identity, account):
        value = dict(self.store.docs("extension.recording")).get(identity)
        if value is None or value["actor"]["id"] != account.id:
            raise HTTPException(404, "Recording not found")
        return value

    def public(self, value):
        path = self.root / value["id"]
        return {
            **{k: v for k, v in value.items() if k != "upload"},
            "bytes": path.stat().st_size if path.exists() else value.get("bytes", 0),
        }

    def call(self, arguments, account):
        action = arguments.get("action")
        with self.shared.lock:
            if action == "list":
                return {
                    "recordings": [
                        self.public(v)
                        for _, v in self.store.docs("extension.recording")
                        if v["actor"]["id"] == account.id
                    ]
                }
            if action == "start":
                mime = arguments.get("mime", "audio/webm")
                suffix = {
                    "audio/webm": "webm",
                    "audio/ogg": "ogg",
                    "audio/mp4": "m4a",
                }.get(mime.split(";")[0])
                if not suffix:
                    raise HTTPException(422, "Unsupported recording format")
                identity = uuid.uuid4().hex
                value = {
                    "id": identity,
                    "title": str(arguments.get("title", "Recording")).strip()
                    or "Recording",
                    "mime": mime,
                    "actor": {"id": account.id, "name": account.name},
                    "status": "recording",
                    "created": time.time(),
                    "bytes": 0,
                    "path": "Recordings/"
                    + time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
                    + "-"
                    + identity[:8]
                    + "."
                    + suffix,
                }
                (self.root / identity).touch(mode=0o600)
                self.store.put("extension.recording", identity, value)
                return self.public(value)
            value = self.get(arguments.get("id"), account)
            path = self.root / value["id"]
            if action == "write":
                if value["status"] != "recording":
                    raise HTTPException(409, "Recording is already finalized")
                try:
                    data = base64.b64decode(arguments.get("data", ""), validate=True)
                    offset = int(arguments["offset"])
                except (ValueError, TypeError, KeyError):
                    raise HTTPException(422, "Invalid audio chunk") from None
                if offset < 0:
                    raise HTTPException(422, "Invalid audio offset")
                if shutil.disk_usage(self.root).free < len(data) + 64 * 1024 * 1024:
                    raise HTTPException(507, "Not enough disk space to save audio")
                with path.open("r+b") as stream:
                    size = stream.seek(0, os.SEEK_END)
                    if offset < size:
                        stream.seek(offset)
                        if stream.read(len(data)) != data:
                            raise HTTPException(
                                409, "Audio retry does not match the saved chunk"
                            )
                    elif offset == size:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    else:
                        raise HTTPException(409, "Audio chunks must arrive in order")
                return self.public(value)
            if action == "finish":
                if value["status"] == "saved":
                    return self.public(value)
                if not value.get("upload"):
                    title = (
                        str(arguments.get("title", value["title"])).strip()
                        or value["title"]
                    )
                    value["title"] = title
                    label = (
                        re.sub(r"[^\w .-]+", "-", title, flags=re.UNICODE).strip(" .-")[
                            :100
                        ]
                        or "Recording"
                    )
                    suffix = value["path"].rsplit(".", 1)[-1]
                    value["path"] = f"Recordings/{label}-{value['id'][:8]}.{suffix}"
                    size = path.stat().st_size
                    if not size:
                        raise HTTPException(422, "Recording contains no audio")
                    upload = self.shared.start_upload(
                        [{"path": value["path"], "size": size}], value["actor"]
                    )
                    value.update(upload=upload["id"], bytes=size, status="finalizing")
                    self.store.put("extension.recording", value["id"], value)
                upload = self.shared.upload(value["upload"], value["actor"])
                if upload["status"] == "uploading":
                    destination = self.shared.staging / upload["id"] / "0"
                    if not destination.exists():
                        temporary = destination.with_suffix(".partial")
                        with path.open("rb") as source, temporary.open("wb") as output:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, destination)
                    upload["files"][0]["ready"] = True
                    self.store.put("shared.upload", upload["id"], upload)
                if upload["status"] == "committing":
                    self.shared.commit_upload(upload)
                else:
                    self.shared.finish_upload(upload["id"], value["actor"])
                value["status"] = "saved"
                self.store.put("extension.recording", value["id"], value)
                path.unlink(missing_ok=True)
                return self.public(value)
            if action == "discard":
                if value["status"] != "recording":
                    raise HTTPException(409, "Use Shared to manage saved recordings")
                path.unlink(missing_ok=True)
                self.store.delete("extension.recording", value["id"])
                return {"ok": True}
            raise HTTPException(422, "Unknown recorder action")
