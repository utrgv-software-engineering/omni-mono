from __future__ import annotations

import base64
import contextvars
import mimetypes
import os
import subprocess
import tempfile
from typing import Callable, Optional

from .discovery import function_tool


class OfficeConversionError(Exception):
    """Raised when LibreOffice conversion to PDF fails."""


def _convert_office_to_pdf(path: str) -> str:
    """Convert a docx/pptx file to a base64-encoded PDF data URI via LibreOffice."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                path,
            ],
            capture_output=True,
            timeout=60,
        )
        pdf_name = os.path.splitext(os.path.basename(path))[0] + ".pdf"
        pdf_path = os.path.join(tmpdir, pdf_name)
        if not os.path.isfile(pdf_path):
            stderr = result.stderr.decode(errors="replace").strip()
            raise OfficeConversionError(
                f"LibreOffice failed to convert {os.path.basename(path)} to PDF"
                + (f": {stderr}" if stderr else "")
            )
        with open(pdf_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        return f"data:application/pdf;base64,{encoded}"


def _resolve_file_content(content: str, mode: str) -> str:
    """If *content* looks like a file path that exists, read it inline.

    Returns the original *content* unchanged when it is not a readable path.
    """
    # Quick heuristics: skip URLs, data URIs, and anything that looks like
    # actual inline content (HTML tags, markdown, multi-line text, base64 blocks).
    if (
        content.startswith(("http://", "https://", "data:"))
        or "\n" in content
        or len(content) > 1024
    ):
        return content

    path = os.path.expanduser(content.strip())
    if not os.path.isfile(path):
        return content

    if mode == "image":
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{encoded}"

    if mode == "pdf":
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        return f"data:application/pdf;base64,{encoded}"

    if mode in ("html", "markdown"):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    if mode in ("docx", "pptx"):
        return _convert_office_to_pdf(path)

    # For unknown modes, try reading as text
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return content

# Global callback used to display artifacts
# Callback signature accepts title, content, mode and an optional artifact_id
ArtifactCallback = Callable[[str, str, str, Optional[str]], None]

_artifact_callback: contextvars.ContextVar[Optional[ArtifactCallback]] = (
    contextvars.ContextVar("omniagents_artifact_callback", default=None)
)


def set_artifact_callback(callback: ArtifactCallback) -> contextvars.Token:
    return _artifact_callback.set(callback)


def reset_artifact_callback(token: contextvars.Token) -> None:
    _artifact_callback.reset(token)


def emit_artifact(
    title: str,
    content: str,
    mode: str = "markdown",
    artifact_id: str | None = None,
) -> None:
    """Emit or update an artifact using the registered callback."""
    callback = _artifact_callback.get()
    if callback:
        try:
            callback(title, content, mode, artifact_id)
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
          Can be a file path to a ``.md`` file.
        - ``"image"`` – URL, base-64 data URI, or **file path** for an image.
          File paths are read and converted to data URIs automatically.
          Clicking the image opens a fullscreen lightbox with zoom and pan.
        - ``"html"`` – Raw HTML string or **file path** to an ``.html`` file.
          Rendered in a sandboxed ``<iframe>`` that auto-resizes to fit
          content height.
        - ``"pdf"`` – Base-64 encoded PDF data, a ``data:`` URI, or a **file
          path** to a ``.pdf`` file. Rendered in an embedded ``<iframe>``
          via a blob URL.
        - ``"docx"`` – **File path** to a ``.docx`` file. Converted to PDF
          via LibreOffice and rendered in the PDF viewer.
        - ``"pptx"`` – **File path** to a ``.pptx`` file. Converted to PDF
          via LibreOffice and rendered in the PDF viewer.
        - Any other value – rendered as plain text (or read from a file path
          if one is provided).

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
    try:
        resolved = _resolve_file_content(content, mode)
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
