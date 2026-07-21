"""Codex (ChatGPT) OAuth support for the OpenAI provider.

ChatGPT Plus/Pro/Team subscribers can drive an agent through their
subscription instead of a metered API key by authenticating with ChatGPT
OAuth and talking to the Codex backend (``chatgpt.com/backend-api/codex``)
which speaks the OpenAI **Responses API** — the same protocol the OpenAI
Agents SDK uses by default.

Usage from a plain OmniAgents project:

1. ``omniagents auth codex login`` (device flow) — stores tokens.
2. In the agent YAML, set ``model_provider: { type: openai-oauth }`` and a
   ``model:`` slug (e.g. ``gpt-5.5``); discovery fills in the rest.

This module owns: the token store, the OAuth refresh + device-code login
flows against ``auth.openai.com``, a refreshing ``AsyncOpenAI`` factory
(:func:`build_codex_openai_client`), live model discovery
(:func:`list_codex_models`), and registration of the ``openai-oauth`` provider
builder with ``run_options`` (:func:`register_codex_provider`, auto-called on
``import omniagents``).

The bearer token is injected (and refreshed) by an httpx request event hook
rather than the SDK's ``api_key`` path, because a serve process can outlive a
token's ~1h lifetime — the hook re-checks expiry on every request and
refreshes through a shared lock.

The token store defaults to ``<config>/codex.json`` (``$OMNIAGENTS_CODEX_HOME``
or ``$XDG_CONFIG_HOME/omniagents`` or ``~/.config/omniagents``). Embedders that
keep credentials elsewhere (e.g. omni-code, whose launcher writes them into the
omni-code config dir) call :func:`set_token_store_dir` to repoint it.

Why no URL rewrite: the Agents SDK POSTs to ``{base_url}/responses``, so setting
``base_url`` to the Codex root lands exactly on ``.../codex/responses``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

# OAuth client shipped by the Codex CLI / opencode. Public PKCE client.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
ORIGINATOR = "omniagents"

CODEX_CRED_FILE = "codex.json"

# The /models endpoint requires a client_version query param. The value is not
# used to gate the returned set (the backend returns all models regardless and
# carries per-model minimal_client_version for the *client* to interpret), so a
# low sentinel is fine and forward-compatible.
CODEX_CLIENT_VERSION = "0.0.0"

# Refresh a little before the real expiry so an in-flight request never races
# the clock.
_EXPIRY_SKEW_MS = 60_000

# Discovered-model cache (process-wide).
_models_cache: dict[str, "Any"] = {}
_MODELS_TTL_SECONDS = 300


# --------------------------------------------------------------------------
# Token store
# --------------------------------------------------------------------------

_token_store_dir: Optional[Path] = None


def set_token_store_dir(path: Any) -> None:
    """Override the directory holding ``codex.json``.

    Lets an embedder (e.g. omni-code) point the store at its own config dir so
    a shared launcher-written token file is found. Takes effect immediately.
    """
    global _token_store_dir
    _token_store_dir = Path(path)


def _config_dir() -> Path:
    if _token_store_dir is not None:
        return _token_store_dir
    env = os.getenv("OMNIAGENTS_CODEX_HOME")
    if env:
        return Path(env)
    # Fall back to the installed product identity's config dir (e.g. a
    # product declaring `product:` in project.yml keeps codex.json in its
    # own config dir without calling set_token_store_dir).
    from omniagents.core.product import DEFAULT_PRODUCT_INFO, product_info

    info = product_info()
    if info is not DEFAULT_PRODUCT_INFO:
        return info.config_dir()
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "omniagents"


def _store_path() -> Path:
    return _config_dir() / CODEX_CRED_FILE


def load_tokens() -> Optional[Dict[str, Any]]:
    """Load stored Codex tokens, or ``None`` when not signed in.

    Shape: ``{"refresh", "access", "expires" (ms epoch), "account_id"?}``.
    """
    path = _store_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_tokens(tokens: Dict[str, Any]) -> None:
    """Persist Codex tokens (owner-only)."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2))
    path.chmod(0o600)


def clear_tokens() -> None:
    path = _store_path()
    if path.exists():
        path.unlink()


def is_authenticated() -> bool:
    return load_tokens() is not None


# --------------------------------------------------------------------------
# JWT claim helpers
# --------------------------------------------------------------------------


def parse_jwt_claims(token: str) -> Optional[Dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None


def extract_account_id(tokens: Dict[str, Any]) -> Optional[str]:
    """Pull the ChatGPT account id from id/access token claims."""

    def from_claims(claims: Optional[Dict[str, Any]]) -> Optional[str]:
        if not claims:
            return None
        auth_ns = claims.get("https://api.openai.com/auth") or {}
        orgs = claims.get("organizations") or []
        return (
            claims.get("chatgpt_account_id")
            or (auth_ns.get("chatgpt_account_id") if isinstance(auth_ns, dict) else None)
            or (orgs[0].get("id") if orgs and isinstance(orgs[0], dict) else None)
        )

    id_token = tokens.get("id_token")
    if id_token:
        acct = from_claims(parse_jwt_claims(id_token))
        if acct:
            return acct
    access = tokens.get("access_token") or tokens.get("access")
    if access:
        return from_claims(parse_jwt_claims(access))
    return None


# --------------------------------------------------------------------------
# OAuth network calls
# --------------------------------------------------------------------------


def _normalize_token_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map an OAuth token endpoint response to our stored shape."""
    expires_in = data.get("expires_in") or 3600
    tokens: Dict[str, Any] = {
        "refresh": data["refresh_token"],
        "access": data["access_token"],
        "expires": int(time.time() * 1000) + int(expires_in) * 1000,
    }
    account_id = extract_account_id(data)
    if account_id:
        tokens["account_id"] = account_id
    return tokens


async def _refresh_async(refresh_token: str) -> Dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ISSUER}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Codex token refresh failed: {resp.status_code}")
    return _normalize_token_response(resp.json())


async def _persist_refresh_to_launcher(tokens: Dict[str, Any]) -> None:
    """Mirror the just-refreshed bundle to the launcher's durable store.

    The cloud launcher's container filesystem is ephemeral, so the runtime's
    ``save_tokens(...)`` only survives the lifetime of the spawn. Without this
    callback, the launcher's PgSecretStore would keep the original (now
    possibly rotated-invalid) refresh token, and the *next* spawn would
    pre-materialize a dead token and silently log the user out.

    Activated when both ``OMNI_CODEX_REFRESH_URL`` and ``OMNI_RUNTIME_TOKEN``
    are set (cloud spawn). Local/Electron leave them unset → no-op. Failures
    are logged and swallowed: the local save above still happened, so the
    current spawn keeps working.
    """
    url = os.getenv("OMNI_CODEX_REFRESH_URL")
    runtime_token = os.getenv("OMNI_RUNTIME_TOKEN")
    if not url or not runtime_token:
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {runtime_token}",
                    "Content-Type": "application/json",
                },
                json=tokens,
            )
        if resp.status_code >= 400:
            print(
                f"[codex] launcher persist callback returned {resp.status_code}; "
                "next spawn may see stale refresh token"
            )
    except Exception as e:  # network blip, launcher down, etc.
        print(f"[codex] launcher persist callback failed: {e}")


def _ensure_fresh_sync() -> Optional[Dict[str, Any]]:
    """Return current tokens, refreshing (synchronously) if expired.

    Returns ``None`` when not signed in. Used by the synchronous discovery path;
    the request-time path uses the async token manager instead.
    """
    import httpx

    tokens = load_tokens()
    if not tokens or not tokens.get("refresh"):
        return None
    expires = tokens.get("expires")
    fresh = isinstance(expires, (int, float)) and (expires - _EXPIRY_SKEW_MS) > (time.time() * 1000)
    if fresh:
        return tokens
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{ISSUER}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh"],
                "client_id": CLIENT_ID,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Codex token refresh failed: {resp.status_code}")
    refreshed = _normalize_token_response(resp.json())
    if "account_id" not in refreshed and tokens.get("account_id"):
        refreshed["account_id"] = tokens["account_id"]
    try:
        save_tokens(refreshed)
    except OSError:
        pass
    return refreshed


def _model_entry(m: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Codex backend model descriptor to a model-config entry."""
    entry: Dict[str, Any] = {
        "model": m["slug"],
        "label": m.get("display_name") or m["slug"],
        # store=False + encrypted reasoning, matching the API gpt-5.x models.
        "model_settings": {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]},
        },
    }
    ctx = m.get("context_window")
    if isinstance(ctx, int):
        entry["max_input_tokens"] = ctx
    # Carry the backend's default reasoning level when it's one we model
    # (the backend may also report "none").
    default_reasoning = m.get("default_reasoning_level")
    if default_reasoning in ("low", "medium", "high", "xhigh"):
        entry["reasoning"] = default_reasoning
    return entry


def list_codex_models(force: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
    """Fetch the account's available Codex models, keyed by slug.

    Returns ``None`` when not signed in or the fetch fails — callers should
    fall back to any static seed. Cached per-account for ``_MODELS_TTL_SECONDS``.
    Hidden models (``visibility != "list"``) are dropped (e.g. the internal
    auto-review model).
    """
    import httpx

    tokens = load_tokens()
    if not tokens or not tokens.get("refresh"):
        return None

    account = tokens.get("account_id") or "default"
    cached = _models_cache.get(account)
    if cached and not force and time.time() < cached["expires_at"]:
        return cached["models"]

    try:
        fresh = _ensure_fresh_sync()
        if not fresh:
            return None
        headers = {"Authorization": f"Bearer {fresh['access']}", "originator": ORIGINATOR}
        if fresh.get("account_id"):
            headers["ChatGPT-Account-Id"] = fresh["account_id"]
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{CODEX_BASE_URL}/models",
                params={"client_version": CODEX_CLIENT_VERSION},
                headers=headers,
            )
        if resp.status_code != 200:
            return None
        items = resp.json().get("models") or []
        models = {
            m["slug"]: _model_entry(m)
            for m in items
            if m.get("slug") and m.get("visibility") == "list"
        }
    except Exception:
        return None

    _models_cache[account] = {"models": models, "expires_at": time.time() + _MODELS_TTL_SECONDS}
    return models


def device_login(poll: bool = True) -> Dict[str, Any]:
    """Run the ChatGPT device-authorization flow and store the tokens.

    Prints the user code + URL, then polls until the user authorizes. Returns
    the stored token dict. Synchronous (CLI use).
    """
    import httpx

    ua = {"User-Agent": f"omniagents/{ORIGINATOR}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        start = client.post(
            f"{ISSUER}/api/accounts/deviceauth/usercode",
            headers=ua,
            json={"client_id": CLIENT_ID},
        )
        if start.status_code != 200:
            raise RuntimeError(f"Failed to start device auth: {start.status_code}")
        info = start.json()
        device_auth_id = info["device_auth_id"]
        user_code = info["user_code"]
        interval = max(int(info.get("interval") or 5), 1)

        print(f"\nVisit {ISSUER}/codex/device and enter code: {user_code}\n")
        if not poll:
            return {"device_auth_id": device_auth_id, "user_code": user_code}

        while True:
            resp = client.post(
                f"{ISSUER}/api/accounts/deviceauth/token",
                headers=ua,
                json={"device_auth_id": device_auth_id, "user_code": user_code},
            )
            if resp.status_code == 200:
                data = resp.json()
                token_resp = client.post(
                    f"{ISSUER}/oauth/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "authorization_code",
                        "code": data["authorization_code"],
                        "redirect_uri": f"{ISSUER}/deviceauth/callback",
                        "client_id": CLIENT_ID,
                        "code_verifier": data["code_verifier"],
                    },
                )
                if token_resp.status_code != 200:
                    raise RuntimeError(f"Token exchange failed: {token_resp.status_code}")
                tokens = _normalize_token_response(token_resp.json())
                save_tokens(tokens)
                return tokens
            if resp.status_code not in (403, 404):
                raise RuntimeError(f"Device authorization failed: {resp.status_code}")
            time.sleep(interval + 3)


# --------------------------------------------------------------------------
# Refreshing AsyncOpenAI client
# --------------------------------------------------------------------------


class _CodexTokenManager:
    """Holds the live token set and refreshes it under a single-flight lock.

    Mutates the on-disk store on refresh so a fresh access token survives a
    restart and is visible to other processes sharing the config dir.
    """

    def __init__(self, tokens: Dict[str, Any]) -> None:
        self._tokens = dict(tokens)
        self._lock = asyncio.Lock()

    @property
    def account_id(self) -> Optional[str]:
        return self._tokens.get("account_id")

    def _expired(self) -> bool:
        expires = self._tokens.get("expires")
        if not isinstance(expires, (int, float)):
            return True
        return (expires - _EXPIRY_SKEW_MS) < (time.time() * 1000)

    async def access_token(self) -> str:
        if not self._expired():
            return self._tokens["access"]
        async with self._lock:
            # Re-check inside the lock: another request may have refreshed.
            if not self._expired():
                return self._tokens["access"]
            refreshed = await _refresh_async(self._tokens["refresh"])
            # Preserve account_id if the refresh response omitted it.
            if "account_id" not in refreshed and self._tokens.get("account_id"):
                refreshed["account_id"] = self._tokens["account_id"]
            self._tokens = refreshed
            try:
                save_tokens(refreshed)
            except OSError:
                # Best-effort persistence; an ephemeral config dir (sandbox)
                # just means we refresh again next restart.
                pass
            await _persist_refresh_to_launcher(refreshed)
            return self._tokens["access"]


def build_codex_openai_client(tokens: Optional[Dict[str, Any]] = None) -> Any:
    """Build an ``AsyncOpenAI`` that talks to the Codex backend.

    Every request gets a freshly refreshed bearer token and the account-id
    header via an httpx request hook, so the client is safe to hold for the
    full lifetime of a serve process.

    Returns ``None`` when the caller isn't signed in to Codex.
    """
    import httpx
    from openai import AsyncOpenAI

    tokens = tokens or load_tokens()
    if not tokens or not tokens.get("refresh"):
        return None

    manager = _CodexTokenManager(tokens)

    async def _inject_auth(request: "httpx.Request") -> None:
        access = await manager.access_token()
        request.headers["Authorization"] = f"Bearer {access}"
        if manager.account_id:
            request.headers["ChatGPT-Account-Id"] = manager.account_id
        request.headers["originator"] = ORIGINATOR

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        event_hooks={"request": [_inject_auth]},
    )

    # api_key is required to be non-empty but is overwritten per-request by the
    # hook above; never sent as-is.
    return AsyncOpenAI(
        api_key="codex-oauth",
        base_url=CODEX_BASE_URL,
        http_client=http_client,
    )


def register_codex_provider() -> None:
    """Register the ``openai-oauth`` model-provider builder with run_options.

    The per-run provider is built by ``run_options.build_model_provider`` from
    the model's ``type``; this injects a builder that returns an
    ``OpenAIProvider`` backed by the refreshing Codex client. Idempotent and
    safe to call before sign-in (the builder returns ``None`` until tokens
    exist, then builds on the next run). Auto-called on ``import omniagents``.
    """
    try:
        from omniagents.core.agents.run_options import register_model_provider_builder
    except Exception:
        return

    cached: Dict[str, Any] = {}

    def _builder(_config: Dict[str, Any]) -> Optional[Any]:
        from agents.models.openai_provider import OpenAIProvider

        client = cached.get("client")
        if client is None:
            client = build_codex_openai_client()
            if client is None:
                return None  # not signed in yet — retry on a later run
            cached["client"] = client
        return OpenAIProvider(openai_client=client)

    register_model_provider_builder("openai-oauth", _builder)
