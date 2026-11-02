"""Revocable item-scoped review links. Feedback is persisted, never queued to agents."""

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .accounts import me
from .canvas.api import canvas_response, runtime
from .canvas.state import CanvasError, media_type
from .library.api import artifact_response

Kind = Literal["canvas", "artifact"]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def view_token(token):
    # A read-only capability, distinct from the review/comment capability.
    return digest("workplace-share-view:" + token)


class Identity(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Comment(BaseModel):
    body: str
    parent_id: str | None = None
    board: str | None = None
    x: float | None = None
    y: float | None = None


class Resolution(BaseModel):
    resolved: bool = True


class Sharing:
    def __init__(self, workplace):
        self.w = workplace
        self.store = workplace.store
        self.lock = threading.RLock()

    def doc(self, kind, key):
        return next((value for k, value in self.store.docs(kind) if k == key), None)

    def item(self, kind, item_id):
        if kind == "artifact":
            return self.w.library.get(item_id)
        try:
            return self.w.canvas.canvas(item_id)
        except CanvasError as exc:
            raise HTTPException(404, "No such canvas") from exc

    def manage(self, kind, item_id, account):
        item = self.item(kind, item_id)
        owner = item["owner_id"] if kind == "artifact" else item.owner_id
        if not account.admin and account.id != owner:
            raise HTTPException(403, "Its owner or an admin manages sharing")

    def create(self, kind, item_id, account):
        with self.lock:
            self.manage(kind, item_id, account)
            token = secrets.token_urlsafe(32)
            link = dict(
                id=uuid.uuid4().hex,
                kind=kind,
                item_id=item_id,
                created_at=time.time(),
                created_by=account.id,
                token_hash=digest(token),
                view_hash=digest(view_token(token)),
            )
            self.store.put("review.share", link["id"], link)
            return link, token

    def link(self, share_id, token, *, viewer=False):
        link = self.doc("review.share", share_id)
        if (
            not link
            or not token
            or not hmac.compare_digest(
                link["view_hash" if viewer else "token_hash"], digest(token)
            )
        ):
            raise HTTPException(404, "This share link is unavailable")
        self.item(link["kind"], link["item_id"])  # Deleted items invalidate links too.
        return link

    def identity(self, link, request):
        token = request.cookies.get("review_guest_" + link["id"], "")
        guest = self.doc("review.guest", digest(token)) if token else None
        return guest if guest and guest["share_id"] == link["id"] else None

    def comments(self, kind, item_id):
        self.item(kind, item_id)
        if kind == "canvas":
            state = self.w.canvas
            return [
                dict(
                    id=c.id,
                    body=c.body,
                    author=state.members[c.author_id].name,
                    author_id=c.author_id,
                    guest=state.members[c.author_id].kind == "guest",
                    at=c.created_at,
                    board=c.board,
                    x=c.x,
                    y=c.y,
                    parent_id=c.parent_id,
                    resolved=c.resolved_at is not None,
                    version=c.version,
                )
                for c in state.thread(self.item(kind, item_id))
            ]
        return sorted(
            (
                c
                for _, c in self.store.docs("library.comment")
                if c["artifact_id"] == item_id
            ),
            key=lambda c: c["at"],
        )

    def comment(self, kind, item_id, author_id, name, body, *, guest=False):
        if not body.body.strip():
            raise HTTPException(422, "A comment has a body")
        with self.lock:
            item = self.item(kind, item_id)
            if kind == "canvas":
                try:
                    note = self.w.canvas.comment(
                        item,
                        author_id,
                        body.body,
                        parent_id=body.parent_id,
                        board=body.board,
                        x=body.x,
                        y=body.y,
                    )
                except CanvasError as exc:
                    raise HTTPException(exc.status, exc.message) from exc
                return next(
                    c for c in self.comments(kind, item_id) if c["id"] == note.id
                )
            parent = None
            if body.parent_id:
                parent = self.doc("library.comment", body.parent_id)
                if not parent or parent["artifact_id"] != item_id:
                    raise HTTPException(404, "No such comment")
            note = dict(
                id=uuid.uuid4().hex,
                artifact_id=item_id,
                author_id=author_id,
                author=name,
                guest=guest,
                body=body.body.strip(),
                at=time.time(),
                parent_id=(parent.get("parent_id") or parent["id"]) if parent else None,
                resolved=False,
                revision=self.w.library.summary(item)["updated_at"],
            )
            self.store.put("library.comment", note["id"], note)
            return note

    def artifact_note(self, item_id, comment_id):
        self.item("artifact", item_id)
        note = self.doc("library.comment", comment_id)
        if not note or note["artifact_id"] != item_id:
            raise HTTPException(404, "No such comment")
        return note


def sharing_router(workplace):
    r = APIRouter()
    state = Sharing(workplace)
    workplace.sharing = state

    def auth(request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return token if scheme.lower() == "bearer" else ""

    def guest_link(share_id, request):
        return state.link(share_id, auth(request))

    def mutation(request):
        from .factory_runtime import trusted_origin

        origin = request.headers.get("origin")
        if origin and not trusted_origin(
            origin, workplace.base_url, workplace.additional_origins
        ):
            raise HTTPException(403, "Request must come from this workplace")
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "Send JSON")

    @r.get("/workplace/api/sharing/{kind}/{item_id}")
    def links(kind: Kind, item_id: str, request: Request):
        state.manage(kind, item_id, me(request))
        return {
            "links": [
                dict(id=v["id"], created_at=v["created_at"])
                for _, v in state.store.docs("review.share")
                if (v["kind"], v["item_id"]) == (kind, item_id)
            ]
        }

    @r.post("/workplace/api/sharing/{kind}/{item_id}")
    def create(kind: Kind, item_id: str, request: Request):
        link, token = state.create(kind, item_id, me(request))
        prefix = getattr(request.state, "factory_prefix", "")
        return {
            "id": link["id"],
            "created_at": link["created_at"],
            "url": f"{prefix}/share/{link['id']}#token={token}",
        }

    @r.delete("/workplace/api/sharing/{kind}/{item_id}/{share_id}")
    def revoke(kind: Kind, item_id: str, share_id: str, request: Request):
        with state.lock:
            state.manage(kind, item_id, me(request))
            link = state.doc("review.share", share_id)
            if not link or (link["kind"], link["item_id"]) != (kind, item_id):
                raise HTTPException(404, "No such share link")
            state.store.delete("review.share", share_id)
        return {"ok": True}

    @r.get("/share/{share_id}/api")
    def shared(share_id: str, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        link = guest_link(share_id, request)
        kind, item_id = link["kind"], link["item_id"]
        item = state.item(kind, item_id)
        guest = state.identity(link, request)
        prefix = getattr(request.state, "factory_prefix", "")
        files = f"{prefix}/share/{share_id}/files/{view_token(auth(request))}/"
        comments = state.comments(kind, item_id)
        if kind == "canvas":
            index = workplace.canvas.index(item)
            detail = dict(
                id=item.id,
                title=item.title,
                version=item.version,
                shown=item.version,
                updated_at=item.updated_at,
                owner_id="",
                owner=None,
                index=index,
                boards=len(index["order"]),
                open_comments=sum(
                    not c["resolved"] and not c["parent_id"] for c in comments
                ),
                view=files + str(item.version) + "/",
                files=[],
                versions=[],
                comments=comments,
            )
        else:
            detail = {k: item[k] for k in ("id", "title", "kind", "main", "updated_at")}
            detail["updated_at"] = workplace.library.summary(item)["updated_at"]
            detail["view"] = files + "current/" + item["main"]
            if item["kind"] == "document":
                detail["markdown"] = workplace.library.read(item, item["main"]).decode(
                    "utf-8", errors="replace"
                )
        return {
            "kind": kind,
            "item": detail,
            "comments": comments,
            "guest": {"id": guest["id"], "name": guest["name"]} if guest else None,
        }

    @r.post("/share/{share_id}/identity")
    def identify(share_id: str, body: Identity, request: Request, response: Response):
        mutation(request)
        with state.lock:
            link = guest_link(share_id, request)
            name = body.name.strip()
            if not name:
                raise HTTPException(422, "Enter your name")
            existing = state.identity(link, request)
            if existing:
                return {"id": existing["id"], "name": existing["name"]}
            token = secrets.token_urlsafe(32)
            guest = dict(id="guest-" + uuid.uuid4().hex, share_id=share_id, name=name)
            state.store.put("review.guest", digest(token), guest)
            # No login or API token: this member exists only for comment attribution.
            workplace.canvas.add_member(guest["id"], name, "guest")
            prefix = getattr(request.state, "factory_prefix", "")
            response.set_cookie(
                "review_guest_" + share_id,
                token,
                httponly=True,
                secure=workplace.base_url.startswith("https://"),
                samesite="strict",
                path=f"{prefix}/share/{share_id}",
                max_age=30 * 86400,
            )
            response.headers["Cache-Control"] = "no-store"
            return {"id": guest["id"], "name": name}

    @r.post("/share/{share_id}/comments")
    def guest_comment(
        share_id: str, body: Comment, request: Request, response: Response
    ):
        mutation(request)
        with state.lock:
            link = guest_link(share_id, request)
            guest = state.identity(link, request)
            if not guest:
                raise HTTPException(401, "Enter your name before commenting")
            response.headers["Cache-Control"] = "no-store"
            return state.comment(
                link["kind"],
                link["item_id"],
                guest["id"],
                guest["name"],
                body,
                guest=True,
            )

    @r.get("/share/{share_id}/files/{capability}/{version}/{path:path}")
    def file(share_id: str, capability: str, version: str, path: str, request: Request):
        link = state.link(share_id, capability, viewer=True)
        item = state.item(link["kind"], link["item_id"])
        if link["kind"] == "canvas":
            if version != str(item.version):
                raise HTTPException(404, "This version is no longer shared")
            try:
                data = workplace.canvas.read(item, path).content
            except CanvasError as exc:
                if path != "support.js":
                    raise HTTPException(404, "No such file") from exc
                data = runtime()
            result = canvas_response(data, media_type(path), request)
            result.headers["Cache-Control"] = "no-store"
            return result
        if version != "current":
            raise HTTPException(404, "No such file")
        prefix = getattr(request.state, "factory_prefix", "")
        own = f"{request.url.scheme}://{request.url.netloc}{prefix}/share/{share_id}/files/{capability}/current/"
        return artifact_response(workplace.library.read(item, path), path, request, own)

    @r.get("/workplace/api/library/artifacts/{item_id}/comments")
    def comments(item_id: str, request: Request):
        me(request)
        return {"comments": state.comments("artifact", item_id)}

    @r.post("/workplace/api/library/artifacts/{item_id}/comments")
    def comment(item_id: str, body: Comment, request: Request):
        account = me(request)
        return state.comment("artifact", item_id, account.id, account.name, body)

    @r.post("/workplace/api/library/artifacts/{item_id}/comments/{comment_id}/resolve")
    def resolve(item_id: str, comment_id: str, body: Resolution, request: Request):
        me(request)
        with state.lock:
            note = state.artifact_note(item_id, comment_id)
            root = state.artifact_note(item_id, note.get("parent_id") or note["id"])
            root["resolved"] = body.resolved
            state.store.put("library.comment", root["id"], root)
            return root

    @r.delete("/workplace/api/library/artifacts/{item_id}/comments/{comment_id}")
    def delete(item_id: str, comment_id: str, request: Request):
        account = me(request)
        with state.lock:
            note = state.artifact_note(item_id, comment_id)
            if not account.admin and note["author_id"] != account.id:
                raise HTTPException(403, "You can only delete your own comments")
            for c in state.comments("artifact", item_id):
                if c["id"] == comment_id or c["parent_id"] == comment_id:
                    state.store.delete("library.comment", c["id"])
        return {"ok": True}

    @r.get("/library/api/artifacts/{item_id}/comments")
    def agent_comments(item_id: str, request: Request):
        if workplace.canvas.member_for_token(auth(request)) is None:
            raise HTTPException(401, "A valid app or member token is required")
        return {"comments": state.comments("artifact", item_id)}

    return r
