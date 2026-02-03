"""Model configuration management for Omni Code.

This module handles loading, saving, and migrating model configurations.

Model sources (merged at runtime):
1. Project defaults - Built-in models shipped with omni-code (see default_models.py)
2. User config - User-added models in ~/.config/omni_code/models.json

User config models override project defaults if they share the same name.

Supports:
- OpenAI API
- Azure OpenAI
- OpenAI-compatible endpoints (Ollama, LM Studio, vLLM, etc.)
- LiteLLM providers (Anthropic, Google, Mistral, etc.)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from omni_code.config import PROJECT_NAME, PROJECT_NAME_WINDOWS
from omni_code.default_models import get_default_models, get_default_model_name_fallback


def get_config_dir() -> Path:
    """Get the config directory for omni_code."""
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / PROJECT_NAME_WINDOWS
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override) / PROJECT_NAME
    return Path.home() / ".config" / PROJECT_NAME


def get_models_config_path() -> Path:
    """Get path to models.json config file."""
    return get_config_dir() / "models.json"


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings."""
    if isinstance(value, str):
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            env_value = os.getenv(var_name)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            return ""

        return re.sub(pattern, replace_var, value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_models_config() -> Dict[str, Any]:
    """Load models config from models.json."""
    models_path = get_models_config_path()

    if models_path.exists():
        try:
            content = models_path.read_text(encoding="utf-8")
            return json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load {models_path}: {e}")
            return {"version": 2, "models": {}}

    return {"version": 2, "models": {}}


def save_models_config(config: Dict[str, Any]) -> None:
    """Save models.json with secure permissions."""
    path = get_models_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(config, indent=2)

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    # Unix: write with 0600 permissions for security (contains API keys)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def get_merged_models() -> Dict[str, Dict[str, Any]]:
    """Get all models merged from project defaults and user config.

    User config models override project defaults with the same name.
    """
    # Start with project defaults
    merged = get_default_models()

    # Override with user config
    user_config = load_models_config()
    for name, model_config in user_config.get("models", {}).items():
        merged[name] = model_config

    return merged


def get_model_config(name: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific model by name.

    Checks both user config and project defaults.
    """
    merged = get_merged_models()
    return merged.get(name)


def get_default_model_name() -> Optional[str]:
    """Get the name of the default model.

    Priority:
    1. User-configured default in models.json
    2. Project default (from default_models.py)
    """
    config = load_models_config()
    user_default = config.get("default")

    if user_default:
        # Verify it exists in merged models
        merged = get_merged_models()
        if user_default in merged:
            return user_default

    # Fall back to project default
    return get_default_model_name_fallback()


def get_default_model_config() -> Optional[Dict[str, Any]]:
    """Get configuration for the default model."""
    default_name = get_default_model_name()
    if default_name:
        return get_model_config(default_name)
    return None


def is_user_model(name: str) -> bool:
    """Check if a model is user-defined (vs project default)."""
    config = load_models_config()
    return name in config.get("models", {})


def list_models() -> List[Dict[str, Any]]:
    """List all available models (merged from project defaults and user config)."""
    merged = get_merged_models()
    default_name = get_default_model_name()
    models = []

    for name, model_config in merged.items():
        entry = {
            "name": name,
            "label": model_config.get("label", name),
            "provider": model_config.get("provider", "unknown"),
            "model": model_config.get("model"),
            "is_default": name == default_name,
            "is_user_defined": is_user_model(name),
            "reasoning": model_config.get("reasoning"),
            "max_input_tokens": model_config.get("max_input_tokens"),
            "max_output_tokens": model_config.get("max_output_tokens"),
            "has_api_key": bool(model_config.get("api_key")),
        }
        # Include model_settings if present (for reasoning models, etc.)
        if model_config.get("model_settings"):
            entry["model_settings"] = model_config["model_settings"]
        models.append(entry)

    return models


def add_model(
    name: str,
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    label: Optional[str] = None,
    base_url: Optional[str] = None,
    api_version: Optional[str] = None,
    reasoning: Optional[str] = None,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    model_settings: Optional[Dict[str, Any]] = None,
    set_default: bool = False,
) -> None:
    """Add a new model configuration.

    Args:
        name: Unique identifier for this model configuration
        provider: Provider type ("openai", "azure", "openai-compatible", "litellm")
        model: Model identifier (e.g., "gpt-5.1", "claude-3-5-sonnet")
        api_key: API key for this model (can use ${ENV_VAR} syntax)
        label: Display name for the model
        base_url: Custom API endpoint URL
        api_version: API version (for Azure)
        reasoning: Default reasoning effort ("low", "medium", "high")
        max_input_tokens: Maximum input context tokens
        max_output_tokens: Maximum output tokens
        model_settings: Model-specific settings dict (store, extra_body, etc.)
            For reasoning models, you may need:
            {"store": False, "extra_body": {"include": ["reasoning.encrypted_content"]}}
        set_default: Whether to set this as the default model
    """
    config = load_models_config()

    model_config: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "label": label or name,
    }

    if api_key:
        model_config["api_key"] = api_key
    if base_url:
        model_config["base_url"] = base_url
    if api_version:
        model_config["api_version"] = api_version
    if reasoning:
        model_config["reasoning"] = reasoning
    if max_input_tokens is not None:
        model_config["max_input_tokens"] = max_input_tokens
    if max_output_tokens is not None:
        model_config["max_output_tokens"] = max_output_tokens
    if model_settings is not None:
        model_config["model_settings"] = model_settings

    if "models" not in config:
        config["models"] = {}
    config["models"][name] = model_config

    if set_default or not config.get("default"):
        config["default"] = name

    save_models_config(config)


def remove_model(name: str) -> bool:
    """Remove a user-defined model configuration. Returns True if removed.

    Note: Cannot remove project default models, only user-defined ones.
    """
    config = load_models_config()

    if name not in config.get("models", {}):
        return False

    del config["models"][name]

    # If we removed the default, reset to project default
    if config.get("default") == name:
        config["default"] = None  # Will fall back to project default

    save_models_config(config)
    return True


def set_default_model(name: str) -> bool:
    """Set the default model. Returns True if successful.

    Works with both project default models and user-defined models.
    """
    # Check if model exists in merged models
    merged = get_merged_models()
    if name not in merged:
        return False

    config = load_models_config()
    config["default"] = name
    save_models_config(config)
    return True


def resolve_model_for_runtime(
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a model configuration for runtime use.

    Returns a dict with:
    - provider: str ("openai", "azure", "openai-compatible", "litellm")
    - model: str (the model identifier)
    - api_key: str (resolved API key from model config or environment)
    - base_url: Optional[str]
    - api_version: Optional[str] (for Azure)
    - reasoning: Optional[str] ("low", "medium", "high")
    - max_input_tokens: Optional[int] (model's max input context)
    - max_output_tokens: Optional[int] (model's max output tokens)
    - model_settings: Optional[Dict] (model-specific settings)
    - label: str

    Expands environment variables in api_key and base_url.
    Uses merged models (project defaults + user config).
    """
    merged = get_merged_models()

    # Determine which model to use
    name = model_name or get_default_model_name()
    if not name or name not in merged:
        # No model configured
        return {}

    model_config = merged[name]
    provider = model_config.get("provider", "openai")

    result: Dict[str, Any] = {
        "name": name,
        "provider": provider,
        "model": _expand_env_vars(model_config.get("model", "")),
        "label": model_config.get("label", name),
        "reasoning": model_config.get("reasoning"),
        "max_input_tokens": model_config.get("max_input_tokens"),
        "max_output_tokens": model_config.get("max_output_tokens"),
    }

    # Include model_settings if present
    if model_config.get("model_settings"):
        result["model_settings"] = model_config["model_settings"]

    # Resolve base_url
    if model_config.get("base_url"):
        result["base_url"] = _expand_env_vars(model_config["base_url"])

    # Resolve API version (Azure)
    if model_config.get("api_version"):
        result["api_version"] = model_config["api_version"]

    # Resolve API key: model config first, then environment fallback
    api_key = model_config.get("api_key")
    if api_key:
        result["api_key"] = _expand_env_vars(api_key)
    else:
        # Fallback to environment variable based on provider
        if provider == "azure":
            result["api_key"] = os.getenv("AZURE_OPENAI_API_KEY")
        elif provider == "litellm":
            model_str = result.get("model", "")
            if model_str.startswith("anthropic/") or model_str.startswith("claude"):
                result["api_key"] = os.getenv("ANTHROPIC_API_KEY")
            elif model_str.startswith("gemini/") or model_str.startswith("google/"):
                result["api_key"] = os.getenv("GOOGLE_API_KEY")
            else:
                result["api_key"] = os.getenv("OPENAI_API_KEY")
        else:
            result["api_key"] = os.getenv("OPENAI_API_KEY")

    return result


def apply_api_keys_to_env() -> None:
    """Apply model API keys to environment variables.

    Sets OPENAI_API_KEY from the default model's api_key if not already set.
    This ensures the SDK can find the API key without explicit configuration.
    """
    # Get the default model's API key
    default_config = resolve_model_for_runtime()
    if not default_config:
        return

    api_key = default_config.get("api_key")
    provider = default_config.get("provider", "openai")

    if api_key:
        # Set the appropriate environment variable based on provider
        if provider == "azure":
            if "AZURE_OPENAI_API_KEY" not in os.environ:
                os.environ["AZURE_OPENAI_API_KEY"] = api_key
        elif provider == "litellm":
            model_str = default_config.get("model", "")
            if model_str.startswith("anthropic/") or model_str.startswith("claude"):
                if "ANTHROPIC_API_KEY" not in os.environ:
                    os.environ["ANTHROPIC_API_KEY"] = api_key
            elif model_str.startswith("gemini/") or model_str.startswith("google/"):
                if "GOOGLE_API_KEY" not in os.environ:
                    os.environ["GOOGLE_API_KEY"] = api_key
            else:
                if "OPENAI_API_KEY" not in os.environ:
                    os.environ["OPENAI_API_KEY"] = api_key
        else:
            if "OPENAI_API_KEY" not in os.environ:
                os.environ["OPENAI_API_KEY"] = api_key


def has_models_configured() -> bool:
    """Check whether a usable default model is configured."""
    resolved = resolve_model_for_runtime()
    if not resolved:
        return False

    provider = (resolved.get("provider") or "").strip().lower()

    if provider == "openai-compatible":
        return bool(resolved.get("model")) and bool(resolved.get("base_url"))

    return bool(resolved.get("api_key"))
