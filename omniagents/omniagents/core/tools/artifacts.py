from __future__ import annotations

import base64
import contextvars
import mimetypes
import os
import subprocess
import tempfile
from typing import Callable, Optional

class OfficeConversionError(Exception):
    """Raised when LibreOffice conversion to PDF fails."""


def convert_office_to_pdf(path: str) -> str:
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


_convert_office_to_pdf = convert_office_to_pdf


def resolve_file_content(path: str, mode: str) -> str:
    path = os.path.expanduser(path.strip())
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

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
        return convert_office_to_pdf(path)

    # For unknown modes, try reading as text
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        raise


_resolve_file_content = resolve_file_content

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

