"""Built-in app entrypoints reuse the authorized Workplace service APIs."""

import base64
import json
import re
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import HTTPException

from ..accounts import me
from .registry import BUILTINS

MIME = "text/html;profile=mcp-app"


def definitions(registry):
    result = []
    for identity, info in BUILTINS.items():
        if not registry.get(identity, enabled=False).get("enabled"):
            continue
        result.extend(
            [
                {
                    "name": identity + ".open",
                    "title": info["name"],
                    "description": "Open " + info["name"],
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "_meta": {
                        "ui": {
                            "resourceUri": "ui://workplace/" + identity,
                            "visibility": ["app"],
                        },
                        "openai/ui": {"entrypoints": [{"type": "global"}]},
                    },
                },
                {
                    "name": identity + ".request",
                    "description": "Access this app's Workplace service as the signed-in person.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "method": {
                                "type": "string",
                                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                            },
                            "body": {"type": "string"},
                            "encoding": {"type": "string", "enum": ["text", "base64"]},
                            "contentType": {"type": "string"},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    "_meta": {"ui": {"visibility": ["app"]}},
                },
            ]
        )
    if registry.get("recorder", enabled=False)["enabled"]:
        result.append(
            {
                "name": "recorder.record",
                "description": "Durably capture audio and publish a completed recording to Shared.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "enum": ["start", "write", "finish", "list", "discard"]
                        },
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "mime": {"type": "string"},
                        "data": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                "_meta": {"ui": {"visibility": ["app"]}},
            }
        )
    return result


def allowed_path(identity, value, method):
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not path.startswith("/")
        or "%" in path
        or "\\" in path
        or any(p in {".", ".."} for p in path.split("/"))
    ):
        return False
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return False
    if identity == "slack":
        if method == "GET" and path.startswith("/slack/files/"):
            return True
        return (
            path.startswith("/workplace/api/slack/")
            or method == "GET"
            and path == "/workplace/api/state"
        )
    if identity == "github":
        return path.startswith("/workplace/api/github/")
    if identity == "linear":
        return path.startswith("/workplace/api/linear/")
    if identity == "recorder":
        # Recorder owns a folder, not the entire shared filesystem. Additional
        # checks on request arguments and upload ownership live in request().
        return (
            path.startswith("/workplace/api/shared/uploads")
            or path == "/workplace/api/shared/workflows"
            or re.fullmatch(r"/workplace/api/shared/workflows/[a-f0-9]+", path)
            is not None
        )
    return False


async def request(registry, identity, arguments, original):
    registry.get(identity)
    account = me(original)
    path = arguments.get("path", "")
    method = arguments.get("method", "GET").upper()
    if not allowed_path(identity, path, method):
        raise HTTPException(403, "This API is outside the app's permissions")
    body = arguments.get("body", "")
    try:
        data = (
            base64.b64decode(body, validate=True)
            if arguments.get("encoding") == "base64"
            else body.encode()
        )
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid request body") from None
    if identity == "recorder":
        upload = re.match(
            r"/workplace/api/shared/uploads/([a-f0-9]{32})(?:/|$)", urlsplit(path).path
        )
        if upload:
            record = registry.workplace.shared.upload(
                upload[1], {"id": account.id, "name": account.name}
            )
            if any(not f["path"].startswith("Recordings/") for f in record["files"]):
                raise HTTPException(403, "Recorder can only access recording uploads")
        elif (
            method == "POST" and urlsplit(path).path == "/workplace/api/shared/uploads"
        ):
            payload = json.loads(data)
            files = payload.get("files") or []
            if (
                not files
                or any(not f.get("path", "").startswith("Recordings/") for f in files)
                or payload.get("replace")
                or payload.get("folders")
            ):
                raise HTTPException(
                    403, "Recordings must be new files in the Recordings folder"
                )
        if "/workflows" in path and method in {"POST", "PUT", "PATCH"}:
            payload = json.loads(data)
            from ..shared.files import directory

            with directory(registry.workplace.shared.root, "Recordings", create=True):
                pass
            if payload.get("folder") != "Recordings":
                raise HTTPException(
                    403, "Recorder workflows must use the Recordings folder"
                )
        if "/workflows/" in path:
            identity_rule = urlsplit(path).path.rsplit("/", 1)[-1]
            rule = next(
                (
                    r
                    for r in registry.workplace.shared.rules()
                    if r["id"] == identity_rule
                ),
                None,
            )
            if rule is None or rule["folder"] != "Recordings":
                raise HTTPException(403, "This workflow does not belong to Recorder")

    # Re-enter the existing API, including its user/admin/provider authorization.
    # Never accept headers, credentials, URLs or account identities from the app.
    async def scoped_app(scope, receive, send):
        scope["state"] = dict(original.scope.get("state", {}))
        await original.app(scope, receive, send)

    headers = {
        "cookie": original.headers.get("cookie", ""),
        "content-type": arguments.get("contentType", "application/json"),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scoped_app),
        base_url="http://workplace",
        follow_redirects=False,
    ) as client:
        response = await client.request(method, path, content=data, headers=headers)
    content = response.content
    if (
        response.status_code == 200
        and identity == "slack"
        and urlsplit(path).path == "/workplace/api/state"
    ):
        state = response.json()
        state["github"] = {"users": [], "repositories": []}
        content = json.dumps(state).encode()
    if (
        response.status_code == 200
        and identity == "recorder"
        and method == "GET"
        and urlsplit(path).path.endswith("/workflows")
    ):
        state = response.json()
        state["workflows"] = [
            r for r in state.get("workflows", []) if r["folder"] == "Recordings"
        ]
        content = json.dumps(state).encode()
    return {
        "status": response.status_code,
        "contentType": response.headers.get("content-type", "application/octet-stream"),
        "body": base64.b64encode(content).decode(),
        "encoding": "base64",
    }
