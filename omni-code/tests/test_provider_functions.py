"""Tests for provider-level CRUD functions in omni_code.models."""

import json
import os
from pathlib import Path

import pytest

from omni_code import models


@pytest.fixture
def tmp_models_config(tmp_path, monkeypatch):
    """Redirect models.json to a temp file and return its path."""
    config_path = tmp_path / "models.json"
    monkeypatch.setattr(models, "get_models_config_path", lambda: config_path)
    return config_path


def _write_config(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config), encoding="utf-8")


def _read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- is_user_provider ---

def test_is_user_provider_true(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "my-openai": {"type": "openai", "models": {}},
        },
    })
    assert models.is_user_provider("my-openai") is True


def test_is_user_provider_false_for_builtin(tmp_models_config):
    # No user config → "openai" only exists as a built-in
    assert models.is_user_provider("openai") is False


def test_is_user_provider_false_for_nonexistent(tmp_models_config):
    assert models.is_user_provider("nonexistent") is False


# --- list_providers ---

def test_list_providers_includes_builtins(tmp_models_config):
    providers = models.list_providers()
    names = [p["name"] for p in providers]
    assert "openai" in names


def test_list_providers_includes_user_defined(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "azure-prod": {"type": "azure", "api_key": "key123", "base_url": "https://example.com", "models": {}},
        },
    })
    providers = models.list_providers()
    azure = next(p for p in providers if p["name"] == "azure-prod")
    assert azure["type"] == "azure"
    assert azure["has_api_key"] is True
    assert azure["is_user_defined"] is True


def test_list_providers_model_count(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "local": {
                "type": "openai-compatible",
                "base_url": "http://localhost:11434/v1",
                "models": {
                    "llama": {"model": "llama3.1", "label": "Llama"},
                    "mistral": {"model": "mistral", "label": "Mistral"},
                },
            },
        },
    })
    providers = models.list_providers()
    local = next(p for p in providers if p["name"] == "local")
    assert local["model_count"] == 2


# --- get_provider ---

def test_get_provider_builtin(tmp_models_config):
    provider = models.get_provider("openai")
    assert provider is not None
    assert provider["type"] == "openai"
    assert "models" in provider


def test_get_provider_user_defined(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "custom": {"type": "litellm", "api_key": "sk-x", "models": {}},
        },
    })
    provider = models.get_provider("custom")
    assert provider is not None
    assert provider["type"] == "litellm"


def test_get_provider_nonexistent(tmp_models_config):
    assert models.get_provider("nonexistent") is None


def test_resolve_model_for_runtime_includes_provider_api_mode(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": "chat/gpt-test",
        "voice_default": None,
        "providers": {
            "chat": {
                "type": "openai-compatible",
                "api_key": "sk-test",
                "base_url": "https://example.test/v1",
                "use_responses": False,
                "use_responses_websocket": False,
                "models": {"gpt-test": {"model": "gpt-test", "label": "GPT Test"}},
            },
        },
    })

    runtime = models.resolve_model_for_runtime("chat/gpt-test")

    assert runtime["use_responses"] is False
    assert runtime["use_responses_websocket"] is False


# --- add_provider ---

def test_add_provider_creates_new(tmp_models_config):
    models.add_provider("azure-test", "azure", api_key="sk-az", base_url="https://az.example.com")

    config = _read_config(tmp_models_config)
    provider = config["providers"]["azure-test"]
    assert provider["type"] == "azure"
    assert provider["api_key"] == "sk-az"
    assert provider["base_url"] == "https://az.example.com"
    assert provider["models"] == {}


def test_add_provider_preserves_existing_models(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "my-provider": {
                "type": "openai",
                "api_key": "old-key",
                "models": {"gpt-5": {"model": "gpt-5", "label": "GPT-5"}},
            },
        },
    })

    models.add_provider("my-provider", "openai", api_key="new-key")

    config = _read_config(tmp_models_config)
    provider = config["providers"]["my-provider"]
    assert provider["api_key"] == "new-key"
    # Models should still be there
    assert "gpt-5" in provider["models"]


# --- update_provider ---

def test_update_provider_changes_key(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "openai-user": {"type": "openai", "api_key": "old", "models": {}},
        },
    })

    result = models.update_provider("openai-user", api_key="new-key")
    assert result is True

    config = _read_config(tmp_models_config)
    assert config["providers"]["openai-user"]["api_key"] == "new-key"


def test_update_provider_returns_false_for_missing(tmp_models_config):
    assert models.update_provider("nonexistent", api_key="x") is False


def test_update_provider_ignores_unknown_keys(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "test": {"type": "openai", "models": {}},
        },
    })

    models.update_provider("test", api_key="k", unknown_field="bad")

    config = _read_config(tmp_models_config)
    assert "unknown_field" not in config["providers"]["test"]


# --- remove_provider ---

def test_remove_provider_deletes(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": None,
        "providers": {
            "removeme": {"type": "openai", "models": {}},
        },
    })

    result = models.remove_provider("removeme")
    assert result is True

    config = _read_config(tmp_models_config)
    assert "removeme" not in config["providers"]


def test_remove_provider_clears_default(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": "myprov/mymodel",
        "voice_default": None,
        "providers": {
            "myprov": {
                "type": "openai",
                "models": {"mymodel": {"model": "mymodel", "label": "My"}},
            },
        },
    })

    models.remove_provider("myprov")

    config = _read_config(tmp_models_config)
    assert config["default"] is None


def test_remove_provider_clears_voice_default(tmp_models_config):
    _write_config(tmp_models_config, {
        "version": models.MODELS_CONFIG_VERSION,
        "default": None,
        "voice_default": "myprov/rt-model",
        "providers": {
            "myprov": {
                "type": "openai",
                "models": {"rt-model": {"model": "rt", "label": "RT", "realtime": True}},
            },
        },
    })

    models.remove_provider("myprov")

    config = _read_config(tmp_models_config)
    assert config["voice_default"] is None


def test_remove_provider_returns_false_for_missing(tmp_models_config):
    assert models.remove_provider("nonexistent") is False
