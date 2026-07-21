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
    "reset_model_store",
    "ModelCatalog",
    "load_model_catalog",
    "model_catalog_path",
    "build_catalog_store",
    "install_model_catalog",
]


# Process-wide active store: the seam the builtin model-picker server
# functions read.
#
# Precedence (mirrors omniagents.core.product's identity precedence):
#
# 1. set_model_store — an explicit install (a product constructing its
#    own ModelConfigStore) always wins and is never clobbered by
#    catalog installs.
# 2. _set_default_model_store — the conventional install performed when
#    a manifest declares a model catalog (paths.model_catalog), by
#    bootstrap_product and load_project. The most recent conventional
#    install wins among themselves.
# 3. Otherwise None (product_cli falls back to a bare product-config
#    store for the reserved `model`/`provider` commands).
_ACTIVE_STORE: Optional[ModelConfigStore] = None
_DEFAULT_STORE: Optional[ModelConfigStore] = None
_SETUP_HINT: str = "No model configured."
_EXPLICIT_HINT: bool = False


def set_model_store(store: ModelConfigStore, *, setup_hint: Optional[str] = None) -> None:
    """Explicitly install *store* as the process-wide model registry.

    An explicit install always wins: later conventional (catalog)
    installs never replace it. *setup_hint* is the user-facing sentence
    shown when no model is configured (e.g. "Run `omni model setup` to
    add one.").
    """
    global _ACTIVE_STORE, _SETUP_HINT, _EXPLICIT_HINT
    _ACTIVE_STORE = store
    if setup_hint:
        _SETUP_HINT = setup_hint
        _EXPLICIT_HINT = True


def _set_default_model_store(
    store: ModelConfigStore, *, setup_hint: Optional[str] = None
) -> None:
    """Install *store* as the conventional (catalog-derived) registry.

    Read by :func:`model_store` only while no explicit
    :func:`set_model_store` has happened; among conventional installs,
    the most recent wins.
    """
    global _DEFAULT_STORE, _SETUP_HINT
    _DEFAULT_STORE = store
    if setup_hint and not _EXPLICIT_HINT:
        _SETUP_HINT = setup_hint


def model_store() -> Optional[ModelConfigStore]:
    """The process-wide model registry (explicit, else conventional), or None."""
    if _ACTIVE_STORE is not None:
        return _ACTIVE_STORE
    return _DEFAULT_STORE


def model_setup_hint() -> str:
    return _SETUP_HINT


def reset_model_store() -> None:
    """Clear both the explicit and conventional stores (tests/embedders)."""
    global _ACTIVE_STORE, _DEFAULT_STORE, _SETUP_HINT, _EXPLICIT_HINT
    _ACTIVE_STORE = None
    _DEFAULT_STORE = None
    _SETUP_HINT = "No model configured."
    _EXPLICIT_HINT = False
    from .catalog import _reset_catalog_store_cache

    _reset_catalog_store_cache()


# Imported after set_model_store/model_store exist: runtime.py resolves the
# process-wide store through this module when no explicit store is passed,
# and catalog.py installs conventional stores through the default seam.
from .runtime import (  # noqa: E402
    provider_from_runtime,
    resolve_model_runtime,
    resolve_service_model_runtime,
)
from .catalog import (  # noqa: E402
    ModelCatalog,
    build_catalog_store,
    install_model_catalog,
    load_model_catalog,
    model_catalog_path,
)
