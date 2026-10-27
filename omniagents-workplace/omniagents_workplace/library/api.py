"""Signed-in Library UI, bearer-authenticated agent tools, and isolated pages."""

import hashlib
import hmac
import mimetypes
import secrets
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..accounts import me


class CreateArtifact(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["page", "document"] = "document"


class RenameArtifact(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def library_router(workplace):
    r = APIRouter()
    library = workplace.library
    secret = secrets.token_bytes(32)

    def key(item):
        return hmac.new(secret, item["id"].encode(), hashlib.sha256).hexdigest()

    def summary(item):
        item = library.summary(item)
        item["url"] = f"{workplace.base_url.rstrip('/')}/library/{item['id']}"
        item["view"] = (
            f"/library/view/{item['id']}/{key(item)}/{item['main']}?v={time.time_ns()}"
        )
        if item["kind"] == "document":
            try:
                item["markdown"] = library.read(item, item["main"]).decode(
                    "utf-8", errors="replace"
                )
            except HTTPException:
                item["markdown"] = ""
        return item

    @r.get("/workplace/api/library/artifacts")
    def listing(request: Request):
        me(request)
        return {"artifacts": [summary(d) for d in library.list()]}

    @r.post("/workplace/api/library/artifacts")
    def create(body: CreateArtifact, request: Request):
        return summary(library.create(body.title, body.kind, me(request).id))

    @r.get("/workplace/api/library/artifacts/{artifact_id}")
    def get(artifact_id: str, request: Request):
        me(request)
        return summary(library.get(artifact_id))

    @r.patch("/workplace/api/library/artifacts/{artifact_id}")
    def rename(artifact_id: str, body: RenameArtifact, request: Request):
        me(request)
        return summary(library.rename(library.get(artifact_id), body.title))

    @r.delete("/workplace/api/library/artifacts/{artifact_id}")
    def delete(artifact_id: str, request: Request):
        account = me(request)
        item = library.get(artifact_id)
        if item["owner_id"] != account.id and not account.admin:
            raise HTTPException(403, "Its owner or an admin deletes an artifact")
        library.delete(item)
        return {"ok": True}

    def agent(request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        member = (
            workplace.canvas.member_for_token(token)
            if scheme.lower() == "bearer"
            else None
        )
        if member is None:
            raise HTTPException(401, "A valid app or member token is required")
        return member

    @r.get("/library/api/artifacts")
    def agent_list(request: Request):
        agent(request)
        return {"artifacts": [summary(d) for d in library.list()]}

    @r.post("/library/api/artifacts")
    def agent_create(body: CreateArtifact, request: Request):
        return summary(library.create(body.title, body.kind, agent(request).id))

    @r.get("/library/api/artifacts/{artifact_id}")
    def agent_get(artifact_id: str, request: Request):
        agent(request)
        return summary(library.get(artifact_id))

    @r.get("/library/view/{artifact_id}/{view_key}/{path:path}")
    def view(artifact_id: str, view_key: str, path: str, request: Request):
        item = library.get(artifact_id)
        if not hmac.compare_digest(view_key, key(item)):
            raise HTTPException(404, "No such file")
        data = library.read(item, path)
        origin = f"{request.url.scheme}://{request.url.netloc}"
        # Opaque origin: no cookies, parent DOM, forms, popups, or navigation.
        # Relative assets and fetches are allowed only inside this artifact.
        own = f"{origin}/library/view/{artifact_id}/{view_key}/"
        csp = (
            f"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' {own}; "
            f"style-src 'unsafe-inline' {own}; img-src data: blob: {own}; font-src data: {own}; "
            f"media-src {own}; connect-src {own}; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return Response(
            data,
            media_type=mime,
            headers={
                "Content-Security-Policy": csp,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    return r
