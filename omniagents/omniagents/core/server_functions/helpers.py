from __future__ import annotations

from typing import Any, Optional


async def ensure_server_session(service: Any, session: Any | None) -> Any | None:
    manager = getattr(service, "_session_manager", None)
    ensured = session
    if manager is not None and (ensured is None or not getattr(ensured, "id", None)):
        ensured = manager.get_or_create(None)
    return ensured


async def request_client_function_safe(
    service: Any,
    function_name: str,
    payload: dict[str, Any],
    *,
    session: Any | None,
    expect_result: bool = False,
) -> bool:
    try:
        await service.request_client_function(
            function_name,
            payload,
            session=session,
            expect_result=expect_result,
        )
        return True
    except Exception:
        return False


async def notify_session_state(service: Any, session: Any) -> bool:
    model_config = getattr(session, "model_config", None) or {}
    payload = {
        "model": getattr(session, "active_model", None),
        "label": model_config.get("label"),
        "provider": model_config.get("provider"),
        "max_input_tokens": model_config.get("max_input_tokens"),
        "max_output_tokens": model_config.get("max_output_tokens"),
        "reasoning_effort": getattr(session, "reasoning_effort", None),
    }
    return await request_client_function_safe(
        service,
        "ui.set_session_state",
        payload,
        session=session,
        expect_result=False,
    )


async def invoke_artifact_bridge(
    service: Any, session: Any | None, args: dict | None = None
) -> dict:
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        raise ValueError("No session available")

    payload = args or {}
    title = str(payload.get("title") or "")
    content = str(payload.get("content") or "")
    mode = str(payload.get("mode") or "markdown")
    artifact_id = payload.get("artifact_id")
    if artifact_id is not None:
        artifact_id = str(artifact_id)

    await request_client_function_safe(
        service,
        "ui.add_artifact",
        {
            "title": title,
            "content": content,
            "mode": mode,
            "artifact_id": artifact_id,
        },
        session=ensured,
        expect_result=False,
    )
    return {"ok": True}


def session_workspace_root(session: Any) -> Optional[str]:
    """The workspace_root stored on a session, or ``None``.

    Single source of the "check ``session.context`` (dict or attr), then
    ``session.variables``" read that server functions need —
    ``workspace_root_from_ctx`` covers the RunContextWrapper shape; this
    covers the Session shape.
    """
    ctx = getattr(session, "context", None)
    if isinstance(ctx, dict):
        value = ctx.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    elif ctx is not None:
        value = getattr(ctx, "workspace_root", None)
        if isinstance(value, str) and value.strip():
            return value
    variables = getattr(session, "variables", None)
    if isinstance(variables, dict):
        value = variables.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    return None


async def ensure_session_with_workspace(service: Any, session: Any) -> Any:
    """Ensure a live session and persist its workspace root.

    Resolves the session through :func:`ensure_server_session`, then
    proxies the session's workspace root to the builtin ``session.ensure``
    RPC so it survives restarts. Returns the ensured session (or None).
    """
    ensured = await ensure_server_session(service, session)
    if ensured is None:
        return None

    workspace_root = session_workspace_root(ensured)
    call_args = {"workspace_root": workspace_root} if workspace_root else None
    await service.server_call(
        "session.ensure",
        call_args,
        session_id=getattr(ensured, "id", None),
    )
    return ensured
