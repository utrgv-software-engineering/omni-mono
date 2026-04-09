"""Auto-detection and injection of platform credentials into security config.

When the management plane is available (via env var or stored credentials),
this module merges platform provider config into the agent's security_config
so that auth, audit, and policy providers activate automatically.

Detection order:
1. OMNI_PLATFORM_URL + OMNI_RUNTIME_TOKEN env vars (container injection)
2. ~/.config/omni_code/platform.json (CLI login)

If neither is present, this is a no-op — open-source mode.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("omniagents.platform.bootstrap")


def _find_stored_credentials() -> Optional[Dict[str, Any]]:
    """Look for stored platform credentials from the omni CLI."""
    # Standard config locations (matching omni_code.config.get_config_dir)
    candidates = []

    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "omni_code" / "platform.json")

    candidates.append(Path.home() / ".config" / "omni_code" / "platform.json")

    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "OmniCode" / "platform.json")

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict) and data.get("access_token"):
                    return data
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _detect_platform() -> Optional[Dict[str, str]]:
    """Detect platform URL and access token from env or stored credentials.

    Returns dict with platform_url and access_token, or None.
    """
    # Priority 1: env vars (container / launcher injection)
    platform_url = os.getenv("OMNI_PLATFORM_URL")
    token = os.getenv("OMNI_RUNTIME_TOKEN") or os.getenv("OMNI_ACCESS_TOKEN")

    if platform_url and token:
        return {
            "platform_url": platform_url.rstrip("/"),
            "access_token": token,
            "session_id": os.getenv("OMNI_SESSION_ID", ""),
        }

    # Priority 2: stored credentials from `omni auth login`
    creds = _find_stored_credentials()
    if creds and creds.get("platform_url") and creds.get("access_token"):
        return {
            "platform_url": creds["platform_url"].rstrip("/"),
            "access_token": creds["access_token"],
            "session_id": "",
        }

    return None


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

    # Auth provider — validate tokens from the platform
    if "auth" not in providers or providers["auth"].get("type", "none") == "none":
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

    # Content scanning — enable baseline scanning when connected to platform
    if "content_scanner" not in providers or providers["content_scanner"].get("type", "none") == "none":
        providers["content_scanner"] = {"type": "regex"}

    sec["providers"] = providers
    sec["audit"] = audit
    spec.security_config = sec
