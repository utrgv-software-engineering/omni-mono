"""MCP server OAuth flows for the GUI protocol's ``mcp_auth_*`` RPCs.

Reuses the unified account-surface patterns (tkt_unKKDF-5OOgc): typed
flows that stop at the URL-handoff point, non-blocking completion, and a
hard rule that credential material never leaves the server — tokens and
PKCE verifiers live in a 0600 file store under the product config dir
and are never echoed by any RPC, log, or event.

The protocol legwork (RFC 9728 protected-resource discovery, RFC 8414
authorization-server metadata, RFC 7591 dynamic client registration,
PKCE, refresh) is delegated to :class:`mcp.client.auth.OAuthClientProvider`
from the official MCP SDK — this module supplies the two integration
personalities:

* **Interactive** (``start``/``complete``/``cancel``): the provider's
  ``redirect_handler`` captures the authorization URL and hands it to
  the caller (RPC result, plus optionally a ``url``-kind elicitation via
  tkt_F9tu2m9PjdpY's protocol); ``callback_handler`` awaits the
  authorization code delivered by ``mcp_auth_complete``.
* **Non-interactive** (session builds / probes): stored tokens are
  attached and silently refreshed, but a flow that would need the
  browser raises :class:`McpInteractiveAuthRequired` instead — the
  caller classifies it as ``auth_required`` / ``auth_expired`` and
  broadcasts the typed status event that tells clients to start reauth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "McpInteractiveAuthRequired",
    "McpOAuthBroker",
]

#: How long a pending flow may sit before ``mcp_auth_complete`` refuses it.
AUTH_FLOW_TTL_SECONDS = 15 * 60

#: Loopback redirect used when the client does not supply one. Clients
#: that run their own callback listener pass an explicit redirect_uri.
DEFAULT_REDIRECT_URI = "http://localhost:53535/mcp/oauth/callback"

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _server_slug(server_name: str) -> str:
    slug = _SLUG_RE.sub("-", server_name.strip().lower()).strip("-")
    return slug or "server"


class McpInteractiveAuthRequired(RuntimeError):
    """A non-interactive connection needs a browser authorization.

    ``had_tokens`` distinguishes *expired* stored credentials (reauth)
    from a server that was never authorized (initial auth) — the two
    typed reasons clients act on.
    """

    def __init__(self, server_name: str, *, had_tokens: bool) -> None:
        super().__init__(f"MCP server {server_name!r} requires browser authorization")
        self.server_name = server_name
        self.had_tokens = had_tokens


@dataclass
class _PendingFlow:
    auth_id: str
    server_name: str
    expires_at: float
    auth_url: str | None = None
    url_ready: asyncio.Event | None = None
    code_future: "asyncio.Future[Tuple[str, str | None]] | None" = None
    task: "asyncio.Task[Any] | None" = None
    state: str = "starting"  # starting | pending | completed | failed | cancelled
    error: str | None = None
    redirect_uri: str = DEFAULT_REDIRECT_URI
    #: CSRF ``state`` parameter parsed from the authorization URL —
    #: substituted when the client pastes a bare code instead of the
    #: full redirect URL.
    oauth_state: str | None = None


class _FileTokenStorage:
    """``mcp.client.auth.TokenStorage`` backed by one 0600 JSON file.

    The file holds tokens + registered client info + bookkeeping
    (``obtained_at``) — it is the ONLY place MCP OAuth credential
    material exists, and nothing in this module ever returns its
    contents to a caller.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, doc: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        tmp = Path(tmp_name)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # -- TokenStorage protocol ------------------------------------------

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: Any) -> None:
        doc = self._read()
        doc["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        doc["obtained_at"] = time.time()
        doc.pop("auth_failure", None)
        self._write(doc)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(self, client_info: Any) -> None:
        doc = self._read()
        doc["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(doc)

    # -- bookkeeping -----------------------------------------------------

    def has_tokens(self) -> bool:
        return isinstance(self._read().get("tokens"), dict)

    def note_failure(self, *, expired: bool) -> None:
        doc = self._read()
        doc["auth_failure"] = {
            "expired": bool(expired),
            "at": time.time(),
        }
        self._write(doc)

    def failure(self) -> Dict[str, Any] | None:
        raw = self._read().get("auth_failure")
        return raw if isinstance(raw, dict) else None

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass


def parse_authorization_code(
    code: str, expected_state: str | None = None
) -> Tuple[str, str | None]:
    """Accept a raw authorization code or the full redirect URL.

    Mirrors the account surface's completion contract: browser clients
    can paste either. Returns ``(code, state)``.
    """
    candidate = code.strip()
    if "://" in candidate or candidate.startswith("/"):
        query = parse_qs(urlparse(candidate).query)
        state_values = query.get("state") or []
        returned_state = state_values[0] if state_values else None
        code_values = query.get("code") or []
        parsed = code_values[0] if code_values else None
        if not parsed:
            from omniagents.core.agents.mcp_admin import McpAuthStateInvalid

            raise McpAuthStateInvalid(
                "code_required",
                message="The redirect URL carries no authorization code",
            )
        # A redirect URL without a state echo falls back to the expected
        # state (some ASes only echo it when the client sent one).
        return parsed, (
            returned_state if returned_state is not None else expected_state
        )
    return candidate, expected_state


class McpOAuthBroker:
    """Per-service MCP OAuth flow + token-store owner. No secrets out."""

    def __init__(
        self,
        *,
        token_dir: Path,
        on_state_change: Callable[[str, str], Awaitable[None]] | None = None,
        http_factory: Callable[..., httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.time,
        flow_timeout: float = AUTH_FLOW_TTL_SECONDS,
    ) -> None:
        self._token_dir = token_dir
        self._on_state_change = on_state_change
        self._http_factory = http_factory or (
            lambda **kwargs: httpx.AsyncClient(**kwargs)
        )
        self._clock = clock
        self._flow_timeout = flow_timeout
        self._pending: Dict[str, _PendingFlow] = {}
        # Side-channel for non-interactive failures: the SDK's transport
        # task groups can swallow the McpInteractiveAuthRequired raised in
        # the redirect handler and surface a bare cancel-scope error
        # instead — the handler records the truth here so failure
        # classification stays typed. server_name → had_tokens.
        self._noninteractive_failures: Dict[str, bool] = {}

    def storage_for(self, server_name: str) -> _FileTokenStorage:
        return _FileTokenStorage(self._token_dir / f"{_server_slug(server_name)}.json")

    # -- state (never credential material) -------------------------------

    def state_of(self, server_name: str) -> str:
        for flow in self._pending.values():
            if flow.server_name == server_name and flow.state in (
                "starting",
                "pending",
            ):
                return "pending"
        storage = self.storage_for(server_name)
        if not storage.has_tokens():
            failure = storage.failure()
            if failure is not None:
                return "expired" if failure.get("expired") else "required"
            return "none"
        failure = storage.failure()
        if failure is not None:
            return "expired"
        return "authenticated"

    def has_tokens(self, server_name: str) -> bool:
        return self.storage_for(server_name).has_tokens()

    def note_auth_failure(self, server_name: str, *, expired: bool) -> None:
        self.storage_for(server_name).note_failure(expired=expired)

    def take_noninteractive_failure(self, server_name: str) -> Optional[bool]:
        """Pop the recorded had_tokens flag for a swallowed auth failure."""
        return self._noninteractive_failures.pop(server_name, None)

    def forget(self, server_name: str) -> None:
        self.storage_for(server_name).clear()

    # -- interactive flow ------------------------------------------------

    def _client_metadata(self, redirect_uri: str) -> Any:
        from mcp.shared.auth import OAuthClientMetadata

        return OAuthClientMetadata.model_validate(
            {
                "client_name": "OmniAgents",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        )

    def _prune(self) -> None:
        now = self._clock()
        for auth_id in [
            key
            for key, flow in self._pending.items()
            if flow.expires_at <= now and flow.state in ("starting", "pending")
        ]:
            self.cancel(auth_id)

    async def start(
        self,
        server_name: str,
        server_url: str,
        *,
        redirect_uri: str | None = None,
    ) -> Dict[str, Any]:
        """Begin a browser flow; returns at the URL-handoff point.

        The result carries ``auth_id`` + ``auth_url`` and NOTHING
        credential-bearing — the PKCE verifier and any tokens stay in
        the broker's file store.
        """
        from mcp.client.auth import OAuthClientProvider

        from omniagents.core.agents.mcp_admin import McpAuthFailed

        self._prune()
        # One live flow per server: a fresh start supersedes the old one.
        for auth_id, flow in list(self._pending.items()):
            if flow.server_name == server_name and flow.state in (
                "starting",
                "pending",
            ):
                self.cancel(auth_id)

        resolved_redirect = redirect_uri or DEFAULT_REDIRECT_URI
        flow = _PendingFlow(
            auth_id=secrets.token_urlsafe(16),
            server_name=server_name,
            expires_at=self._clock() + self._flow_timeout,
            url_ready=asyncio.Event(),
            code_future=asyncio.get_event_loop().create_future(),
            redirect_uri=resolved_redirect,
        )
        self._pending[flow.auth_id] = flow

        async def redirect_handler(authorization_url: str) -> None:
            flow.auth_url = authorization_url
            # Remember the CSRF ``state`` from the handoff URL so a client
            # that pastes the bare authorization code (no redirect URL)
            # still satisfies the provider's state check.
            try:
                values = parse_qs(urlparse(authorization_url).query).get("state")
                flow.oauth_state = values[0] if values else None
            except Exception:
                flow.oauth_state = None
            flow.state = "pending"
            assert flow.url_ready is not None
            flow.url_ready.set()

        async def callback_handler() -> Tuple[str, str | None]:
            assert flow.code_future is not None
            return await asyncio.wait_for(
                asyncio.shield(flow.code_future), timeout=self._flow_timeout
            )

        provider = OAuthClientProvider(
            server_url=server_url,
            client_metadata=self._client_metadata(resolved_redirect),
            storage=self.storage_for(server_name),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

        async def run_flow() -> None:
            try:
                async with self._http_factory(auth=provider) as client:
                    response = await client.post(
                        server_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 0,
                            "method": "ping",
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Content-Type": "application/json",
                        },
                    )
                    if response.status_code == 401:
                        raise McpAuthFailed(
                            "Authorization completed but the server still "
                            "rejects the credential",
                            server_name=server_name,
                            status=401,
                        )
                flow.state = "completed"
                await self._notify(server_name, "authenticated")
            except asyncio.CancelledError:
                if flow.state not in ("completed", "cancelled"):
                    flow.state = "cancelled"
                raise
            except BaseException as exc:
                if flow.state != "cancelled":
                    flow.state = "failed"
                    # Never include URLs-with-codes or tokens: keep the
                    # upstream error's message only.
                    flow.error = str(exc) or exc.__class__.__name__
                logger.debug("MCP OAuth flow failed for %s", server_name, exc_info=True)

        flow.task = asyncio.create_task(
            run_flow(), name=f"mcp_oauth_{_server_slug(server_name)}"
        )

        # Wait for the URL handoff — or an early failure (e.g. the server
        # needs no auth, discovery failed, metadata invalid).
        assert flow.url_ready is not None
        url_ready = asyncio.create_task(flow.url_ready.wait())
        done, pending_tasks = await asyncio.wait(
            {url_ready, flow.task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending_tasks:
            if task is url_ready:
                task.cancel()
        if not flow.url_ready.is_set():
            self._pending.pop(flow.auth_id, None)
            if flow.state == "completed":
                # Stored/refreshed credentials already satisfied the
                # server — no browser round-trip needed.
                return {
                    "state": "completed",
                    "server_name": server_name,
                    "auth_state": self.state_of(server_name),
                }
            raise McpAuthFailed(
                flow.error or "Could not start the authorization flow",
                server_name=server_name,
            )
        return {
            "state": "pending",
            "server_name": server_name,
            "auth_id": flow.auth_id,
            "auth_url": flow.auth_url,
            "redirect_uri": flow.redirect_uri,
        }

    def _pending_flow(self, auth_id: str) -> _PendingFlow:
        from omniagents.core.agents.mcp_admin import McpAuthStateInvalid

        self._prune()
        flow = self._pending.get(auth_id)
        if flow is None:
            raise McpAuthStateInvalid(
                "unknown_auth",
                message=(
                    "No pending MCP authorization with this id "
                    "(expired or cancelled)"
                ),
            )
        return flow

    async def complete(self, auth_id: str, code: str) -> Dict[str, Any]:
        """Deliver the authorization code and finish the flow."""
        from omniagents.core.agents.mcp_admin import (
            McpAuthFailed,
            McpAuthStateInvalid,
        )

        flow = self._pending_flow(auth_id)
        if not code or not code.strip():
            raise McpAuthStateInvalid(
                "code_required",
                server_name=flow.server_name,
                message="mcp_auth_complete requires the authorization code",
            )
        parsed_code, state = parse_authorization_code(code, flow.oauth_state)
        assert flow.code_future is not None
        if not flow.code_future.done():
            flow.code_future.set_result((parsed_code, state))
        assert flow.task is not None
        try:
            await asyncio.wait_for(asyncio.shield(flow.task), timeout=60.0)
        except asyncio.TimeoutError:
            raise McpAuthFailed(
                "Timed out completing the authorization flow",
                server_name=flow.server_name,
            ) from None
        finally:
            if flow.state in ("completed", "failed", "cancelled"):
                self._pending.pop(auth_id, None)
        if flow.state != "completed":
            raise McpAuthFailed(
                flow.error or "Authorization failed",
                server_name=flow.server_name,
            )
        return {
            "state": "completed",
            "server_name": flow.server_name,
            "auth_state": self.state_of(flow.server_name),
        }

    def cancel(self, auth_id: str) -> bool:
        """Drop a pending flow. True when one was actually cancelled."""
        flow = self._pending.pop(auth_id, None)
        if flow is None:
            return False
        flow.state = "cancelled"
        if flow.code_future is not None and not flow.code_future.done():
            flow.code_future.cancel()
        if flow.task is not None and not flow.task.done():
            flow.task.cancel()
        return True

    async def _notify(self, server_name: str, state: str) -> None:
        if self._on_state_change is None:
            return
        try:
            await self._on_state_change(server_name, state)
        except Exception:
            pass

    # -- non-interactive personality ------------------------------------

    def noninteractive_auth(self, server_name: str, server_url: str) -> Any:
        """httpx.Auth for session builds and probes.

        Attaches stored tokens and refreshes silently; anything that
        would require the browser raises
        :class:`McpInteractiveAuthRequired` so the connect fails with a
        typed ``auth_required`` / ``auth_expired`` reason instead of
        hanging on a redirect that can never be answered.
        """
        from mcp.client.auth import OAuthClientProvider

        storage = self.storage_for(server_name)
        had_tokens = storage.has_tokens()

        async def redirect_handler(_authorization_url: str) -> None:
            storage.note_failure(expired=had_tokens)
            self._noninteractive_failures[server_name] = had_tokens
            raise McpInteractiveAuthRequired(server_name, had_tokens=had_tokens)

        async def callback_handler() -> Tuple[str, str | None]:
            self._noninteractive_failures[server_name] = had_tokens
            raise McpInteractiveAuthRequired(server_name, had_tokens=had_tokens)

        return OAuthClientProvider(
            server_url=server_url,
            client_metadata=self._client_metadata(DEFAULT_REDIRECT_URI),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
