import json
from types import SimpleNamespace

import pytest

from omniagents.core.allowed_services import (
    INTEGRATIONS_ENV,
    MODELS_ENV,
    ServiceNotAllowed,
    integration_allowed,
    mcp_server_allowed,
    model_allowed,
    require_model,
)

AZURE = "https://utrgv-ha.openai.azure.com/openai/v1"


def env(models=None, integrations=None):
    out = {}
    if models is not None:
        out[MODELS_ENV] = json.dumps(models)
    if integrations is not None:
        out[INTEGRATIONS_ENV] = json.dumps(integrations)
    return out


def test_nothing_is_restricted_until_a_deployment_says_so():
    assert model_allowed({"provider": "openai"}, {})
    assert integration_allowed({"type": "slack"}, {})


def test_models_must_use_an_allowed_endpoint():
    allowed = env(models=[{"provider": "azure", "base_url": AZURE}])
    assert model_allowed({"provider": "azure", "base_url": AZURE}, allowed)
    assert model_allowed(
        {"provider": "azure", "base_url": AZURE + "/deployments/x"}, allowed
    )
    for runtime in (
        {
            "provider": "azure",
            "base_url": "https://elsewhere.openai.azure.com/openai/v1",
        },
        {"provider": "azure", "base_url": AZURE + "-evil"},
        {"provider": "openai"},
        {"provider": "openai-compatible", "base_url": AZURE},
    ):
        assert not model_allowed(runtime, allowed)
    with pytest.raises(ServiceNotAllowed, match="not one of the allowed services"):
        require_model({"provider": "openai"}, allowed)
    # An empty list allows nothing.
    assert not model_allowed({"provider": "azure", "base_url": AZURE}, env(models=[]))


def test_providers_without_an_endpoint_mean_their_own():
    allowed = env(models=[{"provider": "openai-oauth"}, {"provider": "openai"}])
    assert model_allowed({"provider": "openai-oauth"}, allowed)
    assert model_allowed({"provider": "openai"}, allowed)
    assert not model_allowed(
        {"provider": "openai", "base_url": "https://proxy.example/v1"}, allowed
    )


def test_the_model_store_refuses_a_model_that_is_not_allowed(tmp_path, monkeypatch):
    from omniagents.core.models.store import ModelConfigStore

    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "default": "openai/gpt",
                "providers": {
                    "openai": {
                        "type": "openai",
                        "api_key": "k",
                        "models": {"gpt": {"model": "gpt"}},
                    },
                    "azure": {
                        "type": "azure",
                        "api_key": "k",
                        "base_url": AZURE,
                        "models": {"gpt": {"model": "gpt"}},
                    },
                },
            }
        )
    )
    store = ModelConfigStore(config_dir=lambda: tmp_path)
    monkeypatch.setenv(
        MODELS_ENV, json.dumps([{"provider": "azure", "base_url": AZURE}])
    )
    assert store.resolve_model_for_runtime("azure/gpt")["base_url"] == AZURE
    with pytest.raises(ServiceNotAllowed):
        store.resolve_model_for_runtime("openai/gpt")


def test_integrations_must_be_allowed():
    allowed = env(
        integrations=[{"type": "slack", "url": "http://workplace:8080/slack/api"}]
    )
    assert integration_allowed(
        {"type": "slack", "api_url": "http://workplace:8080/slack/api"}, allowed
    )
    assert not integration_allowed(
        {"type": "slack", "api_url": "https://slack.com/api"}, allowed
    )
    assert not integration_allowed({"type": "linear"}, allowed)


def test_parse_integrations_leaves_out_one_not_allowed(monkeypatch):
    from omniagents.integrations.server import parse_integrations

    monkeypatch.setenv(INTEGRATIONS_ENV, json.dumps([{"type": "github"}]))
    monkeypatch.setenv("SLACK_TEST_TOKEN", "xapp-1")
    configs = parse_integrations(
        [{"type": "slack", "delivery": "socket", "app_token": "${SLACK_TEST_TOKEN}"}]
    )
    assert configs == []


def server(name, **params):
    return SimpleNamespace(name=name, params=params)


def test_mcp_servers_are_allowed_by_name_and_pinned_address():
    allowed = [
        "memory",
        "github=https://workplace.example/github/mcp",
        "fs=command:mcp-fs",
    ]
    assert mcp_server_allowed(
        server("memory", url="https://anywhere.example/mcp"), allowed
    )
    assert mcp_server_allowed(
        server("github", url="https://workplace.example/github/mcp"), allowed
    )
    assert mcp_server_allowed(server("fs", command="mcp-fs"), allowed)
    # An outside server taking an allowed name is refused.
    assert not mcp_server_allowed(
        server("github", url="https://attacker.example/mcp"), allowed
    )
    assert not mcp_server_allowed(server("fs", command="curl"), allowed)
    assert not mcp_server_allowed(
        server("other", url="https://workplace.example/github/mcp"), allowed
    )
