"""omni-code import surface for platform-governed model configuration.

The implementation lives in the framework now: the policy-to-governed-file
application (policy cache, model_providers/allow_user_models/default_model
extraction, governed write) is
``omniagents.core.models.governed.apply_platform_model_governance``, and
conventional credential vending is
``omniagents.core.providers.platform.credentials.fetch_provider_credential``
— both default to omni-code's identity (config dir, slug) through the
product info installed by the ``omni_code`` package import.

This module exists only for import-surface stability: omni-code's call
sites (the models CLI preflight, tests) keep importing from
``omni_code.platform_models``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Re-export the API omni-code uses.
from omniagents.core.models.governed import (  # noqa: F401
    apply_platform_model_governance,
    fetch_and_cache_platform_policy as fetch_and_cache_policy,
    get_cached_platform_policy as get_platform_policy_cache,
)
from omniagents.core.providers.platform.credentials import (  # noqa: F401
    fetch_provider_credential,
)


def load_governed_config() -> Optional[Dict[str, Any]]:
    """Load the governed model config if it exists (framework contract)."""
    from omniagents.core.config import product_config_dir
    from omniagents.core.models import load_governed_config as _load_governed_config

    return _load_governed_config(product_config_dir())
