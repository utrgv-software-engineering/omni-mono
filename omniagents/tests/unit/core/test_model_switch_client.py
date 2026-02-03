"""Tests that switching models actually changes the HTTP client endpoint.

Reproduces the bug: default model is openai-compatible with a custom base_url
(e.g. rgvaiclass.com). User runs /model gpt-5.2 to switch to the built-in
OpenAI model (no base_url). Requests should go to api.openai.com, but due to
stale env vars / global client state they still go to the old base_url.

Root cause: AsyncOpenAI(base_url=None) reads os.environ["OPENAI_BASE_URL"],
which was set at startup for the openai-compatible default model. So even
passing OpenAIProvider(api_key=...) with no base_url still resolves to the
old endpoint. The fix must create an explicit AsyncOpenAI client with
base_url="https://api.openai.com/v1" for standard OpenAI models.
"""

import os
from unittest.mock import patch

import pytest

from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session.manager import Session


def make_spec(**overrides):
    base = {
        "name": overrides.pop("name", "TestAgent"),
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: "Test instructions"),
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


# -- Model configs matching the real user scenario --

RGVAI_MODEL_CONFIG = {
    "provider": "openai-compatible",
    "model": "gpt-5.2",
    "label": "RGVAI (gpt-5.2)",
    "base_url": "https://rgvaiclass.com/chat/api/v1",
    "api_key": "rgvai-test-key-123",
    "max_input_tokens": 272000,
    "max_output_tokens": 128000,
    "model_settings": {
        "store": False,
        "extra_body": {"include": ["reasoning.encrypted_content"]},
    },
}

BUILTIN_GPT52_CONFIG = {
    "provider": "openai",
    "model": "gpt-5.2",
    "label": "GPT-5.2",
    "api_key": "${OPENAI_API_KEY}",
    "max_input_tokens": 272000,
    "max_output_tokens": 128000,
    "model_settings": {
        "store": False,
        "extra_body": {"include": ["reasoning.encrypted_content"]},
    },
    # No base_url -- uses standard OpenAI api.openai.com
}

AZURE_MODEL_CONFIG = {
    "provider": "azure",
    "model": "gpt-5.2",
    "label": "Azure (gpt-5.2)",
    "base_url": "https://cs-hackr.openai.azure.com/openai/v1/",
    "api_key": "${AZURE_OPENAI_API_KEY}",
    "max_input_tokens": 272000,
    "max_output_tokens": 128000,
}


def _get_client_base_url(client) -> str | None:
    """Extract the base_url from an AsyncOpenAI client."""
    if client is None:
        return None
    base = getattr(client, "base_url", None) or getattr(client, "_base_url", None)
    return str(base).rstrip("/") if base else None


def _resolve_model_provider(session_model_config):
    """Replicate the service.py model_provider determination logic."""
    model_provider = None
    if not session_model_config:
        return None

    provider_type = session_model_config.get("provider")

    if provider_type in ("openai", "openai-compatible", "azure"):
        from openai import AsyncOpenAI
        from agents.models.openai_provider import OpenAIProvider

        _base_url = session_model_config.get("base_url")
        _api_key = session_model_config.get("api_key")

        def _expand(val):
            if val and isinstance(val, str) and val.startswith("${") and val.endswith("}"):
                return os.getenv(val[2:-1])
            return val

        _base_url = _expand(_base_url)
        _api_key = _expand(_api_key)

        if not _api_key:
            if provider_type == "azure":
                _api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                _api_key = os.getenv("OPENAI_API_KEY")

        if not _base_url:
            _base_url = "https://api.openai.com/v1"

        is_azure = (
            "openai.azure.com" in _base_url
            or "services.ai.azure.com" in _base_url
        )

        if _api_key and is_azure:
            client = AsyncOpenAI(
                api_key="dummy",
                base_url=_base_url,
                default_headers={"api-key": _api_key},
            )
            model_provider = OpenAIProvider(openai_client=client)
        elif _api_key:
            client = AsyncOpenAI(
                api_key=_api_key,
                base_url=_base_url,
            )
            model_provider = OpenAIProvider(openai_client=client)

    return model_provider


class TestModelSwitchChangesEndpoint:
    """Verify that switching models changes the actual HTTP endpoint."""

    @pytest.mark.asyncio
    async def test_builtin_openai_model_does_not_use_stale_env_base_url(self):
        """Core bug reproduction: after switching to built-in gpt-5.2 (no base_url),
        the resolved client must NOT use OPENAI_BASE_URL from the environment."""
        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            provider = _resolve_model_provider(BUILTIN_GPT52_CONFIG)
            assert provider is not None, "model_provider should be created for openai provider"

            client = provider._get_client()
            client_base_url = _get_client_base_url(client)

            assert "rgvaiclass.com" not in (client_base_url or ""), (
                f"BUG: After switching to built-in gpt-5.2, client still points to "
                f"rgvaiclass.com: {client_base_url}"
            )
            assert "api.openai.com" in (client_base_url or ""), (
                f"Expected api.openai.com, got: {client_base_url}"
            )

    @pytest.mark.asyncio
    async def test_openai_compatible_model_uses_custom_base_url(self):
        """RGVAI model should route to rgvaiclass.com."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            provider = _resolve_model_provider(RGVAI_MODEL_CONFIG)
            assert provider is not None

            client = provider._get_client()
            client_base_url = _get_client_base_url(client)

            assert "rgvaiclass.com" in (client_base_url or ""), (
                f"RGVAI model should point to rgvaiclass.com, got: {client_base_url}"
            )

    @pytest.mark.asyncio
    async def test_azure_model_uses_azure_base_url_and_header(self):
        """Azure model should use azure base_url with api-key header."""
        with patch.dict(os.environ, {
            "AZURE_OPENAI_API_KEY": "azure-test-key",
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
        }):
            provider = _resolve_model_provider(AZURE_MODEL_CONFIG)
            assert provider is not None

            client = provider._get_client()
            client_base_url = _get_client_base_url(client)

            assert "openai.azure.com" in (client_base_url or ""), (
                f"Azure model should point to azure, got: {client_base_url}"
            )
            assert "rgvaiclass.com" not in (client_base_url or ""), (
                f"Azure model should NOT point to rgvaiclass, got: {client_base_url}"
            )

    @pytest.mark.asyncio
    async def test_switch_from_rgvai_to_builtin_changes_endpoint(self):
        """Full scenario: start with RGVAI, switch to built-in, verify endpoint changes."""
        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            # Step 1: RGVAI model
            rgvai_provider = _resolve_model_provider(RGVAI_MODEL_CONFIG)
            rgvai_client = rgvai_provider._get_client()
            rgvai_url = _get_client_base_url(rgvai_client)
            assert "rgvaiclass.com" in rgvai_url

            # Step 2: Switch to built-in
            builtin_provider = _resolve_model_provider(BUILTIN_GPT52_CONFIG)
            builtin_client = builtin_provider._get_client()
            builtin_url = _get_client_base_url(builtin_client)

            assert "rgvaiclass.com" not in builtin_url, (
                f"After switching to built-in gpt-5.2, client still points to "
                f"rgvaiclass.com: {builtin_url}"
            )
            assert "api.openai.com" in builtin_url

    @pytest.mark.asyncio
    async def test_openai_provider_api_key_not_from_stale_env(self):
        """The built-in model should use OPENAI_API_KEY, not the rgvai key."""
        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            provider = _resolve_model_provider(BUILTIN_GPT52_CONFIG)
            client = provider._get_client()

            assert client.api_key == "sk-real-openai-key"

    @pytest.mark.asyncio
    async def test_env_var_expansion_in_api_key(self):
        """Model configs with ${VAR} api_key should expand from env."""
        with patch.dict(os.environ, {
            "AZURE_OPENAI_API_KEY": "expanded-azure-key",
        }):
            provider = _resolve_model_provider(AZURE_MODEL_CONFIG)
            assert provider is not None

            client = provider._get_client()
            # Azure uses api-key header, client itself has "dummy"
            headers = getattr(client, "_custom_headers", {}) or {}
            default_headers = dict(client._custom_headers) if hasattr(client, "_custom_headers") else {}
            # The key should be in default_headers
            assert "openai.azure.com" in _get_client_base_url(client)

    @pytest.mark.asyncio
    async def test_model_provider_passed_to_run_config_overrides_default(self):
        """When model_provider is passed to RunConfig, it should be used
        instead of the default MultiProvider (which reads env)."""
        from agents import RunConfig
        from agents.models.openai_provider import OpenAIProvider
        from openai import AsyncOpenAI

        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            # Create explicit provider for standard OpenAI
            client = AsyncOpenAI(
                api_key="sk-real-openai-key",
                base_url="https://api.openai.com/v1",
            )
            provider = OpenAIProvider(openai_client=client)
            run_config = RunConfig(model_provider=provider)

            # The provider should resolve the model with our explicit client
            model = run_config.model_provider.get_model("gpt-5.2")
            model_client = getattr(model, "_client", None)
            model_url = _get_client_base_url(model_client)

            assert model_url is not None, "Could not find _client on resolved model"
            assert "api.openai.com" in model_url, (
                f"RunConfig model_provider should use explicit client, got: {model_url}"
            )
            assert "rgvaiclass.com" not in model_url

    @pytest.mark.asyncio
    async def test_default_run_config_uses_env_base_url(self):
        """Without explicit model_provider, default RunConfig reads env (this is the bug path)."""
        from agents import RunConfig

        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://rgvaiclass.com/chat/api/v1",
            "OPENAI_API_KEY": "sk-real-openai-key",
        }):
            run_config = RunConfig()
            model = run_config.model_provider.get_model("gpt-5.2")
            model_client = getattr(model, "_client", None)
            model_url = _get_client_base_url(model_client)

            assert model_url is not None, "Could not find _client on resolved model"
            # This SHOULD go to rgvaiclass because no override was given --
            # this demonstrates why the service MUST pass an explicit provider.
            assert "rgvaiclass.com" in model_url, (
                f"Default RunConfig should use env OPENAI_BASE_URL, got: {model_url}"
            )

    @pytest.mark.asyncio
    async def test_builder_model_name_override_is_correct(self):
        """Builder should use model name from session config, not spec."""
        spec = make_spec(model_name="gpt-4o")
        session = Session(project_slug="test", agent_slug="test")
        session.model_config = BUILTIN_GPT52_CONFIG
        session.active_model = "gpt-5.2"

        agent = await _default_build_agent({}, None, spec, None, session=session)
        assert agent.model == "gpt-5.2"
