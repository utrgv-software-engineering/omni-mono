from __future__ import annotations

from typing import Callable, Optional

from .tooling import function_tool

# Global callback used to display artifacts
# Callback signature accepts title, content, mode and an optional artifact_id
ArtifactCallback = Callable[[str, str, str, Optional[str]], None]

_artifact_callback: Optional[ArtifactCallback] = None


def set_artifact_callback(callback: ArtifactCallback) -> None:
    """Register a callback for displaying artifacts."""
    global _artifact_callback
    _artifact_callback = callback


def emit_artifact(
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> None:
    """Emit or update an artifact using the registered callback."""
    if _artifact_callback:
        try:
            _artifact_callback(title, content, mode, artifact_id)
        except Exception as e:
            print(f"Error during artifact callback: {e}")


@function_tool
def display_artifact(
    title: str, content: str, mode: str = "markdown", artifact_id: str | None = None
) -> dict:
    """Tool accessible by the LLM to display or update an artifact."""
    emit_artifact(title, content, mode, artifact_id)
    return {"success": True}

