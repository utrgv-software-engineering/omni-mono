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
from typing import Any, Dict, Optional

logger = logging.getLogger("omni_code.platform_models")

# Process-cached credential provider (fetch/cache logic lives in the
# framework; this module only wires omni-code's platform auth into it).
_provider: Optional[Any] = None


def _get_credential_provider() -> Optional[Any]:
    """Build (once) the framework credential provider for this process.

    In container mode, the launcher injects OMNI_SESSION_ID /
    OMNI_RUNTIME_TOKEN. In local mode, a lightweight session is
    bootstrapped on demand — ensure_platform_session exports those env
    vars so the runtime token is picked up below.
    """
    global _provider
    if _provider is not None:
        return _provider

    from omni_code.platform import get_platform_url, get_access_token
    from omniagents.core.providers.platform.credentials import (
        PlatformCredentialProvider,
        ensure_platform_session,
    )

    platform_url = get_platform_url()
    token = get_access_token()
    if not platform_url or not token:
        logger.warning("Cannot fetch credential: no platform URL or token available")
        return None

    session_id = os.getenv("OMNI_SESSION_ID")
    if not session_id:
        session = ensure_platform_session(platform_url, token, agent="omni_code")
        session_id = session["session_id"] if session else None
    if not session_id:
        logger.warning(
            "Cannot fetch credential: unable to establish session with platform"
        )
        return None

    # After session creation, use the runtime token (not the user access
    # token) since the credential endpoint requires runtime auth.
    runtime_token = os.getenv("OMNI_RUNTIME_TOKEN") or token
    _provider = PlatformCredentialProvider(platform_url, runtime_token, session_id)
    return _provider


def fetch_provider_credential(credential_ref: str) -> Optional[Dict[str, Any]]:
    """Fetch a provider credential from the management plane.

    Uses the credential_ref path from the platform policy to retrieve
    an access token or API key for a model provider. Results are cached
    in memory and refreshed 60 seconds before expiry (see the framework's
    PlatformCredentialProvider).

    Args:
        credential_ref: Relative URL path, e.g. "/api/v1/sessions/{session_id}/credentials/42"

    Returns:
        Credential dict on success, None on failure.
    """
    provider = _get_credential_provider()
    if provider is None:
        return None
    return provider.get_credential_by_ref_sync(credential_ref)


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
    from omni_code.platform import (
        fetch_policy,
        get_access_token,
        get_platform_url,
        load_credentials,
        save_credentials,
    )

    policy = fetch_policy()
    if policy is None:
        return None

    # Cache in credentials file
    creds = load_credentials() or {}
    creds["cached_policy"] = policy
    creds["platform_url"] = get_platform_url()
    creds["access_token"] = get_access_token()
    save_credentials(creds)
    return policy


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

    # The write (provider stamping, markers, 0600 perms) is the
    # framework's; only the policy-key extraction above is omni-code's.
    from omniagents.core.models import write_governed_config

    from omni_code.config import get_config_dir

    write_governed_config(
        get_config_dir(),
        platform_providers,
        default_model,
        allow_user_models=allow_user_models,
    )
    if not allow_user_models:
        # Platform providers replace user config entirely
        logger.info(
            "Model governance: enforced (providers: %s, default: %s)",
            list(platform_providers.keys()),
            default_model,
        )
    else:
        # Platform providers are the base, user config merges on top
        logger.info(
            "Model governance: advisory (platform base + user overrides)",
        )


def load_governed_config() -> Optional[Dict[str, Any]]:
    """Load the governed model config if it exists (framework contract)."""
    from omniagents.core.models import load_governed_config as _load_governed_config

    from omni_code.config import get_config_dir

    return _load_governed_config(get_config_dir())
