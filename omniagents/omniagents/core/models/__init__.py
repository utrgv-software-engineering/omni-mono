"""Model configuration: per-user ``models.json`` store and runtime resolution."""

from typing import Optional

from .store import (
    MODELS_CONFIG_VERSION,
    ModelConfigStore,
    expand_env_vars,
    litellm_api_key_env,
    normalize_model_name,
)
from .governed import (
    GOVERNED_CONFIG_FILENAME,
    governed_config_path,
    load_governed_config,
    write_governed_config,
)

__all__ = [
    "MODELS_CONFIG_VERSION",
    "ModelConfigStore",
    "expand_env_vars",
    "litellm_api_key_env",
    "normalize_model_name",
    "GOVERNED_CONFIG_FILENAME",
    "governed_config_path",
    "load_governed_config",
    "write_governed_config",
    "resolve_model_runtime",
    "resolve_service_model_runtime",
    "provider_from_runtime",
    "set_model_store",
    "model_store",
    "model_setup_hint",
]


# Process-wide active store: the seam the builtin model-picker server
# functions read. Products construct their ModelConfigStore and install
# it here (typically at import time of their models module).
_ACTIVE_STORE: Optional[ModelConfigStore] = None
_SETUP_HINT: str = "No model configured."


def set_model_store(store: ModelConfigStore, *, setup_hint: Optional[str] = None) -> None:
    """Install *store* as the process-wide model registry.

    *setup_hint* is the user-facing sentence shown when no model is
    configured (e.g. "Run `omni model setup` to add one.").
    """
    global _ACTIVE_STORE, _SETUP_HINT
    _ACTIVE_STORE = store
    if setup_hint:
        _SETUP_HINT = setup_hint


def model_store() -> Optional[ModelConfigStore]:
    """The installed process-wide model registry, or None."""
    return _ACTIVE_STORE


def model_setup_hint() -> str:
    return _SETUP_HINT


# Imported after set_model_store/model_store exist: runtime.py resolves the
# process-wide store through this module when no explicit store is passed.
from .runtime import (  # noqa: E402
    provider_from_runtime,
    resolve_model_runtime,
    resolve_service_model_runtime,
)
