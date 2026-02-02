"""Default models provided by omni-code.

These are the built-in models that ship with omni-code.
Users can add their own models via ~/.config/omni-code/models.json,
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

# Project-provided default models
# Users don't need to configure these - they work out of the box with an OpenAI API key
DEFAULT_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-5.1": {
        "provider": "openai",
        "model": "gpt-5.1",
        "label": "GPT-5.1",
        "max_input_tokens": 272000,
        "max_output_tokens": 128000,
        "model_settings": _REASONING_MODEL_SETTINGS,
    },
    "gpt-5.1-codex": {
        "provider": "openai",
        "model": "gpt-5.1-codex",
        "label": "GPT-5.1 Codex",
        "max_input_tokens": 272000,
        "max_output_tokens": 128000,
        "model_settings": _REASONING_MODEL_SETTINGS,
    },
    "gpt-5.2": {
        "provider": "openai",
        "model": "gpt-5.2",
        "label": "GPT-5.2",
        "max_input_tokens": 272000,
        "max_output_tokens": 128000,
        "model_settings": _REASONING_MODEL_SETTINGS,
    },
}

# Default model to use if user hasn't set one
DEFAULT_MODEL_NAME = "gpt-5.1"


def get_default_models() -> Dict[str, Dict[str, Any]]:
    """Get the project-provided default models."""
    return DEFAULT_MODELS.copy()


def get_default_model_name_fallback() -> str:
    """Get the fallback default model name."""
    return DEFAULT_MODEL_NAME
