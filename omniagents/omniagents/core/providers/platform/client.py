"""Management-plane client: sign-in, credential store, token refresh, policy.

This is the user-facing half of the platform integration — the part a
product CLI drives (``<prog> auth login``) and governance code queries.
It owns:

- the device-code OAuth flow against the management plane
  (:func:`device_code_login`),
- the on-disk credential store (``platform.json``, owner-only) with
  :func:`load_credentials` / :func:`save_credentials` /
  :func:`clear_credentials`,
- env-then-file resolution of the platform URL and access token
  (:func:`get_platform_url` / :func:`get_access_token` /
  :func:`resolve_platform`),
- access-token refresh (:func:`refresh_access_token`), and
- the one policy client (:func:`fetch_policy`) — user-scoped
  (``/api/v1/policy/{slug}``) or session-scoped
  (``/api/v1/sessions/{id}/policy``), with a single refresh-on-401 retry
  in both scopes.

The credential store defaults to ``platform.json`` under the installed
product identity's config dir (``omniagents.core.product``) — the
framework's own ``$XDG_CONFIG_HOME/omniagents`` / ``~/.config/omniagents``
when no product is installed — and the policy scope defaults to that
identity's slug. Products that declare a ``product:`` section in
project.yml therefore need no binding at all; explicit
:func:`set_credential_store_dir` / :func:`set_product_slug` calls still
win — mirroring the codex token-store binding
(``omniagents.core.providers.codex.set_token_store_dir``).

Credential file format::

    {
      "platform_url": "https://omni-platform.example.edu",
      "access_token": "eyJ...",
      "refresh_token": "eyJ...",
      "user": {"id": 1, "email": "...", "name": "...", "domains": [...]},
      "expires_at": "2026-04-09T..."
    }
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("omniagents.platform.client")

PLATFORM_CRED_FILE = "platform.json"

DEFAULT_PRODUCT_SLUG = "omniagents"

# --------------------------------------------------------------------------
# Product binding (mirrors the codex token-store binding)
# --------------------------------------------------------------------------

_credential_store_dir: Optional[Path] = None
_product_slug: Optional[str] = None


def set_credential_store_dir(path: Any) -> None:
    """Override the directory holding ``platform.json``.

    Lets a product (e.g. omni-code) point the store at its own config dir
    so credentials written by its login flow are found. Takes effect
    immediately.
    """
    global _credential_store_dir
    _credential_store_dir = Path(path) if path is not None else None


def set_product_slug(slug: Optional[str]) -> None:
    """Explicitly bind the product slug used for user-scoped policy fetches.

    Passing a falsy value clears the explicit binding, falling back to
    the installed product identity (see :func:`get_product_slug`).
    """
    global _product_slug
    _product_slug = slug or None


def get_product_slug() -> str:
    """Return the bound product slug.

    An explicit :func:`set_product_slug` wins; otherwise the slug comes
    from the installed product identity
    (``omniagents.core.product.product_info().slug``), which is
    ``"omniagents"`` when no product is installed.
    """
    if _product_slug:
        return _product_slug
    from omniagents.core.product import product_info

    return product_info().slug or DEFAULT_PRODUCT_SLUG


def _config_dir() -> Path:
    if _credential_store_dir is not None:
        return _credential_store_dir
    from omniagents.core.product import product_info

    return product_info().config_dir()


def credential_path() -> Path:
    """Path of the credential file (may not exist yet)."""
    return _config_dir() / PLATFORM_CRED_FILE


# --------------------------------------------------------------------------
# Credential store
# --------------------------------------------------------------------------


def load_credentials() -> Optional[Dict[str, Any]]:
    """Load stored platform credentials, or ``None`` when not signed in."""
    path = credential_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(creds: Dict[str, Any]) -> None:
    """Persist platform credentials (owner-only, 0600)."""
    path = credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    path.chmod(0o600)


def clear_credentials() -> None:
    """Remove stored platform credentials."""
    path = credential_path()
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------
# Env-then-file resolution
# --------------------------------------------------------------------------


def get_platform_url() -> Optional[str]:
    """Return the platform URL from env (``OMNI_PLATFORM_URL``) or store."""
    url = os.getenv("OMNI_PLATFORM_URL")
    if url:
        return url.rstrip("/")
    creds = load_credentials()
    if creds:
        return creds.get("platform_url")
    return None


def get_access_token() -> Optional[str]:
    """Return the access token from env or stored credentials.

    Env priority: ``OMNI_RUNTIME_TOKEN`` then ``OMNI_ACCESS_TOKEN``
    (container / launcher injection), then the credential store.
    """
    token = os.getenv("OMNI_RUNTIME_TOKEN") or os.getenv("OMNI_ACCESS_TOKEN")
    if token:
        return token
    creds = load_credentials()
    if creds:
        return creds.get("access_token")
    return None


def is_authenticated() -> bool:
    """Return True if platform credentials are available."""
    return get_platform_url() is not None and get_access_token() is not None


def resolve_platform(
    *,
    credentials_loader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve platform URL + access token as one unit, env first.

    Priority:

    1. ``OMNI_PLATFORM_URL`` + (``OMNI_RUNTIME_TOKEN`` or
       ``OMNI_ACCESS_TOKEN``) env vars — both must be present (container /
       launcher injection); ``OMNI_SESSION_ID`` is carried along when set.
    2. Stored credentials — via *credentials_loader* when given (a product
       may keep its own store), otherwise this module's credential store.

    Returns ``{"platform_url", "access_token", "session_id"}`` or ``None``.
    """
    platform_url = os.getenv("OMNI_PLATFORM_URL")
    token = os.getenv("OMNI_RUNTIME_TOKEN") or os.getenv("OMNI_ACCESS_TOKEN")
    if platform_url and token:
        return {
            "platform_url": platform_url.rstrip("/"),
            "access_token": token,
            "session_id": os.getenv("OMNI_SESSION_ID", ""),
        }

    loader = credentials_loader or load_credentials
    try:
        creds = loader()
    except Exception as exc:
        logger.warning("Platform credentials loader failed: %s", exc)
        return None
    if (
        isinstance(creds, dict)
        and creds.get("platform_url")
        and creds.get("access_token")
    ):
        return {
            "platform_url": str(creds["platform_url"]).rstrip("/"),
            "access_token": creds["access_token"],
            "session_id": "",
        }
    return None


# --------------------------------------------------------------------------
# Device-code sign-in + refresh
# --------------------------------------------------------------------------


def device_code_login(platform_url: str) -> Dict[str, Any]:
    """Run the device code OAuth flow against the management plane.

    1. POST /api/v1/auth/device_code → get device_code + user_code
    2. User visits verification_uri and enters user_code
    3. Poll POST /api/v1/auth/token until authenticated
    4. Return credentials dict (caller persists via :func:`save_credentials`)
    """
    import requests

    platform_url = platform_url.rstrip("/")

    # Step 1: Get device code
    resp = requests.post(f"{platform_url}/api/v1/auth/device_code", timeout=10)
    resp.raise_for_status()
    device = resp.json()

    print(f"\nTo sign in, open: {device['verification_uri']}")
    print(f"Enter code: {device['user_code']}\n")
    print("Waiting for authentication...", end="", flush=True)

    # Step 2: Poll for token
    interval = device.get("interval", 5)
    expires_in = device.get("expires_in", 900)
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        print(".", end="", flush=True)

        resp = requests.post(
            f"{platform_url}/api/v1/auth/token",
            json={"device_code": device["device_code"]},
            timeout=10,
        )

        body = resp.json()
        status = body.get("status")

        if status == "authenticated":
            print(" done!\n")
            return {
                "platform_url": platform_url,
                "access_token": body["access_token"],
                "refresh_token": body["refresh_token"],
                "expires_at": body.get("expires_in"),
                "user": body.get("user"),
            }
        elif status == "expired":
            print("\n")
            raise RuntimeError("Device code expired. Please try again.")
        # status == "pending" → keep polling

    print("\n")
    raise RuntimeError("Timed out waiting for authentication.")


def refresh_access_token() -> Optional[str]:
    """Refresh the access token using the stored refresh token.

    On success the store is updated in place and the new access token is
    returned; on any failure returns ``None``.
    """
    creds = load_credentials()
    if not creds or not creds.get("refresh_token"):
        return None

    import requests

    platform_url = creds["platform_url"]
    try:
        resp = requests.post(
            f"{platform_url}/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {creds['refresh_token']}"},
            timeout=10,
        )
        if resp.ok:
            body = resp.json()
            creds["access_token"] = body["access_token"]
            save_credentials(creds)
            return body["access_token"]
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Policy client — one client, both scopes
# --------------------------------------------------------------------------


def fetch_policy(
    *,
    slug: Optional[str] = None,
    session_id: Optional[str] = None,
    platform_url: Optional[str] = None,
    token: Optional[str] = None,
    refresh_on_401: bool = True,
) -> Optional[Dict[str, Any]]:
    """Fetch policy from the management plane. No caching.

    The single policy client — product governance (fail-closed sandbox
    decisions, model governance) and the session policy hook both call
    this instead of carrying their own copies of the HTTP dance.

    Scope:

    - *session_id* given → session-scoped:
      ``GET /api/v1/sessions/{session_id}/policy``
    - otherwise → user-scoped: ``GET /api/v1/policy/{slug}`` where *slug*
      defaults to the bound product slug (:func:`set_product_slug`).

    *platform_url* / *token* default to env-then-file resolution. On a 401
    the stored token is refreshed once (:func:`refresh_access_token`)
    before giving up, in both scopes. Returns the policy dict or ``None``
    on any failure.
    """
    try:
        import requests

        url = (platform_url or get_platform_url() or "").rstrip("/")
        tok = token or get_access_token()
        if not url or not tok:
            return None

        if session_id:
            endpoint = f"{url}/api/v1/sessions/{session_id}/policy"
        else:
            endpoint = f"{url}/api/v1/policy/{slug or get_product_slug()}"

        def _get(t: str):
            return requests.get(
                endpoint,
                headers={"Authorization": f"Bearer {t}"},
                timeout=10,
            )

        resp = _get(tok)
        if resp.status_code == 401 and refresh_on_401:
            new_token = refresh_access_token()
            if new_token:
                resp = _get(new_token)
        if resp.ok:
            return resp.json()
        if resp.status_code == 401:
            logger.warning("Platform token expired — re-run auth login")
        elif resp.status_code == 403:
            logger.warning(
                "Not entitled to %s — contact your admin",
                (
                    f"session {session_id}"
                    if session_id
                    else f"the {slug or get_product_slug()} project"
                ),
            )
        else:
            logger.warning(
                "Policy fetch returned %d: %s",
                resp.status_code,
                (resp.text or "")[:200],
            )
        return None
    except Exception as e:
        logger.debug("Policy fetch failed: %s", e)
        return None
