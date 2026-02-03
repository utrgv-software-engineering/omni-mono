from omni_code import models


def test_has_models_configured_false_when_no_api_key(monkeypatch):
    monkeypatch.setattr(models, "resolve_model_for_runtime", lambda model_name=None: {"provider": "openai", "model": "gpt-5.1", "api_key": ""})
    assert models.has_models_configured() is False


def test_has_models_configured_true_for_openai_compatible_without_key(monkeypatch):
    monkeypatch.setattr(
        models,
        "resolve_model_for_runtime",
        lambda model_name=None: {"provider": "openai-compatible", "model": "llama", "base_url": "http://localhost:11434/v1"},
    )
    assert models.has_models_configured() is True


def test_has_models_configured_true_when_api_key_present(monkeypatch):
    monkeypatch.setattr(models, "resolve_model_for_runtime", lambda model_name=None: {"provider": "openai", "model": "gpt-5.1", "api_key": "sk-test"})
    assert models.has_models_configured() is True
