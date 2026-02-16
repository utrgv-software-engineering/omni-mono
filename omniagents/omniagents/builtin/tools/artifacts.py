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
    """Display content as a rich artifact in the UI.

    Provide either ``content`` (raw string) or ``path`` (filesystem path), not both.

    When using ``path``, the file is read according to ``mode``:
    - ``"markdown"`` / ``"html"``: read as UTF-8 text.
    - ``"image"``: base64-encoded with its MIME type.
    - ``"pdf"``: base64-encoded as application/pdf.
    - ``"docx"`` / ``"pptx"``: converted to PDF via LibreOffice, then base64-encoded.

    When using ``content``, the string is emitted directly with no processing.

    Args:
        title: Display label shown in the artifact UI.
        content: Raw string content to display.
        path: Filesystem path to a file to display.
        mode: Content type — one of "markdown", "html", "image", "pdf", "docx", or "pptx".
              Defaults to "markdown". "docx" and "pptx" are emitted as "pdf".
        artifact_id: Optional identifier. Pass an existing ID to update an artifact in place;
                     omit to create a new one.
    """
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
