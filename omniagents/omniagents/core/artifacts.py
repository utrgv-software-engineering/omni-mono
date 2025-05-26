from __future__ import annotations

from typing import Callable, Optional

from .tooling import function_tool

# Global callback used to display artifacts
_artifact_callback: Optional[Callable[[str, str, str], None]] = None


def set_artifact_callback(callback: Callable[[str, str, str], None]) -> None:
    """Register a callback for displaying artifacts."""
    global _artifact_callback
    _artifact_callback = callback


def emit_artifact(title: str, content: str, mode: str = "markdown") -> None:
    """Emit an artifact using the registered callback."""
    if _artifact_callback:
        try:
            _artifact_callback(title, content, mode)
        except Exception as e:
            print(f"Error during artifact callback: {e}")


@function_tool
def display_artifact(title: str, content: str, mode: str = "markdown") -> dict:
    """Tool accessible by the LLM to display an artifact."""
    emit_artifact(title, content, mode)
    return {"success": True}

