import subprocess

from omniagents.core.tools.artifacts import (
    OfficeConversionError,
    emit_artifact,
    resolve_file_content,
)
from omniagents.core.tools.discovery import function_tool


@function_tool
def display_artifact(
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> dict:
    try:
        resolved = resolve_file_content(content, mode)
    except OfficeConversionError as e:
        return {"success": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "LibreOffice conversion timed out"}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "LibreOffice is not installed — cannot convert office documents to PDF",
        }

    emit_mode = "pdf" if mode in ("docx", "pptx") else mode
    emit_artifact(title, resolved, emit_mode, artifact_id)
    return {"success": True}


__all__ = ["display_artifact"]
