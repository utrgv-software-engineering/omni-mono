from omniagents import model_config_resolver


@model_config_resolver
def openai_compatible_model():
    return {
        "name": "openai_compatible_default",
        "label": "OpenAI-compatible",
        "provider": "openai-compatible",
        "model": "gpt-4o-mini",
        "base_url": "${OPENAI_BASE_URL}",
        "api_key": "${OPENAI_API_KEY}",
    }
