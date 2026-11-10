"""Factory workspace desktop access, restricted to the environment owner."""

from fastapi import Request, WebSocket
from starlette.responses import JSONResponse

from omniagents.core.sandbox.desktop import SandboxDesktop, relay_vnc
from omniagents.rpc.auth import AuthenticationFailed, principal_can_access


def mount_desktop(app, workspace, principal):
    async def authorized(connection):
        authenticator = getattr(app.state, "connection_authenticator", None)
        if authenticator is None:
            return False
        try:
            # Both HTTP and WebSocket requests carry the upstream bearer header.
            caller = await authenticator.authenticate_http(connection)
        except AuthenticationFailed:
            return False
        if not authenticator.enforces_authorization:
            return True
        owner = await principal()
        return principal_can_access(caller, owner.user_id if owner else None)

    def desktop():
        sandbox = workspace()
        return SandboxDesktop(sandbox.session if sandbox else None)

    @app.get("/integrations/desktop")
    async def status(request: Request):
        if not await authorized(request):
            return JSONResponse({"detail": "Not authorized"}, status_code=403)
        try:
            return {"available": bool(desktop().vnc_port)}
        except RuntimeError:
            return {"available": False}

    @app.websocket("/integrations/desktop/ws")
    async def connect(websocket: WebSocket):
        if not await authorized(websocket):
            await websocket.close(code=1008)
            return
        try:
            port = desktop().vnc_port
            if port is None:
                raise RuntimeError("Desktop not available")
        except RuntimeError:
            await websocket.close(code=1013)
            return
        try:
            await relay_vnc(websocket, port)
        finally:
            try:
                await websocket.close()
            except RuntimeError:
                pass
