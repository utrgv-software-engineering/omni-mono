"""Canvases over HTTP: their boards as pages, and the UI's canvas API.

- ``/canvas/view/<canvas>/<key>/<version>/<path>``: a canvas's files as a
  board's page loads them (``./support.js`` and the rest resolve beside it).
  ``key`` is the canvas's view key, which the UI's API hands to people who
  can see it: the page runs sandboxed, without the workplace's cookies, so
  its own requests carry the key in their path instead. Every response is
  sandboxed (``Content-Security-Policy: sandbox allow-scripts``): a board's
  script runs, but as no one, and reaches nothing but its canvas's files.
  ``support.js`` is the workplace's runtime (``canvas/support.js``) unless
  the canvas has its own.
- ``/workplace/api/canvas/...``: what people see and do: canvases, their
  index, files and versions, publishing (moving a board is a publish of the
  index), restoring a version, and comments.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from importlib import resources
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..accounts import me
from .state import CanvasError, CanvasState, is_text, media_type

__all__ = ["canvas_router"]


class NewCanvas(BaseModel):
    title: str


class EditCanvas(BaseModel):
    title: str


class Publish(BaseModel):
    #: path -> new text (``None`` removes it).
    files: Dict[str, Optional[str]]
    base_version: int
    label: str = ""


class Restore(BaseModel):
    version: int


class NewComment(BaseModel):
    body: str
    board: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    parent_id: Optional[str] = None


class Resolve(BaseModel):
    resolved: bool = True


def runtime() -> bytes:
    """The workplace's board runtime."""
    return (
        resources.files("omniagents_workplace")
        .joinpath("canvas/support.js")
        .read_bytes()
    )


def canvas_router(
    state: CanvasState, base_url: Callable[[], str], member_of: Callable[[Any], str]
) -> APIRouter:
    """``member_of(account)`` is the canvas member a signed-in person acts as."""
    r = APIRouter()
    secret = secrets.token_bytes(32)

    def view_key(canvas_id: str) -> str:
        return hmac.new(secret, canvas_id.encode(), hashlib.sha256).hexdigest()[:32]

    def _refused(exc: CanvasError):
        raise HTTPException(exc.status, exc.message)

    def _try(call):
        try:
            return call()
        except CanvasError as exc:
            _refused(exc)

    # -- boards, as pages ------------------------------------------------------------

    def _sandboxed(content: bytes, kind: str, request: Request) -> Response:
        origin = f"{request.url.scheme}://{request.url.netloc}"
        policy = (
            "sandbox allow-scripts; default-src 'none'; "
            f"script-src {origin} 'unsafe-eval'; "
            f"style-src {origin} 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com data:; "
            f"img-src {origin} data: blob: https:; media-src {origin} data: blob:; "
            "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        return Response(
            content,
            media_type=kind,
            headers={
                "Content-Security-Policy": policy,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "private, max-age=60",
            },
        )

    @r.get("/canvas/view/{canvas_id}/{key}/{version}/{path:path}")
    def view(canvas_id: str, key: str, version: int, path: str, request: Request):
        canvas = state.canvases.get(canvas_id)
        if canvas is None or not hmac.compare_digest(key, view_key(canvas_id)):
            return Response(status_code=404)
        try:
            file = state.read(canvas, path, version)
        except CanvasError:
            if path == "support.js":
                return _sandboxed(runtime(), "text/javascript; charset=utf-8", request)
            return Response(status_code=404)
        return _sandboxed(file.content, media_type(path), request)

    # -- the UI's API -------------------------------------------------------------------

    def acting(request: Request) -> str:
        return member_of(me(request))

    def canvas_of(ref: str):
        return _try(lambda: state.canvas(ref))

    def name(member_id: str) -> Optional[str]:
        member = state.members.get(member_id)
        return member.name if member else None

    def summary(canvas) -> Dict[str, Any]:
        index = state.index(canvas)
        first = next(iter(index.get("order") or []), None)
        board = (index.get("boards") or {}).get(first, {})
        preview = (
            {
                "src": f"/canvas/view/{canvas.id}/{view_key(canvas.id)}/{canvas.version}/{first}",
                "width": board.get("w", 1440),
                "height": board.get("h", 900),
            }
            if first
            else None
        )
        return {
            "preview": preview,
            "id": canvas.id,
            "title": canvas.title,
            "version": canvas.version,
            "owner": name(canvas.owner_id),
            "owner_id": canvas.owner_id,
            "updated_at": canvas.updated_at,
            "boards": len(index.get("order") or []),
            "open_comments": sum(
                1
                for c in state.thread(canvas)
                if c.parent_id is None and not c.resolved_at
            ),
        }

    def comment_json(c) -> Dict[str, Any]:
        return {
            "id": c.id,
            "body": c.body,
            "author": name(c.author_id),
            "author_id": c.author_id,
            "at": c.created_at,
            "board": c.board,
            "x": c.x,
            "y": c.y,
            "parent_id": c.parent_id,
            "resolved": c.resolved_at is not None,
            "version": c.version,
        }

    @r.get("/workplace/api/canvas/canvases")
    def canvases(request: Request):
        acting(request)
        items = sorted(
            state.canvases.values(), key=lambda c: c.updated_at, reverse=True
        )
        return {"canvases": [summary(c) for c in items]}

    @r.post("/workplace/api/canvas/canvases")
    def create(body: NewCanvas, request: Request):
        canvas = _try(lambda: state.create(acting(request), body.title))
        return summary(canvas)

    @r.get("/workplace/api/canvas/canvases/{canvas_id}")
    def canvas(canvas_id: str, request: Request, version: Optional[int] = None):
        acting(request)
        c = canvas_of(canvas_id)
        shown = version or c.version
        index = _try(lambda: state.index(c, shown))
        files = _try(lambda: state.version(c, shown).files)
        history = [
            {
                "version": v.number,
                "author": name(v.author_id),
                "at": v.at,
                "label": v.label,
                "changed": v.changed,
            }
            for v in state.history(c)
        ]
        return {
            **summary(c),
            "shown": shown,
            "index": index,
            "files": [
                {"path": p, "size": len(state.blobs[s])}
                for p, s in sorted(files.items())
            ],
            "view": f"/canvas/view/{c.id}/{view_key(c.id)}/{shown}/",
            "versions": history,
            "comments": [comment_json(x) for x in state.thread(c)],
        }

    @r.patch("/workplace/api/canvas/canvases/{canvas_id}")
    def rename(canvas_id: str, body: EditCanvas, request: Request):
        c = canvas_of(canvas_id)
        _try(lambda: state.rename(c, acting(request), body.title))
        return summary(c)

    @r.delete("/workplace/api/canvas/canvases/{canvas_id}")
    def delete(canvas_id: str, request: Request):
        account = me(request)
        c = canvas_of(canvas_id)
        if c.owner_id != member_of(account) and not account.admin:
            raise HTTPException(403, "Its owner or an admin deletes a canvas")
        state.delete(c)
        return {"ok": True}

    @r.get("/workplace/api/canvas/canvases/{canvas_id}/files/{path:path}")
    def read(
        canvas_id: str, path: str, request: Request, version: Optional[int] = None
    ):
        acting(request)
        c = canvas_of(canvas_id)
        f = _try(lambda: state.read(c, path, version))
        if not is_text(path):
            raise HTTPException(415, f"{path} is not text")
        return {"path": f.path, "text": f.text, "version": f.version}

    @r.post("/workplace/api/canvas/canvases/{canvas_id}/publish")
    def publish(canvas_id: str, body: Publish, request: Request):
        c = canvas_of(canvas_id)
        files = {p: (None if v is None else v.encode()) for p, v in body.files.items()}
        v = _try(
            lambda: state.publish(
                c,
                files,
                base_version=body.base_version,
                author_id=acting(request),
                label=body.label,
            )
        )
        return {"version": v.number, "changed": v.changed}

    @r.post("/workplace/api/canvas/canvases/{canvas_id}/restore")
    def restore(canvas_id: str, body: Restore, request: Request):
        c = canvas_of(canvas_id)
        v = _try(lambda: state.restore(c, body.version, acting(request)))
        return {"version": v.number, "changed": v.changed}

    @r.post("/workplace/api/canvas/canvases/{canvas_id}/comments")
    def comment(canvas_id: str, body: NewComment, request: Request):
        c = canvas_of(canvas_id)
        note = _try(
            lambda: state.comment(
                c,
                acting(request),
                body.body,
                board=body.board,
                x=body.x,
                y=body.y,
                parent_id=body.parent_id,
            )
        )
        return comment_json(note)

    @r.post("/workplace/api/canvas/comments/{comment_id}/resolve")
    def resolve(comment_id: str, body: Resolve, request: Request):
        acting(request)
        note = state.comments.get(comment_id)
        if note is None:
            raise HTTPException(404, "No such comment")
        return comment_json(state.resolve(note, body.resolved))

    @r.delete("/workplace/api/canvas/comments/{comment_id}")
    def delete_comment(comment_id: str, request: Request):
        account = me(request)
        note = state.comments.get(comment_id)
        if note is None:
            raise HTTPException(404, "No such comment")
        if note.author_id != member_of(account) and not account.admin:
            raise HTTPException(403, "You can only delete your own comments")
        state.delete_comment(note)
        return {"ok": True}

    return r
