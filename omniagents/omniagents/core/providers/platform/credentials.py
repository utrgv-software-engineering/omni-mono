"""Credential vending — fetches scoped credentials from omni-platform.

When an agent needs to access a governed resource (e.g., Databricks SQL
via an MCP server), this module fetches a scoped, time-limited credential
from the management plane rather than using a static token.

The management plane verifies:
1. The session is still active
2. The user is still entitled to the resource
3. The resource is active/published

Then returns a credential payload with scope constraints.

Usage::

    cred_provider = PlatformCredentialProvider(platform_url, runtime_token, session_id)
    cred = await cred_provider.get_credential(resource_id)
    # cred = {"config": {...}, "scope": {...}, "expires_at": "..."}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("omniagents.credentials.platform")


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

    async def get_credential(self, resource_id: str | int) -> Dict[str, Any]:
        """Fetch a scoped credential for a governed resource.

        Returns cached credential if still valid, otherwise fetches fresh.
        """
        rid = str(resource_id)

        # Return cached if not expired
        if rid in self._cache:
            if time.time() < self._cache_expiry.get(rid, 0):
                return self._cache[rid]

        import requests

        url = (
            f"{self._platform_url}/api/v1/sessions/"
            f"{self._session_id}/credentials/{rid}"
        )

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                headers={"Authorization": f"Bearer {self._runtime_token}"},
                timeout=10,
            )

            if response.status_code == 401:
                logger.warning("Credential fetch returned 401 — token may be expired")
                return {"error": "session_expired"}

            if response.status_code == 403:
                logger.warning("Credential denied for resource %s", rid)
                return response.json()

            if response.ok:
                data = response.json()
                # Cache with margin before actual expiry
                expires_at = data.get("expires_at")
                if expires_at:
                    from datetime import datetime, timezone

                    exp = datetime.fromisoformat(expires_at).timestamp()
                    self._cache_expiry[rid] = exp - self._cache_margin
                self._cache[rid] = data
                return data
            else:
                logger.warning(
                    "Credential fetch returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                return {"error": f"http_{response.status_code}"}
        except Exception as exc:
            logger.warning("Credential fetch failed: %s", exc)
            return {"error": str(exc)}

    def invalidate(self, resource_id: str | int) -> None:
        """Remove a cached credential (e.g., after a revocation signal)."""
        rid = str(resource_id)
        self._cache.pop(rid, None)
        self._cache_expiry.pop(rid, None)

    def invalidate_all(self) -> None:
        """Clear all cached credentials."""
        self._cache.clear()
        self._cache_expiry.clear()
