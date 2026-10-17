"""Provider-neutral model/capability catalog view over a ModelConfigStore.

The typed contract behind the GUI protocol's model-catalog RPC methods
(``list_models`` / ``get_model`` / ``list_providers`` /
``set_session_model`` / ``set_session_reasoning`` / ``set_voice_model``
— see ``omniagents/rpc/protocol.md`` § "Model & Capability Catalog").

The :class:`~omniagents.core.models.store.ModelConfigStore` remains the
single source of model/provider truth (defaults → governance → user
config → live discovery). This module only *shapes* that data into the
stable wire contract every maintained client consumes:

- **Model descriptors** — display metadata, provider identity,
  context/output limits, modalities, realtime support, reasoning
  choices/defaults, service/speed tiers, personality support,
  availability (with actionable typed reasons), entitlement, and
  deprecation/upgrade guidance.
- **Provider overviews** — per-provider capability summary plus a
  config-derived health read (``ok`` / ``needs_auth`` / ``error``).
- **Session application** — the promoted ``/model`` and ``/reasoning``
  switching logic, shared with the builtin model-picker server
  functions so the chat and RPC surfaces cannot drift.

Failure isolation is a contract here: one provider's bad config or
failing discovery hook must never hide another provider's models. Every
per-provider and per-model step is fenced; failures degrade to a typed
``errors`` entry (and an ``error`` provider health status), never to an
empty catalog.

All functions are pure reads over the store except the ``apply_*``
session helpers and :func:`set_voice_default_model` — none of them ever
include resolved secrets (API keys, tokens) in their results.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .store import ModelConfigStore

__all__ = [
    "REASONING_EFFORT_LEVELS",
    "build_model_catalog",
    "describe_model",
    "build_provider_overview",
    "apply_session_model",
    "apply_session_reasoning",
    "set_voice_default_model",
]

#: The reasoning-effort vocabulary shared by ``/reasoning`` and
#: ``set_session_reasoning``. Used as the default ``reasoning.options``
#: for models that declare a default effort but no explicit option list.
REASONING_EFFORT_LEVELS: Tuple[str, ...] = ("low", "medium", "high", "xhigh")


def _reason(code: str, message: str, action: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"code": code, "message": message}
    if action:
        entry["action"] = action
    return entry


def _no_store_reason() -> Dict[str, Any]:
    from . import model_setup_hint

    return _reason(
        "no_model_store",
        "No model registry is configured for this product.",
        model_setup_hint(),
    )


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            # Discovery payloads may describe options as objects
            # (e.g. {"effort": "high", "description": ...}).
            for key in ("effort", "name", "id", "value"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    result.append(candidate.strip())
                    break
    return result


def _modalities(merged: Dict[str, Any]) -> List[str]:
    explicit = _string_list(merged.get("modalities"))
    if explicit:
        return explicit
    return ["text", "audio"] if merged.get("realtime") else ["text"]


def _reasoning(merged: Dict[str, Any]) -> Dict[str, Any]:
    options = _string_list(merged.get("reasoning_options"))
    default = merged.get("reasoning")
    if not isinstance(default, str) or not default.strip():
        default = None
    if not options and default:
        options = list(REASONING_EFFORT_LEVELS)
    if default and default not in options:
        options.append(default)
    return {"default": default, "options": options}


def _tiers(merged: Dict[str, Any]) -> Dict[str, Any]:
    service = merged.get("service_tier")
    speed = merged.get("speed_tier")
    return {
        "service": service if isinstance(service, str) and service else None,
        "speed": speed if isinstance(speed, str) and speed else None,
    }


def _personality(merged: Dict[str, Any]) -> Dict[str, Any]:
    raw = merged.get("personality")
    if isinstance(raw, dict):
        return {
            "supported": bool(raw.get("supported", True)),
            "options": _string_list(raw.get("options")),
            "default": (
                raw.get("default") if isinstance(raw.get("default"), str) else None
            ),
        }
    return {"supported": bool(raw), "options": [], "default": None}


def _deprecation(merged: Dict[str, Any]) -> Dict[str, Any]:
    deprecated = bool(merged.get("deprecated"))
    message = merged.get("deprecation_message")
    replace_with = merged.get("replace_with")
    return {
        "deprecated": deprecated,
        "message": message if isinstance(message, str) and message else None,
        "replace_with": (
            replace_with if isinstance(replace_with, str) and replace_with else None
        ),
    }


def _credential_source(store: ModelConfigStore, merged: Dict[str, Any]) -> str:
    if merged.get("credential_ref"):
        return "platform"
    if (merged.get("provider") or "").strip().lower() == "openai-oauth":
        return "oauth"
    if merged.get("api_key"):
        return "api_key"
    try:
        resolved = store.resolve_model_for_runtime(merged.get("name"))
    except Exception:
        return "none"
    return "env" if resolved.get("api_key") else "none"


def _availability(
    store: ModelConfigStore, merged: Dict[str, Any]
) -> Tuple[Dict[str, Any], str]:
    """``(availability, credential_source)`` for a merged model config.

    Availability is a *configuration* read (credentials resolvable,
    endpoint known), never a network probe. Reasons are typed and
    actionable so pickers can render sign-in / setup affordances.
    """
    provider = (merged.get("provider") or "").strip().lower()
    reasons: List[Dict[str, Any]] = []
    credential = _credential_source(store, merged)

    if provider == "openai-oauth":
        authenticated = False
        oauth_hook = getattr(store, "_oauth_authenticated", None)
        if oauth_hook is not None:
            try:
                authenticated = bool(oauth_hook())
            except Exception:
                authenticated = False
        if not authenticated:
            reasons.append(
                _reason(
                    "oauth_signin_required",
                    "The provider account is not signed in.",
                    "Sign in to the provider account to use this model.",
                )
            )
    elif provider == "openai-compatible" and not merged.get("base_url"):
        reasons.append(
            _reason(
                "missing_base_url",
                "The provider has no base URL configured.",
                "Configure the provider's base_url.",
            )
        )
    elif credential == "none":
        reasons.append(
            _reason(
                "missing_credentials",
                "No API key or credential is configured for this model.",
                "Add an API key for the provider or sign in to the "
                "management plane.",
            )
        )

    return {"available": not reasons, "reasons": reasons}, credential


def _descriptor(
    store: ModelConfigStore,
    ref: str,
    merged: Dict[str, Any],
    *,
    default_name: Optional[str],
    voice_default_name: Optional[str],
) -> Dict[str, Any]:
    availability, credential = _availability(store, merged)
    description = merged.get("description")
    return {
        "id": ref,
        "model": merged.get("model"),
        "label": merged.get("label") or ref,
        "description": (
            description if isinstance(description, str) and description else None
        ),
        "provider": {
            "name": merged.get("provider_name"),
            "type": merged.get("provider"),
        },
        "modalities": _modalities(merged),
        "realtime": bool(merged.get("realtime")),
        "limits": {
            "max_input_tokens": merged.get("max_input_tokens"),
            "max_output_tokens": merged.get("max_output_tokens"),
        },
        "reasoning": _reasoning(merged),
        "tiers": _tiers(merged),
        "personality": _personality(merged),
        "availability": availability,
        "entitlement": {
            "entitled": availability["available"],
            "credential": credential,
        },
        "deprecation": _deprecation(merged),
        "hidden": bool(merged.get("hidden")),
        "is_default": ref == default_name,
        "is_voice_default": ref == voice_default_name,
        "is_user_defined": store.is_user_model(ref),
    }


def _provider_error(provider_name: str, exc: Exception) -> Dict[str, Any]:
    return {
        "provider": provider_name,
        "code": "provider_error",
        "message": f"{exc.__class__.__name__}: {exc}",
    }


def _iter_provider_models(
    store: ModelConfigStore,
    provider_name: str,
    provider_config: Dict[str, Any],
    *,
    default_name: Optional[str],
    voice_default_name: Optional[str],
    errors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Descriptors for one provider, isolating that provider's failures."""
    descriptors: List[Dict[str, Any]] = []
    models = provider_config.get("models")
    if not isinstance(models, dict):
        return descriptors
    for model_name in sorted(models):
        if not isinstance(models.get(model_name), dict):
            continue
        ref = store.format_model_ref(provider_name, model_name)
        try:
            merged = store.get_model_config(ref)
            if not merged:
                continue
            descriptors.append(
                _descriptor(
                    store,
                    ref,
                    merged,
                    default_name=default_name,
                    voice_default_name=voice_default_name,
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            errors.append(_provider_error(provider_name, exc))
            return []
    return descriptors


def _catalog_inputs(
    store: ModelConfigStore, errors: List[Dict[str, Any]]
) -> Tuple[Dict[str, Dict[str, Any]], Optional[str], Optional[str]]:
    providers = store.merged_providers()
    try:
        default_name = store.get_default_model_name()
    except Exception as exc:  # noqa: BLE001
        errors.append(_provider_error("<default>", exc))
        default_name = None
    try:
        voice_default_name = store.get_voice_default_model_name()
    except Exception as exc:  # noqa: BLE001
        errors.append(_provider_error("<voice_default>", exc))
        voice_default_name = None
    return providers, default_name, voice_default_name


def build_model_catalog(
    store: ModelConfigStore,
    *,
    include_hidden: bool = False,
    modality: Optional[str] = None,
) -> Dict[str, Any]:
    """The full model catalog: descriptors plus default selections.

    Ordering is deterministic: providers lexicographically, then model
    names lexicographically within each provider. ``hidden`` models are
    excluded unless *include_hidden* is set (they stay individually
    resolvable via :func:`describe_model` and selectable via
    :func:`apply_session_model`). *modality* filters to models that
    support it (e.g. ``"text"`` or ``"audio"``).
    """
    errors: List[Dict[str, Any]] = []
    providers, default_name, voice_default_name = _catalog_inputs(store, errors)

    descriptors: List[Dict[str, Any]] = []
    for provider_name in sorted(providers):
        provider_config = providers.get(provider_name)
        if not isinstance(provider_config, dict):
            continue
        descriptors.extend(
            _iter_provider_models(
                store,
                provider_name,
                provider_config,
                default_name=default_name,
                voice_default_name=voice_default_name,
                errors=errors,
            )
        )

    if not include_hidden:
        descriptors = [d for d in descriptors if not d["hidden"]]
    if modality:
        descriptors = [d for d in descriptors if modality in d["modalities"]]

    return {
        "models": descriptors,
        "default_model": default_name,
        "voice_default_model": voice_default_name,
        "errors": errors,
    }


def describe_model(store: ModelConfigStore, ref: str) -> Dict[str, Any]:
    """One model's descriptor, or typed reasons when it cannot be served."""
    errors: List[Dict[str, Any]] = []
    normalized = store.normalize_model_ref(ref)
    merged = store.get_model_config(normalized) if normalized else None
    if not normalized or not merged:
        return {
            "found": False,
            "model": None,
            "reasons": [
                _reason(
                    "unknown_model",
                    f"Model {ref!r} is not in the catalog.",
                    "Call list_models for the available catalog.",
                )
            ],
        }
    _, default_name, voice_default_name = _catalog_inputs(store, errors)
    return {
        "found": True,
        "model": _descriptor(
            store,
            normalized,
            merged,
            default_name=default_name,
            voice_default_name=voice_default_name,
        ),
        "reasons": [],
    }


def _provider_entry(
    store: ModelConfigStore,
    provider_name: str,
    provider_config: Dict[str, Any],
    *,
    default_name: Optional[str],
    voice_default_name: Optional[str],
) -> Dict[str, Any]:
    provider_errors: List[Dict[str, Any]] = []
    descriptors = _iter_provider_models(
        store,
        provider_name,
        provider_config,
        default_name=default_name,
        voice_default_name=voice_default_name,
        errors=provider_errors,
    )
    provider_type = (
        provider_config.get("type") or provider_config.get("provider") or "unknown"
    )
    base_url = provider_config.get("base_url")

    visible = [d for d in descriptors if not d["hidden"]]
    unavailable_codes = sorted(
        {
            reason["code"]
            for descriptor in descriptors
            for reason in descriptor["availability"]["reasons"]
        }
    )
    if provider_errors:
        health = {"status": "error", "detail": provider_errors[0]["message"]}
    elif not descriptors:
        health = {"status": "ok", "detail": "No models configured."}
    elif any(d["availability"]["available"] for d in descriptors):
        health = {"status": "ok", "detail": None}
    elif "oauth_signin_required" in unavailable_codes:
        health = {
            "status": "needs_auth",
            "detail": "The provider account is not signed in.",
        }
    else:
        health = {
            "status": "needs_auth",
            "detail": "No usable credentials are configured "
            f"({', '.join(unavailable_codes)}).",
        }

    return {
        "name": provider_name,
        "type": provider_type,
        "base_url": base_url if isinstance(base_url, str) and base_url else None,
        "is_default_provider": provider_name == store.default_provider_name,
        "is_user_defined": store.is_user_provider(provider_name),
        "model_count": len(visible),
        "hidden_model_count": len(descriptors) - len(visible),
        "capabilities": {
            "realtime": any(d["realtime"] for d in descriptors),
            "reasoning": any(d["reasoning"]["default"] for d in descriptors),
            "modalities": sorted({m for d in descriptors for m in d["modalities"]}),
        },
        "health": health,
    }


def build_provider_overview(store: ModelConfigStore) -> Dict[str, Any]:
    """Per-provider capability + config-derived health, failures isolated."""
    errors: List[Dict[str, Any]] = []
    providers, default_name, voice_default_name = _catalog_inputs(store, errors)

    entries: List[Dict[str, Any]] = []
    for provider_name in sorted(providers):
        provider_config = providers.get(provider_name)
        if not isinstance(provider_config, dict):
            continue
        try:
            entries.append(
                _provider_entry(
                    store,
                    provider_name,
                    provider_config,
                    default_name=default_name,
                    voice_default_name=voice_default_name,
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            errors.append(_provider_error(provider_name, exc))
            provider_type = (
                provider_config.get("type")
                or provider_config.get("provider")
                or "unknown"
            )
            entries.append(
                {
                    "name": provider_name,
                    "type": provider_type,
                    "base_url": None,
                    "is_default_provider": (
                        provider_name == store.default_provider_name
                    ),
                    "is_user_defined": False,
                    "model_count": 0,
                    "hidden_model_count": 0,
                    "capabilities": {
                        "realtime": False,
                        "reasoning": False,
                        "modalities": [],
                    },
                    "health": {
                        "status": "error",
                        "detail": f"{exc.__class__.__name__}: {exc}",
                    },
                }
            )

    return {"providers": entries, "errors": errors}


# ---------------------------------------------------------------------------
# Session application — the promoted /model + /reasoning switching logic.
# ---------------------------------------------------------------------------


def apply_session_model(
    store: ModelConfigStore, session: Any, ref: str
) -> Dict[str, Any]:
    """Switch *session* to catalog model *ref*.

    The single switching implementation shared by the builtin ``/model``
    server function and the ``set_session_model`` RPC. Unknown refs and
    realtime (voice-only) refs are refused with typed reasons; an
    available-but-unentitled model still switches (the failure surfaces
    at run time) but carries its availability reasons as ``warnings`` so
    pickers can flag it.
    """
    try:
        store.require_selected_provider(ref)
    except ValueError as exc:
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "provider_mismatch",
                    str(exc),
                    "Change the workspace provider in Settings.",
                )
            ],
        }
    normalized = store.normalize_model_ref(ref)
    merged = store.get_model_config(normalized) if normalized else None
    if not normalized or not merged:
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "unknown_model",
                    f"Model {ref!r} is not in the catalog.",
                    "Call list_models for the available catalog.",
                )
            ],
        }
    if merged.get("realtime"):
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "realtime_only",
                    f"Model {normalized!r} is a realtime voice model and "
                    "cannot serve text sessions.",
                    "Use set_voice_model for voice, or pick a text model.",
                )
            ],
        }

    session.active_model = normalized
    session.model_config = merged
    if merged.get("reasoning") and not getattr(
        session, "_reasoning_explicitly_set", False
    ):
        session.reasoning_effort = merged["reasoning"]

    availability, _ = _availability(store, merged)
    return {
        "ok": True,
        "model": normalized,
        "label": merged.get("label", normalized),
        "provider": merged.get("provider"),
        "max_input_tokens": merged.get("max_input_tokens"),
        "max_output_tokens": merged.get("max_output_tokens"),
        "reasoning_effort": getattr(session, "reasoning_effort", None),
        "warnings": availability["reasons"],
    }


def apply_session_reasoning(session: Any, effort: str) -> Dict[str, Any]:
    """Set *session*'s reasoning effort (the promoted ``/reasoning`` logic)."""
    level = (effort or "").lower().strip()
    if level not in REASONING_EFFORT_LEVELS:
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "invalid_reasoning_effort",
                    f"Invalid reasoning effort {effort!r}.",
                    "Use one of: " + ", ".join(REASONING_EFFORT_LEVELS) + ".",
                )
            ],
        }
    session.reasoning_effort = level
    session._reasoning_explicitly_set = True
    return {
        "ok": True,
        "reasoning_effort": level,
        "model": getattr(session, "active_model", None),
    }


def set_voice_default_model(store: ModelConfigStore, ref: str) -> Dict[str, Any]:
    """Set the process-wide voice (realtime) default to catalog model *ref*."""
    try:
        store.require_selected_provider(ref)
    except ValueError as exc:
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "provider_mismatch",
                    str(exc),
                    "Change the workspace provider in Settings.",
                )
            ],
        }
    normalized = store.normalize_model_ref(ref)
    merged = store.get_model_config(normalized) if normalized else None
    if not normalized or not merged:
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "unknown_model",
                    f"Model {ref!r} is not in the catalog.",
                    "Call list_models for the available catalog.",
                )
            ],
        }
    if not merged.get("realtime"):
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "not_realtime",
                    f"Model {normalized!r} is not a realtime voice model.",
                    "Pick a model with realtime support.",
                )
            ],
        }
    if not store.set_voice_default_model(normalized):
        return {
            "ok": False,
            "reasons": [
                _reason(
                    "voice_default_rejected",
                    f"The store refused {normalized!r} as the voice default.",
                )
            ],
        }
    return {
        "ok": True,
        "model": normalized,
        "label": merged.get("label", normalized),
        "provider": merged.get("provider"),
    }
