"""Factory-authenticated transport to the factory's native Coolify MCP."""

import asyncio
import secrets

import httpx
from fastapi import HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .coolify import compose


async def proxy(installation, factory_id, request):
    if factory_id not in installation.records:
        raise HTTPException(404, "No such factory")
    w, _ = await installation.mount(factory_id)
    configured = (w.factory_config.load().get("app") or {}).get("username")
    app = next((a for a in w.apps.active() if a.username == configured), None)
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not app or not token or not secrets.compare_digest(token, app.memory_token):
        raise HTTPException(403, "Factory agent access required")
    d = installation.deployments
    if not (installation.records[factory_id].get("deployment") or {}).get("enabled"):
        raise HTTPException(503, "Coolify is not enabled for this factory")
    for _ in range(60):
        if factory_id in d.runners:
            break
        await asyncio.sleep(1)
    if factory_id not in d.runners:
        raise HTTPException(503, "Coolify is starting")
    async with d.mcp_locks.setdefault(factory_id, asyncio.Lock()):
        if factory_id not in d.mcp_tokens:
            output = await compose(
                d.runners[factory_id],
                "exec",
                "-T",
                "coolify",
                "php",
                "artisan",
                "tinker",
                "--execute="
                "$s=App\\Models\\InstanceSettings::findOrFail(0);$s->is_api_enabled=true;$s->is_mcp_server_enabled=true;$s->save();"
                "$t=App\\Models\\Team::findOrFail(0);$t->is_mcp_server_enabled=true;$t->save();"
                "$u=App\\Models\\User::findOrFail(0);$u->tokens()->where('name','Workplace agent MCP')->delete();"
                "echo 'MCP_TOKEN:'.$u->createToken('Workplace agent MCP',['read','read:sensitive','deploy'],null,0)->plainTextToken;",
            )
            d.mcp_tokens[factory_id] = output.split("MCP_TOKEN:", 1)[1].strip()
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower()
        in {
            "content-type",
            "accept",
            "mcp-session-id",
            "mcp-protocol-version",
            "last-event-id",
        }
    }
    headers["authorization"] = "Bearer " + d.mcp_tokens[factory_id]
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, read=None), trust_env=False)
    try:
        response = await client.send(
            client.build_request(
                request.method,
                d.upstream(factory_id) + "/mcp",
                headers=headers,
                content=await request.body(),
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, "Coolify MCP is unavailable") from None

    async def close():
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers={
            k: v
            for k, v in response.headers.items()
            if k.lower() in {"content-type", "mcp-session-id", "cache-control"}
        },
        background=BackgroundTask(close),
    )
