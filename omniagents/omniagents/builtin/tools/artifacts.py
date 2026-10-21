import base64
import mimetypes
import os
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools.artifacts import emit_artifact
from omniagents.core.tools.discovery import function_tool
from omniagents import workspace as _workspace

from ._workspace import workspace_root_from_ctx as _workspace_root_from_ctx


async def _resolve_workspace_file_content(path: str, mode: str) -> str:
    if mode in ("docx", "pptx"):
        raise ValueError(
            "display_artifact(path=...) cannot convert office documents through "
            "the active workspace yet; convert the file to PDF first and render "
            "it with mode='pdf'."
        )

    if not await _workspace.is_file(path):
        raise FileNotFoundError(path)

    if mode == "image":
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        encoded = base64.b64encode(await _workspace.read_bytes(path)).decode()
        return f"data:{mime};base64,{encoded}"

    if mode == "pdf":
        encoded = base64.b64encode(await _workspace.read_bytes(path)).decode()
        return f"data:application/pdf;base64,{encoded}"

    if mode in ("html", "markdown"):
        return await _workspace.read_text(path, encoding="utf-8")

    try:
        return await _workspace.read_text(path, encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        raise


@function_tool
async def display_artifact(
    ctx: RunContextWrapper[Any],
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
        mode: Content type — one of "markdown", "html", "image", or "pdf".
              Defaults to "markdown". "docx" and "pptx" require prior PDF conversion.
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

    from omniagents import workspace as _ws

    # Workspace path syntax: a Linux sandbox stays POSIX on a Windows host.
    try:
        pm = _ws.current_workspace().path_module
    except Exception:
        pm = os.path
    expanded_path = (path or "").strip()
    if pm is os.path:
        expanded_path = os.path.expanduser(expanded_path)
    ctx_workspace_root = _workspace_root_from_ctx(ctx)
    if expanded_path and not pm.isabs(expanded_path) and ctx_workspace_root:
        expanded_path = pm.normpath(pm.join(ctx_workspace_root, expanded_path))

    try:
        resolved = await _resolve_workspace_file_content(expanded_path, mode)
    except UnicodeDecodeError:
        return {
            "success": False,
            "error": f"Unable to decode file as UTF-8: {expanded_path}",
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {expanded_path}"}

    emitted_mode = "pdf" if mode in ("docx", "pptx") else mode
    emit_artifact(title, resolved, emitted_mode, artifact_id)
    return {"success": True, "emitted_mode": emitted_mode}


__all__ = ["display_artifact"]
