"""The canvas MCP server, served by the workplace at ``/canvas/mcp``.

Its tools (``reference/canvas_mcp_tools.json``) are the design canvas's
operations as Claude's artifacts have them: read a canvas's index and files,
publish changes against the version they were made on (refused when stale),
the version history, and comment threads. Authorized with a token
(``Authorization: Bearer <token>``): an app's or a person's.
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
from importlib import resources
from typing import Any, Callable, Dict, List, Optional

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..wire import Wire
from .state import Canvas, CanvasComment, CanvasError, CanvasState, Member, is_text

__all__ = ["CanvasMCP", "load_tool_definitions"]


def load_tool_definitions() -> Dict[str, Any]:
    text = (
        resources.files("omniagents_workplace")
        .joinpath("reference/canvas_mcp_tools.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


class ToolError(Exception):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _iso(t: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


class CanvasMCP:
    def __init__(self, state: CanvasState, wire: Wire, base_url: Callable[[], str]):
        self.state = state
        self.wire = wire
        self.base_url = base_url
        reference = load_tool_definitions()
        self._definitions: List[Dict[str, Any]] = reference["tools"]
        self._schemas = {d["name"]: d["inputSchema"] for d in self._definitions}
        info = reference.get("serverInfo") or {}
        self.server = Server(
            info.get("name", "canvas"),
            version=info.get("version"),
            instructions=reference.get("instructions"),
        )
        self.server.list_tools()(self._list_tools)
        self.server.call_tool(validate_input=False)(self._call_tool)
        self.manager = StreamableHTTPSessionManager(
            self.server, json_response=False, stateless=True
        )

    @contextlib.asynccontextmanager
    async def run(self):
        async with self.manager.run():
            yield

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if self._caller(headers) is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer error="invalid_token"'),
                    ],
                }
            )
            await send(
                {"type": "http.response.body", "body": b'{"error":"invalid_token"}'}
            )
            return
        await self.manager.handle_request(scope, receive, send)

    def _caller(self, headers: Dict[str, str]) -> Optional[Member]:
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return self.state.member_for_token(token)

    def _me(self) -> Member:
        request = self.server.request_context.request
        member = (
            self._caller({k.lower(): v for k, v in request.headers.items()})
            if request
            else None
        )
        if member is None:
            raise ToolError("Error: Unauthorized")
        return member

    async def _list_tools(self) -> List[types.Tool]:
        self._me()
        return [types.Tool.model_validate(d) for d in self._definitions]

    async def _call_tool(self, name: str, arguments: Dict[str, Any]):
        me = self._me()
        started = time.monotonic()
        shown = dict(arguments or {})
        if isinstance(shown.get("files"), dict):
            shown["files"] = {
                p: (
                    None
                    if v is None
                    else f"<{len(v) if isinstance(v, str) else 'binary'} chars>"
                )
                for p, v in shown["files"].items()
            }
        entry = self.wire.record(
            service="canvas",
            direction="in",
            kind="mcp",
            title=name,
            actor=me.name,
            status="pending",
            request=shown,
        )
        try:
            schema = self._schemas.get(name)
            if schema is None:
                raise ToolError(f"MCP error -32602: Tool {name} not found")
            errors = sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(arguments or {}),
                key=lambda e: list(e.path),
            )
            if errors:
                raise ToolError(
                    "Input validation error: "
                    + "; ".join(
                        f"{'.'.join(str(p) for p in e.path) or 'arguments'}: {e.message}"
                        for e in errors
                    )
                )
            try:
                text = getattr(self, f"_{name}")(me, arguments or {})
            except CanvasError as exc:
                raise ToolError(f"Error: {exc.message}")
        except ToolError as exc:
            self.wire.update(
                entry,
                status="error",
                response=str(exc),
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], isError=True
            )
        self.wire.update(
            entry,
            status="ok",
            response=text[:2000],
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return [types.TextContent(type="text", text=text)]

    # -- JSON ------------------------------------------------------------------------

    def url(self, canvas: Canvas) -> str:
        return f"{self.base_url().rstrip('/')}/canvas/{canvas.id}"

    def canvas_json(self, canvas: Canvas) -> Dict[str, Any]:
        owner = self.state.members.get(canvas.owner_id)
        index = self.state.index(canvas)
        return {
            "id": canvas.id,
            "title": canvas.title,
            "url": self.url(canvas),
            "version": canvas.version,
            "owner": owner.name if owner else None,
            "updatedAt": _iso(canvas.updated_at),
            "boards": list(index.get("order") or []),
        }

    def _comment_json(self, c: CanvasComment) -> Dict[str, Any]:
        author = self.state.members.get(c.author_id)
        out: Dict[str, Any] = {
            "id": c.id,
            "body": c.body,
            "author": author.name if author else None,
            "createdAt": _iso(c.created_at),
            "version": c.version,
        }
        if c.board:
            out["board"] = c.board
            if c.x is not None:
                out["x"], out["y"] = c.x, c.y
        if c.parent_id is None:
            out["resolved"] = c.resolved_at is not None
        return out

    # -- tools -------------------------------------------------------------------------

    def _list_canvases(self, me, args):
        query = (args.get("query") or "").lower()
        items = sorted(
            (c for c in self.state.canvases.values() if query in c.title.lower()),
            key=lambda c: c.updated_at,
            reverse=True,
        )
        return _json({"canvases": [self.canvas_json(c) for c in items]})

    def _create_canvas(self, me, args):
        return _json(self.canvas_json(self.state.create(me.id, args["title"])))

    def _get_canvas(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        files = [
            {"path": p, "size": len(self.state.blobs[s])}
            for p, s in sorted(canvas.files.items())
        ]
        return _json(
            {
                **self.canvas_json(canvas),
                "index": self.state.index(canvas),
                "files": files,
            }
        )

    def _read_file(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        paths = list(args.get("paths") or []) + (
            [args["path"]] if args.get("path") else []
        )
        if not paths:
            raise ToolError("Error: Give path or paths")
        out = []
        for path in paths:
            f = self.state.read(canvas, path, args.get("version"))
            item: Dict[str, Any] = {"path": f.path, "version": f.version}
            if is_text(path):
                item["text"] = f.text
            else:
                item["base64"] = base64.b64encode(f.content).decode()
            out.append(item)
        return _json(
            out[0] if args.get("path") and not args.get("paths") else {"files": out}
        )

    def _publish(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        files: Dict[str, Optional[bytes]] = {}
        for path, value in args["files"].items():
            if value is None:
                files[path] = None
            elif isinstance(value, dict):
                try:
                    files[path] = base64.b64decode(value["base64"], validate=True)
                except Exception:
                    raise ToolError(f"Error: {path}: base64 is not valid")
            else:
                files[path] = value.encode()
        version = self.state.publish(
            canvas,
            files,
            base_version=int(args["base_version"]),
            author_id=me.id,
            label=args.get("label") or "",
        )
        return _json(
            {
                "id": canvas.id,
                "version": version.number,
                "changed": version.changed,
                "url": self.url(canvas),
            }
        )

    def _list_versions(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        out = []
        for v in self.state.history(canvas):
            author = self.state.members.get(v.author_id)
            out.append(
                {
                    "version": v.number,
                    "author": author.name if author else None,
                    "at": _iso(v.at),
                    "label": v.label,
                    "changed": v.changed,
                }
            )
        return _json({"versions": out, "current": canvas.version})

    def _restore_version(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        version = self.state.restore(canvas, int(args["version"]), me.id)
        return _json(
            {"id": canvas.id, "version": version.number, "changed": version.changed}
        )

    def _list_comments(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        comments = self.state.thread(canvas)
        roots = [
            c
            for c in comments
            if c.parent_id is None
            and (args.get("include_resolved") or not c.resolved_at)
        ]
        return _json(
            {
                "threads": [
                    {
                        **self._comment_json(r),
                        "replies": [
                            self._comment_json(c)
                            for c in comments
                            if c.parent_id == r.id
                        ],
                    }
                    for r in roots
                ]
            }
        )

    def _add_comment(self, me, args):
        canvas = self.state.canvas(args["canvas"])
        note = self.state.comment(
            canvas,
            me.id,
            args["body"],
            board=args.get("board"),
            x=args.get("x"),
            y=args.get("y"),
        )
        return _json(self._comment_json(note))

    def _comment(self, ref: str) -> CanvasComment:
        note = self.state.comments.get(ref)
        if note is None:
            raise ToolError(f'Error: No comment "{ref}"')
        return note

    def _reply_comment(self, me, args):
        parent = self._comment(args["comment"])
        canvas = self.state.canvases[parent.canvas_id]
        return _json(
            self._comment_json(
                self.state.comment(canvas, me.id, args["body"], parent_id=parent.id)
            )
        )

    def _resolve_comment(self, me, args):
        note = self.state.resolve(
            self._comment(args["comment"]), args.get("resolved", True)
        )
        return _json(self._comment_json(note))
