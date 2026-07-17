"""Platform credential management for Omni Code.

Stores and retrieves management plane credentials from
~/.config/omni_code/platform.json. Used by both the CLI
(omni auth login) and the omniagents platform providers
(auto-activated when credentials are present).

Credential file format:
{
  "platform_url": "https://omni-platform.example.edu",
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": {"id": 1, "email": "...", "name": "...", "domains": [...]},
  "expires_at": "2026-04-09T..."
}
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from omni_code.config import get_config_dir


logger = logging.getLogger(__name__)

PLATFORM_CRED_FILE = "platform.json"

# The management plane scopes policy to a project slug.
POLICY_PROJECT_SLUG = "omni_code"


def fetch_policy(*, refresh_on_401: bool = True) -> Optional[Dict[str, Any]]:
    """Fetch this product's policy from the management plane. No caching.

    The single policy client — governance (fail-closed sandbox decisions)
    and model governance both call this instead of carrying their own
    copies of the HTTP dance. On 401 the token is refreshed once before
    giving up.
    """
    try:
        import requests

        url = get_platform_url()
        token = get_access_token()
        if not url or not token:
            return None

        def _get(tok: str):
            return requests.get(
                f"{url}/api/v1/policy/{POLICY_PROJECT_SLUG}",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=10,
            )

        resp = _get(token)
        if resp.status_code == 401 and refresh_on_401:
            new_token = refresh_access_token()
            if new_token:
                resp = _get(new_token)
        if resp.ok:
            return resp.json()
        if resp.status_code == 401:
            logger.warning("Platform token expired — run: omni auth login")
        elif resp.status_code == 403:
            logger.warning(
                "Not entitled to the %s project — contact your admin",
                POLICY_PROJECT_SLUG,
            )
        return None
    except Exception as e:
        logger.debug("Policy fetch failed: %s", e)
        return None


def get_platform_url() -> Optional[str]:
    """Return the platform URL from env or stored credentials."""
    url = os.getenv("OMNI_PLATFORM_URL")
    if url:
        return url.rstrip("/")
    creds = load_credentials()
    if creds:
        return creds.get("platform_url")
    return None


def get_access_token() -> Optional[str]:
    """Return the access token from env or stored credentials."""
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


def load_credentials() -> Optional[Dict[str, Any]]:
    """Load stored platform credentials."""
    path = get_config_dir() / PLATFORM_CRED_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(creds: Dict[str, Any]) -> None:
    """Save platform credentials to disk."""
    path = get_config_dir() / PLATFORM_CRED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    path.chmod(0o600)


def clear_credentials() -> None:
    """Remove stored platform credentials."""
    path = get_config_dir() / PLATFORM_CRED_FILE
    if path.exists():
        path.unlink()


def device_code_login(platform_url: str) -> Dict[str, Any]:
    """Run the device code OAuth flow against the management plane.

    1. POST /api/v1/auth/device_code → get device_code + user_code
    2. User visits verification_uri and enters user_code
    3. Poll POST /api/v1/auth/token until authenticated
    4. Return credentials dict
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
    """Refresh the access token using the stored refresh token."""
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
