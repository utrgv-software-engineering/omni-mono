"""Product-agnostic model configuration store (``models.json``).

Manages a per-user model/provider registry and resolves a named model to
a runtime config dict shaped for
``omniagents.core.agents.run_options.build_model_provider``.

Config schema (models.json, version 3)::

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

Model references: unprefixed names ("gpt-5.1") imply the default provider
profile; other providers use "<provider_name>/<model_name>".

Merge order for the effective provider map:

1. Product defaults (the ``default_providers`` hook)
2. Governed providers (the ``governed_config`` hook — e.g. a management
   plane policy; may also veto user models via ``_allow_user_models``)
3. User config (models.json)

The store is generic; products supply identity and integrations through
constructor hooks (see :class:`ModelConfigStore`). All hooks are
optional except ``config_dir``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MODELS_CONFIG_VERSION = 3

__all__ = [
    "ModelConfigStore",
    "MODELS_CONFIG_VERSION",
    "litellm_api_key_env",
    "expand_env_vars",
    "normalize_model_name",
]


def litellm_api_key_env(model: str | None) -> str:
    """The env var LiteLLM reads the API key from, by model prefix."""
    model_name = (model or "").lower()
    if model_name.startswith("anthropic/") or model_name.startswith("claude"):
        return "ANTHROPIC_API_KEY"
    if model_name.startswith("gemini/") or model_name.startswith("google/"):
        return "GOOGLE_API_KEY"
    return "OPENAI_API_KEY"


def normalize_model_name(
    model_ref: Optional[str], runtime: Dict[str, Any]
) -> Optional[str]:
    """The bare model id to hand a provider, from a resolved runtime dict.

    The counterpart of :meth:`ModelConfigStore.parse_model_ref` for the
    *runtime* side: where refs are ``<provider_name>/<model_name>``, the
    runtime's ``model`` may still carry a routing prefix. Strip it unless
    it is one the SDK's providers route on themselves (``openai/...``,
    ``litellm/...``) or the runtime's provider is ``litellm`` (LiteLLM
    model ids are ``<vendor>/<model>`` and must stay whole). Falls back
    to *model_ref* when the runtime carries no model id.
    """
    model_name = runtime.get("model") if isinstance(runtime, dict) else None
    if (not isinstance(model_name, str) or not model_name.strip()) and isinstance(
        model_ref, str
    ):
        model_name = model_ref.strip()
    if (
        isinstance(model_name, str)
        and "/" in model_name
        and runtime.get("provider") != "litellm"
    ):
        prefix = model_name.split("/", 1)[0]
        if prefix not in {"openai", "litellm"}:
            model_name = model_name.split("/", 1)[1]
    return model_name


def expand_env_vars(value: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` in strings."""
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
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    return value


class ModelConfigStore:
    """A per-user model/provider registry with layered merge + resolution.

    Args:
        config_dir: Callable returning the product config directory
            (``models.json`` lives directly inside it).
        default_providers: Product's built-in provider/model catalog.
        default_model_fallback: Model ref to fall back to when neither
            governance nor the user configured a default.
        governed_config: Returns the governed model config (dict with
            optional ``providers``, ``default``, ``_allow_user_models``)
            or None when governance doesn't apply. The hook owns any
            auth checks and cache refreshing.
        oauth_models: Live model discovery for ``type: openai-oauth``
            providers (slug → model entry), or None when signed out.
        oauth_authenticated: Whether the OAuth provider is signed in
            (used by :meth:`has_models_configured`).
        credential_resolver: Resolves a ``credential_ref`` on a model
            config to a credential dict (``credential_type`` of
            ``api_key``/``entra_token`` plus optional base_url and
            api_version), or None.
        default_provider_name: Provider profile implied by unprefixed
            model refs.
    """

    def __init__(
        self,
        *,
        config_dir: Callable[[], Path],
        default_providers: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
        default_model_fallback: Optional[Callable[[], Optional[str]]] = None,
        governed_config: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        oauth_models: Optional[
            Callable[[], Optional[Dict[str, Dict[str, Any]]]]
        ] = None,
        oauth_authenticated: Optional[Callable[[], bool]] = None,
        credential_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        default_provider_name: str = "openai",
    ) -> None:
        self._config_dir = config_dir
        self._default_providers = default_providers or (lambda: {})
        self._default_model_fallback = default_model_fallback or (lambda: None)
        self._governed_config = governed_config
        self._oauth_models = oauth_models
        self._oauth_authenticated = oauth_authenticated
        self._credential_resolver = credential_resolver
        self.default_provider_name = default_provider_name

    # -- config file ----------------------------------------------------

    def config_path(self) -> Path:
        return Path(self._config_dir()) / "models.json"

    def _empty_config(self) -> Dict[str, Any]:
        return {
            "version": MODELS_CONFIG_VERSION,
            "default": None,
            "voice_default": None,
            "providers": {},
        }

    def load_config(self) -> Dict[str, Any]:
        """Load the user's models.json (empty schema when absent/invalid)."""
        models_path = self.config_path()

        if not models_path.exists():
            return self._empty_config()

        try:
            content = models_path.read_text(encoding="utf-8")
            raw = json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load {models_path}: {e}")
            return self._empty_config()

        if raw.get("version") != MODELS_CONFIG_VERSION:
            print(
                f"Warning: Unsupported models.json version {raw.get('version')}. "
                f"Expected version {MODELS_CONFIG_VERSION}."
            )
            return self._empty_config()

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

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save models.json with secure permissions."""
        path = self.config_path()
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

    # -- merge ----------------------------------------------------------

    @staticmethod
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

    def _governed(self) -> Optional[Dict[str, Any]]:
        if self._governed_config is None:
            return None
        try:
            return self._governed_config()
        except Exception:
            return None

    def _apply_oauth_discovery(
        self, merged: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Replace each ``openai-oauth`` provider's models with a live fetch.

        Best-effort: when not signed in or the fetch fails, the provider
        keeps its statically-seeded models. Discovered models merge over
        the seed so per-model overrides in models.json still apply.
        """
        if self._oauth_models is None:
            return merged
        for provider_config in merged.values():
            if not isinstance(provider_config, dict):
                continue
            if (provider_config.get("type") or "").strip().lower() != "openai-oauth":
                continue
            try:
                discovered = self._oauth_models()
            except Exception:
                discovered = None
            if not discovered:
                continue
            seed_candidate = provider_config.get("models")
            seed = seed_candidate if isinstance(seed_candidate, dict) else {}
            provider_config["models"] = {
                slug: {**entry, **(seed.get(slug) or {})}
                for slug, entry in discovered.items()
            }
        return merged

    def _merge_providers_config(self) -> Dict[str, Dict[str, Any]]:
        """Static provider merge (defaults → governance → user config)."""
        merged = self._default_providers()

        governed = self._governed()
        if governed:
            platform_providers = governed.get("providers")
            if isinstance(platform_providers, dict):
                for provider_name, provider_config in platform_providers.items():
                    if not isinstance(provider_config, dict):
                        continue
                    existing = merged.get(provider_name) or {}
                    merged[provider_name] = self._merge_provider_config(
                        existing, provider_config
                    )

            # Governance may veto user-defined models entirely.
            if not governed.get("_allow_user_models", True):
                return merged

        user_config = self.load_config()
        user_providers = user_config.get("providers")
        if not isinstance(user_providers, dict):
            return merged

        for provider_name, provider_config in user_providers.items():
            if not isinstance(provider_config, dict):
                continue
            existing = merged.get(provider_name) or {}
            merged[provider_name] = self._merge_provider_config(
                existing, provider_config
            )

        return merged

    def merged_providers(self) -> Dict[str, Dict[str, Any]]:
        """All providers merged from defaults, governance, and user config."""
        return self._apply_oauth_discovery(self._merge_providers_config())

    # -- refs -----------------------------------------------------------

    def format_model_ref(self, provider_name: str, model_name: str) -> str:
        if provider_name == self.default_provider_name:
            return model_name
        return f"{provider_name}/{model_name}"

    def parse_model_ref(self, ref: str) -> Optional[tuple[str, str]]:
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
        return self.default_provider_name, name

    def normalize_model_ref(self, ref: str) -> Optional[str]:
        parsed = self.parse_model_ref(ref)
        if not parsed:
            return None
        provider_name, model_name = parsed
        return self.format_model_ref(provider_name, model_name)

    # -- lookup ---------------------------------------------------------

    _MODEL_CONFIG_KEYS = [
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
    ]

    def get_model_config(self, ref: str) -> Optional[Dict[str, Any]]:
        """The merged (provider + model) configuration for a model ref."""
        parsed = self.parse_model_ref(ref)
        if not parsed:
            return None

        provider_name, model_name = parsed
        providers = self.merged_providers()
        provider_config = providers.get(provider_name)
        if not isinstance(provider_config, dict):
            return None
        provider_models = provider_config.get("models")
        if not isinstance(provider_models, dict):
            return None
        model_config = provider_models.get(model_name)
        if not isinstance(model_config, dict):
            return None

        provider_type = (
            provider_config.get("type") or provider_config.get("provider") or "unknown"
        )
        merged: Dict[str, Any] = {
            "name": self.format_model_ref(provider_name, model_name),
            "provider": provider_type,
            "provider_name": provider_name,
            "model": model_config.get("model") or model_name,
            "label": model_config.get("label") or model_name,
        }

        for key in self._MODEL_CONFIG_KEYS:
            if key in provider_config:
                merged[key] = provider_config.get(key)
            if key in model_config:
                merged[key] = model_config.get(key)

        return merged

    def get_default_model_name(self) -> Optional[str]:
        """The default model ref: governance, then user config, then the
        product fallback."""
        governed = self._governed()
        if governed and governed.get("default"):
            gov_default = governed["default"]
            merged = self.get_model_config(gov_default)
            if merged and not merged.get("realtime"):
                normalized = self.normalize_model_ref(gov_default)
                if normalized:
                    return normalized

        config = self.load_config()
        user_default = config.get("default")

        if user_default:
            merged = self.get_model_config(user_default)
            if merged and not merged.get("realtime"):
                normalized = self.normalize_model_ref(user_default)
                if normalized:
                    return normalized

        return self._default_model_fallback()

    def get_default_model_config(self) -> Optional[Dict[str, Any]]:
        default_name = self.get_default_model_name()
        if default_name:
            return self.get_model_config(default_name)
        return None

    def get_voice_default_model_name(self) -> Optional[str]:
        """The default voice (realtime) model ref, or None."""
        config = self.load_config()
        user_default = config.get("voice_default")
        if user_default and self.get_model_config(user_default):
            normalized = self.normalize_model_ref(user_default)
            if normalized:
                merged = self.get_model_config(normalized) or {}
                if merged.get("realtime"):
                    return normalized
        return None

    def get_voice_default_model_config(self) -> Optional[Dict[str, Any]]:
        name = self.get_voice_default_model_name()
        if not name:
            return None
        return self.get_model_config(name)

    def is_user_model(self, ref: str) -> bool:
        """Whether a model is user-defined (vs product default)."""
        parsed = self.parse_model_ref(ref)
        if not parsed:
            return False

        provider_name, model_name = parsed
        config = self.load_config()
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

    def list_models(self) -> List[Dict[str, Any]]:
        """All available models (merged), with default/user-defined flags."""
        providers = self.merged_providers()
        default_name = self.get_default_model_name()
        voice_default_name = self.get_voice_default_model_name()
        results: List[Dict[str, Any]] = []

        for provider_name, provider_config in providers.items():
            if not isinstance(provider_config, dict):
                continue
            provider_type = (
                provider_config.get("type")
                or provider_config.get("provider")
                or "unknown"
            )
            provider_models = provider_config.get("models")
            if not isinstance(provider_models, dict):
                continue
            for model_name, model_config in provider_models.items():
                if not isinstance(model_config, dict):
                    continue
                ref = self.format_model_ref(provider_name, model_name)
                merged = self.get_model_config(ref) or {}
                entry = {
                    "name": ref,
                    "label": merged.get("label", ref),
                    "provider": provider_type,
                    "provider_name": provider_name,
                    "model": merged.get("model"),
                    "is_default": ref == default_name,
                    "is_voice_default": ref == voice_default_name,
                    "is_user_defined": self.is_user_model(ref),
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

    def list_text_models(self) -> List[Dict[str, Any]]:
        return [m for m in self.list_models() if not m.get("realtime")]

    def list_voice_models(self) -> List[Dict[str, Any]]:
        return [m for m in self.list_models() if m.get("realtime")]

    # -- mutation -------------------------------------------------------

    def add_model(
        self,
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
        config = self.load_config()
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

        model_ref = self.format_model_ref(provider_name, model_name)
        if set_default or not config.get("default"):
            config["default"] = model_ref
        if set_voice_default and realtime:
            config["voice_default"] = model_ref

        self.save_config(config)

    def remove_model(self, name: str) -> bool:
        """Remove a user-defined model configuration. Returns True if removed."""
        config = self.load_config()
        parsed = self.parse_model_ref(name)
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

        normalized = self.normalize_model_ref(name)
        if normalized and config.get("default") == normalized:
            config["default"] = None

        self.save_config(config)
        return True

    def set_default_model(self, name: str) -> bool:
        """Set the default (text) model. Returns True if successful."""
        normalized = self.normalize_model_ref(name)
        config = self.get_model_config(normalized) if normalized else None
        if not normalized or not config or config.get("realtime"):
            return False
        config = self.load_config()
        config["default"] = normalized
        self.save_config(config)
        return True

    def set_voice_default_model(self, name: str) -> bool:
        normalized = self.normalize_model_ref(name)
        if not normalized:
            return False
        config = self.get_model_config(normalized)
        if not config or not config.get("realtime"):
            return False
        models_config = self.load_config()
        models_config["voice_default"] = normalized
        self.save_config(models_config)
        return True

    # -- providers ------------------------------------------------------

    def is_user_provider(self, name: str) -> bool:
        config = self.load_config()
        providers = config.get("providers")
        if not isinstance(providers, dict):
            return False
        return name in providers

    def list_providers(self) -> List[Dict[str, Any]]:
        """All providers (merged) with summary info."""
        merged = self.merged_providers()
        user_config = self.load_config()
        user_providers = user_config.get("providers")
        if not isinstance(user_providers, dict):
            user_providers = {}

        results: List[Dict[str, Any]] = []
        for name, provider in merged.items():
            if not isinstance(provider, dict):
                continue
            provider_type = (
                provider.get("type") or provider.get("provider") or "unknown"
            )
            models = provider.get("models")
            model_count = len(models) if isinstance(models, dict) else 0
            results.append(
                {
                    "name": name,
                    "type": provider_type,
                    "base_url": provider.get("base_url"),
                    "has_api_key": bool(provider.get("api_key")),
                    "model_count": model_count,
                    "is_user_defined": name in user_providers,
                }
            )

        results.sort(key=lambda item: item["name"])
        return results

    def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        merged = self.merged_providers()
        provider = merged.get(name)
        if not isinstance(provider, dict):
            return None
        return dict(provider)

    def add_provider(
        self,
        name: str,
        provider_type: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_version: Optional[str] = None,
        realtime_url: Optional[str] = None,
        realtime_base_url: Optional[str] = None,
    ) -> None:
        """Create or update a provider profile in user config."""
        config = self.load_config()
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

        if "models" not in provider_obj:
            provider_obj["models"] = {}

        providers[name] = provider_obj
        self.save_config(config)

    _PROVIDER_UPDATE_KEYS = {
        "type",
        "api_key",
        "base_url",
        "api_version",
        "realtime_url",
        "realtime_base_url",
        "use_responses",
        "use_responses_websocket",
    }

    def update_provider(self, name: str, **kwargs: Any) -> bool:
        """Update fields on a user-defined provider. False if not found."""
        config = self.load_config()
        providers = config.get("providers")
        if not isinstance(providers, dict):
            return False
        provider_obj = providers.get(name)
        if not isinstance(provider_obj, dict):
            return False

        for key, value in kwargs.items():
            if key in self._PROVIDER_UPDATE_KEYS and value is not None:
                provider_obj[key] = value

        providers[name] = provider_obj
        self.save_config(config)
        return True

    def remove_provider(self, name: str) -> bool:
        """Remove a user-defined provider and its models. False if not found."""
        config = self.load_config()
        providers = config.get("providers")
        if not isinstance(providers, dict) or name not in providers:
            return False

        del providers[name]

        default_ref = config.get("default")
        if default_ref:
            parsed = self.parse_model_ref(default_ref)
            if parsed and parsed[0] == name:
                config["default"] = None

        voice_ref = config.get("voice_default")
        if voice_ref:
            parsed = self.parse_model_ref(voice_ref)
            if parsed and parsed[0] == name:
                config["voice_default"] = None

        self.save_config(config)
        return True

    # -- runtime resolution ---------------------------------------------

    def resolve_model_for_runtime(
        self, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resolve a model configuration for runtime use.

        Returns a dict with provider/model/api_key/base_url/api_version/
        reasoning/token limits/model_settings/label, with ``${VAR}``
        expansion applied. Credential resolution order: platform
        ``credential_ref`` (via the hook), then configured api_key, then
        the provider-appropriate environment variable.
        """
        name = (
            self.normalize_model_ref(model_name)
            if model_name
            else self.get_default_model_name()
        )
        if not name:
            return {}

        merged = self.get_model_config(name)
        if not merged:
            return {}

        provider = (merged.get("provider") or "openai").strip().lower()

        result: Dict[str, Any] = {
            "name": name,
            "provider": provider,
            "provider_name": merged.get("provider_name"),
            "model": expand_env_vars(merged.get("model", "")),
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
            result["base_url"] = expand_env_vars(merged["base_url"])

        if merged.get("api_version"):
            result["api_version"] = merged["api_version"]

        # Priority 1: platform credential via credential_ref
        credential_ref = merged.get("credential_ref")
        if credential_ref and self._credential_resolver is not None:
            try:
                cred = self._credential_resolver(credential_ref)
                if cred:
                    cred_type = cred.get("credential_type")
                    if cred_type == "entra_token":
                        result["api_key"] = cred["access_token"]
                    elif cred_type == "api_key":
                        result["api_key"] = cred["api_key"]
                    if not result.get("base_url") and cred.get("base_url"):
                        result["base_url"] = cred["base_url"]
                    if not result.get("api_version") and cred.get("api_version"):
                        result["api_version"] = cred["api_version"]
            except Exception:
                pass

        # Priority 2: configured api_key
        if not result.get("api_key"):
            api_key_value = merged.get("api_key")
            if api_key_value:
                result["api_key"] = expand_env_vars(api_key_value)
            else:
                result["api_key"] = None

        # Priority 3: environment variable fallback
        if not result.get("api_key"):
            if provider == "azure":
                result["api_key"] = os.getenv("AZURE_OPENAI_API_KEY")
            elif provider == "litellm":
                result["api_key"] = os.getenv(litellm_api_key_env(result.get("model")))
            else:
                result["api_key"] = os.getenv("OPENAI_API_KEY")

        return result

    def resolve_voice_settings_for_realtime(
        self, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Realtime-voice connection settings for a (or the default) voice model."""
        name = (
            self.normalize_model_ref(model_name)
            if model_name
            else self.get_voice_default_model_name()
        )
        if not name:
            return {}
        merged = self.get_model_config(name)
        if not merged or not merged.get("realtime"):
            return {}
        runtime = self.resolve_model_for_runtime(name)
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
            settings["openai_realtime_url"] = expand_env_vars(realtime_url)
        realtime_base_url = merged.get("realtime_base_url")
        if realtime_base_url:
            settings["openai_realtime_base_url"] = expand_env_vars(realtime_base_url)

        return settings

    def apply_api_keys_to_env(self) -> None:
        """Export the default model's API key into the environment (no clobber)."""
        default_config = self.resolve_model_for_runtime()
        if not default_config:
            return

        api_key = default_config.get("api_key")
        provider = default_config.get("provider", "openai")

        if api_key:
            if provider == "azure":
                env_key = "AZURE_OPENAI_API_KEY"
            elif provider == "litellm":
                env_key = litellm_api_key_env(default_config.get("model"))
            else:
                env_key = "OPENAI_API_KEY"
            if env_key not in os.environ:
                os.environ[env_key] = api_key

    def has_models_configured(self) -> bool:
        """Whether a usable default model is configured."""
        resolved = self.resolve_model_for_runtime()
        if not resolved:
            return False

        provider = (resolved.get("provider") or "").strip().lower()

        if provider == "openai-compatible":
            return bool(resolved.get("model")) and bool(resolved.get("base_url"))

        if provider == "openai-oauth":
            if self._oauth_authenticated is None:
                return False
            try:
                return bool(self._oauth_authenticated())
            except Exception:
                return False

        return bool(resolved.get("api_key"))
