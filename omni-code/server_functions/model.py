"""Server functions for model management.

Provides runtime commands:
- /models - List available models and current state
- /model <name> - Switch to a different model for this session
- /reasoning <low|medium|high> - Set reasoning effort level
"""

from omniagents import server_function
from omniagents.core.server_functions.helpers import (
    ensure_server_session,
    notify_session_state,
)
from omniagents.core.session import Session


async def _ensure_session(service, session: Session | None) -> Session | None:
    ensured = await ensure_server_session(service, session)

    if ensured is None:
        return None

    call_args = None
    ctx = getattr(ensured, "context", None)
    if isinstance(ctx, dict):
        workspace_root = ctx.get("workspace_root")
        if isinstance(workspace_root, str) and workspace_root.strip():
            call_args = {"workspace_root": workspace_root}
    if call_args is None:
        variables = getattr(ensured, "variables", None)
        if isinstance(variables, dict):
            workspace_root = variables.get("workspace_root")
            if isinstance(workspace_root, str) and workspace_root.strip():
                call_args = {"workspace_root": workspace_root}

    try:
        await service.server_call(
            "session.ensure",
            call_args,
            session_id=getattr(ensured, "id", None),
        )
    except ValueError:
        pass
    return ensured


@server_function(
    description="List available models", strict=True, name_override="models"
)
async def list_models(session: Session) -> dict:
    """List all configured models with current session state."""
    from omni_code.models import (
        list_text_models as get_all_models,
        get_default_model_name,
    )

    models = get_all_models()
    default_name = get_default_model_name()

    # Get session state
    from omni_code.models import normalize_model_ref

    active_model = getattr(session, "active_model", None)
    current = normalize_model_ref(active_model) if active_model else default_name
    reasoning_effort = getattr(session, "reasoning_effort", None)

    # Mark current model
    for m in models:
        m["is_current"] = m["name"] == current

    # Build response message
    if not models:
        message = "No models available."
    else:
        # Separate user-defined and built-in models
        user_models = [m for m in models if m.get("is_user_defined")]
        builtin_models = [m for m in models if not m.get("is_user_defined")]

        def format_model(m):
            marker = "*" if m["is_current"] else " "
            default_marker = " (default)" if m["is_default"] else ""
            tokens = ""
            if m.get("max_input_tokens") or m.get("max_output_tokens"):
                input_t = (
                    f"{m['max_input_tokens']//1000}k"
                    if m.get("max_input_tokens")
                    else "?"
                )
                output_t = (
                    f"{m['max_output_tokens']//1000}k"
                    if m.get("max_output_tokens")
                    else "?"
                )
                tokens = f" [{input_t}/{output_t}]"
            return f"  {marker} `{m['name']}` - {m['label']} [{m['provider']}]{default_marker}{tokens}"

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
    from omni_code.models import (
        get_model_config,
        list_text_models as get_all_models,
        normalize_model_ref,
    )

    name = text.strip()

    # No argument = show current model (and initialize session if needed)
    if not name:
        from omni_code.models import normalize_model_ref

        active = getattr(session, "active_model", None)
        active = normalize_model_ref(active) if active else None
        model_config = getattr(session, "model_config", None)
        reasoning = getattr(session, "reasoning_effort", None)

        if active and model_config:
            tokens = ""
            if model_config.get("max_input_tokens") or model_config.get(
                "max_output_tokens"
            ):
                input_t = (
                    f"{model_config['max_input_tokens']//1000}k"
                    if model_config.get("max_input_tokens")
                    else "?"
                )
                output_t = (
                    f"{model_config['max_output_tokens']//1000}k"
                    if model_config.get("max_output_tokens")
                    else "?"
                )
                tokens = f" [{input_t}/{output_t}]"
            return {
                "model": active,
                "label": model_config.get("label", active),
                "provider": model_config.get("provider"),
                "max_input_tokens": model_config.get("max_input_tokens"),
                "max_output_tokens": model_config.get("max_output_tokens"),
                "reasoning_effort": reasoning,
                "message": f"Current model: **{model_config.get('label', active)}** [{model_config.get('provider')}]{tokens}",
            }
        else:
            # No active model - initialize from default
            from omni_code.models import (
                get_default_model_name,
                get_default_model_config,
            )

            default_name = get_default_model_name()
            default_config = get_default_model_config()
            if default_name and default_config:
                # Initialize session with default model
                session.active_model = default_name
                session.model_config = default_config
                return {
                    "model": default_name,
                    "label": default_config.get("label", default_name),
                    "provider": default_config.get("provider"),
                    "reasoning_effort": reasoning,
                    "message": f"Current model: **{default_config.get('label', default_name)}** [{default_config.get('provider')}] (default)",
                }
            return {
                "model": None,
                "message": "No model configured. Run `omni model setup` to add one.",
            }

    # Look up the requested model
    normalized = normalize_model_ref(name)
    model_config = get_model_config(normalized) if normalized else None
    if not model_config or model_config.get("realtime"):
        available = get_all_models()
        names = [m["name"] for m in available]
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

    tokens = ""
    if model_config.get("max_input_tokens") or model_config.get("max_output_tokens"):
        input_t = (
            f"{model_config['max_input_tokens']//1000}k"
            if model_config.get("max_input_tokens")
            else "?"
        )
        output_t = (
            f"{model_config['max_output_tokens']//1000}k"
            if model_config.get("max_output_tokens")
            else "?"
        )
        tokens = f" [{input_t}/{output_t}]"

    return {
        "model": normalized,
        "label": model_config.get("label", normalized),
        "provider": model_config.get("provider"),
        "max_input_tokens": model_config.get("max_input_tokens"),
        "max_output_tokens": model_config.get("max_output_tokens"),
        "reasoning_effort": reasoning,
        "message": f"Switched to **{model_config.get('label', normalized)}** [{model_config.get('provider')}]{tokens}",
    }


@server_function(
    description="Set reasoning effort", strict=True, name_override="reasoning"
)
async def set_reasoning(session: Session, text: str) -> dict:
    """Set reasoning effort level for this session.

    Usage: /reasoning <low|medium|high>
    """
    level = text.lower().strip()

    # No argument = show current
    if not level:
        current = getattr(session, "reasoning_effort", "low")
        return {
            "reasoning_effort": current,
            "message": f"Current reasoning effort: **{current}**",
        }

    # Validate level
    valid_levels = ("low", "medium", "high")
    if level not in valid_levels:
        return {
            "error": f"Invalid level `{level}`",
            "valid": list(valid_levels),
            "message": f"Invalid level `{level}`. Use: low, medium, or high",
        }

    # Update session state
    session.reasoning_effort = level
    session._reasoning_explicitly_set = True  # Mark as explicit override

    active_model = getattr(session, "active_model", None)

    return {
        "reasoning_effort": level,
        "model": active_model,
        "message": f"Reasoning effort set to **{level}**",
    }


# Custom invoke handlers that notify UI after state changes
async def _notify_session_state(service, session) -> None:
    await notify_session_state(service, session)


async def _list_models_on_invoke(service, session, args=None):
    ensured = await _ensure_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    return await list_models(ensured)


async def _switch_model_on_invoke(service, session, args=None):
    """Custom invoke handler that notifies UI after model switch."""
    ensured = await _ensure_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await switch_model(ensured, text)
    # Notify UI if we switched or initialized (not just querying, and no error)
    if "error" not in result and text.strip():
        await _notify_session_state(service, ensured)
    return result


async def _set_reasoning_on_invoke(service, session, args=None):
    """Custom invoke handler that notifies UI after reasoning change."""
    ensured = await _ensure_session(service, session)
    if ensured is None:
        raise ValueError("No session available")
    text = (args or {}).get("text", "")
    result = await set_reasoning(ensured, text)
    # Notify UI if we actually set a level (not just querying, and no error)
    if "error" not in result and text.strip():
        await _notify_session_state(service, ensured)
    return result


# Override the default invoke handlers with our custom ones
setattr(list_models, "_server_function_on_invoke", _list_models_on_invoke)
setattr(switch_model, "_server_function_on_invoke", _switch_model_on_invoke)
setattr(set_reasoning, "_server_function_on_invoke", _set_reasoning_on_invoke)
