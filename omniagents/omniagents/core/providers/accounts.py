"""Unified provider account state for the GUI protocol's ``account_*`` RPCs.

One :class:`AccountManager` normalizes every model-provider credential
surface the runtime understands — the ChatGPT (Codex) OAuth machinery in
:mod:`omniagents.core.providers.codex` and the environment-variable API
keys the model runtime reads — behind a provider-agnostic contract:

* ``status()`` / ``usage()`` — read-only snapshots, safe for any client.
* ``login_start`` / ``login_complete`` / ``login_cancel`` — non-blocking
  login flows (device-code, browser OAuth to the URL-handoff point, and
  API keys), each cancellable mid-flight.
* ``logout`` / ``refresh`` / ``select`` — credential lifecycle.

Capability differences are typed, not generic failures: an operation a
provider does not support raises :class:`AccountCapabilityUnsupported`
(JSON-RPC ``-32040``), a supported operation in the wrong state raises
:class:`AccountStateInvalid` (``-32041``), and an upstream credential
rejection raises :class:`AccountAuthFailed` (``-32042``).

SECRETS: nothing returned by this module ever contains credential
material. Snapshots are built field-by-field from an allowlist (ids,
plans, booleans, timestamps); tokens and API keys stay in the codex
token store / process environment. ``tests/unit/core/providers/
test_accounts.py`` and ``tests/integration/test_server_accounts.py``
adversarially assert this on every broadcast and logging path.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from omniagents.core.providers import codex

__all__ = [
    "AccountAuthFailed",
    "AccountCapabilityUnsupported",
    "AccountManager",
    "AccountRpcError",
    "AccountStateInvalid",
    "CHATGPT_PROVIDER_ID",
    "CODEX_STORE_DURABILITY_ENV",
    "RPC_ERROR_ACCOUNT_AUTH_FAILED",
    "RPC_ERROR_ACCOUNT_CAPABILITY_UNSUPPORTED",
    "RPC_ERROR_ACCOUNT_STATE_INVALID",
]

#: JSON-RPC error codes reserved for the account surface (see
#: ``protocol/openrpc`` components.errors).
RPC_ERROR_ACCOUNT_CAPABILITY_UNSUPPORTED = -32040
RPC_ERROR_ACCOUNT_STATE_INVALID = -32041
RPC_ERROR_ACCOUNT_AUTH_FAILED = -32042

CHATGPT_PROVIDER_ID = "openai-chatgpt"

#: Host attestation for deployments that mount the existing ``codex.json``
#: token store on storage that survives AgentHost process restarts.  Merely
#: being able to write the store is insufficient proof of durability, so the
#: capability is fail-closed unless the host explicitly supplies ``host``.
CODEX_STORE_DURABILITY_ENV = "OMNIAGENTS_CODEX_STORE_DURABILITY"

#: How long a pending login may sit before ``login_complete`` refuses it.
LOGIN_TTL_SECONDS = 15 * 60


class AccountRpcError(Exception):
    """Base typed account error: carries a JSON-RPC ``code`` + ``data``.

    ``JsonRpcEndpoint`` surfaces these verbatim (code, message, data)
    instead of the generic ``-32603`` internal error.
    """

    code: int = -32603
    kind: str = "account_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.extra = {key: value for key, value in extra.items() if value is not None}

    @property
    def data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"kind": self.kind}
        if self.provider is not None:
            data["provider"] = self.provider
        data.update(self.extra)
        return data


class AccountCapabilityUnsupported(AccountRpcError):
    """The provider does not support the requested operation at all."""

    code = RPC_ERROR_ACCOUNT_CAPABILITY_UNSUPPORTED
    kind = "account_capability_unsupported"

    def __init__(
        self,
        provider: str,
        operation: str,
        *,
        supported: Tuple[str, ...] | list[str] = (),
        message: str | None = None,
    ) -> None:
        super().__init__(
            message or f"Provider {provider} does not support {operation}",
            provider=provider,
            operation=operation,
            supported=sorted(supported),
        )


class AccountStateInvalid(AccountRpcError):
    """A supported operation was requested in a state that forbids it."""

    code = RPC_ERROR_ACCOUNT_STATE_INVALID
    kind = "account_state_invalid"

    def __init__(
        self,
        reason: str,
        *,
        provider: str | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(
            message or f"Account operation is not valid here: {reason}",
            provider=provider,
            reason=reason,
        )


class AccountAuthFailed(AccountRpcError):
    """The upstream identity provider rejected or failed the credential."""

    code = RPC_ERROR_ACCOUNT_AUTH_FAILED
    kind = "account_auth_failed"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status: int | None = None,
    ) -> None:
        super().__init__(message, provider=provider, status=status)


@dataclass(frozen=True)
class ProviderDescriptor:
    """Static capability description of one credential provider."""

    id: str
    label: str
    kind: str  # "oauth" | "api_key"
    login_modes: Tuple[str, ...]
    env_var: str | None = None
    supports_logout: bool = True
    supports_refresh: bool = False
    supports_usage: bool = False

    def capabilities(self) -> Dict[str, Any]:
        return {
            "login_modes": list(self.login_modes),
            "logout": self.supports_logout,
            "refresh": self.supports_refresh,
            "usage": self.supports_usage,
        }


_API_KEY_MODES: Tuple[str, ...] = ("api_key",)

PROVIDERS: Dict[str, ProviderDescriptor] = {
    descriptor.id: descriptor
    for descriptor in (
        ProviderDescriptor(
            id=CHATGPT_PROVIDER_ID,
            label="ChatGPT (Codex)",
            kind="oauth",
            login_modes=("device_code", "browser"),
            supports_refresh=True,
            supports_usage=True,
        ),
        ProviderDescriptor(
            id="openai",
            label="OpenAI API key",
            kind="api_key",
            login_modes=_API_KEY_MODES,
            env_var="OPENAI_API_KEY",
        ),
        ProviderDescriptor(
            id="anthropic",
            label="Anthropic API key",
            kind="api_key",
            login_modes=_API_KEY_MODES,
            env_var="ANTHROPIC_API_KEY",
        ),
        ProviderDescriptor(
            id="google",
            label="Google API key",
            kind="api_key",
            login_modes=_API_KEY_MODES,
            env_var="GOOGLE_API_KEY",
        ),
        ProviderDescriptor(
            id="azure-openai",
            label="Azure OpenAI API key",
            kind="api_key",
            login_modes=_API_KEY_MODES,
            env_var="AZURE_OPENAI_API_KEY",
        ),
    )
}


@dataclass
class _PendingLogin:
    login_id: str
    provider: str
    mode: str
    expires_at: float
    # device-code flow
    device_auth_id: str | None = None
    user_code: str | None = None
    interval: int | None = None
    # browser (PKCE) flow — the verifier NEVER leaves the server
    code_verifier: str | None = None
    state: str | None = None
    redirect_uri: str | None = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_browser_code(code: str, expected_state: str | None) -> str:
    """Accept a raw authorization code or the full redirect URL."""
    candidate = code.strip()
    if "://" in candidate or candidate.startswith("/"):
        query = parse_qs(urlparse(candidate).query)
        state_values = query.get("state") or []
        returned_state = state_values[0] if state_values else None
        if expected_state and returned_state and returned_state != expected_state:
            raise AccountStateInvalid(
                "state_mismatch",
                message="OAuth state in the redirect does not match this login",
            )
        code_values = query.get("code") or []
        parsed = code_values[0] if code_values else None
        if not parsed:
            raise AccountStateInvalid(
                "code_required",
                message="The redirect URL carries no authorization code",
            )
        return parsed
    return candidate


class AccountManager:
    """Provider-agnostic account state, one instance per agent service."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._pending: Dict[str, _PendingLogin] = {}
        # API keys installed through the RPC surface (provider id → True).
        # Keys found in the environment WITHOUT an entry here are
        # externally managed: visible as signed-in, but not ours to clear.
        self._rpc_managed: Dict[str, bool] = {}
        self._selected: str | None = None

    # ------------------------------------------------------------------
    # Descriptors and snapshots (no secrets, ever)
    # ------------------------------------------------------------------
    def _descriptor(self, provider: str) -> ProviderDescriptor:
        descriptor = PROVIDERS.get(provider)
        if descriptor is None:
            raise AccountStateInvalid(
                "unknown_provider",
                provider=provider,
                message=f"Unknown provider: {provider}",
            )
        return descriptor

    def _require_mode(self, descriptor: ProviderDescriptor, mode: str) -> None:
        if mode not in descriptor.login_modes:
            raise AccountCapabilityUnsupported(
                descriptor.id, f"login:{mode}", supported=descriptor.login_modes
            )

    def provider_snapshot(self, provider: str) -> Dict[str, Any]:
        """Redacted state of one provider — identity yes, credentials no."""
        descriptor = self._descriptor(provider)
        snapshot: Dict[str, Any] = {
            "id": descriptor.id,
            "label": descriptor.label,
            "kind": descriptor.kind,
            "capabilities": descriptor.capabilities(),
            "state": "signed_out",
            "source": None,
            "identity": None,
            "error": None,
            "selected": self._selected == descriptor.id,
        }
        if descriptor.kind == "oauth":
            tokens = codex.load_tokens()
            if tokens and tokens.get("refresh"):
                auth_error = tokens.get("auth_error")
                snapshot["state"] = "error" if auth_error else "signed_in"
                snapshot["source"] = "oauth"
                identity: Dict[str, Any] = {}
                account_id = tokens.get("account_id")
                if isinstance(account_id, str) and account_id:
                    identity["account_id"] = account_id
                plan = codex.extract_plan_type(tokens)
                if plan:
                    identity["plan"] = plan
                email = codex.extract_email(tokens)
                if email:
                    identity["email"] = email
                snapshot["identity"] = identity or None
                if isinstance(auth_error, dict):
                    snapshot["error"] = {
                        "status": auth_error.get("code"),
                        "at": auth_error.get("at"),
                    }
        elif descriptor.env_var:
            value = os.environ.get(descriptor.env_var) or ""
            if value:
                snapshot["state"] = "signed_in"
                snapshot["source"] = (
                    "rpc" if self._rpc_managed.get(descriptor.id) else "env"
                )
        return snapshot

    def status(self) -> Dict[str, Any]:
        codex_store_is_host_durable = os.getenv(CODEX_STORE_DURABILITY_ENV) == "host"
        return {
            "providers": [self.provider_snapshot(name) for name in PROVIDERS],
            "selected_provider": self._selected,
            "mutation_persistence": {
                "codex_oauth": {
                    "durable": codex_store_is_host_durable,
                    "scope": "host" if codex_store_is_host_durable else None,
                },
                # Login ids, device codes, and PKCE verifiers intentionally
                # stay in this manager.  A restarted process must begin a
                # fresh OAuth exchange even when completed tokens are durable.
                "pending_logins": {"durable": False, "scope": "process"},
            },
        }

    # ------------------------------------------------------------------
    # Usage / rate limits
    # ------------------------------------------------------------------
    def _usage_entry(self, descriptor: ProviderDescriptor) -> Dict[str, Any] | None:
        tokens = codex.load_tokens()
        if not tokens or not tokens.get("refresh"):
            return None
        entry: Dict[str, Any] = {
            "id": descriptor.id,
            "plan": codex.extract_plan_type(tokens),
            "rate_limits": [],
            "captured_at": None,
            "notices": [],
        }
        snapshot = codex.rate_limit_snapshot()
        if snapshot:
            entry["rate_limits"] = snapshot.get("rate_limits") or []
            entry["captured_at"] = snapshot.get("captured_at")
            if snapshot.get("plan") and not entry["plan"]:
                entry["plan"] = snapshot["plan"]
        return entry

    def usage(self, provider: str | None = None) -> Dict[str, Any]:
        if provider is not None:
            descriptor = self._descriptor(provider)
            if not descriptor.supports_usage:
                raise AccountCapabilityUnsupported(
                    descriptor.id,
                    "usage",
                    supported=tuple(
                        name for name, item in PROVIDERS.items() if item.supports_usage
                    ),
                )
            entry = self._usage_entry(descriptor)
            if entry is None:
                raise AccountStateInvalid("not_signed_in", provider=descriptor.id)
            return {"providers": [entry]}
        entries = []
        for descriptor in PROVIDERS.values():
            if not descriptor.supports_usage:
                continue
            entry = self._usage_entry(descriptor)
            if entry is not None:
                entries.append(entry)
        return {"providers": entries}

    # ------------------------------------------------------------------
    # Login flows
    # ------------------------------------------------------------------
    def _prune_logins(self) -> None:
        now = self._clock()
        for login_id in [
            key for key, item in self._pending.items() if item.expires_at <= now
        ]:
            self._pending.pop(login_id, None)

    def _register_login(self, login: _PendingLogin) -> None:
        self._prune_logins()
        self._pending[login.login_id] = login

    async def login_start(
        self,
        provider: str,
        mode: str,
        *,
        api_key: str | None = None,
        redirect_uri: str | None = None,
    ) -> Dict[str, Any]:
        descriptor = self._descriptor(provider)
        self._require_mode(descriptor, mode)
        if mode == "api_key":
            return self._login_api_key(descriptor, api_key)
        if mode == "device_code":
            return await self._login_start_device(descriptor)
        if mode == "browser":
            return self._login_start_browser(descriptor, redirect_uri)
        raise AccountCapabilityUnsupported(
            descriptor.id, f"login:{mode}", supported=descriptor.login_modes
        )

    def _login_api_key(
        self, descriptor: ProviderDescriptor, api_key: str | None
    ) -> Dict[str, Any]:
        key = (api_key or "").strip()
        if not key:
            raise AccountStateInvalid(
                "api_key_required",
                provider=descriptor.id,
                message="api_key is required for api_key login",
            )
        assert descriptor.env_var is not None  # api_key providers declare one
        os.environ[descriptor.env_var] = key
        self._rpc_managed[descriptor.id] = True
        return {
            "state": "completed",
            "mode": "api_key",
            "provider": descriptor.id,
            "account": self.provider_snapshot(descriptor.id),
        }

    async def _login_start_device(
        self, descriptor: ProviderDescriptor
    ) -> Dict[str, Any]:
        try:
            info = await codex.device_auth_start()
        except RuntimeError as exc:
            raise AccountAuthFailed(str(exc), provider=descriptor.id) from exc
        login = _PendingLogin(
            login_id=secrets.token_urlsafe(16),
            provider=descriptor.id,
            mode="device_code",
            expires_at=self._clock() + LOGIN_TTL_SECONDS,
            device_auth_id=str(info["device_auth_id"]),
            user_code=str(info["user_code"]),
            interval=int(info.get("interval") or 5),
        )
        self._register_login(login)
        return {
            "state": "pending",
            "mode": "device_code",
            "provider": descriptor.id,
            "login_id": login.login_id,
            "user_code": login.user_code,
            "verification_url": info.get(
                "verification_url", codex.DEVICE_VERIFICATION_URL
            ),
            "interval": login.interval,
        }

    def _login_start_browser(
        self, descriptor: ProviderDescriptor, redirect_uri: str | None
    ) -> Dict[str, Any]:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        resolved_redirect = redirect_uri or codex.DEFAULT_BROWSER_REDIRECT_URI
        login = _PendingLogin(
            login_id=secrets.token_urlsafe(16),
            provider=descriptor.id,
            mode="browser",
            expires_at=self._clock() + LOGIN_TTL_SECONDS,
            code_verifier=verifier,
            state=state,
            redirect_uri=resolved_redirect,
        )
        self._register_login(login)
        return {
            "state": "pending",
            "mode": "browser",
            "provider": descriptor.id,
            "login_id": login.login_id,
            "auth_url": codex.build_authorize_url(
                redirect_uri=resolved_redirect,
                code_challenge=challenge,
                state=state,
            ),
            "redirect_uri": resolved_redirect,
        }

    def _pending_login(self, login_id: str) -> _PendingLogin:
        self._prune_logins()
        login = self._pending.get(login_id)
        if login is None:
            raise AccountStateInvalid(
                "unknown_login",
                message="No pending login with this id (expired or cancelled)",
            )
        return login

    async def login_complete(
        self, login_id: str, *, code: str | None = None
    ) -> Dict[str, Any]:
        login = self._pending_login(login_id)
        if login.mode == "device_code":
            return await self._complete_device(login)
        return await self._complete_browser(login, code)

    async def _complete_device(self, login: _PendingLogin) -> Dict[str, Any]:
        try:
            tokens = await codex.device_auth_poll(
                login.device_auth_id or "", login.user_code or ""
            )
        except RuntimeError as exc:
            self._pending.pop(login.login_id, None)
            raise AccountAuthFailed(str(exc), provider=login.provider) from exc
        if tokens is None:
            return {
                "state": "pending",
                "mode": "device_code",
                "provider": login.provider,
                "login_id": login.login_id,
                "user_code": login.user_code,
                "interval": login.interval,
            }
        self._pending.pop(login.login_id, None)
        return {
            "state": "completed",
            "mode": "device_code",
            "provider": login.provider,
            "account": self.provider_snapshot(login.provider),
        }

    async def _complete_browser(
        self, login: _PendingLogin, code: str | None
    ) -> Dict[str, Any]:
        if not code or not code.strip():
            raise AccountStateInvalid(
                "code_required",
                provider=login.provider,
                message="Browser login completion requires the authorization code",
            )
        parsed = _parse_browser_code(code, login.state)
        try:
            await codex.exchange_authorization_code(
                parsed,
                code_verifier=login.code_verifier or "",
                redirect_uri=login.redirect_uri or codex.DEFAULT_BROWSER_REDIRECT_URI,
            )
        except RuntimeError as exc:
            raise AccountAuthFailed(str(exc), provider=login.provider) from exc
        self._pending.pop(login.login_id, None)
        return {
            "state": "completed",
            "mode": "browser",
            "provider": login.provider,
            "account": self.provider_snapshot(login.provider),
        }

    def login_cancel(self, login_id: str) -> bool:
        """Drop a pending login. True when one was actually cancelled."""
        self._prune_logins()
        return self._pending.pop(login_id, None) is not None

    # ------------------------------------------------------------------
    # Logout / refresh / selection
    # ------------------------------------------------------------------
    def logout(self, provider: str) -> Dict[str, Any]:
        descriptor = self._descriptor(provider)
        if not descriptor.supports_logout:
            raise AccountCapabilityUnsupported(descriptor.id, "logout")
        if descriptor.kind == "oauth":
            if not codex.is_authenticated():
                raise AccountStateInvalid("not_signed_in", provider=descriptor.id)
            codex.clear_tokens()
        else:
            assert descriptor.env_var is not None
            value = os.environ.get(descriptor.env_var) or ""
            if not value:
                raise AccountStateInvalid("not_signed_in", provider=descriptor.id)
            if not self._rpc_managed.get(descriptor.id):
                raise AccountStateInvalid(
                    "externally_managed",
                    provider=descriptor.id,
                    message=(
                        "This credential is managed by the host environment; "
                        "sign out where it was configured"
                    ),
                )
            os.environ.pop(descriptor.env_var, None)
            self._rpc_managed.pop(descriptor.id, None)
        return {"ok": True, "account": self.provider_snapshot(descriptor.id)}

    async def refresh(self, provider: str) -> Dict[str, Any]:
        descriptor = self._descriptor(provider)
        if not descriptor.supports_refresh:
            raise AccountCapabilityUnsupported(
                descriptor.id,
                "refresh",
                supported=tuple(
                    name for name, item in PROVIDERS.items() if item.supports_refresh
                ),
            )
        if not codex.is_authenticated():
            raise AccountStateInvalid("not_signed_in", provider=descriptor.id)
        try:
            await codex.refresh_now_async()
        except RuntimeError as exc:
            stamp = (codex.load_tokens() or {}).get("auth_error")
            status = stamp.get("code") if isinstance(stamp, dict) else None
            raise AccountAuthFailed(
                str(exc), provider=descriptor.id, status=status
            ) from exc
        return {"ok": True, "account": self.provider_snapshot(descriptor.id)}

    def select(self, provider: str) -> Dict[str, Any]:
        descriptor = self._descriptor(provider)
        self._selected = descriptor.id
        return {
            "selected_provider": self._selected,
            "account": self.provider_snapshot(descriptor.id),
        }

    def selected_provider(self) -> str | None:
        return self._selected
