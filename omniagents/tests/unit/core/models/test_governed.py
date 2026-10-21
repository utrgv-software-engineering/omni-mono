"""Tests for the governed model config producer (models.governed.json)."""

import json
import os
import stat

import pytest

from omniagents.core.models import (
    GOVERNED_CONFIG_FILENAME,
    MODELS_CONFIG_VERSION,
    ModelConfigStore,
    governed_config_path,
    load_governed_config,
    write_governed_config,
)


def _platform_providers():
    return {
        "azure-gov": {
            "type": "azure",
            "api_key": "az-key",
            "base_url": "https://gov.openai.azure.com",
            "models": {"gpt-gov": {"model": "deploy-gov", "label": "Governed GPT"}},
        }
    }


def test_write_governed_config_markers_and_shape(tmp_path):
    path = write_governed_config(
        tmp_path, _platform_providers(), "azure-gov/gpt-gov", allow_user_models=False
    )
    assert path == governed_config_path(tmp_path)
    assert path.name == GOVERNED_CONFIG_FILENAME

    data = json.loads(path.read_text())
    assert data["version"] == MODELS_CONFIG_VERSION
    assert data["default"] == "azure-gov/gpt-gov"
    assert data["voice_default"] is None
    assert data["_governed"] is True
    assert data["_allow_user_models"] is False
    assert data["providers"]["azure-gov"]["_governed"] is True
    assert data["providers"]["azure-gov"]["models"]["gpt-gov"]["model"] == "deploy-gov"


def test_write_governed_config_does_not_mutate_caller_dicts(tmp_path):
    providers = _platform_providers()
    write_governed_config(tmp_path, providers, None, allow_user_models=True)
    assert "_governed" not in providers["azure-gov"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_write_governed_config_owner_only_perms(tmp_path):
    path = write_governed_config(
        tmp_path, _platform_providers(), None, allow_user_models=True
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    # Rewriting an existing file keeps the perms clamped.
    path.chmod(0o644)
    path = write_governed_config(
        tmp_path, _platform_providers(), None, allow_user_models=True
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_governed_config_replaces_previous_file(tmp_path):
    write_governed_config(
        tmp_path, _platform_providers(), "azure-gov/gpt-gov", allow_user_models=False
    )
    write_governed_config(tmp_path, {}, None, allow_user_models=True)
    data = json.loads(governed_config_path(tmp_path).read_text())
    assert data["providers"] == {}
    assert data["default"] is None
    assert data["_allow_user_models"] is True


def test_load_governed_config_roundtrip(tmp_path):
    write_governed_config(
        tmp_path, _platform_providers(), "azure-gov/gpt-gov", allow_user_models=False
    )
    data = load_governed_config(tmp_path)
    assert data is not None
    assert data["default"] == "azure-gov/gpt-gov"
    assert data["_allow_user_models"] is False


def test_load_governed_config_absent_or_unmarked(tmp_path):
    assert load_governed_config(tmp_path) is None

    path = governed_config_path(tmp_path)
    path.write_text(json.dumps({"providers": {}}))  # no _governed marker
    assert load_governed_config(tmp_path) is None

    path.write_text("{not json")
    assert load_governed_config(tmp_path) is None


# -- store integration: merge-base vs replace semantics ------------------


def _store_with_governance(tmp_path):
    return ModelConfigStore(
        config_dir=lambda: tmp_path,
        governed_config=lambda: load_governed_config(tmp_path),
    )


def test_governed_merge_base_allows_user_models(tmp_path):
    store = _store_with_governance(tmp_path)
    store.add_model(
        provider_name="mine",
        provider_type="openai-compatible",
        model_name="local",
        model="local-model",
        provider_base_url="http://localhost:8080/v1",
    )
    write_governed_config(
        tmp_path, _platform_providers(), "azure-gov/gpt-gov", allow_user_models=True
    )

    merged = store.merged_providers()
    assert "azure-gov" in merged
    assert "mine" in merged  # user config merges on top of the governed base
    assert store.get_default_model_name() == "azure-gov/gpt-gov"


def test_governed_replace_vetoes_user_models(tmp_path):
    store = _store_with_governance(tmp_path)
    store.add_model(
        provider_name="mine",
        provider_type="openai-compatible",
        model_name="local",
        model="local-model",
        provider_base_url="http://localhost:8080/v1",
    )
    write_governed_config(
        tmp_path, _platform_providers(), "azure-gov/gpt-gov", allow_user_models=False
    )

    merged = store.merged_providers()
    assert "azure-gov" in merged
    assert "mine" not in merged  # governed providers replace user config
    assert store.get_default_model_name() == "azure-gov/gpt-gov"
