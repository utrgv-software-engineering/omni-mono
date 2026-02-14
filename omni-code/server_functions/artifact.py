from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.core.session import Session


@server_function(
    description="Display an artifact in the UI",
    params_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string"},
            "artifact_id": {"type": ["string", "null"]},
        },
        "required": ["title", "content"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
    strict=True,
    name_override="artifact",
)
async def artifact(
    session: Session,
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: Optional[str] = None,
) -> dict:
    return {"ok": True}


async def _artifact_on_invoke(service, session, args=None):
    ensured = session
    if ensured is None:
        try:
            sess = getattr(service, "_session_manager", None)
            if sess is not None:
                ensured = sess.get_or_create(None)
        except Exception:
            ensured = None

    if ensured is None:
        raise ValueError("No session available")

    payload = args or {}
    title = str(payload.get("title") or "")
    content = str(payload.get("content") or "")
    mode = str(payload.get("mode") or "markdown")
    artifact_id = payload.get("artifact_id")
    if artifact_id is not None:
        artifact_id = str(artifact_id)

    try:
        await service.request_client_function(
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
    except Exception:
        pass

    return {"ok": True}


setattr(artifact, "_server_function_on_invoke", _artifact_on_invoke)
