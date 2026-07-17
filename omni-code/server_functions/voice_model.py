"""Server functions for voice model management."""

from omniagents import server_function
from omniagents.core.session import Session

from omni_code.utility_runs import ensure_session_with_workspace as _ensure_session


@server_function(
    description="List available voice models", strict=True, name_override="voice-models"
)
async def list_voice_models(session: Session) -> dict:
    from omni_code.models import get_voice_default_model_name, list_voice_models

    models = list_voice_models()
    current = get_voice_default_model_name()

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
    from omni_code.models import (
        get_model_config,
        get_voice_default_model_name,
        list_voice_models,
        normalize_model_ref,
        set_voice_default_model,
    )

    name = text.strip()

    if not name:
        current = get_voice_default_model_name()
        if not current:
            return {
                "model": None,
                "message": "No voice model configured. Use `/voice-model <name>` or `omni model voice-default <name>`.",
            }
        config = get_model_config(current) or {}
        return {
            "model": current,
            "label": config.get("label", current),
            "provider": config.get("provider"),
            "message": f"Current voice model: **{config.get('label', current)}** [{config.get('provider')}]",
        }

    normalized = normalize_model_ref(name)
    config = get_model_config(normalized) if normalized else None
    if not config or not config.get("realtime"):
        available = list_voice_models()
        names = [m["name"] for m in available]
        return {
            "error": f"Unknown voice model `{name}`",
            "available": names,
            "message": f"Unknown voice model `{name}`. Available: {', '.join(names) or 'none'}",
        }

    if not set_voice_default_model(normalized):
        return {
            "error": f"Failed to set voice model `{name}`",
            "message": f"Failed to set voice model `{name}`.",
        }

    return {
        "model": normalized,
        "label": config.get("label", normalized),
        "provider": config.get("provider"),
        "message": f"Set voice model to **{config.get('label', normalized)}** [{config.get('provider')}]. Reconnect voice mode to apply.",
    }


async def _list_voice_models_on_invoke(service, session, args=None):
    ensured = await _ensure_session(service, session)
    if ensured is None:
        return {"error": "No session"}
    return await list_voice_models(ensured)


async def _switch_voice_model_on_invoke(service, session, args=None):
    ensured = await _ensure_session(service, session)
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


setattr(list_voice_models, "_server_function_on_invoke", _list_voice_models_on_invoke)
setattr(switch_voice_model, "_server_function_on_invoke", _switch_voice_model_on_invoke)
