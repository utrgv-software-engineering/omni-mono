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
from omniagents.core.models.catalog_api import (
    apply_session_model,
    apply_session_reasoning,
    REASONING_EFFORT_LEVELS,
    set_voice_default_model,
)
from omniagents.core.server_functions.helpers import (
    ensure_server_session,
    notify_session_state,
)
from omniagents.core.session import Session
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


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


from omniagents.core.server_functions.slash import (
    slash_command,
    text as slash_text,
)


@slash_command(description="List available models and the current selection", order=11)
@server_function(
    description="List available models",
    strict=True,
    name_override="models",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def list_models(session: Session) -> dict:
    """List all configured models with current session state."""
    store = model_store()
    if store is None:
        return _no_store_result()

    models = store.list_text_models()
    if store.single_provider:
        models = [
            m for m in models if m.get("provider_name") == store.selected_provider()
        ]
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


@slash_command(
    description="Switch the session's model",
    usage="<name>",
    args=slash_text(),
    during_run=False,
    order=10,
)
@server_function(
    description="Switch model",
    strict=True,
    name_override="model",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
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

    # Look up + apply via the shared catalog switching logic (also behind
    # the typed ``set_session_model`` RPC — the two surfaces must not drift).
    result = apply_session_model(store, session, name)
    if not result.get("ok"):
        names = [m["name"] for m in store.list_text_models()]
        return {
            "error": f"Unknown model `{name}`",
            "available": names,
            "message": f"Unknown model `{name}`. Available: {', '.join(names) or 'none'}",
        }

    model_config = session.model_config or {}
    return {
        "model": result["model"],
        "label": result["label"],
        "provider": result["provider"],
        "max_input_tokens": result["max_input_tokens"],
        "max_output_tokens": result["max_output_tokens"],
        "reasoning_effort": result["reasoning_effort"],
        "message": (
            f"Switched to **{result['label']}** "
            f"[{result['provider']}]{_tokens_suffix(model_config)}"
        ),
    }


@slash_command(
    description="Set reasoning effort",
    usage="<low|medium|high>",
    args=slash_text(),
    during_run=False,
    order=12,
)
@server_function(
    description="Set reasoning effort",
    strict=True,
    name_override="reasoning",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
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

    result = apply_session_reasoning(session, level)
    if not result.get("ok"):
        return {
            "error": f"Invalid level `{level}`",
            "valid": list(REASONING_EFFORT_LEVELS),
            "message": f"Invalid level `{level}`. Use: low, medium, high, or xhigh",
        }

    return {
        "reasoning_effort": result["reasoning_effort"],
        "model": result["model"],
        "message": f"Reasoning effort set to **{level}**",
    }


@server_function(
    description="List available voice models",
    strict=True,
    name_override="voice-models",
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
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
    description="Switch voice model",
    strict=True,
    name_override="voice-model",
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
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

    # Apply via the shared catalog logic (also behind the typed
    # ``set_voice_model`` RPC — the two surfaces must not drift).
    result = set_voice_default_model(store, name)
    if not result.get("ok"):
        codes = {reason.get("code") for reason in result.get("reasons", [])}
        if "voice_default_rejected" in codes:
            return {
                "error": f"Failed to set voice model `{name}`",
                "message": f"Failed to set voice model `{name}`.",
            }
        names = [m["name"] for m in store.list_voice_models()]
        return {
            "error": f"Unknown voice model `{name}`",
            "available": names,
            "message": f"Unknown voice model `{name}`. Available: {', '.join(names) or 'none'}",
        }

    return {
        "model": result["model"],
        "label": result["label"],
        "provider": result["provider"],
        "message": (
            f"Set voice model to **{result['label']}** "
            f"[{result['provider']}]. Reconnect voice mode to apply."
        ),
    }


# -- invoke handlers: ensure the session and notify the UI on changes ----


async def _list_models_on_invoke(service, session, args=None):
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    return await list_models(ensured)


async def _switch_model_on_invoke(service, session, args=None):
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await switch_model(ensured, text)
    # Notify UI if we switched (not just queried, and no error).
    if "error" not in result and text.strip():
        await notify_session_state(service, ensured)
    return result


async def _set_reasoning_on_invoke(service, session, args=None):
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await set_reasoning(ensured, text)
    if "error" not in result and text.strip():
        await notify_session_state(service, ensured)
    return result


async def _list_voice_models_on_invoke(service, session, args=None):
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        return {"error": "No session"}
    return await list_voice_models(ensured)


async def _switch_voice_model_on_invoke(service, session, args=None):
    ensured = await ensure_server_session(service, session)
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


@server_function(
    description="List configured workspace providers and their default text models.",
    strict=True,
    name_override="model_providers",
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
)
async def list_model_providers() -> dict:
    store = model_store()
    if store is None or not store.single_provider:
        return {
            "providers": [],
            "current": None,
            "error": "Workspace provider selection is not enabled.",
        }
    default = store.get_default_model_name()
    providers = {}
    for model in store.list_text_models():
        name = model.get("provider_name")
        if not name or model.get("hidden"):
            continue
        if name not in providers or model["name"] == default:
            providers[name] = {
                "name": name,
                "model": model["name"],
                "label": model["label"],
            }
    return {"providers": list(providers.values()), "current": store.selected_provider()}


@server_function(
    description="Change the workspace provider after explicit user confirmation.",
    strict=True,
    name_override="switch_provider",
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
)
async def switch_provider(
    model: str, expected_provider: str | None, confirm: bool
) -> dict:
    choices = await list_model_providers()
    if choices.get("error"):
        return {"ok": False, "error": choices["error"]}
    if not confirm:
        return {
            "ok": False,
            "error": "Confirm that continuing conversations will send history to the new provider.",
        }
    if choices["current"] != expected_provider:
        return {
            "ok": False,
            "error": "The workspace provider changed. Refresh the list and confirm again.",
        }
    choice = next(
        (entry for entry in choices["providers"] if entry["model"] == model), None
    )
    if choice is None:
        return {
            "ok": False,
            "error": "This provider or its default model is no longer available. Refresh the list.",
        }
    store = model_store()
    assert store is not None
    # Resolve and validate the connection before changing the default, without
    # sending a model request or touching any active run's frozen configuration.
    store.get_model_config(model)
    config = store.load_config()
    config["default"] = model
    config["provider_scope_version"] = 1
    store.save_config(config)
    return {"ok": True, "provider": choice["name"], "model": model}
