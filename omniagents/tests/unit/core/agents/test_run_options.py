"""Tests for omniagents.core.agents.run_options.build_model_provider."""

import pytest

from omniagents.core.agents.run_options import build_model_provider


def test_returns_none_for_empty():
    assert build_model_provider(None) is None
    assert build_model_provider({}) is None


def test_builds_openai_provider():
    provider = build_model_provider({"type": "openai", "api_key": "sk-test"})
    from agents.models.openai_provider import OpenAIProvider

    assert isinstance(provider, OpenAIProvider)


def test_use_responses_websocket_sets_transport():
    provider = build_model_provider(
        {
            "type": "openai",
            "api_key": "sk-test",
            "use_responses_websocket": True,
            "websocket_options": {"ping_interval": 30},
        }
    )
    # SDK 0.16 stores transport mode privately; assert via the back-compat shim.
    assert getattr(provider, "_use_responses_websocket", False) is True
    assert getattr(provider, "_responses_websocket_options") == {"ping_interval": 30}


def test_multi_provider_with_websocket():
    provider = build_model_provider(
        {
            "type": "multi",
            "api_key": "sk-test",
            "use_responses_websocket": True,
        }
    )
    from agents.models.multi_provider import MultiProvider

    assert isinstance(provider, MultiProvider)
    # The OpenAIProvider underneath should have websocket transport
    inner = provider.openai_provider
    assert getattr(inner, "_use_responses_websocket", False) is True


def test_unknown_type_returns_none():
    out = build_model_provider({"type": "bogus_provider", "api_key": "k"})
    assert out is None


def test_reasoning_replay_wrapper_attaches_predicate():
    provider = build_model_provider(
        {
            "type": "openai",
            "api_key": "sk-test",
            "reasoning_content_replay": {"enabled": True},
        }
    )
    # The wrapper still implements ModelProvider and forwards openai_provider attr
    assert hasattr(provider, "get_model")
    # The wrapper class is private; check by name to avoid importing
    assert "ReasoningReplay" in type(provider).__name__


def test_websocket_options_validation_rejects_non_dict():
    # A non-dict websocket_options is logged-and-ignored, NOT propagated.
    provider = build_model_provider(
        {
            "type": "openai",
            "api_key": "sk-test",
            "websocket_options": "not a dict",
        }
    )
    assert provider is not None
    # Options stash should be None since the bogus value was rejected
    assert getattr(provider, "_responses_websocket_options") is None
