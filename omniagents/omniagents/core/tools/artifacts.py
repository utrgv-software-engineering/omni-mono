from __future__ import annotations

from typing import Callable, Optional

from .discovery import function_tool

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
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> dict:
    """Display or update a rich artifact in the UI.

    Parameters
    ----------
    title : str
        Human-readable title shown in the collapsible header.
    content : str
        The body of the artifact. Meaning depends on *mode*:

        - ``"markdown"`` (default) – GitHub-flavoured Markdown.
        - ``"image"`` – URL/file path or base-64 data URI for an image. Displayed
          inside an ``InteractiveViewer`` so the user can pan and zoom.
        - Any other value – rendered as plain text.

    mode : str, optional
        Rendering mode for *content*. Defaults to ``"markdown"``.
    artifact_id : str, optional
        Stable identifier. If supplied, subsequent calls with the same
        ``artifact_id`` will update the existing artifact instead of creating a
        new one.

    Returns
    -------
    dict
        Always ``{"success": True}``. Included for consistency with other
        tools.
    """
    emit_artifact(title, content, mode, artifact_id)
    return {"success": True}
