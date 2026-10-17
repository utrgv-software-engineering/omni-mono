"""Contract tests for the provider-neutral model catalog view
(``omniagents.core.models.catalog_api``) behind the GUI protocol's
model-catalog RPCs.

Covers heterogeneous provider metadata (API-key, Azure/platform
credential, OAuth-discovered, openai-compatible), typed availability /
entitlement reasons, hidden + user-defined models, deterministic
ordering, per-provider failure isolation, and the shared session
application logic promoted from the ``/model`` / ``/reasoning`` server
functions.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omniagents.core.models.catalog_api import (
    REASONING_EFFORT_LEVELS,
    apply_session_model,
    apply_session_reasoning,
    build_model_catalog,
    build_provider_overview,
    describe_model,
    set_voice_default_model,
)
from omniagents.core.models.store import ModelConfigStore

#: Heterogeneous provider fixture — one of each credential/metadata shape.
HETEROGENEOUS_PROVIDERS = {
    "openai": {
        "type": "openai",
        "api_key": "sk-test",
        "models": {
            "gpt-5.1": {
                "model": "gpt-5.1",
                "label": "GPT-5.1",
                "description": "Flagship",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "reasoning": "medium",
                "service_tier": "standard",
                "speed_tier": "fast",
                "personality": {
                    "supported": True,
                    "options": ["friendly", "concise"],
                    "default": "friendly",
                },
            },
            "gpt-5-legacy": {
                "model": "gpt-5-legacy",
                "label": "GPT-5 (legacy)",
                "deprecated": True,
                "deprecation_message": "Retiring soon.",
                "replace_with": "gpt-5.1",
            },
            "gpt-internal": {
                "model": "gpt-internal",
                "label": "Internal",
                "hidden": True,
            },
            "gpt-realtime": {
                "model": "gpt-realtime",
                "label": "GPT Realtime",
                "realtime": True,
            },
        },
    },
    "azure-prod": {
        "type": "azure",
        "credential_ref": "azure-prod-cred",
        "models": {
            "gpt-5.2": {
                "model": "deployment-52",
                "label": "GPT-5.2 (Azure)",
                "modalities": ["text"],
            }
        },
    },
    "codex": {
        "type": "openai-oauth",
        "models": {
            "gpt-5.5-codex": {
                "model": "gpt-5.5-codex",
                "label": "GPT-5.5 Codex",
                "reasoning": "medium",
                "reasoning_options": [
                    {"effort": "low"},
                    {"effort": "medium"},
                    {"effort": "high"},
                ],
            }
        },
    },
    "local": {
        "type": "openai-compatible",
        # No base_url — permanently misconfigured.
        "models": {"llama": {"model": "llama", "label": "Llama"}},
    },
}


def make_store(tmp_path, *, providers=None, oauth_authenticated=None, **kwargs):
    return ModelConfigStore(
        config_dir=lambda: tmp_path,
        default_providers=lambda: json.loads(
            json.dumps(providers if providers is not None else HETEROGENEOUS_PROVIDERS)
        ),
        default_model_fallback=lambda: "gpt-5.1",
        oauth_authenticated=oauth_authenticated,
        **kwargs,
    )


def by_id(catalog):
    return {m["id"]: m for m in catalog["models"]}


# ---------------------------------------------------------------------------
# Descriptors — heterogeneous provider metadata
# ---------------------------------------------------------------------------


def test_catalog_descriptor_carries_full_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = make_store(tmp_path)
    catalog = build_model_catalog(store)
    model = by_id(catalog)["gpt-5.1"]

    assert model["label"] == "GPT-5.1"
    assert model["description"] == "Flagship"
    assert model["provider"] == {"name": "openai", "type": "openai"}
    assert model["modalities"] == ["text"]
    assert model["realtime"] is False
    assert model["limits"] == {
        "max_input_tokens": 272000,
        "max_output_tokens": 128000,
    }
    assert model["reasoning"] == {
        "default": "medium",
        "options": list(REASONING_EFFORT_LEVELS),
    }
    assert model["tiers"] == {"service": "standard", "speed": "fast"}
    assert model["personality"] == {
        "supported": True,
        "options": ["friendly", "concise"],
        "default": "friendly",
    }
    assert model["availability"] == {"available": True, "reasons": []}
    assert model["entitlement"] == {"entitled": True, "credential": "api_key"}
    assert model["deprecation"] == {
        "deprecated": False,
        "message": None,
        "replace_with": None,
    }
    assert model["hidden"] is False
    assert model["is_default"] is True
    assert model["is_user_defined"] is False


def test_catalog_covers_heterogeneous_credentials_and_deprecation(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    store = make_store(tmp_path, oauth_authenticated=lambda: False)
    catalog = build_model_catalog(store, include_hidden=True)
    models = by_id(catalog)

    # Azure model resolves via a platform credential_ref.
    azure = models["azure-prod/gpt-5.2"]
    assert azure["entitlement"]["credential"] == "platform"
    assert azure["modalities"] == ["text"]

    # OAuth provider signed out → typed, actionable reason.
    codex = models["codex/gpt-5.5-codex"]
    assert codex["availability"]["available"] is False
    assert [r["code"] for r in codex["availability"]["reasons"]] == [
        "oauth_signin_required"
    ]
    assert codex["entitlement"] == {"entitled": False, "credential": "oauth"}
    # Discovery-shaped reasoning options ({"effort": ...} objects) normalize.
    assert codex["reasoning"]["options"] == ["low", "medium", "high"]

    # openai-compatible without base_url → typed reason.
    local = models["local/llama"]
    assert [r["code"] for r in local["availability"]["reasons"]] == ["missing_base_url"]

    # Realtime model advertises audio modality.
    realtime = models["gpt-realtime"]
    assert realtime["realtime"] is True
    assert realtime["modalities"] == ["text", "audio"]

    # Deprecation/upgrade guidance.
    legacy = models["gpt-5-legacy"]
    assert legacy["deprecation"] == {
        "deprecated": True,
        "message": "Retiring soon.",
        "replace_with": "gpt-5.1",
    }


def test_oauth_signed_in_models_are_available(tmp_path):
    store = make_store(tmp_path, oauth_authenticated=lambda: True)
    codex = by_id(build_model_catalog(store))["codex/gpt-5.5-codex"]
    assert codex["availability"] == {"available": True, "reasons": []}
    assert codex["entitlement"] == {"entitled": True, "credential": "oauth"}


def test_missing_credentials_reason_is_actionable(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    providers = {"openai": {"type": "openai", "models": {"gpt": {"model": "gpt"}}}}
    store = make_store(tmp_path, providers=providers)
    model = by_id(build_model_catalog(store))["gpt"]
    (reason,) = model["availability"]["reasons"]
    assert reason["code"] == "missing_credentials"
    assert reason["message"]
    assert reason["action"]


def test_descriptors_never_leak_secrets(tmp_path):
    store = make_store(tmp_path)
    catalog = build_model_catalog(store, include_hidden=True)
    assert "sk-test" not in json.dumps(catalog)
    for model in catalog["models"]:
        assert "api_key" not in model


# ---------------------------------------------------------------------------
# Hidden / user-defined models + deterministic ordering
# ---------------------------------------------------------------------------


def test_hidden_models_excluded_by_default_but_resolvable(tmp_path):
    store = make_store(tmp_path)
    catalog = build_model_catalog(store)
    assert "gpt-internal" not in by_id(catalog)

    with_hidden = build_model_catalog(store, include_hidden=True)
    assert by_id(with_hidden)["gpt-internal"]["hidden"] is True

    described = describe_model(store, "gpt-internal")
    assert described["found"] is True
    assert described["model"]["hidden"] is True


def test_user_defined_models_are_flagged(tmp_path):
    store = make_store(tmp_path)
    store.add_model(
        provider_name="openai",
        provider_type="openai",
        model_name="my-gpt",
        model="my-gpt",
        label="Mine",
    )
    model = by_id(build_model_catalog(store))["my-gpt"]
    assert model["is_user_defined"] is True


def test_ordering_is_deterministic_provider_then_model(tmp_path):
    store = make_store(tmp_path)
    ids = [m["id"] for m in build_model_catalog(store, include_hidden=True)["models"]]
    assert ids == [
        "azure-prod/gpt-5.2",
        "codex/gpt-5.5-codex",
        "local/llama",
        "gpt-5-legacy",
        "gpt-5.1",
        "gpt-internal",
        "gpt-realtime",
    ]
    assert ids == [
        m["id"] for m in build_model_catalog(store, include_hidden=True)["models"]
    ]


def test_modality_filter(tmp_path):
    store = make_store(tmp_path)
    audio_only = build_model_catalog(store, modality="audio")
    assert [m["id"] for m in audio_only["models"]] == ["gpt-realtime"]


# ---------------------------------------------------------------------------
# describe_model — typed not-found reasons
# ---------------------------------------------------------------------------


def test_describe_unknown_model_returns_typed_reason(tmp_path):
    store = make_store(tmp_path)
    result = describe_model(store, "nope/never")
    assert result["found"] is False
    assert result["model"] is None
    (reason,) = result["reasons"]
    assert reason["code"] == "unknown_model"
    assert reason["action"]


# ---------------------------------------------------------------------------
# Provider overview — capabilities + health
# ---------------------------------------------------------------------------


def test_provider_overview_capabilities_and_health(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    store = make_store(tmp_path, oauth_authenticated=lambda: False)
    overview = build_provider_overview(store)
    providers = {p["name"]: p for p in overview["providers"]}

    assert list(providers) == sorted(providers)  # deterministic

    openai = providers["openai"]
    assert openai["is_default_provider"] is True
    assert openai["model_count"] == 3  # hidden model excluded from count
    assert openai["hidden_model_count"] == 1
    assert openai["capabilities"]["realtime"] is True
    assert openai["capabilities"]["reasoning"] is True
    assert openai["health"]["status"] == "ok"

    codex = providers["codex"]
    assert codex["type"] == "openai-oauth"
    assert codex["health"]["status"] == "needs_auth"

    local = providers["local"]
    assert local["health"]["status"] == "needs_auth"


# ---------------------------------------------------------------------------
# Failure isolation — one provider's failure never hides the others
# ---------------------------------------------------------------------------


class _BrokenProviderStore(ModelConfigStore):
    """A store whose 'broken' provider fails on every model resolution."""

    def get_model_config(self, ref):
        if ref.startswith("broken/"):
            raise RuntimeError("discovery exploded")
        return super().get_model_config(ref)


def test_provider_failure_is_isolated_in_catalog(tmp_path):
    providers = {
        "openai": {
            "type": "openai",
            "api_key": "sk-test",
            "models": {"gpt-5.1": {"model": "gpt-5.1"}},
        },
        "broken": {"type": "openai", "models": {"boom": {"model": "boom"}}},
    }
    store = _BrokenProviderStore(
        config_dir=lambda: tmp_path,
        default_providers=lambda: json.loads(json.dumps(providers)),
        default_model_fallback=lambda: "gpt-5.1",
    )
    catalog = build_model_catalog(store)
    assert [m["id"] for m in catalog["models"]] == ["gpt-5.1"]
    (error,) = catalog["errors"]
    assert error["provider"] == "broken"
    assert error["code"] == "provider_error"
    assert "discovery exploded" in error["message"]


def test_provider_failure_reports_error_health_in_overview(tmp_path):
    providers = {
        "openai": {
            "type": "openai",
            "api_key": "sk-test",
            "models": {"gpt-5.1": {"model": "gpt-5.1"}},
        },
        "broken": {"type": "openai", "models": {"boom": {"model": "boom"}}},
    }
    store = _BrokenProviderStore(
        config_dir=lambda: tmp_path,
        default_providers=lambda: json.loads(json.dumps(providers)),
        default_model_fallback=lambda: "gpt-5.1",
    )
    overview = build_provider_overview(store)
    providers_by_name = {p["name"]: p for p in overview["providers"]}
    assert providers_by_name["openai"]["health"]["status"] == "ok"
    broken = providers_by_name["broken"]
    assert broken["health"]["status"] == "error"
    assert "discovery exploded" in broken["health"]["detail"]


def test_failing_oauth_discovery_hook_keeps_other_providers(tmp_path):
    def exploding_oauth_models():
        raise RuntimeError("oauth backend down")

    store = make_store(tmp_path, oauth_models=exploding_oauth_models)
    catalog = build_model_catalog(store, include_hidden=True)
    # The codex provider degrades to its static seed; nothing disappears.
    ids = {m["id"] for m in catalog["models"]}
    assert {"gpt-5.1", "codex/gpt-5.5-codex", "azure-prod/gpt-5.2"} <= ids


# ---------------------------------------------------------------------------
# Session application — promoted /model and /reasoning logic
# ---------------------------------------------------------------------------


def make_session():
    return SimpleNamespace(
        active_model=None,
        model_config=None,
        reasoning_effort=None,
    )


def test_apply_session_model_switches_and_applies_default_reasoning(tmp_path):
    store = make_store(tmp_path)
    session = make_session()
    result = apply_session_model(store, session, "gpt-5.1")
    assert result["ok"] is True
    assert result["model"] == "gpt-5.1"
    assert result["warnings"] == []
    assert session.active_model == "gpt-5.1"
    assert session.model_config["label"] == "GPT-5.1"
    assert session.reasoning_effort == "medium"  # model default applied


def test_apply_session_model_preserves_explicit_reasoning(tmp_path):
    store = make_store(tmp_path)
    session = make_session()
    assert apply_session_reasoning(session, "high")["ok"] is True
    assert apply_session_model(store, session, "gpt-5.1")["ok"] is True
    assert session.reasoning_effort == "high"


def test_apply_session_model_unknown_ref_is_typed(tmp_path):
    store = make_store(tmp_path)
    session = make_session()
    result = apply_session_model(store, session, "not-a-model")
    assert result["ok"] is False
    assert [r["code"] for r in result["reasons"]] == ["unknown_model"]
    assert session.active_model is None


def test_apply_session_model_refuses_realtime_models(tmp_path):
    store = make_store(tmp_path)
    session = make_session()
    result = apply_session_model(store, session, "gpt-realtime")
    assert result["ok"] is False
    assert [r["code"] for r in result["reasons"]] == ["realtime_only"]


def test_apply_session_model_warns_when_unentitled(tmp_path):
    store = make_store(tmp_path, oauth_authenticated=lambda: False)
    session = make_session()
    result = apply_session_model(store, session, "codex/gpt-5.5-codex")
    assert result["ok"] is True  # switch succeeds; failure surfaces at run
    assert [w["code"] for w in result["warnings"]] == ["oauth_signin_required"]


def test_apply_session_reasoning_validates_levels():
    session = make_session()
    bad = apply_session_reasoning(session, "ultra")
    assert bad["ok"] is False
    assert [r["code"] for r in bad["reasons"]] == ["invalid_reasoning_effort"]
    good = apply_session_reasoning(session, "XHigh")
    assert good["ok"] is True
    assert session.reasoning_effort == "xhigh"


# ---------------------------------------------------------------------------
# Voice default
# ---------------------------------------------------------------------------


def test_set_voice_default_model(tmp_path):
    store = make_store(tmp_path)
    ok = set_voice_default_model(store, "gpt-realtime")
    assert ok["ok"] is True
    assert store.get_voice_default_model_name() == "gpt-realtime"

    not_realtime = set_voice_default_model(store, "gpt-5.1")
    assert not_realtime["ok"] is False
    assert [r["code"] for r in not_realtime["reasons"]] == ["not_realtime"]

    unknown = set_voice_default_model(store, "ghost")
    assert unknown["ok"] is False
    assert [r["code"] for r in unknown["reasons"]] == ["unknown_model"]


# ---------------------------------------------------------------------------
# The chat surface (/model, /reasoning) shares this logic — no drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_picker_server_function_uses_shared_switch(tmp_path):
    from omniagents.core import models as models_pkg
    from omniagents.builtin.server_functions.model_picker import (
        set_reasoning,
        switch_model,
    )

    store = make_store(tmp_path)
    models_pkg.set_model_store(store)
    try:
        session = make_session()
        result = await switch_model(session, "gpt-5.1")
        assert result["model"] == "gpt-5.1"
        assert session.active_model == "gpt-5.1"
        assert "Switched to" in result["message"]

        unknown = await switch_model(session, "ghost")
        assert "error" in unknown
        assert "gpt-5.1" in unknown["available"]

        invalid = await set_reasoning(session, "ultra")
        assert "error" in invalid
        valid = await set_reasoning(session, "high")
        assert valid["reasoning_effort"] == "high"
        assert session.reasoning_effort == "high"
    finally:
        models_pkg.reset_model_store()
