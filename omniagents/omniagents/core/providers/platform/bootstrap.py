"""Auto-detection and injection of platform credentials into security config.

When the management plane is available (via env var or stored credentials),
this module merges platform provider config into the agent's security_config
so that auth, audit, and policy providers activate automatically.

Detection order:
1. OMNI_PLATFORM_URL + OMNI_RUNTIME_TOKEN env vars (container injection)
2. Product-stored credentials, via the loader installed with
   ``set_platform_credentials_loader`` (e.g. the product CLI's login
   flow writing ``platform.json`` under its own config dir)

If neither is present, this is a no-op — open-source mode. The framework
deliberately knows no product config paths; products own their credential
store and install a loader for it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("omniagents.platform.bootstrap")

# Product-installed loader for stored platform credentials. Returns the
# stored credential dict (must carry platform_url + access_token to count)
# or None. Installed at import time from the product's context/install
# module, which precedes AgentService construction in every process shape.
_stored_credentials_loader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None


def set_platform_credentials_loader(
    loader: Optional[Callable[[], Optional[Dict[str, Any]]]],
) -> None:
    """Install the product's stored-credentials loader.

    Without one, platform detection is env-var-only.
    """
    global _stored_credentials_loader
    _stored_credentials_loader = loader


def _find_stored_credentials() -> Optional[Dict[str, Any]]:
    """Load stored platform credentials via the product-installed loader."""
    if _stored_credentials_loader is None:
        return None
    try:
        data = _stored_credentials_loader()
    except Exception as exc:
        logger.warning("Platform credentials loader failed: %s", exc)
        return None
    if isinstance(data, dict) and data.get("access_token"):
        return data
    return None


def _detect_platform() -> Optional[Dict[str, str]]:
    """Detect platform URL and access token from env or stored credentials.

    Delegates the env-then-stored priority chain to
    ``platform.client.resolve_platform``. A product-installed loader
    (``set_platform_credentials_loader``) takes precedence for the stored
    step; without one, the client's own credential store is consulted
    (which products may have re-bound via ``set_credential_store_dir``).

    Returns dict with platform_url, access_token and session_id, or None.
    """
    from omniagents.core.providers.platform import client

    loader = (
        _find_stored_credentials if _stored_credentials_loader is not None else None
    )
    return client.resolve_platform(credentials_loader=loader)


def maybe_inject_platform_config(spec: Any) -> None:
    """Inject platform provider config into the agent spec if credentials exist.

    Merges platform config into security_config without overwriting any
    existing user-specified config. If the user already configured
    providers.auth or audit.backend in their project.yml, those take
    precedence.
    """
    detected = _detect_platform()
    if not detected:
        return

    platform_url = detected["platform_url"]
    access_token = detected["access_token"]
    session_id = detected["session_id"]

    logger.info("Platform detected: %s", platform_url)

    # Set env vars so downstream providers can read them
    os.environ.setdefault("OMNI_PLATFORM_URL", platform_url)
    os.environ.setdefault("OMNI_ACCESS_TOKEN", access_token)
    if session_id:
        os.environ.setdefault("OMNI_SESSION_ID", session_id)

    sec = getattr(spec, "security_config", None) or {}

    # Don't override if user explicitly configured security
    providers = sec.setdefault("providers", {})
    audit = sec.setdefault("audit", {})

    # Auth provider — only activate for container/server mode where the
    # WebSocket is exposed externally. In local/TUI mode, the client connects
    # to localhost without a token and auth would block it.
    # Container mode is signaled by OMNI_SESSION_ID (set by the compute controller).
    if session_id and (
        "auth" not in providers or providers["auth"].get("type", "none") == "none"
    ):
        providers["auth"] = {
            "type": "platform",
            "platform_url": platform_url,
        }

    # Audit — ship events to the platform
    if not audit.get("enabled"):
        audit["enabled"] = True
        audit["backend"] = "remote"
        audit["platform_url"] = platform_url

    # Policy — fetch and apply policy at session start
    if "policy" not in providers:
        providers["policy"] = {
            "type": "platform",
            "platform_url": platform_url,
        }

    # Content scanning — configured by platform policy, not hardcoded
    # The policy hook applies the full scanning config at session start.
    # Bootstrap just sets a baseline so scanning is active before the
    # first policy fetch completes.
    if (
        "content_scanner" not in providers
        or providers["content_scanner"].get("type", "none") == "none"
    ):
        providers["content_scanner"] = {"type": "regex"}

    sec["providers"] = providers
    sec["audit"] = audit
    spec.security_config = sec
