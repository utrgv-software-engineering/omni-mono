"""Builtin model-picker server functions: /models /model /reasoning
/voice-models /voice-model.

Session-scoped model and reasoning selection over the process-wide
:func:`omniagents.core.models.model_store` (products install their
``ModelConfigStore`` with ``set_model_store``). Activated by the
``model_picker`` capability pack; lifted from omni-code.
"""

from __future__ import annotations

from omniagents import server_function
from omniagents.core.models import model_setup_hint, model_store
from omniagents.core.server_functions.helpers import (
    ensure_session_with_workspace,
    notify_session_state,
)
from omniagents.core.session import Session


def _tokens_suffix(config: dict) -> str:
    if not (config.get("max_input_tokens") or config.get("max_output_tokens")):
        return ""
    input_t = (
        f"{config['max_input_tokens'] // 1000}k"
        if config.get("max_input_tokens")
        else "?"
    )
    output_t = (
        f"{config['max_output_tokens'] // 1000}k"
        if config.get("max_output_tokens")
        else "?"
    )
    return f" [{input_t}/{output_t}]"


def _no_store_result() -> dict:
    return {"model": None, "models": [], "message": model_setup_hint()}


@server_function(
    description="List available models", strict=True, name_override="models"
)
async def list_models(session: Session) -> dict:
    """List all configured models with current session state."""
    store = model_store()
    if store is None:
        return _no_store_result()

    models = store.list_text_models()
    default_name = store.get_default_model_name()

    active_model = getattr(session, "active_model", None)
    current = store.normalize_model_ref(active_model) if active_model else default_name
    reasoning_effort = getattr(session, "reasoning_effort", None)

    for m in models:
        m["is_current"] = m["name"] == current

    # Fall back to the current model's own default reasoning when the session
    # has no explicit override, so we report what the agent actually runs.
    if not reasoning_effort:
        current_model = next((m for m in models if m["is_current"]), None)
        if current_model:
            reasoning_effort = current_model.get("reasoning")

    if not models:
        message = "No models available."
    else:
        user_models = [m for m in models if m.get("is_user_defined")]
        builtin_models = [m for m in models if not m.get("is_user_defined")]

        def format_model(m):
            marker = "*" if m["is_current"] else " "
            default_marker = " (default)" if m["is_default"] else ""
            return (
                f"  {marker} `{m['name']}` - {m['label']} "
                f"[{m['provider']}]{default_marker}{_tokens_suffix(m)}"
            )

        lines = []
        if builtin_models:
            lines.append("**Built-in Models:**")
            for m in builtin_models:
                lines.append(format_model(m))
        if user_models:
            if builtin_models:
                lines.append("")
            lines.append("**User-defined Models:**")
            for m in user_models:
                lines.append(format_model(m))
        if reasoning_effort:
            lines.append(f"\n**Reasoning effort:** {reasoning_effort}")
        message = "\n".join(lines)

    return {
        "models": models,
        "current": current,
        "reasoning_effort": reasoning_effort or "low",
        "message": message,
    }


@server_function(description="Switch model", strict=True, name_override="model")
async def switch_model(session: Session, text: str) -> dict:
    """Switch to a different model for this session.

    Usage: /model <name>
    """
    store = model_store()
    if store is None:
        return _no_store_result()

    name = text.strip()

    # No argument = show current model (and initialize session if needed)
    if not name:
        active = getattr(session, "active_model", None)
        active = store.normalize_model_ref(active) if active else None
        model_config = getattr(session, "model_config", None)
        reasoning = getattr(session, "reasoning_effort", None)

        if active and model_config:
            # Show the effective reasoning effort: the session override if
            # set, otherwise the model's own default.
            effective_reasoning = reasoning or model_config.get("reasoning")
            return {
                "model": active,
                "label": model_config.get("label", active),
                "provider": model_config.get("provider"),
                "max_input_tokens": model_config.get("max_input_tokens"),
                "max_output_tokens": model_config.get("max_output_tokens"),
                "reasoning_effort": effective_reasoning,
                "message": (
                    f"Current model: **{model_config.get('label', active)}** "
                    f"[{model_config.get('provider')}]{_tokens_suffix(model_config)}"
                ),
            }

        # No active model — initialize from default.
        default_name = store.get_default_model_name()
        default_config = store.get_default_model_config()
        if default_name and default_config:
            session.active_model = default_name
            session.model_config = default_config
            return {
                "model": default_name,
                "label": default_config.get("label", default_name),
                "provider": default_config.get("provider"),
                "reasoning_effort": reasoning,
                "message": (
                    f"Current model: **{default_config.get('label', default_name)}** "
                    f"[{default_config.get('provider')}] (default)"
                ),
            }
        return {"model": None, "message": model_setup_hint()}

    # Look up the requested model
    normalized = store.normalize_model_ref(name)
    model_config = store.get_model_config(normalized) if normalized else None
    if not model_config or model_config.get("realtime"):
        names = [m["name"] for m in store.list_text_models()]
        return {
            "error": f"Unknown model `{name}`",
            "available": names,
            "message": f"Unknown model `{name}`. Available: {', '.join(names) or 'none'}",
        }

    # Update session state
    session.active_model = normalized
    session.model_config = model_config

    # Apply model's default reasoning if set and not already overridden
    if model_config.get("reasoning") and not getattr(
        session, "_reasoning_explicitly_set", False
    ):
        session.reasoning_effort = model_config["reasoning"]

    reasoning = getattr(session, "reasoning_effort", None)

    return {
        "model": normalized,
        "label": model_config.get("label", normalized),
        "provider": model_config.get("provider"),
        "max_input_tokens": model_config.get("max_input_tokens"),
        "max_output_tokens": model_config.get("max_output_tokens"),
        "reasoning_effort": reasoning,
        "message": (
            f"Switched to **{model_config.get('label', normalized)}** "
            f"[{model_config.get('provider')}]{_tokens_suffix(model_config)}"
        ),
    }


@server_function(
    description="Set reasoning effort", strict=True, name_override="reasoning"
)
async def set_reasoning(session: Session, text: str) -> dict:
    """Set reasoning effort level for this session.

    Usage: /reasoning <low|medium|high|xhigh>
    """
    level = text.lower().strip()

    # No argument = show current
    if not level:
        # Session override if set, else the active model's default, else low.
        current = getattr(session, "reasoning_effort", None)
        if not current:
            model_config = getattr(session, "model_config", None)
            current = (model_config or {}).get("reasoning") or "low"
        return {
            "reasoning_effort": current,
            "message": f"Current reasoning effort: **{current}**",
        }

    valid_levels = ("low", "medium", "high", "xhigh")
    if level not in valid_levels:
        return {
            "error": f"Invalid level `{level}`",
            "valid": list(valid_levels),
            "message": f"Invalid level `{level}`. Use: low, medium, high, or xhigh",
        }

    session.reasoning_effort = level
    session._reasoning_explicitly_set = True  # Mark as explicit override

    active_model = getattr(session, "active_model", None)

    return {
        "reasoning_effort": level,
        "model": active_model,
        "message": f"Reasoning effort set to **{level}**",
    }


@server_function(
    description="List available voice models", strict=True, name_override="voice-models"
)
async def list_voice_models(session: Session) -> dict:
    store = model_store()
    if store is None:
        return _no_store_result()

    models = store.list_voice_models()
    current = store.get_voice_default_model_name()

    for m in models:
        m["is_current"] = m["name"] == current

    if not models:
        message = "No voice models available."
    else:
        lines = ["**Voice Models:**"]
        for m in models:
            marker = "*" if m.get("is_current") else " "
            default_marker = " (default)" if m.get("is_voice_default") else ""
            lines.append(
                f"  {marker} `{m['name']}` - {m['label']} [{m['provider']}]{default_marker}"
            )
        message = "\n".join(lines)

    return {
        "models": models,
        "current": current,
        "message": message,
    }


@server_function(
    description="Switch voice model", strict=True, name_override="voice-model"
)
async def switch_voice_model(session: Session, text: str) -> dict:
    store = model_store()
    if store is None:
        return _no_store_result()

    name = text.strip()

    if not name:
        current = store.get_voice_default_model_name()
        if not current:
            return {
                "model": None,
                "message": "No voice model configured. Use `/voice-model <name>`.",
            }
        config = store.get_model_config(current) or {}
        return {
            "model": current,
            "label": config.get("label", current),
            "provider": config.get("provider"),
            "message": (
                f"Current voice model: **{config.get('label', current)}** "
                f"[{config.get('provider')}]"
            ),
        }

    normalized = store.normalize_model_ref(name)
    config = store.get_model_config(normalized) if normalized else None
    if not config or not config.get("realtime"):
        names = [m["name"] for m in store.list_voice_models()]
        return {
            "error": f"Unknown voice model `{name}`",
            "available": names,
            "message": f"Unknown voice model `{name}`. Available: {', '.join(names) or 'none'}",
        }

    if not store.set_voice_default_model(normalized):
        return {
            "error": f"Failed to set voice model `{name}`",
            "message": f"Failed to set voice model `{name}`.",
        }

    return {
        "model": normalized,
        "label": config.get("label", normalized),
        "provider": config.get("provider"),
        "message": (
            f"Set voice model to **{config.get('label', normalized)}** "
            f"[{config.get('provider')}]. Reconnect voice mode to apply."
        ),
    }


# -- invoke handlers: ensure the session and notify the UI on changes ----


async def _list_models_on_invoke(service, session, args=None):
    ensured = await ensure_session_with_workspace(service, session)
    if ensured is None:
        raise ValueError("No session available")
    return await list_models(ensured)


async def _switch_model_on_invoke(service, session, args=None):
    ensured = await ensure_session_with_workspace(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await switch_model(ensured, text)
    # Notify UI if we switched (not just queried, and no error).
    if "error" not in result and text.strip():
        await notify_session_state(service, ensured)
    return result


async def _set_reasoning_on_invoke(service, session, args=None):
    ensured = await ensure_session_with_workspace(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await set_reasoning(ensured, text)
    if "error" not in result and text.strip():
        await notify_session_state(service, ensured)
    return result


async def _list_voice_models_on_invoke(service, session, args=None):
    ensured = await ensure_session_with_workspace(service, session)
    if ensured is None:
        return {"error": "No session"}
    return await list_voice_models(ensured)


async def _switch_voice_model_on_invoke(service, session, args=None):
    ensured = await ensure_session_with_workspace(service, session)
    if ensured is None:
        return {"error": "No session"}
    text = ""
    if isinstance(args, dict):
        text = args.get("text") or ""
    result = await switch_voice_model(ensured, text)
    try:
        await service.channel.async_call(
            "notification",
            {
                "type": "voice_model_changed",
                "model": result.get("model"),
                "message": result.get("message"),
            },
        )
    except Exception:
        pass
    return result


setattr(list_models, "_server_function_on_invoke", _list_models_on_invoke)
setattr(switch_model, "_server_function_on_invoke", _switch_model_on_invoke)
setattr(set_reasoning, "_server_function_on_invoke", _set_reasoning_on_invoke)
setattr(list_voice_models, "_server_function_on_invoke", _list_voice_models_on_invoke)
setattr(switch_voice_model, "_server_function_on_invoke", _switch_voice_model_on_invoke)
