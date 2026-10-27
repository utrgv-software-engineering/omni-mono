"""Signed-in people's memory API; all operations use the same store ACLs as MCP."""

from fastapi import APIRouter, HTTPException, Request
import jsonschema

from ..accounts import me, admin
from .state import MemoryError, AdminPrincipal
from .mcp import SPECS


def memory_router(workplace):
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

    @r.get("/stores")
    def stores(request: Request):
        return state.stores(principal(request))

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
        return attempt(lambda: getattr(state, operation)(who, **args))

    return r
