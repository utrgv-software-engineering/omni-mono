"""Legacy import surface for omni-code's model store.

The catalog is data, not code: it lives in ``omni_code/models.yml``,
declared by ``paths.model_catalog`` in the packaged project.yml. The
framework parses it and installs the process-wide
:class:`omniagents.core.models.ModelConfigStore` — with the conventional
hooks (product config dir, auth-gated platform model governance, codex
OAuth discovery, platform credential vending) — during product bootstrap
(``import omni_code``). See :mod:`omniagents.core.models.catalog`.

This module remains only as the historical function API for existing
importers, bound to that installed store. New code should call
``omniagents.core.models.model_store()`` directly.
"""

from omniagents.core.models import (  # noqa: F401  — legacy re-exports
    MODELS_CONFIG_VERSION,
    litellm_api_key_env,
    model_store,
)

__all__ = [
    "MODELS_CONFIG_VERSION",
    "DEFAULT_PROVIDER_NAME",
    "litellm_api_key_env",
]

# Installed by the omni_code package import (bootstrap_product reads
# paths.model_catalog); importing this submodule imports the package
# first, so the store is always present here.
_STORE = model_store()
if _STORE is None:  # pragma: no cover — packaging bug guard
    raise RuntimeError(
        "omni-code's model catalog store is not installed; "
        "omni_code/models.yml missing from the package?"
    )

DEFAULT_PROVIDER_NAME = _STORE.default_provider_name

# The historical module-level function API, bound to the installed store.
get_models_config_path = _STORE.config_path
load_models_config = _STORE.load_config
save_models_config = _STORE.save_config
get_merged_providers = _STORE.merged_providers
normalize_model_ref = _STORE.normalize_model_ref
get_model_config = _STORE.get_model_config
get_default_model_name = _STORE.get_default_model_name
get_default_model_config = _STORE.get_default_model_config
get_voice_default_model_name = _STORE.get_voice_default_model_name
get_voice_default_model_config = _STORE.get_voice_default_model_config
is_user_model = _STORE.is_user_model
list_models = _STORE.list_models
list_text_models = _STORE.list_text_models
list_voice_models = _STORE.list_voice_models
add_model = _STORE.add_model
remove_model = _STORE.remove_model
set_default_model = _STORE.set_default_model
set_voice_default_model = _STORE.set_voice_default_model
is_user_provider = _STORE.is_user_provider
list_providers = _STORE.list_providers
get_provider = _STORE.get_provider
add_provider = _STORE.add_provider
update_provider = _STORE.update_provider
remove_provider = _STORE.remove_provider
resolve_model_for_runtime = _STORE.resolve_model_for_runtime
resolve_voice_settings_for_realtime = _STORE.resolve_voice_settings_for_realtime
apply_api_keys_to_env = _STORE.apply_api_keys_to_env
has_models_configured = _STORE.has_models_configured
