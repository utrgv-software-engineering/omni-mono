"""Fake MCP resource server + OAuth authorization server (one ASGI app).

Served through ``httpx.ASGITransport`` so the real
``mcp.client.auth.OAuthClientProvider`` can exercise RFC 9728 discovery,
RFC 8414 metadata, dynamic client registration, PKCE authorization, code
exchange, and refresh — without any network. Shared by the MCP OAuth
unit tests and the /ws MCP management integration tests.
"""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

BASE = "http://localhost:9600"
SERVER_URL = f"{BASE}/mcp"
ACCESS_SECRET = "ACCESS-TOKEN-SECRET-111"
REFRESH_SECRET = "REFRESH-TOKEN-SECRET-222"
AUTH_CODE = "AUTH-CODE-333"


def make_fake_oauth_app(state: dict, base: str = BASE) -> FastAPI:
    app = FastAPI()
    server_url = f"{base}/mcp"

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {ACCESS_SECRET}":
            return JSONResponse({"jsonrpc": "2.0", "id": 0, "result": {}})
        state.setdefault("rejected", []).append(auth)
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    "Bearer resource_metadata="
                    f'"{base}/.well-known/oauth-protected-resource"'
                )
            },
        )

    @app.get("/.well-known/oauth-protected-resource")
    async def protected_resource():
        return {"resource": server_url, "authorization_servers": [base]}

    @app.get("/.well-known/oauth-authorization-server")
    async def as_metadata():
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
        }

    @app.post("/register")
    async def register(request: Request):
        body = await request.json()
        state["registered"] = body
        return JSONResponse(
            {
                "client_id": "test-client-id",
                "redirect_uris": body["redirect_uris"],
                "grant_types": body.get("grant_types", []),
                "response_types": body.get("response_types", []),
                "token_endpoint_auth_method": body.get(
                    "token_endpoint_auth_method", "none"
                ),
                "client_name": body.get("client_name", ""),
            },
            status_code=201,
        )

    @app.post("/token")
    async def token(request: Request):
        form = parse_qs((await request.body()).decode())
        grant_type = (form.get("grant_type") or [""])[0]
        state.setdefault("token_grants", []).append(grant_type)
        if grant_type == "authorization_code":
            if (form.get("code") or [""])[0] != AUTH_CODE:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not (form.get("code_verifier") or [""])[0]:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            return {
                "access_token": ACCESS_SECRET,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": REFRESH_SECRET,
            }
        if grant_type == "refresh_token":
            if state.get("refresh_ok"):
                return {
                    "access_token": ACCESS_SECRET,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": REFRESH_SECRET,
                }
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    return app
