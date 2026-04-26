"""API key authentication middleware.

In testing mode (APP_ENV=testing) authentication is bypassed so tests
can call endpoints without injecting an API key header.
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import API_KEY

_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if os.getenv("APP_ENV") == "testing":
            return await call_next(request)

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        key = request.headers.get("x-api-key")
        if key != API_KEY:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)

        return await call_next(request)
