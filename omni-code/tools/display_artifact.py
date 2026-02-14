from __future__ import annotations

from omniagents import function_tool
from omniagents.core.tools.artifacts import emit_artifact


@function_tool
def display_artifact(
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> dict:
    emit_artifact(title=title, content=content, mode=mode, artifact_id=artifact_id)
    return {"success": True}
