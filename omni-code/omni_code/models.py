"""Model configuration for Omni Code.

A thin product binding over the framework's
:class:`omniagents.core.models.ModelConfigStore`: the store owns the
``models.json`` schema, the layered merge (product defaults → platform
governance → user config), ref parsing, CRUD, and runtime resolution.
This module supplies only omni-code's parts — the config directory, the
default model catalog (``default_models.py``), and the platform/codex
integration hooks — and re-exports the historical function API bound to
a singleton store.

Model references: unprefixed names ("gpt-5.1") imply the default
provider profile "openai"; other providers use
"<provider_name>/<model_name>" ("azure-prod/gpt-5.2").
"""

from typing import Any, Dict, Optional

from omniagents.core.models import (
    MODELS_CONFIG_VERSION,
    ModelConfigStore,
    litellm_api_key_env,
)

from omni_code.config import get_config_dir
from omni_code.default_models import (
    get_default_model_name_fallback,
    get_default_providers,
)

__all__ = [
    "MODELS_CONFIG_VERSION",
    "DEFAULT_PROVIDER_NAME",
    "litellm_api_key_env",
]

DEFAULT_PROVIDER_NAME = "openai"


def _governed_model_config() -> Optional[Dict[str, Any]]:
    """Management-plane model governance — only when authenticated.

    A stale governed config on disk must not override user settings when
    the user is not connected to the management plane. When credentials
    exist but the governed cache is missing, re-apply governance once.
    """
    try:
        from omni_code.platform import is_authenticated
        from omni_code.platform_models import (
            apply_platform_model_governance,
            load_governed_config,
        )

        if not is_authenticated():
            return None
        governed = load_governed_config()
        if not governed:
            apply_platform_model_governance()
            governed = load_governed_config()
        return governed
    except Exception:
        return None


def _codex_models() -> Optional[Dict[str, Dict[str, Any]]]:
    from omni_code.codex_auth import list_codex_models

    return list_codex_models()


def _codex_authenticated() -> bool:
    from omni_code.codex_auth import is_authenticated

    return is_authenticated()


def _platform_credential(ref: str) -> Optional[Dict[str, Any]]:
    from omni_code.platform_models import fetch_provider_credential

    return fetch_provider_credential(ref)


_STORE = ModelConfigStore(
    config_dir=get_config_dir,
    default_providers=get_default_providers,
    default_model_fallback=get_default_model_name_fallback,
    governed_config=_governed_model_config,
    oauth_models=_codex_models,
    oauth_authenticated=_codex_authenticated,
    credential_resolver=_platform_credential,
    default_provider_name=DEFAULT_PROVIDER_NAME,
)

# Install as the process-wide registry the builtin model-picker server
# functions (/models, /model, /reasoning, /voice-model*) read.
from omniagents.core.models import set_model_store  # noqa: E402

set_model_store(
    _STORE, setup_hint="No model configured. Run `omni model setup` to add one."
)

# The historical module-level function API, bound to the singleton store.
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
