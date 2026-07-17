from omni_code import models


def test_has_models_configured_false_when_no_api_key(monkeypatch):
    monkeypatch.setattr(models._STORE, "resolve_model_for_runtime", lambda model_name=None: {"provider": "openai", "model": "gpt-5.1", "api_key": ""})
    assert models.has_models_configured() is False


def test_has_models_configured_true_for_openai_compatible_without_key(monkeypatch):
    monkeypatch.setattr(
        models._STORE,
        "resolve_model_for_runtime",
        lambda model_name=None: {"provider": "openai-compatible", "model": "llama", "base_url": "http://localhost:11434/v1"},
    )
    assert models.has_models_configured() is True


def test_has_models_configured_true_when_api_key_present(monkeypatch):
    monkeypatch.setattr(models._STORE, "resolve_model_for_runtime", lambda model_name=None: {"provider": "openai", "model": "gpt-5.1", "api_key": "sk-test"})
    assert models.has_models_configured() is True


def test_normalize_model_ref_defaults_to_openai():
    assert models.normalize_model_ref("gpt-5.1") == "gpt-5.1"
    assert models.normalize_model_ref("openai/gpt-5.1") == "gpt-5.1"
    assert models.normalize_model_ref("azure-prod/gpt-5.2") == "azure-prod/gpt-5.2"


def test_get_model_config_uses_openai_defaults_for_unprefixed_ref(monkeypatch):
    monkeypatch.setattr(
        models._STORE,
        "load_config",
        lambda: {"version": models.MODELS_CONFIG_VERSION, "default": None, "providers": {}},
    )
    config = models.get_model_config("gpt-5.1")
    assert config["name"] == "gpt-5.1"
    assert config["provider"] == "openai"
