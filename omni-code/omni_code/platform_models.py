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
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("omni_code.platform_models")


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

    platform_providers = project.get("model_providers")
    if not platform_providers:
        return

    allow_user_models = project.get("allow_user_models", True)
    default_model = project.get("default_model")

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
