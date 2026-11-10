"""Human-operated MCP App discovery and bridge, independent of agent sessions."""

import json
import jsonschema
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..accounts import admin, me
from .registry import AppRegistry, BUILTINS, entrypoints
from .sandbox import app_origin, isolated_origin
from . import builtin


class Install(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str
    token: str = ""


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    permissions: dict[str, bool] = {}


NATIVE_DESTINATIONS = {
    "personal",
    "factory",
    "shared",
    "library",
    "desktop",
    "coolify",
    "memory",
}


class NavigationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    show_in_navigation: bool


class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict = {}


class Open(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str


def result(value):
    return {"content": [], "structuredContent": value}


class WorkplaceApps:
    def __init__(self, workplace, static_dir):
        self.workplace = workplace
        self.registry = AppRegistry(workplace)
        from .recordings import Recordings

        self.recordings = Recordings(workplace)
        self.static_dir = Path(static_dir)
        self.server = Server(
            "workplace-apps",
            version="1.0",
            on_list_tools=self.listed,
            on_call_tool=self.called,
            on_read_resource=self.read,
            on_list_resources=self.resources,
        )
        self.manager = StreamableHTTPSessionManager(
            self.server, json_response=True, stateless=True
        )

    def run(self):
        return self.manager.run()

    async def __call__(self, scope, receive, send):
        # Mounted under /workplace/api: normal session/CSRF/factory checks apply.
        return await self.manager.handle_request(scope, receive, send)

    async def listed(self, ctx, params):
        me(ctx.request)
        return types.ListToolsResult(
            tools=[
                types.Tool.model_validate(t) for t in builtin.definitions(self.registry)
            ]
        )

    async def resources(self, ctx, params):
        me(ctx.request)
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri="ui://workplace/" + key,
                    name=value["name"],
                    mime_type=builtin.MIME,
                )
                for key, value in BUILTINS.items()
                if self.registry.get(key, enabled=False)["enabled"]
            ]
        )

    async def called(self, ctx, params):
        value = await self.call(params.name, params.arguments or {}, ctx.request)
        return types.CallToolResult.model_validate(value)

    async def read(self, ctx, params):
        me(ctx.request)
        return types.ReadResourceResult.model_validate(self.resource(str(params.uri)))

    def resource(self, uri):
        identity = uri.removeprefix("ui://workplace/")
        if identity not in BUILTINS or uri != "ui://workplace/" + identity:
            raise HTTPException(404, "Unknown app resource")
        self.registry.get(identity)
        path = self.static_dir / "extension.html"
        if not path.exists():
            raise HTTPException(503, "Build the Workplace app resources first")
        html = path.read_text()
        # Serve immutable bundles from the app's own credential-free origin.
        # The namespace also avoids legacy cached responses from before apps.
        base = isolated_origin(
            self.workplace.base_url,
            identity,
            self.workplace.environ.get("WORKPLACE_APP_ORIGIN", ""),
        )
        html = re.sub(
            r'(src|href)="(/assets/[^\"]+)"',
            lambda m: f'{m[1]}="{base}/assets/mcp-apps-v1/{m[2][8:]}"',
            html,
        )
        permissions = {"microphone": {}} if identity == "recorder" else {}
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": builtin.MIME,
                    "text": html,
                    "_meta": {
                        "ui": {
                            "csp": {
                                "resourceDomains": [
                                    f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
                                ]
                            },
                            "permissions": permissions,
                        }
                    },
                }
            ]
        }

    async def call(self, name, arguments, request):
        account = me(request)
        definition = next(
            (
                tool
                for tool in builtin.definitions(self.registry)
                if tool["name"] == name
            ),
            None,
        )
        if definition is None:
            raise HTTPException(404, "Unknown app tool")
        try:
            jsonschema.validate(arguments, definition["inputSchema"])
        except jsonschema.ValidationError:
            raise HTTPException(422, "Invalid app tool arguments") from None
        identity, _, operation = name.partition(".")
        if identity not in BUILTINS:
            raise HTTPException(404, "Unknown app")
        self.registry.get(identity)
        if operation == "open":
            if arguments:
                raise HTTPException(422, "App entrypoints accept empty arguments")
            from ..server import person_json

            value = {
                "app": identity,
                "account": person_json(account),
                "factory": getattr(request.state, "factory_prefix", ""),
                "origin": f"{urlsplit(self.workplace.base_url).scheme}://{urlsplit(self.workplace.base_url).netloc}",
                "path": BUILTINS[identity]["path"],
            }
            if identity == "github":
                settings = self.workplace.repository_settings.settings()
                value["selectedRepositories"] = settings.get("repositories", [])
            return result(value)
        if identity == "recorder" and operation == "record":
            import asyncio

            return result(
                await asyncio.to_thread(self.recordings.call, arguments, account)
            )
        if operation == "request":
            return result(
                await builtin.request(self.registry, identity, arguments, request)
            )
        raise HTTPException(404, "Unknown app operation")

    def router(self):
        router = APIRouter(prefix="/workplace/api/extensions")

        def accessible(identity, request, *, enabled=True):
            account = me(request)
            value = self.registry.get(identity, enabled=enabled)
            if (
                value["kind"] == "remote"
                and value.get("owner", account.id) != account.id
            ):
                raise HTTPException(
                    403, "This app connection belongs to another account"
                )
            return value

        @router.get("")
        def listing(request: Request):
            account = me(request)
            definitions = builtin.definitions(self.registry)
            preferences = dict(self.registry.store.docs("extension.navigation"))
            items = []
            for value in self.registry.installed().values():
                if (
                    value["kind"] == "remote"
                    and value.get("owner", account.id) != account.id
                ):
                    continue
                public = self.registry.public(value)
                public["showInNavigation"] = preferences.get(
                    account.id + ":" + value["id"], {}
                ).get("show_in_navigation", True)
                public["sandboxOrigin"] = isolated_origin(
                    self.workplace.base_url,
                    value["id"],
                    self.workplace.environ.get("WORKPLACE_APP_ORIGIN", ""),
                )
                installation = getattr(self.workplace.accounts, "installation", None)
                if installation is not None:
                    host = urlsplit(public["sandboxOrigin"]).hostname
                    installation.store.put("extension.origin", host, {"host": host})
                if value["kind"] == "builtin":
                    public["entrypoints"] = [
                        e
                        for e in entrypoints(definitions)
                        if e["tool"] == value["id"] + ".open"
                    ]
                items.append(public)
            return {
                "apps": items,
                "nativeNavigation": {
                    name: preferences.get(account.id + ":native:" + name, {}).get(
                        "show_in_navigation", True
                    )
                    for name in NATIVE_DESTINATIONS
                },
                "sandboxOrigin": app_origin(
                    self.workplace.base_url,
                    self.workplace.environ.get("WORKPLACE_APP_ORIGIN", ""),
                ),
            }

        @router.put("/{identity}/navigation")
        def navigation(identity: str, body: NavigationPreference, request: Request):
            me(request)
            if not (
                identity.startswith("native:") and identity[7:] in NATIVE_DESTINATIONS
            ):
                accessible(identity, request, enabled=False)
            self.registry.store.put(
                "extension.navigation",
                me(request).id + ":" + identity,
                body.model_dump(),
            )
            return {"showInNavigation": body.show_in_navigation}

        @router.post("")
        async def install(body: Install, request: Request):
            admin(request)
            return await self.registry.install(
                body.name, body.url, body.token, me(request).id
            )

        @router.put("/{identity}")
        def update(identity: str, body: Update, request: Request):
            admin(request)
            if set(body.permissions) - {
                "microphone",
                "camera",
                "clipboardWrite",
                "geolocation",
            }:
                raise HTTPException(422, "Unknown browser permission")
            return self.registry.update(
                identity, enabled=body.enabled, permissions=body.permissions
            )

        @router.delete("/{identity}")
        def uninstall(identity: str, request: Request):
            admin(request)
            value = self.registry.get(identity, enabled=False)
            if value["kind"] == "builtin":
                return self.registry.update(identity, enabled=False, permissions={})
            self.registry.store.delete("extension.installation", identity)
            return {"ok": True}

        @router.post("/{identity}/open")
        async def opened(identity: str, body: Open, request: Request):
            me(request)
            value = accessible(identity, request)
            definitions = (
                builtin.definitions(self.registry)
                if value["kind"] == "builtin"
                else value["tools"]
            )
            entries = entrypoints(definitions)
            entry = next((e for e in entries if e["tool"] == body.tool), None)
            if (
                entry is None
                or value["kind"] == "builtin"
                and body.tool != identity + ".open"
            ):
                raise HTTPException(403, "Unknown app entrypoint")
            if value["kind"] == "builtin":
                output = await self.call(body.tool, {}, request)
                resource = self.resource(entry["uri"])
            else:
                output = await self.registry.call(identity, body.tool, {})
                resource = await self.registry.resource(identity, entry["uri"])
            return {
                "entrypoint": entry,
                "result": output,
                "resource": resource,
                "permissions": value.get("permissions", {}),
            }

        @router.post("/{identity}/tools/call")
        async def call(identity: str, body: Call, request: Request):
            me(request)
            value = accessible(identity, request)
            if value["kind"] == "builtin":
                if body.name not in {
                    identity + ".request",
                    *(["recorder.record"] if identity == "recorder" else []),
                }:
                    raise HTTPException(403, "Tool is outside this app's permissions")
                return await self.call(body.name, body.arguments, request)
            return await self.registry.call(identity, body.name, body.arguments)

        @router.get("/{identity}/resources/read")
        async def read(identity: str, uri: str, request: Request):
            me(request)
            value = accessible(identity, request)
            if value["kind"] == "builtin":
                if uri != "ui://workplace/" + identity:
                    raise HTTPException(403, "Resource is outside this app")
                return self.resource(uri)
            return await self.registry.resource(identity, uri)

        return router
