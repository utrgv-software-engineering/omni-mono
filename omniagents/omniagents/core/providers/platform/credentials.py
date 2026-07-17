"""Credential vending — fetches scoped credentials from omni-platform.

When an agent needs to access a governed resource (a model provider, a
Databricks SQL warehouse via MCP, ...), this module fetches a scoped,
time-limited credential from the management plane rather than using a
static token.

The management plane verifies:
1. The session is still active
2. The user is still entitled to the resource
3. The resource is active/published

Then returns a credential payload with scope constraints.

The core is synchronous (the one production caller today resolves model
config synchronously at CLI startup); ``get_credential`` /
``get_credential_by_ref`` are thin async wrappers for async callers.
All fetchers return the credential dict on success and ``None`` on any
failure (details are logged) — callers never need to check error shapes.

Usage::

    provider = PlatformCredentialProvider(platform_url, runtime_token, session_id)
    cred = provider.get_credential_by_ref_sync(
        "/api/v1/sessions/{session_id}/credentials/42"
    )
    # cred = {"config": {...}, "scope": {...}, "expires_at": "..."}

For local (non-container) processes that have no platform session yet,
``ensure_platform_session`` bootstraps one — see its docstring.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("omniagents.credentials.platform")

# Fallback cache TTL when a credential carries no parseable expires_at.
_DEFAULT_TTL_SECONDS = 600


class PlatformCredentialProvider:
    """Fetches and caches scoped credentials from the management plane."""

    def __init__(
        self,
        platform_url: str,
        runtime_token: str = "",
        session_id: str = "",
        cache_margin_seconds: int = 60,
    ) -> None:
        self._platform_url = platform_url.rstrip("/")
        self._runtime_token = runtime_token or os.getenv("OMNI_RUNTIME_TOKEN", "")
        self._session_id = session_id or os.getenv("OMNI_SESSION_ID", "")
        self._cache_margin = cache_margin_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry: Dict[str, float] = {}

    # ---- Sync core ----

    def get_credential_sync(self, resource_id: str | int) -> Optional[Dict[str, Any]]:
        """Fetch a scoped credential for a governed resource by id."""
        rid = str(resource_id)
        path = f"/api/v1/sessions/{self._session_id}/credentials/{rid}"
        return self._fetch(cache_key=rid, path=path)

    def get_credential_by_ref_sync(
        self, credential_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a credential by policy reference path.

        *credential_ref* is the relative URL path carried in the platform
        policy document (e.g. ``/api/v1/sessions/{session_id}/credentials/42``);
        a ``{session_id}`` placeholder is resolved against this provider's
        session.
        """
        path = credential_ref.replace("{session_id}", self._session_id)
        return self._fetch(cache_key=credential_ref, path=path)

    def _fetch(self, *, cache_key: str, path: str) -> Optional[Dict[str, Any]]:
        cached = self._cache.get(cache_key)
        if cached is not None and time.time() < self._cache_expiry.get(cache_key, 0):
            return cached

        import requests

        url = f"{self._platform_url}{path}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._runtime_token}"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Credential fetch failed for %s: %s", cache_key, exc)
            return None

        if response.status_code == 401:
            logger.warning(
                "Credential fetch returned 401 for %s — token may be expired",
                cache_key,
            )
            return None
        if response.status_code == 403:
            logger.warning("Not entitled to credential: %s", cache_key)
            return None
        if not response.ok:
            logger.warning(
                "Credential fetch returned %d for %s: %s",
                response.status_code,
                cache_key,
                response.text[:200],
            )
            return None

        try:
            data = response.json()
        except Exception as exc:
            logger.warning("Invalid credential response for %s: %s", cache_key, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("Invalid credential response for %s: not an object", cache_key)
            return None

        # Cache with a margin before actual expiry so callers never hold a
        # credential that dies mid-use.
        expires_at = data.get("expires_at")
        exp: float = time.time() + _DEFAULT_TTL_SECONDS
        if expires_at:
            try:
                exp = datetime.fromisoformat(
                    str(expires_at).replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                pass
        self._cache[cache_key] = data
        self._cache_expiry[cache_key] = exp - self._cache_margin
        return data

    # ---- Async wrappers ----

    async def get_credential(self, resource_id: str | int) -> Optional[Dict[str, Any]]:
        """Async wrapper over :meth:`get_credential_sync`."""
        return await asyncio.to_thread(self.get_credential_sync, resource_id)

    async def get_credential_by_ref(
        self, credential_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Async wrapper over :meth:`get_credential_by_ref_sync`."""
        return await asyncio.to_thread(self.get_credential_by_ref_sync, credential_ref)

    # ---- Cache control ----

    def invalidate(self, resource_id: str | int) -> None:
        """Remove a cached credential (e.g., after a revocation signal)."""
        rid = str(resource_id)
        self._cache.pop(rid, None)
        self._cache_expiry.pop(rid, None)

    def invalidate_all(self) -> None:
        """Clear all cached credentials."""
        self._cache.clear()
        self._cache_expiry.clear()


def ensure_platform_session(
    platform_url: str,
    access_token: str,
    *,
    agent: str,
    export_env: bool = True,
) -> Optional[Dict[str, str]]:
    """Create a lightweight platform session for local credential vending.

    In container mode, the launcher calls compute/start which creates a
    session and injects ``OMNI_SESSION_ID`` / ``OMNI_RUNTIME_TOKEN``. In
    local mode there is no launcher, so processes call
    ``POST /api/v1/sessions/bootstrap`` directly to get a session_id and
    session token without provisioning any compute.

    Args:
        platform_url: management-plane base URL.
        access_token: the user's access token (from product sign-in).
        agent: the product/agent slug the session is for.
        export_env: when True (default, matching the launcher contract),
            export ``OMNI_SESSION_ID`` / ``OMNI_RUNTIME_TOKEN`` so
            downstream platform consumers in this process pick them up.

    Returns ``{"session_id": ..., "session_token": ...}`` or ``None``.
    Stateless — callers cache the result for the process lifetime.
    """
    import requests

    try:
        resp = requests.post(
            f"{platform_url.rstrip('/')}/api/v1/sessions/bootstrap",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"agent": agent},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Session bootstrap error: %s", exc)
        return None

    if not resp.ok:
        logger.warning(
            "Session bootstrap failed (HTTP %d): %s", resp.status_code, resp.text[:200]
        )
        return None

    try:
        data = resp.json()
        session = {
            "session_id": data["session_id"],
            "session_token": data["session_token"],
        }
    except Exception as exc:
        logger.warning("Invalid session bootstrap response: %s", exc)
        return None

    if export_env:
        # The session token has the right audience/claims for the runtime API.
        os.environ["OMNI_SESSION_ID"] = session["session_id"]
        os.environ["OMNI_RUNTIME_TOKEN"] = session["session_token"]
    logger.info("Local platform session created: %s", session["session_id"])
    return session
