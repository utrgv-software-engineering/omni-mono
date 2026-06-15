"""Model configuration management for Omni Code.

Model sources (merged at runtime):
1. Project defaults - Built-in providers/models shipped with omni-code
2. User config - User providers/models in ~/.config/omni_code/models.json

User config overrides project defaults for any matching provider/model.

Config schema (models.json):
{
  "version": 3,
  "default": "gpt-5.1" | "azure-prod/gpt-5.2" | null,
  "voice_default": "gpt-realtime" | "azure-prod/gpt" | null,
  "providers": {
    "openai": {
      "type": "openai",
      "api_key": "${OPENAI_API_KEY}",
      "models": {"gpt-5.1": {"model": "gpt-5.1", "label": "GPT-5.1"}}
    }
  }
}

Model references:
- Unprefixed names ("gpt-5.1") imply the default provider profile "openai".
- Other providers use "<provider_name>/<model_name>" ("azure-prod/gpt-5.2").
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from omni_code.config import PROJECT_NAME, PROJECT_NAME_WINDOWS

from omni_code.default_models import get_default_model_name_fallback, get_default_providers


MODELS_CONFIG_VERSION = 3
DEFAULT_PROVIDER_NAME = "openai"


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

    if not models_path.exists():
        return {
            "version": MODELS_CONFIG_VERSION,
            "default": None,
            "voice_default": None,
            "providers": {},
        }

    try:
        content = models_path.read_text(encoding="utf-8")
        raw = json.loads(content)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {models_path}: {e}")
        return {
            "version": MODELS_CONFIG_VERSION,
            "default": None,
            "voice_default": None,
            "providers": {},
        }

    if raw.get("version") != MODELS_CONFIG_VERSION:
        print(
            f"Warning: Unsupported models.json version {raw.get('version')}. "
            f"Expected version {MODELS_CONFIG_VERSION}."
        )
        return {
            "version": MODELS_CONFIG_VERSION,
            "default": None,
            "voice_default": None,
            "providers": {},
        }

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    default_value = raw.get("default")
    if default_value is not None and not isinstance(default_value, str):
        default_value = None

    voice_default_value = raw.get("voice_default")
    if voice_default_value is not None and not isinstance(voice_default_value, str):
        voice_default_value = None

    return {
        "version": MODELS_CONFIG_VERSION,
        "default": default_value,
        "voice_default": voice_default_value,
        "providers": providers,
    }


def save_models_config(config: Dict[str, Any]) -> None:
    """Save models.json with secure permissions."""
    path = get_models_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    to_write = {
        "version": MODELS_CONFIG_VERSION,
        "default": config.get("default"),
        "voice_default": config.get("voice_default"),
        "providers": config.get("providers") or {},
    }

    content = json.dumps(to_write, indent=2)

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def _merge_provider_config(
    base_provider: Dict[str, Any],
    override_provider: Dict[str, Any],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base_provider)
    base_models = base_provider.get("models")
    if not isinstance(base_models, dict):
        base_models = {}

    override_models = override_provider.get("models")
    if not isinstance(override_models, dict):
        override_models = {}

    merged_models = dict(base_models)
    merged_models.update(override_models)

    for key, value in override_provider.items():
        if key == "models":
            continue
        merged[key] = value

    merged["models"] = merged_models
    return merged


def get_merged_providers() -> Dict[str, Dict[str, Any]]:
    """Get all providers merged from project defaults, platform governance, and user config.

    Merge order:
    1. Project defaults (default_models.py)
    2. Platform-governed providers (from management plane policy)
    3. User config (models.json) — only if allow_user_models is true

    OAuth providers (``type: openai-oauth``) have their model list discovered
    live from the provider when signed in (see ``_apply_oauth_discovery``); the
    static seed is the offline fallback.
    """
    return _apply_oauth_discovery(_merge_providers_config())


def _apply_oauth_discovery(merged: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Replace each ``openai-oauth`` provider's models with a live fetch.

    Best-effort: when not signed in or the fetch fails, the provider keeps its
    statically-seeded models. Discovered models merge over the seed so a user's
    per-model overrides in models.json (label, reasoning, …) still apply.
    """
    for provider_config in merged.values():
        if not isinstance(provider_config, dict):
            continue
        if (provider_config.get("type") or "").strip().lower() != "openai-oauth":
            continue
        try:
            from omni_code.codex_auth import list_codex_models

            discovered = list_codex_models()
        except Exception:
            discovered = None
        if not discovered:
            continue
        seed = provider_config.get("models") if isinstance(provider_config.get("models"), dict) else {}
        provider_config["models"] = {
            slug: {**entry, **(seed.get(slug) or {})} for slug, entry in discovered.items()
        }
    return merged


def _merge_providers_config() -> Dict[str, Dict[str, Any]]:
    """Static provider merge (defaults → platform governance → user config)."""
    merged = get_default_providers()

    # Layer 2: platform governance (only when authenticated)
    governed = None
    try:
        from omni_code.platform_models import load_governed_config
        from omni_code.platform import is_authenticated
        if is_authenticated():
            governed = load_governed_config()
    except Exception:
        pass

    if governed:
        platform_providers = governed.get("providers")
        if isinstance(platform_providers, dict):
            for provider_name, provider_config in platform_providers.items():
                if not isinstance(provider_config, dict):
                    continue
                existing = merged.get(provider_name) or {}
                merged[provider_name] = _merge_provider_config(existing, provider_config)

        # If platform says no user models, skip user config
        if not governed.get("_allow_user_models", True):
            return merged

    # Layer 3: user config
    user_config = load_models_config()
    user_providers = user_config.get("providers")
    if not isinstance(user_providers, dict):
        return merged

    for provider_name, provider_config in user_providers.items():
        if not isinstance(provider_config, dict):
            continue
        existing = merged.get(provider_name) or {}
        merged[provider_name] = _merge_provider_config(existing, provider_config)

    return merged


def _format_model_ref(provider_name: str, model_name: str) -> str:
    if provider_name == DEFAULT_PROVIDER_NAME:
        return model_name
    return f"{provider_name}/{model_name}"


def _parse_model_ref(ref: str) -> Optional[tuple[str, str]]:
    if not isinstance(ref, str):
        return None
    name = (ref or "").strip()
    if not name:
        return None
    if "/" in name:
        provider_name, model_name = name.split("/", 1)
        provider_name = provider_name.strip()
        model_name = model_name.strip()
        if not provider_name or not model_name:
            return None
        return provider_name, model_name
    return DEFAULT_PROVIDER_NAME, name


def normalize_model_ref(ref: str) -> Optional[str]:
    parsed = _parse_model_ref(ref)
    if not parsed:
        return None
    provider_name, model_name = parsed
    return _format_model_ref(provider_name, model_name)


def get_model_config(ref: str) -> Optional[Dict[str, Any]]:
    """Get the merged (provider + model) configuration for a model reference."""
    parsed = _parse_model_ref(ref)
    if not parsed:
        return None

    provider_name, model_name = parsed
    providers = get_merged_providers()
    provider_config = providers.get(provider_name)
    if not isinstance(provider_config, dict):
        return None
    provider_models = provider_config.get("models")
    if not isinstance(provider_models, dict):
        return None
    model_config = provider_models.get(model_name)
    if not isinstance(model_config, dict):
        return None

    provider_type = provider_config.get("type") or provider_config.get("provider") or "unknown"
    merged: Dict[str, Any] = {
        "name": _format_model_ref(provider_name, model_name),
        "provider": provider_type,
        "provider_name": provider_name,
        "model": model_config.get("model") or model_name,
        "label": model_config.get("label") or model_name,
    }

    for key in [
        "api_key",
        "base_url",
        "api_version",
        "credential_ref",
        "realtime_url",
        "realtime_base_url",
        "realtime",
        "reasoning",
        "max_input_tokens",
        "max_output_tokens",
        "model_settings",
        "use_responses",
        "use_responses_websocket",
    ]:
        if key in provider_config:
            merged[key] = provider_config.get(key)
        if key in model_config:
            merged[key] = model_config.get(key)

    return merged


def get_default_model_name() -> Optional[str]:
    """Get the name of the default model.

    Priority:
    1. Platform-governed default (from management plane policy)
    2. User-configured default in models.json
    3. Project default (from default_models.py)
    """
    # Platform governance takes priority — but only when authenticated.
    # A stale governed config on disk should not override user settings
    # when the user is not connected to the management plane.
    try:
        from omni_code.platform_models import load_governed_config
        from omni_code.platform import is_authenticated
        if is_authenticated():
            governed = load_governed_config()
            if governed and governed.get("default"):
                gov_default = governed["default"]
                merged = get_model_config(gov_default)
                if merged and not merged.get("realtime"):
                    normalized = normalize_model_ref(gov_default)
                    if normalized:
                        return normalized
            elif not governed:
                # Credentials exist but governed config is missing — re-apply
                from omni_code.platform_models import apply_platform_model_governance
                apply_platform_model_governance()
                governed = load_governed_config()
                if governed and governed.get("default"):
                    gov_default = governed["default"]
                    normalized = normalize_model_ref(gov_default)
                    if normalized:
                        return normalized
    except Exception:
        pass

    config = load_models_config()
    user_default = config.get("default")

    if user_default:
        merged = get_model_config(user_default)
        if merged and not merged.get("realtime"):
            normalized = normalize_model_ref(user_default)
            if normalized:
                return normalized

    return get_default_model_name_fallback()


def get_voice_default_model_name() -> Optional[str]:
    """Get the name of the default voice (realtime) model."""

    config = load_models_config()
    user_default = config.get("voice_default")
    if user_default and get_model_config(user_default):
        normalized = normalize_model_ref(user_default)
        if normalized:
            merged = get_model_config(normalized) or {}
            if merged.get("realtime"):
                return normalized
    return None


def get_voice_default_model_config() -> Optional[Dict[str, Any]]:
    name = get_voice_default_model_name()
    if not name:
        return None
    return get_model_config(name)


def get_default_model_config() -> Optional[Dict[str, Any]]:
    """Get configuration for the default model."""
    default_name = get_default_model_name()
    if default_name:
        return get_model_config(default_name)
    return None


def is_user_model(ref: str) -> bool:
    """Check if a model is user-defined (vs project default)."""
    parsed = _parse_model_ref(ref)
    if not parsed:
        return False

    provider_name, model_name = parsed
    config = load_models_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return False
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return False
    models = provider.get("models")
    if not isinstance(models, dict):
        return False
    return model_name in models


def list_models() -> List[Dict[str, Any]]:
    """List all available models (merged from project defaults and user config)."""
    providers = get_merged_providers()
    default_name = get_default_model_name()
    voice_default_name = get_voice_default_model_name()
    results: List[Dict[str, Any]] = []

    for provider_name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        provider_type = provider_config.get("type") or provider_config.get("provider") or "unknown"
        provider_models = provider_config.get("models")
        if not isinstance(provider_models, dict):
            continue
        for model_name, model_config in provider_models.items():
            if not isinstance(model_config, dict):
                continue
            ref = _format_model_ref(provider_name, model_name)
            merged = get_model_config(ref) or {}
            entry = {
                "name": ref,
                "label": merged.get("label", ref),
                "provider": provider_type,
                "provider_name": provider_name,
                "model": merged.get("model"),
                "is_default": ref == default_name,
                "is_voice_default": ref == voice_default_name,
                "is_user_defined": is_user_model(ref),
                "realtime": bool(merged.get("realtime")),
                "reasoning": merged.get("reasoning"),
                "max_input_tokens": merged.get("max_input_tokens"),
                "max_output_tokens": merged.get("max_output_tokens"),
                "has_api_key": bool(merged.get("api_key")),
            }
            if merged.get("model_settings"):
                entry["model_settings"] = merged["model_settings"]
            results.append(entry)

    results.sort(key=lambda item: item["name"])
    return results


def add_model(
    *,
    provider_name: str,
    provider_type: str,
    model_name: str,
    model: str,
    label: Optional[str] = None,
    provider_api_key: Optional[str] = None,
    provider_base_url: Optional[str] = None,
    provider_api_version: Optional[str] = None,
    provider_realtime_url: Optional[str] = None,
    provider_realtime_base_url: Optional[str] = None,
    model_api_key: Optional[str] = None,
    realtime: Optional[bool] = None,
    realtime_url: Optional[str] = None,
    realtime_base_url: Optional[str] = None,
    reasoning: Optional[str] = None,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    model_settings: Optional[Dict[str, Any]] = None,
    set_default: bool = False,
    set_voice_default: bool = False,
) -> None:
    """Add or update a model under a provider profile."""
    config = load_models_config()
    providers = config.setdefault("providers", {})

    provider_obj = providers.get(provider_name)
    if not isinstance(provider_obj, dict):
        provider_obj = {}

    provider_obj["type"] = provider_type
    if provider_api_key is not None:
        provider_obj["api_key"] = provider_api_key
    if provider_base_url is not None:
        provider_obj["base_url"] = provider_base_url
    if provider_api_version is not None:
        provider_obj["api_version"] = provider_api_version
    if provider_realtime_url is not None:
        provider_obj["realtime_url"] = provider_realtime_url
    if provider_realtime_base_url is not None:
        provider_obj["realtime_base_url"] = provider_realtime_base_url

    models_obj = provider_obj.get("models")
    if not isinstance(models_obj, dict):
        models_obj = {}

    model_config: Dict[str, Any] = {
        "model": model,
        "label": label or model_name,
    }
    if model_api_key is not None:
        model_config["api_key"] = model_api_key
    if realtime is not None:
        model_config["realtime"] = bool(realtime)
    if realtime_url is not None:
        model_config["realtime_url"] = realtime_url
    if realtime_base_url is not None:
        model_config["realtime_base_url"] = realtime_base_url
    if reasoning:
        model_config["reasoning"] = reasoning
    if max_input_tokens is not None:
        model_config["max_input_tokens"] = max_input_tokens
    if max_output_tokens is not None:
        model_config["max_output_tokens"] = max_output_tokens
    if model_settings is not None:
        model_config["model_settings"] = model_settings

    models_obj[model_name] = model_config
    provider_obj["models"] = models_obj
    providers[provider_name] = provider_obj

    model_ref = _format_model_ref(provider_name, model_name)
    if set_default or not config.get("default"):
        config["default"] = model_ref
    if set_voice_default and realtime:
        config["voice_default"] = model_ref

    save_models_config(config)


def remove_model(name: str) -> bool:
    """Remove a user-defined model configuration. Returns True if removed.

    Note: Cannot remove project default models, only user-defined ones.
    """
    config = load_models_config()
    parsed = _parse_model_ref(name)
    if not parsed:
        return False

    provider_name, model_name = parsed
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return False
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return False
    models_obj = provider.get("models")
    if not isinstance(models_obj, dict) or model_name not in models_obj:
        return False

    del models_obj[model_name]
    provider["models"] = models_obj
    providers[provider_name] = provider
    config["providers"] = providers

    normalized = normalize_model_ref(name)
    if normalized and config.get("default") == normalized:
        config["default"] = None

    save_models_config(config)
    return True


def set_default_model(name: str) -> bool:
    """Set the default model. Returns True if successful.

    Works with both project default models and user-defined models.
    """
    normalized = normalize_model_ref(name)
    config = get_model_config(normalized) if normalized else None
    if not normalized or not config or config.get("realtime"):
        return False
    config = load_models_config()
    config["default"] = normalized
    save_models_config(config)
    return True


def set_voice_default_model(name: str) -> bool:
    normalized = normalize_model_ref(name)
    if not normalized:
        return False
    config = get_model_config(normalized)
    if not config or not config.get("realtime"):
        return False
    models_config = load_models_config()
    models_config["voice_default"] = normalized
    save_models_config(models_config)
    return True


def list_text_models() -> List[Dict[str, Any]]:
    return [m for m in list_models() if not m.get("realtime")]


def list_voice_models() -> List[Dict[str, Any]]:
    return [m for m in list_models() if m.get("realtime")]


def resolve_voice_settings_for_realtime(model_name: Optional[str] = None) -> Dict[str, Any]:
    name = normalize_model_ref(model_name) if model_name else get_voice_default_model_name()
    if not name:
        return {}
    merged = get_model_config(name)
    if not merged or not merged.get("realtime"):
        return {}
    runtime = resolve_model_for_runtime(name)
    provider = (runtime.get("provider") or "").strip().lower()
    api_key = runtime.get("api_key") or ""
    base_url = runtime.get("base_url")
    settings: Dict[str, Any] = {
        "openai_api_key": api_key,
        "openai_realtime_url": None,
        "openai_realtime_base_url": None,
    }

    if provider == "azure":
        if base_url:
            settings["openai_base_url"] = base_url
            settings["azure_openai_endpoint"] = base_url
        api_version = runtime.get("api_version")
        if api_version:
            settings["azure_openai_api_version"] = api_version
        deployment = runtime.get("model")
        if deployment:
            settings["azure_openai_deployment_name"] = deployment
    else:
        if base_url:
            settings["openai_base_url"] = base_url
        else:
            settings["openai_base_url"] = "https://api.openai.com/v1"
        model_id = runtime.get("model")
        if model_id:
            settings["openai_realtime_model"] = model_id

    realtime_url = merged.get("realtime_url")
    if realtime_url:
        settings["openai_realtime_url"] = _expand_env_vars(realtime_url)
    realtime_base_url = merged.get("realtime_base_url")
    if realtime_base_url:
        settings["openai_realtime_base_url"] = _expand_env_vars(realtime_base_url)

    return settings


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
    name = normalize_model_ref(model_name) if model_name else get_default_model_name()
    if not name:
        return {}

    merged = get_model_config(name)
    if not merged:
        return {}

    provider = (merged.get("provider") or "openai").strip().lower()

    result: Dict[str, Any] = {
        "name": name,
        "provider": provider,
        "provider_name": merged.get("provider_name"),
        "model": _expand_env_vars(merged.get("model", "")),
        "label": merged.get("label", name),
        "reasoning": merged.get("reasoning"),
        "max_input_tokens": merged.get("max_input_tokens"),
        "max_output_tokens": merged.get("max_output_tokens"),
    }

    if "use_responses" in merged:
        result["use_responses"] = merged["use_responses"]
    if "use_responses_websocket" in merged:
        result["use_responses_websocket"] = merged["use_responses_websocket"]

    if merged.get("model_settings"):
        result["model_settings"] = merged["model_settings"]

    if merged.get("base_url"):
        result["base_url"] = _expand_env_vars(merged["base_url"])

    if merged.get("api_version"):
        result["api_version"] = merged["api_version"]

    # Priority 1: Platform credential via credential_ref
    credential_ref = merged.get("credential_ref")
    if credential_ref:
        try:
            from omni_code.platform_models import fetch_provider_credential

            cred = fetch_provider_credential(credential_ref)
            if cred:
                cred_type = cred.get("credential_type")
                if cred_type == "entra_token":
                    result["api_key"] = cred["access_token"]
                elif cred_type == "api_key":
                    result["api_key"] = cred["api_key"]
                # Also pick up base_url/api_version from credential if not already set
                if not result.get("base_url") and cred.get("base_url"):
                    result["base_url"] = cred["base_url"]
                if not result.get("api_version") and cred.get("api_version"):
                    result["api_version"] = cred["api_version"]
        except Exception:
            pass

    # Priority 2: Provider config api_key
    if not result.get("api_key"):
        api_key_value = merged.get("api_key")
        if api_key_value:
            resolved_key = _expand_env_vars(api_key_value)
            result["api_key"] = resolved_key
        else:
            result["api_key"] = None

    # Priority 3: Environment variable fallback
    if not result.get("api_key"):
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


def is_user_provider(name: str) -> bool:
    """Check if a provider exists in the user config."""
    config = load_models_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return False
    return name in providers


def list_providers() -> List[Dict[str, Any]]:
    """List all providers (merged from defaults and user config) with summary info."""
    merged = get_merged_providers()
    user_config = load_models_config()
    user_providers = user_config.get("providers")
    if not isinstance(user_providers, dict):
        user_providers = {}

    results: List[Dict[str, Any]] = []
    for name, provider in merged.items():
        if not isinstance(provider, dict):
            continue
        provider_type = provider.get("type") or provider.get("provider") or "unknown"
        models = provider.get("models")
        model_count = len(models) if isinstance(models, dict) else 0
        results.append({
            "name": name,
            "type": provider_type,
            "base_url": provider.get("base_url"),
            "has_api_key": bool(provider.get("api_key")),
            "model_count": model_count,
            "is_user_defined": name in user_providers,
        })

    results.sort(key=lambda item: item["name"])
    return results


def get_provider(name: str) -> Optional[Dict[str, Any]]:
    """Get a single provider's full config from merged providers."""
    merged = get_merged_providers()
    provider = merged.get(name)
    if not isinstance(provider, dict):
        return None
    return dict(provider)


def add_provider(
    name: str,
    provider_type: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_version: Optional[str] = None,
    realtime_url: Optional[str] = None,
    realtime_base_url: Optional[str] = None,
) -> None:
    """Create or update a provider profile in user config (without touching models)."""
    config = load_models_config()
    providers = config.setdefault("providers", {})

    provider_obj = providers.get(name)
    if not isinstance(provider_obj, dict):
        provider_obj = {}

    provider_obj["type"] = provider_type
    if api_key is not None:
        provider_obj["api_key"] = api_key
    if base_url is not None:
        provider_obj["base_url"] = base_url
    if api_version is not None:
        provider_obj["api_version"] = api_version
    if realtime_url is not None:
        provider_obj["realtime_url"] = realtime_url
    if realtime_base_url is not None:
        provider_obj["realtime_base_url"] = realtime_base_url

    # Preserve existing models
    if "models" not in provider_obj:
        provider_obj["models"] = {}

    providers[name] = provider_obj
    save_models_config(config)


def update_provider(name: str, **kwargs: Any) -> bool:
    """Update specific fields on a user-defined provider. Returns False if not found."""
    config = load_models_config()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return False
    provider_obj = providers.get(name)
    if not isinstance(provider_obj, dict):
        return False

    allowed_keys = {"type", "api_key", "base_url", "api_version", "realtime_url", "realtime_base_url", "use_responses", "use_responses_websocket"}
    for key, value in kwargs.items():
        if key in allowed_keys and value is not None:
            provider_obj[key] = value

    providers[name] = provider_obj
    save_models_config(config)
    return True


def remove_provider(name: str) -> bool:
    """Remove a user-defined provider and all its models. Returns False if not found."""
    config = load_models_config()
    providers = config.get("providers")
    if not isinstance(providers, dict) or name not in providers:
        return False

    del providers[name]

    # Clear default/voice_default if they referenced models under this provider
    default_ref = config.get("default")
    if default_ref:
        parsed = _parse_model_ref(default_ref)
        if parsed and parsed[0] == name:
            config["default"] = None

    voice_ref = config.get("voice_default")
    if voice_ref:
        parsed = _parse_model_ref(voice_ref)
        if parsed and parsed[0] == name:
            config["voice_default"] = None

    save_models_config(config)
    return True


def has_models_configured() -> bool:
    """Check whether a usable default model is configured."""
    resolved = resolve_model_for_runtime()
    if not resolved:
        return False

    provider = (resolved.get("provider") or "").strip().lower()

    if provider == "openai-compatible":
        return bool(resolved.get("model")) and bool(resolved.get("base_url"))

    if provider == "openai-oauth":
        from omni_code.codex_auth import is_authenticated

        return is_authenticated()

    return bool(resolved.get("api_key"))
