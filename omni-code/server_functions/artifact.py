from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.core.server_functions.helpers import invoke_artifact_bridge
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
    return await invoke_artifact_bridge(service, session, args)


setattr(artifact, "_server_function_on_invoke", _artifact_on_invoke)
