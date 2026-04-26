"""Platform-governed model configuration.

When authenticated to the management plane, the platform policy may
include approved model providers and a default model. This module
integrates platform model governance into the local model config system.

Behavior:
- allow_user_models=true  → platform providers are merged WITH user config
                             (user config can add models but platform provides the base)
- allow_user_models=false → platform providers REPLACE user config entirely
                             (user can't use unapproved providers/models)

The platform policy is fetched once at startup and cached.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("omni_code.platform_models")

# Module-level credential cache: {credential_ref: {"data": {...}, "expires_at": float}}
_credential_cache: Dict[str, Dict[str, Any]] = {}

# Cached local session (created on demand when no OMNI_SESSION_ID is set)
_local_session: Optional[Dict[str, Any]] = None


def _ensure_local_session(platform_url: str, token: str) -> Optional[str]:
    """Create a lightweight session on the platform for local credential vending.

    In container mode, the launcher calls compute/start which creates a session.
    In local mode, omni-code calls sessions/bootstrap directly to get a session_id
    and runtime_token without provisioning any compute.

    The session is cached for the process lifetime.
    """
    global _local_session

    if _local_session:
        return _local_session.get("session_id")

    import requests

    try:
        resp = requests.post(
            f"{platform_url}/api/v1/sessions/bootstrap",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"agent": "omni_code"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            _local_session = {
                "session_id": data["session_id"],
                "session_token": data["session_token"],
            }
            # Use the session token for subsequent credential requests
            # (it has the right audience/claims for the runtime API)
            os.environ["OMNI_SESSION_ID"] = data["session_id"]
            os.environ["OMNI_RUNTIME_TOKEN"] = data["session_token"]
            logger.info("Local session created: %s", data["session_id"])
            return data["session_id"]
        else:
            logger.warning("Session bootstrap failed (HTTP %d): %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Session bootstrap error: %s", e)

    return None


def fetch_provider_credential(credential_ref: str) -> Optional[Dict[str, Any]]:
    """Fetch a provider credential from the management plane.

    Uses the credential_ref path from the platform policy to retrieve
    an access token or API key for a model provider.

    Results are cached in memory and refreshed 60 seconds before expiry.

    Args:
        credential_ref: Relative URL path, e.g. "/api/v1/sessions/{id}/credentials/42"

    Returns:
        Credential dict on success, None on failure.
    """
    global _credential_cache

    # Check cache first
    cached = _credential_cache.get(credential_ref)
    if cached:
        expires_at = cached.get("expires_at", 0)
        # Refresh 60 seconds before expiry
        if time.time() < (expires_at - 60):
            return cached["data"]

    from omni_code.platform import get_platform_url, get_access_token

    platform_url = get_platform_url()
    token = get_access_token()
    if not platform_url or not token:
        logger.warning("Cannot fetch credential: no platform URL or token available")
        return None

    import requests

    # Resolve {session_id} placeholder in credential_ref.
    # In container mode, OMNI_SESSION_ID is injected. In local mode,
    # we create a lightweight session on demand via sessions/bootstrap.
    session_id = os.getenv("OMNI_SESSION_ID") or _ensure_local_session(platform_url, token)
    if not session_id:
        logger.warning("Cannot fetch credential: unable to establish session with platform")
        return None

    # After session creation, use the runtime token (not the user access token)
    # since the credential endpoint requires runtime auth
    runtime_token = os.getenv("OMNI_RUNTIME_TOKEN") or token

    resolved_ref = credential_ref.replace("{session_id}", session_id)
    url = f"{platform_url}{resolved_ref}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {runtime_token}"},
            timeout=10,
        )
        if not resp.ok:
            if resp.status_code == 401:
                logger.warning("Platform token expired fetching credential — run: omni auth login")
            elif resp.status_code == 403:
                logger.warning("Not entitled to credential: %s", credential_ref)
            else:
                logger.warning("Credential fetch failed (HTTP %d): %s", resp.status_code, credential_ref)
            return None

        cred = resp.json()

        # Determine cache expiry from the credential's expires_at field
        expires_at_str = cred.get("expires_at")
        if expires_at_str:
            try:
                dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                expires_at = dt.timestamp()
            except (ValueError, TypeError):
                # Default to 10 minutes if we can't parse
                expires_at = time.time() + 600
        else:
            expires_at = time.time() + 600

        _credential_cache[credential_ref] = {
            "data": cred,
            "expires_at": expires_at,
        }
        return cred

    except requests.RequestException as e:
        logger.warning("Credential fetch error for %s: %s", credential_ref, e)
        return None
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Invalid credential response for %s: %s", credential_ref, e)
        return None


def get_platform_policy_cache() -> Optional[Dict[str, Any]]:
    """Read cached platform policy from disk (written by bootstrap or policy hook)."""
    # Check env first (container injection)
    if os.getenv("OMNI_PLATFORM_POLICY"):
        try:
            return json.loads(os.environ["OMNI_PLATFORM_POLICY"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Check stored credentials for cached policy
    from omni_code.platform import load_credentials

    creds = load_credentials()
    if creds and creds.get("cached_policy"):
        return creds["cached_policy"]

    return None


def fetch_and_cache_policy() -> Optional[Dict[str, Any]]:
    """Fetch policy from management plane and cache it."""
    from omni_code.platform import get_platform_url, get_access_token, load_credentials, save_credentials

    url = get_platform_url()
    token = get_access_token()
    if not url or not token:
        return None

    import requests

    try:
        # Use the policy endpoint with project slug
        resp = requests.get(
            f"{url}/api/v1/policy/omni_code",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.ok:
            policy = resp.json()
            # Cache in credentials file
            creds = load_credentials() or {}
            creds["cached_policy"] = policy
            creds["platform_url"] = url
            creds["access_token"] = token
            save_credentials(creds)
            return policy
        elif resp.status_code == 401:
            logger.warning("Platform token expired — run: omni auth login")
        elif resp.status_code == 403:
            logger.warning("Not entitled to omni_code project — contact your admin")
    except Exception as e:
        logger.debug("Policy fetch failed: %s", e)

    return None


def apply_platform_model_governance() -> None:
    """Apply platform model governance to the local model config.

    Called during CLI startup, after models config is loaded.
    Modifies the model resolution to respect platform-approved providers.
    """
    policy = get_platform_policy_cache()
    if not policy:
        # Try fetching fresh
        policy = fetch_and_cache_policy()
    if not policy:
        return

    project = policy.get("project")
    if not project:
        return

    # model_providers is a top-level policy key (not nested under project)
    platform_providers = policy.get("model_providers") or project.get("model_providers")
    if not platform_providers:
        return

    allow_user_models = project.get("allow_user_models", True)
    default_model = project.get("default_model")

    # Mark each provider as governed so downstream code knows it came from the platform
    for provider_config in platform_providers.values():
        if isinstance(provider_config, dict):
            provider_config["_governed"] = True

    from omni_code.models import load_models_config, get_models_config_path

    if not allow_user_models:
        # Platform providers replace user config entirely
        _write_governed_config(platform_providers, default_model)
        logger.info(
            "Model governance: enforced (providers: %s, default: %s)",
            list(platform_providers.keys()),
            default_model,
        )
    else:
        # Platform providers are the base, user config merges on top
        _merge_platform_base(platform_providers, default_model)
        logger.info(
            "Model governance: advisory (platform base + user overrides)",
        )


def _write_governed_config(
    platform_providers: Dict[str, Any], default_model: Optional[str]
) -> None:
    """Replace the effective model config with platform-governed providers."""
    from omni_code.models import get_models_config_path, MODELS_CONFIG_VERSION

    # Write to a separate governed config that takes precedence
    governed_path = get_models_config_path().parent / "models.governed.json"
    config = {
        "version": MODELS_CONFIG_VERSION,
        "default": default_model,
        "voice_default": None,
        "providers": platform_providers,
        "_governed": True,
        "_allow_user_models": False,
    }
    governed_path.parent.mkdir(parents=True, exist_ok=True)
    governed_path.write_text(json.dumps(config, indent=2))
    governed_path.chmod(0o600)


def _merge_platform_base(
    platform_providers: Dict[str, Any], default_model: Optional[str]
) -> None:
    """Merge platform providers as the base, user config on top."""
    from omni_code.models import get_models_config_path, MODELS_CONFIG_VERSION

    governed_path = get_models_config_path().parent / "models.governed.json"
    config = {
        "version": MODELS_CONFIG_VERSION,
        "default": default_model,
        "voice_default": None,
        "providers": platform_providers,
        "_governed": True,
        "_allow_user_models": True,
    }
    governed_path.parent.mkdir(parents=True, exist_ok=True)
    governed_path.write_text(json.dumps(config, indent=2))
    governed_path.chmod(0o600)


def load_governed_config() -> Optional[Dict[str, Any]]:
    """Load the governed model config if it exists."""
    from omni_code.models import get_models_config_path

    governed_path = get_models_config_path().parent / "models.governed.json"
    if not governed_path.exists():
        return None
    try:
        data = json.loads(governed_path.read_text())
        if data.get("_governed"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None
