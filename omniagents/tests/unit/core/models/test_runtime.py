"""Tests for runtime-side model plumbing: resolve helpers + provider adapter."""

from types import SimpleNamespace

import pytest

import omniagents.core.models as models_pkg
from omniagents.core.agents import run_options
from omniagents.core.models import (
    ModelConfigStore,
    normalize_model_name,
    provider_from_runtime,
    resolve_model_runtime,
    resolve_service_model_runtime,
)


def _providers():
    return {
        "openai": {
            "type": "openai",
            "api_key": "sk-live",
            "models": {
                "gpt-test": {"model": "gpt-test", "label": "GPT Test"},
                "prefixed": {"model": "vendor/model-x", "label": "Prefixed"},
            },
        },
        "azure-prod": {
            "type": "azure",
            "api_key": "az-key",
            "base_url": "https://x.openai.azure.com",
            "models": {"gpt-az": {"model": "deploy-az", "label": "Azure GPT"}},
        },
    }


def _store(tmp_path, **kwargs):
    kwargs.setdefault("default_providers", _providers)
    return ModelConfigStore(config_dir=lambda: tmp_path, **kwargs)


# -- normalize_model_name -----------------------------------------------


def test_normalize_strips_unknown_provider_prefix():
    assert (
        normalize_model_name("openai/prefixed", {"provider": "openai", "model": "vendor/model-x"})
        == "model-x"
    )


def test_normalize_keeps_openai_and_litellm_prefixes():
    assert (
        normalize_model_name(None, {"provider": "openai", "model": "openai/gpt-test"})
        == "openai/gpt-test"
    )
    assert (
        normalize_model_name(None, {"provider": "openai-compatible", "model": "litellm/x"})
        == "litellm/x"
    )


def test_normalize_keeps_full_id_for_litellm_provider():
    assert (
        normalize_model_name(None, {"provider": "litellm", "model": "anthropic/claude-x"})
        == "anthropic/claude-x"
    )


def test_normalize_falls_back_to_ref_when_runtime_has_no_model():
    assert normalize_model_name(" gpt-test ", {"provider": "openai"}) == "gpt-test"
    assert (
        normalize_model_name("azure-prod/deploy", {"provider": "azure", "model": ""})
        == "deploy"
    )


def test_normalize_none_when_nothing_available():
    assert normalize_model_name(None, {}) is None


# -- resolve_model_runtime ----------------------------------------------


def test_resolve_model_runtime_explicit_ref(tmp_path):
    store = _store(tmp_path)
    name, runtime = resolve_model_runtime("azure-prod/gpt-az", store=store)
    assert name == "deploy-az"
    assert runtime["provider"] == "azure"
    assert runtime["api_key"] == "az-key"


def test_resolve_model_runtime_normalizes_by_default(tmp_path):
    store = _store(tmp_path)
    name, runtime = resolve_model_runtime("prefixed", store=store)
    assert name == "model-x"

    raw_name, _ = resolve_model_runtime("prefixed", store=store, normalize=False)
    assert raw_name == "vendor/model-x"


def test_resolve_model_runtime_default_model(tmp_path):
    store = _store(tmp_path, default_model_fallback=lambda: "gpt-test")
    name, runtime = resolve_model_runtime(store=store)
    assert name == "gpt-test"
    assert runtime["model"] == "gpt-test"


def test_resolve_model_runtime_unconfigured(tmp_path):
    store = _store(tmp_path)
    assert resolve_model_runtime("nope/none", store=store) == (None, {})
    assert resolve_model_runtime(store=store) == (None, {})


def test_resolve_model_runtime_uses_process_global_store(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(models_pkg, "_ACTIVE_STORE", store)
    name, runtime = resolve_model_runtime("gpt-test")
    assert name == "gpt-test"
    assert runtime["provider"] == "openai"


def test_resolve_model_runtime_no_store_installed(monkeypatch):
    monkeypatch.setattr(models_pkg, "_ACTIVE_STORE", None)
    assert resolve_model_runtime("gpt-test") == (None, {})


# -- resolve_service_model_runtime --------------------------------------


def _service(model_name=None):
    return SimpleNamespace(spec=SimpleNamespace(model_name=model_name))


def test_service_runtime_prefers_session_active_model(tmp_path):
    store = _store(tmp_path)
    session = SimpleNamespace(active_model="azure-prod/gpt-az", model_config=None)
    ref, runtime = resolve_service_model_runtime(
        _service("gpt-test"), session, store=store
    )
    assert ref == "azure-prod/gpt-az"
    assert runtime["model"] == "deploy-az"


def test_service_runtime_falls_back_to_spec_model(tmp_path):
    store = _store(tmp_path)
    session = SimpleNamespace(active_model="ghost/none", model_config=None)
    ref, runtime = resolve_service_model_runtime(
        _service("gpt-test"), session, store=store
    )
    assert ref == "gpt-test"
    assert runtime["model"] == "gpt-test"


def test_service_runtime_adapts_raw_model_config(tmp_path):
    store = _store(tmp_path)
    session = SimpleNamespace(
        active_model="ghost/none",
        model_config={
            "provider": "OpenAI-Compatible",
            "model": "local-model",
            "api_key": "k",
            "base_url": "http://localhost:8080/v1",
            "use_responses": False,
        },
    )
    ref, runtime = resolve_service_model_runtime(_service(None), session, store=store)
    assert ref == "ghost/none"  # first candidate is reported alongside the fallback
    assert runtime["provider"] == "openai-compatible"
    assert runtime["model"] == "local-model"
    assert runtime["use_responses"] is False


def test_service_runtime_nothing_configured(tmp_path):
    store = _store(tmp_path)
    session = SimpleNamespace(active_model=None, model_config=None)
    assert resolve_service_model_runtime(_service(None), session, store=store) == (
        None,
        {},
    )


def test_service_runtime_without_any_store(monkeypatch):
    monkeypatch.setattr(models_pkg, "_ACTIVE_STORE", None)
    session = SimpleNamespace(
        active_model="gpt-test",
        model_config={"provider": "openai", "model": "gpt-test", "api_key": "k"},
    )
    ref, runtime = resolve_service_model_runtime(_service(None), session)
    assert ref == "gpt-test"
    assert runtime["model"] == "gpt-test"


# -- provider_from_runtime ----------------------------------------------


def test_provider_from_runtime_empty_runtime():
    assert provider_from_runtime({}) == (None, {})
    assert provider_from_runtime({"provider": "  "}) == (None, {})


def test_provider_from_runtime_credentialed_openai_compatible():
    provider, env = provider_from_runtime(
        {
            "provider": "openai-compatible",
            "model": "gpt-test",
            "api_key": "sk-test",
            "base_url": "https://example.test/v1",
            "use_responses": False,
        }
    )
    assert env == {}
    assert provider is not None
    assert provider._use_responses is False


def test_provider_from_runtime_no_credential_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert provider_from_runtime({"provider": "openai", "model": "gpt-test"}) == (
        None,
        {},
    )


def test_provider_from_runtime_no_credential_azure(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    provider, env = provider_from_runtime(
        {
            "provider": "azure",
            "model": "deploy-az",
            "base_url": "https://x.openai.azure.com",
        }
    )
    assert provider is None
    assert env == {}


def test_provider_from_runtime_env_credential_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    captured = {}

    def fake_builder(config):
        captured.update(config)
        return object()

    monkeypatch.setattr(run_options, "build_model_provider", fake_builder)
    provider, env = provider_from_runtime({"provider": "openai", "model": "gpt-test"})
    assert provider is not None
    assert captured["api_key"] == "sk-env"
    assert captured["type"] == "openai"


def test_provider_from_runtime_dummy_key_for_local_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured = {}

    def fake_builder(config):
        captured.update(config)
        return object()

    monkeypatch.setattr(run_options, "build_model_provider", fake_builder)
    provider, _env = provider_from_runtime(
        {
            "provider": "openai-compatible",
            "model": "local-model",
            "base_url": "http://localhost:8080/v1",
        }
    )
    assert provider is not None
    assert captured["api_key"] == "dummy"
    assert captured["base_url"] == "http://localhost:8080/v1"


def test_provider_from_runtime_local_endpoint_constructs_real_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider, env = provider_from_runtime(
        {
            "provider": "openai-compatible",
            "model": "local-model",
            "base_url": "http://localhost:8080/v1",
        }
    )
    assert provider is not None
    assert env == {}


def test_provider_from_runtime_litellm_env_overrides(monkeypatch):
    monkeypatch.setattr(run_options, "build_model_provider", lambda config: object())
    _provider, env = provider_from_runtime(
        {"provider": "litellm", "model": "anthropic/claude-x", "api_key": "sk-a"}
    )
    assert env == {"ANTHROPIC_API_KEY": "sk-a"}

    _provider, env = provider_from_runtime(
        {"provider": "litellm", "model": "gemini/flash", "api_key": "sk-g"}
    )
    assert env == {"GOOGLE_API_KEY": "sk-g"}


def test_provider_from_runtime_no_env_overrides_when_builder_declines(monkeypatch):
    monkeypatch.setattr(run_options, "build_model_provider", lambda config: None)
    provider, env = provider_from_runtime(
        {"provider": "litellm", "model": "anthropic/claude-x", "api_key": "sk-a"}
    )
    assert provider is None
    assert env == {}
