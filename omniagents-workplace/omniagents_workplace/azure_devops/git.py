import base64
import fnmatch
import re

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from .client import segment


def receive_refs(body, patterns):
    offset = 0
    refs = []
    while True:
        try:
            length = int(body[offset : offset + 4], 16)
        except ValueError:
            raise HTTPException(422, "Invalid Git command packet") from None
        if length == 0:
            break
        if length < 4 or offset + length > len(body):
            raise HTTPException(422, "Incomplete Git command packet")
        packet = body[offset + 4 : offset + length]
        command = packet.split(b"\x00", 1)[0].rstrip(b"\n")
        match = re.fullmatch(
            rb"([0-9a-f]{40}) ([0-9a-f]{40}) (refs/heads/[^\s\x00]+)", command
        )
        if not match:
            raise HTTPException(403, "Only branch updates are permitted")
        try:
            ref = match[3].decode("utf-8")
        except UnicodeError:
            raise HTTPException(422, "Invalid branch name") from None
        if match[2] == b"0" * 40 or not any(
            fnmatch.fnmatchcase(ref, pattern) for pattern in patterns
        ):
            raise HTTPException(
                403, "Branch deletion or an unselected branch is forbidden"
            )
        refs.append(ref)
        offset += length
    if not refs:
        raise HTTPException(422, "No Git branch commands supplied")
    return refs


def router(connections):
    routes = APIRouter()

    @routes.api_route(
        "/azure-devops/git/{identifier}/{project}/{repository}/{path:path}",
        methods=["GET", "POST"],
    )
    async def broker(
        identifier: str, project: str, repository: str, path: str, request: Request
    ):
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("basic "):
            try:
                password = (
                    base64.b64decode(authorization[6:], validate=True)
                    .decode()
                    .split(":", 1)[1]
                )
                authorization = "Bearer " + password
            except (ValueError, UnicodeError, IndexError):
                raise HTTPException(
                    401,
                    "Invalid Git capability",
                    headers={"WWW-Authenticate": 'Basic realm="Azure DevOps Git"'},
                ) from None
        try:
            app_id = connections.app_for_token(authorization)
        except HTTPException as error:
            if error.status_code == 401:
                error.headers = {
                    **(error.headers or {}),
                    "WWW-Authenticate": 'Basic realm="Azure DevOps Git"',
                }
            raise
        service = request.query_params.get("service")
        if (
            path == "info/refs"
            and request.method == "GET"
            and service in {"git-upload-pack", "git-receive-pack"}
        ):
            write = service == "git-receive-pack"
        elif (
            path in {"git-upload-pack", "git-receive-pack"}
            and request.method == "POST"
            and not request.query_params
        ):
            write = path == "git-receive-pack"
        else:
            raise HTTPException(404, "Unsupported Git endpoint")
        if path == "info/refs" and set(request.query_params) != {"service"}:
            raise HTTPException(422, "Unsupported Git query")
        connection = connections.authorize(
            identifier, app_id, project, repository, "git_push" if write else None
        )
        body = await request.body()
        if request.headers.get("content-encoding"):
            raise HTTPException(422, "Compressed Git requests are not supported")
        if len(body) > 128 * 1024 * 1024:
            raise HTTPException(413, "Git request exceeds 128 MiB")
        if write and request.method == "POST":
            receive_refs(body, connection["push_branches"])
            if request.headers.get("content-encoding"):
                raise HTTPException(422, "Compressed Git commands are not supported")
        client = connections.client(connection)
        headers = {"Authorization": await client.authorization()}
        for key in ("content-type", "git-protocol", "accept"):
            if request.headers.get(key):
                headers[key] = request.headers[key]
        connections.authorize(
            identifier, app_id, project, repository, "git_push" if write else None
        )
        url = f"https://dev.azure.com/{segment(connection['organization'])}/{segment(project)}/_git/{segment(repository)}/{path}"
        try:
            response = await client.http.request(
                request.method,
                url,
                params={"service": service} if service else None,
                headers=headers,
                content=body,
                timeout=120,
            )
        except httpx.HTTPError:
            raise HTTPException(502, "Azure DevOps Git transport failed") from None
        if not 200 <= response.status_code < 300:
            raise HTTPException(502, "Azure DevOps rejected the Git request")
        return Response(
            response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers={"Cache-Control": "no-store"},
        )

    return routes
