"""Factory-local MCP app installations. Credentials never enter discovery responses."""

from __future__ import annotations

import asyncio
import ipaddress
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from fastapi import HTTPException
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

BUILTINS = {
    "slack": {"name": "Slack", "path": "/", "icon": "slack"},
    "github": {"name": "GitHub", "path": "/github", "icon": "github"},
    "linear": {"name": "Linear", "path": "/linear", "icon": "linear"},
    "recorder": {"name": "Recorder", "path": "/apps/recorder", "icon": "recorder"},
}


def entrypoints(tools):
    """Keep standard UI metadata and OpenAI entrypoint metadata separate."""
    result = []
    for tool in tools:
        meta = tool.get("_meta") or {}
        ui = meta.get("ui") or {}
        uri = ui.get("resourceUri")
        entries = (meta.get("openai/ui") or {}).get("entrypoints") or []
        if not isinstance(uri, str) or not uri.startswith("ui://"):
            continue
        if not any(isinstance(e, dict) and e.get("type") == "global" for e in entries):
            continue
        result.append(
            {
                "tool": tool["name"],
                "name": tool.get("title")
                or (tool.get("annotations") or {}).get("title")
                or tool["name"],
                "uri": uri,
                "icons": tool.get("icons", []),
            }
        )
    return result


def validate_endpoint(value):
    url = urlsplit(value)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.fragment
    ):
        raise HTTPException(
            422, "Use an HTTP(S) MCP endpoint without embedded credentials"
        )
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        address = None
    if address and (
        address.is_link_local or address.is_multicast or address.is_unspecified
    ):
        raise HTTPException(422, "This network address cannot host an app connection")
    if url.scheme != "https" and not (
        url.hostname == "localhost" or address and address.is_loopback
    ):
        raise HTTPException(422, "Remote app servers require HTTPS")
    return value


class AppRegistry:
    def __init__(self, workplace):
        self.workplace = workplace
        self.store = workplace.store

    def installed(self):
        saved = dict(self.store.docs("extension.installation"))
        defaults = {
            key: {
                "id": key,
                "kind": "builtin",
                "enabled": True,
                "permissions": {"microphone": key == "recorder"},
                **value,
            }
            for key, value in BUILTINS.items()
        }
        return {**defaults, **saved}

    def get(self, identity, *, enabled=True):
        value = self.installed().get(identity)
        if value is None or enabled and not value.get("enabled"):
            raise HTTPException(404, "App is not installed or is disabled")
        return value

    def public(self, value):
        return {k: v for k, v in value.items() if k not in {"token", "tools"}}

    def update(self, identity, *, enabled, permissions):
        value = self.get(identity, enabled=False)
        value = {**value, "enabled": enabled, "permissions": permissions}
        self.store.put("extension.installation", identity, value)
        return self.public(value)

    async def install(self, name, url, token, owner):
        value = {
            "id": uuid.uuid4().hex,
            "kind": "remote",
            "owner": owner,
            "name": name,
            "url": validate_endpoint(url),
            "token": token,
            "enabled": True,
            "permissions": {},
        }
        tools = await self.discover(value)
        entries = entrypoints(tools)
        if not entries:
            raise HTTPException(
                422, "This server does not expose any global MCP App entrypoints"
            )
        value.update(tools=tools, entrypoints=entries)
        self.store.put("extension.installation", value["id"], value)
        return self.public(value)

    @asynccontextmanager
    async def session(self, value):
        # Only admins configure these endpoints. Credentials are specific to the
        # installed MCP server; Workplace cookies/credentials are never forwarded.
        headers = (
            {"Authorization": "Bearer " + value["token"]} if value.get("token") else {}
        )
        try:
            async with asyncio.timeout(45):
                async with httpx2.AsyncClient(
                    headers=headers, timeout=30, follow_redirects=False, trust_env=False
                ) as http:
                    async with streamable_http_client(
                        value["url"],
                        http_client=http,
                        max_sse_event_size=64 * 1024 * 1024,
                    ) as (
                        read,
                        write,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
        except HTTPException:
            raise
        except TimeoutError as exc:
            raise HTTPException(504, "App server timed out") from exc
        except Exception as exc:
            raise HTTPException(
                502, "App server request failed; check the connection and credentials"
            ) from exc

    async def discover(self, value):
        tools = []
        async with self.session(value) as session:
            cursor = None
            seen = set()
            while True:
                page = await session.list_tools(
                    params=(
                        types.PaginatedRequestParams(cursor=cursor) if cursor else None
                    )
                )
                tools.extend(
                    tool.model_dump(by_alias=True, exclude_none=True)
                    for tool in page.tools
                )
                cursor = page.next_cursor
                if not cursor:
                    break
                if cursor in seen:
                    raise HTTPException(
                        502, "App server returned a repeated discovery cursor"
                    )
                seen.add(cursor)
        return tools

    async def call(self, identity, name, arguments):
        value = self.get(identity)
        tool = next((t for t in value.get("tools", []) if t["name"] == name), None)
        if tool is None:
            raise HTTPException(403, "Tool is not part of this app installation")
        visibility = ((tool.get("_meta") or {}).get("ui") or {}).get("visibility")
        is_entry = any(e["tool"] == name for e in value.get("entrypoints", []))
        if visibility is not None and "app" not in visibility and not is_entry:
            raise HTTPException(403, "Tool is not available to this app")
        async with self.session(value) as session:
            result = await session.call_tool(name, arguments)
        return result.model_dump(by_alias=True, exclude_none=True)

    async def resource(self, identity, uri):
        value = self.get(identity)
        if uri not in {e["uri"] for e in value.get("entrypoints", [])}:
            raise HTTPException(403, "Resource is not an installed app entrypoint")
        async with self.session(value) as session:
            result = await session.read_resource(uri)
        return result.model_dump(by_alias=True, exclude_none=True)
