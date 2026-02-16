import subprocess
import os

from omniagents.core.tools.artifacts import OfficeConversionError, emit_artifact, resolve_file_content
from omniagents.core.tools.discovery import function_tool


@function_tool
def display_artifact(
    title: str,
    *,
    content: str | None = None,
    path: str | None = None,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> dict:
    if content is not None and path is not None:
        return {"success": False, "error": "Provide only one of content or path"}

    if content is None and path is None:
        return {"success": False, "error": "Provide content or path"}

    if content is not None:
        emit_artifact(title, content, mode, artifact_id)
        return {"success": True, "emitted_mode": mode}

    expanded_path = os.path.expanduser((path or "").strip())
    if not expanded_path or not os.path.isfile(expanded_path):
        return {"success": False, "error": f"File not found: {expanded_path}"}

    try:
        resolved = resolve_file_content(expanded_path, mode)
    except UnicodeDecodeError:
        return {"success": False, "error": f"Unable to decode file as UTF-8: {expanded_path}"}
    except OfficeConversionError as e:
        return {"success": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "LibreOffice conversion timed out"}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "LibreOffice is not installed — cannot convert office documents to PDF",
        }

    emitted_mode = "pdf" if mode in ("docx", "pptx") else mode
    emit_artifact(title, resolved, emitted_mode, artifact_id)
    return {"success": True, "emitted_mode": emitted_mode}


__all__ = ["display_artifact"]
