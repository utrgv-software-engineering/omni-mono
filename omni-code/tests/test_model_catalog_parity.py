"""Parity check: the declarative catalog equals the old Python catalog.

``omni_code/default_models.py`` (the shipped provider/model dicts and
default-model fallback) and ``omni_code/models.py`` (the store
construction) collapsed into ``omni_code/models.yml``, declared via
``paths.model_catalog`` and loaded by the framework at product
bootstrap. These tests pin byte-equivalent semantics: the
catalog-loaded values must be identical to the exact dicts the deleted
module returned (snapshotted below from default_models.py at deletion),
and the installed store must serve them as its default layer.
"""

from omniagents.core.models import install_model_catalog, model_store
from omniagents.core.models.catalog import load_model_catalog
from omniagents.core.product import product_info

#: The discovered product manifest — the same path bootstrap installs
#: the catalog from; installed here so the store tests below see it.
PROJECT_MANIFEST = product_info().manifest_path
install_model_catalog(PROJECT_MANIFEST)

# --- Snapshot of the deleted omni_code/default_models.py -------------------

_REASONING_MODEL_SETTINGS = {
    "store": False,
    "extra_body": {"include": ["reasoning.encrypted_content"]},
}

OLD_DEFAULT_PROVIDERS = {
    "openai": {
        "type": "openai",
        "api_key": "${OPENAI_API_KEY}",
        "models": {
            "gpt-5.1": {
                "model": "gpt-5.1",
                "label": "GPT-5.1",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-5.1-codex": {
                "model": "gpt-5.1-codex",
                "label": "GPT-5.1 Codex",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-5.2": {
                "model": "gpt-5.2",
                "label": "GPT-5.2",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-realtime": {
                "model": "gpt-realtime",
                "label": "GPT Realtime",
                "realtime": True,
            },
        },
    },
    "codex": {
        "type": "openai-oauth",
        "models": {
            "gpt-5.2": {
                "model": "gpt-5.2",
                "label": "GPT-5.2 (ChatGPT)",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
        },
    },
}

OLD_DEFAULT_MODEL_NAME = "gpt-5.1"
OLD_DEFAULT_PROVIDER_NAME = "openai"

CATALOG_PATH = PROJECT_MANIFEST.parent / "models.yml"


def test_catalog_providers_identical_to_old_python_catalog():
    catalog = load_model_catalog(CATALOG_PATH)
    assert catalog.providers == OLD_DEFAULT_PROVIDERS


def test_catalog_defaults_identical_to_old_python_catalog():
    catalog = load_model_catalog(CATALOG_PATH)
    assert catalog.default_model == OLD_DEFAULT_MODEL_NAME
    assert catalog.default_provider == OLD_DEFAULT_PROVIDER_NAME


def test_installed_store_serves_the_catalog_as_default_layer():
    store = model_store()
    assert store is not None
    assert store._default_providers() == OLD_DEFAULT_PROVIDERS
    assert store._default_model_fallback() == OLD_DEFAULT_MODEL_NAME
    assert store.default_provider_name == OLD_DEFAULT_PROVIDER_NAME


def test_bootstrap_reinstall_keeps_the_same_store_instance():
    # Everything bound at import time must stay bound to the live store:
    # re-installing the packaged catalog (load_project does this) hands
    # back the identical instance.
    assert install_model_catalog(PROJECT_MANIFEST) is model_store()
