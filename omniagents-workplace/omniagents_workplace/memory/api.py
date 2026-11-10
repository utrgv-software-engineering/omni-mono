"""Signed-in people's memory API; all operations use the same store ACLs as MCP."""

from fastapi import APIRouter, HTTPException, Query, Request
import jsonschema

from ..accounts import me, admin
from .state import MemoryError, AdminPrincipal
from .mcp import SPECS


def memory_router(workplace, conversations=None):
    r = APIRouter(prefix="/workplace/api/memory")
    state = workplace.memory

    def principal(request):
        account = me(request)
        state.provision(account.id, "personal")
        return AdminPrincipal(account.id) if account.admin else account.id

    def attempt(fn):
        try:
            return fn()
        except MemoryError as exc:
            raise HTTPException(exc.status, exc.message) from exc

    def agent_store(request):
        if request.query_params.get("scope") != "agent":
            return None
        admin(request)
        if workplace.factory_config is None:
            raise HTTPException(404, "Factory agent is not configured")
        config = workplace.factory_config.load()
        app = workplace.apps.by_username(str(config.get("app", {}).get("username", "")))
        if app is None:
            raise HTTPException(404, "Factory agent is not configured")
        chat_id = request.query_params.get("chat_id")
        chat = (
            next((c for c in conversations.chats() if c["id"] == chat_id), None)
            if conversations
            else None
        )
        if chat is None or not chat.get("agent"):
            raise HTTPException(404, "Chat agent is not configured")
        store = f"agent:{app.id}/{chat['agent']}"
        attempt(lambda: state._access(app.id, store))
        return store

    @r.get("/stores")
    def stores(request: Request):
        who = principal(request)
        scoped = agent_store(request)
        return [s for s in state.stores(who) if scoped is None or s["id"] == scoped]

    @r.get("/page")
    def page(
        request: Request,
        store: str,
        query: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(10, ge=1, le=50),
    ):
        who = principal(request)
        scoped = agent_store(request)
        if scoped is not None and store != scoped:
            raise HTTPException(403, "Memory belongs to another agent")
        return attempt(lambda: state.page(who, store, query, offset, limit))

    @r.post("/grants")
    async def grants(request: Request):
        admin(request)
        args = await request.json()
        if set(args) != {"principal", "store", "access"} or not all(
            isinstance(v, str) for v in args.values()
        ):
            raise HTTPException(422, "Give principal, store and access")
        attempt(lambda: state.grant(**args))
        return {"ok": True}

    @r.post("/{operation}")
    async def call(operation: str, request: Request):
        who = principal(request)
        if operation not in SPECS:
            raise HTTPException(404, "Unknown operation")
        args = await request.json()
        properties, required = SPECS[operation]
        try:
            jsonschema.validate(
                args,
                {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            )
        except jsonschema.ValidationError as exc:
            raise HTTPException(422, exc.message) from exc
        scoped = agent_store(request)
        if scoped:
            if operation in {"search", "core"}:
                args["stores"] = [scoped]
            if operation == "remember" and args["store"] != scoped:
                raise HTTPException(403, "Memory belongs to another agent")
            ids = args.get("old_ids", []) + ([args["id"]] if "id" in args else [])
            for memory_id in ids:
                if attempt(lambda: state.get(who, memory_id))["store"] != scoped:
                    raise HTTPException(403, "Memory belongs to another agent")
        return attempt(lambda: getattr(state, operation)(who, **args))

    return r
