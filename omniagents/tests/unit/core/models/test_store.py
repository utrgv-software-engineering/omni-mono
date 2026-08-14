"""Tests for ModelConfigStore: schema, merge layering, refs, resolution."""

from omniagents.core.models import ModelConfigStore, litellm_api_key_env


def _store(tmp_path, **kwargs):
    return ModelConfigStore(config_dir=lambda: tmp_path, **kwargs)


def _defaults():
    return {
        "openai": {
            "type": "openai",
            "models": {
                "gpt-test": {"model": "gpt-test", "label": "GPT Test"},
            },
        }
    }


def test_load_config_empty_when_absent(tmp_path):
    store = _store(tmp_path)
    config = store.load_config()
    assert config["providers"] == {}
    assert config["default"] is None


def test_save_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.save_config(
        {"default": "gpt-x", "providers": {"p": {"type": "openai", "models": {}}}}
    )
    config = store.load_config()
    assert config["default"] == "gpt-x"
    assert "p" in config["providers"]


def test_load_config_rejects_wrong_version(tmp_path, capsys):
    (tmp_path / "models.json").write_text('{"version": 1, "providers": {}}')
    store = _store(tmp_path)
    assert store.load_config()["providers"] == {}
    assert "Unsupported models.json version" in capsys.readouterr().out


def test_ref_parsing_and_default_provider(tmp_path):
    store = _store(tmp_path)
    assert store.normalize_model_ref("gpt-test") == "gpt-test"
    assert store.normalize_model_ref("openai/gpt-test") == "gpt-test"
    assert store.normalize_model_ref("azure-prod/gpt") == "azure-prod/gpt"
    assert store.normalize_model_ref("") is None
    assert store.parse_model_ref("a/b") == ("a", "b")


def test_merge_defaults_and_user_config(tmp_path):
    store = _store(tmp_path, default_providers=_defaults)
    store.add_model(
        provider_name="local",
        provider_type="openai-compatible",
        model_name="llama",
        model="llama3",
        provider_base_url="http://localhost:11434/v1",
    )
    merged = store.merged_providers()
    assert "openai" in merged and "local" in merged
    assert store.get_model_config("local/llama")["provider"] == "openai-compatible"
    # Default was auto-set to the first added model.
    assert store.load_config()["default"] == "local/llama"


def test_user_overrides_default_model_fields(tmp_path):
    store = _store(tmp_path, default_providers=_defaults)
    store.add_model(
        provider_name="openai",
        provider_type="openai",
        model_name="gpt-test",
        model="gpt-test",
        label="Renamed",
    )
    assert store.get_model_config("gpt-test")["label"] == "Renamed"


def test_governance_layers_and_user_veto(tmp_path):
    governed = {
        "providers": {
            "gov": {"type": "azure", "models": {"g": {"model": "g"}}},
        },
        "_allow_user_models": False,
        "default": "gov/g",
    }
    store = _store(
        tmp_path,
        default_providers=_defaults,
        governed_config=lambda: governed,
    )
    store.add_model(
        provider_name="mine",
        provider_type="openai",
        model_name="m",
        model="m",
    )
    merged = store.merged_providers()
    assert "gov" in merged
    assert "mine" not in merged  # governance vetoed user models
    assert store.get_default_model_name() == "gov/g"


def test_oauth_discovery_merges_over_seed(tmp_path):
    def defaults():
        return {
            "codex": {
                "type": "openai-oauth",
                "models": {"gpt-5.2": {"model": "gpt-5.2", "label": "Seed Label"}},
            }
        }

    store = _store(
        tmp_path,
        default_providers=defaults,
        oauth_models=lambda: {
            "gpt-5.2": {"model": "gpt-5.2", "label": "Live"},
            "gpt-new": {"model": "gpt-new", "label": "New"},
        },
    )
    merged = store.merged_providers()
    models = merged["codex"]["models"]
    # Seed overrides survive over the live entry; new models appear.
    assert models["gpt-5.2"]["label"] == "Seed Label"
    assert "gpt-new" in models


def test_oauth_discovery_keeps_realtime_seeds(tmp_path):
    def defaults():
        return {
            "codex": {
                "type": "openai-oauth",
                "models": {
                    "gpt-old": {"model": "gpt-old", "label": "Stale Text Seed"},
                    "gpt-realtime-2.1": {
                        "model": "gpt-realtime-2.1",
                        "label": "Realtime 2.1",
                        "realtime": True,
                    },
                },
            }
        }

    store = _store(
        tmp_path,
        default_providers=defaults,
        oauth_models=lambda: {"gpt-new": {"model": "gpt-new", "label": "New"}},
    )
    models = store.merged_providers()["codex"]["models"]
    # Discovery replaces stale text seeds but can never return realtime
    # models, so realtime-flagged seeds must survive the replacement.
    assert "gpt-old" not in models
    assert "gpt-new" in models
    assert models["gpt-realtime-2.1"]["realtime"] is True


def test_resolve_model_for_runtime_oauth_ignores_env_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-shadow")

    def defaults():
        return {
            "codex": {
                "type": "openai-oauth",
                "models": {
                    "gpt-realtime-2.1": {
                        "model": "gpt-realtime-2.1",
                        "realtime": True,
                    }
                },
            }
        }

    store = _store(tmp_path, default_providers=defaults)
    runtime = store.resolve_model_for_runtime("codex/gpt-realtime-2.1")
    # The OAuth token is the credential; an ambient key must not shadow it.
    assert runtime["api_key"] is None
    settings = store.resolve_voice_settings_for_realtime("codex/gpt-realtime-2.1")
    assert settings["openai_api_key"] == ""


def test_resolve_model_for_runtime_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_MODELS_KEY", "sk-expanded")
    store = _store(tmp_path)
    store.add_model(
        provider_name="openai",
        provider_type="openai",
        model_name="gpt-test",
        model="gpt-test",
        provider_api_key="${TEST_MODELS_KEY}",
    )
    runtime = store.resolve_model_for_runtime("gpt-test")
    assert runtime["api_key"] == "sk-expanded"
    assert runtime["provider"] == "openai"


def test_resolve_model_for_runtime_credential_ref_priority(tmp_path):
    def resolver(ref):
        assert ref == "cred-1"
        return {
            "credential_type": "api_key",
            "api_key": "sk-from-platform",
            "base_url": "https://gov.example/v1",
        }

    def defaults():
        return {
            "openai": {
                "type": "azure",
                "models": {
                    "gov-model": {
                        "model": "gpt",
                        "credential_ref": "cred-1",
                        "api_key": "sk-static",
                    }
                },
            }
        }

    store = _store(tmp_path, default_providers=defaults, credential_resolver=resolver)
    runtime = store.resolve_model_for_runtime("gov-model")
    assert runtime["api_key"] == "sk-from-platform"
    assert runtime["base_url"] == "https://gov.example/v1"


def test_resolve_model_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    store = _store(tmp_path, default_providers=_defaults)
    runtime = store.resolve_model_for_runtime("gpt-test")
    assert runtime["api_key"] == "sk-env"


def test_remove_model_and_provider(tmp_path):
    store = _store(tmp_path)
    store.add_model(
        provider_name="p", provider_type="openai", model_name="m", model="m"
    )
    assert store.load_config()["default"] == "p/m"
    assert store.remove_model("p/m") is True
    assert store.load_config()["default"] is None
    assert store.remove_model("p/m") is False

    store.add_provider("q", "openai", api_key="k")
    assert store.is_user_provider("q") is True
    assert store.remove_provider("q") is True
    assert store.remove_provider("q") is False


def test_set_default_rejects_realtime(tmp_path):
    store = _store(tmp_path)
    store.add_model(
        provider_name="p",
        provider_type="openai",
        model_name="voice",
        model="voice",
        realtime=True,
        set_voice_default=True,
    )
    assert store.set_default_model("p/voice") is False
    assert store.set_voice_default_model("p/voice") is True
    assert store.get_voice_default_model_name() == "p/voice"
    assert [m["name"] for m in store.list_voice_models()] == ["p/voice"]


def test_default_model_fallback(tmp_path):
    store = _store(
        tmp_path,
        default_providers=_defaults,
        default_model_fallback=lambda: "gpt-test",
    )
    assert store.get_default_model_name() == "gpt-test"


def test_has_models_configured_variants(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = _store(
        tmp_path, default_providers=_defaults, default_model_fallback=lambda: "gpt-test"
    )
    assert store.has_models_configured() is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    assert store.has_models_configured() is True


def test_litellm_api_key_env_table():
    assert litellm_api_key_env("anthropic/claude-x") == "ANTHROPIC_API_KEY"
    assert litellm_api_key_env("claude-x") == "ANTHROPIC_API_KEY"
    assert litellm_api_key_env("gemini/g") == "GOOGLE_API_KEY"
    assert litellm_api_key_env("gpt") == "OPENAI_API_KEY"
