"""Default model/provider configuration provided by omni-code.

These are the built-in provider profiles and models that ship with omni-code.
Users can add their own providers/models via ~/.config/omni_code/models.json,
which will be merged with (and can override) these defaults.
"""

from typing import Any, Dict

# Model settings for OpenAI reasoning models (GPT-5.x)
# When store=False (the default for privacy), reasoning models require
# include: ["reasoning.encrypted_content"] to maintain reasoning context.
# See: https://github.com/openai/openai-agents-python/issues/2265
_REASONING_MODEL_SETTINGS: Dict[str, Any] = {
    "store": False,
    "extra_body": {"include": ["reasoning.encrypted_content"]},
}

# Project-provided default providers and models.
DEFAULT_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "type": "openai",
        "api_key": "${OPENAI_API_KEY}",
        "models": {
            "gpt-5.1": {
                "model": "gpt-5.1",
                "label": "GPT-5.1",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-5.1-codex": {
                "model": "gpt-5.1-codex",
                "label": "GPT-5.1 Codex",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-5.2": {
                "model": "gpt-5.2",
                "label": "GPT-5.2",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
            "gpt-realtime": {
                "model": "gpt-realtime",
                "label": "GPT Realtime",
                "realtime": True,
            },
        },
    },
    # ChatGPT subscription via the Codex backend (OAuth). No API key — auth is
    # a ChatGPT login managed by omni_code.codex_auth. Talks the Responses API
    # to chatgpt.com/backend-api/codex. When signed in, the model list is
    # discovered live from the backend (see models._apply_oauth_discovery); the
    # entries below are only the offline fallback.
    "codex": {
        "type": "openai-oauth",
        "models": {
            "gpt-5.2": {
                "model": "gpt-5.2",
                "label": "GPT-5.2 (ChatGPT)",
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
                "model_settings": _REASONING_MODEL_SETTINGS,
            },
        },
    },
}

# Default model to use if user hasn't set one
DEFAULT_MODEL_NAME = "gpt-5.1"


def get_default_models() -> Dict[str, Dict[str, Any]]:
    """Get the project-provided default models.

    Returns a flat mapping of model_reference -> model_config.
    """
    models: Dict[str, Dict[str, Any]] = {}
    for provider_name, provider in DEFAULT_PROVIDERS.items():
        provider_models = provider.get("models") or {}
        for model_name, model_config in provider_models.items():
            key = model_name if provider_name == "openai" else f"{provider_name}/{model_name}"
            models[key] = model_config
    return models


def get_default_providers() -> Dict[str, Dict[str, Any]]:
    """Get the project-provided default provider profiles."""
    return {k: v.copy() for k, v in DEFAULT_PROVIDERS.items()}


def get_default_model_name_fallback() -> str:
    """Get the fallback default model name."""
    return DEFAULT_MODEL_NAME
