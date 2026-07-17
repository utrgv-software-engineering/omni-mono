# Initialize MarkItDown
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from markitdown import MarkItDown

from omniagents.builtin.tools._workspace import workspace_root_from_ctx
from omniagents.core.tools import RichToolOutput, rich_function_tool

md_converter = MarkItDown(enable_plugins=True)  # Enable plugins with all features


# Define tools
@rich_function_tool
async def convert_to_markdown(
    ctx: RunContextWrapper[Any],
    file_path: str,
) -> RichToolOutput:
    """
    Extract text/markdown from BINARY or RICH-FORMAT files (PDF, Word,
    PowerPoint, Excel, images, audio, HTML, YouTube). Use this only when
    the file is in a format `read_file` cannot handle as plain text.

    DO NOT use this for plain-text files. For source code, markdown,
    YAML, JSON, TOML, INI, plain `.txt`, `.log`, `.csv` (when treated as
    text), config files, or anything you can read with `cat`, use
    `read_file` instead. This tool is heavier (loads MarkItDown,
    sometimes does OCR or transcription) and provides no benefit for
    files that are already text.

    Args:
        file_path: Path to a binary/rich-format file, or a URL
            (YouTube, web pages). Plain-text local files should be read
            with `read_file`, not this tool.

    Returns:
        JSON object with:
        {
            "success": boolean,
            "content": markdown content of the converted file/URL,
            "error": error message (if unsuccessful)
        }

    Supported source formats this tool is FOR:
    - Documents: PDF, Word (DOCX, DOC), PowerPoint (PPTX, PPT), Excel (XLSX, XLS)
    - Media: Images (with EXIF + optional OCR), Audio (with transcription)
    - Web content: HTML pages, YouTube videos (with transcripts)
    - Archives: ZIP (processes contained files)
    - E-books: EPUB
    - Emails: Outlook .msg messages

    Formats this tool is NOT for (use `read_file`):
    - Source code: .py, .ts, .tsx, .js, .jsx, .go, .rs, .rb, .java, .c, .h, .cpp, .swift, .kt, etc.
    - Markup: .md, .mdx, .rst, .adoc
    - Config: .yaml, .yml, .json, .toml, .ini, .env, .conf
    - Plain text: .txt, .log
    - Anything ending in SKILL.md (use `load_skill` instead)

    For YouTube videos, returns title/metadata/description/transcript.
    For images, returns EXIF + OCR text.
    For audio, returns metadata + transcription.
    """
    from omniagents import workspace as _ws
    from omniagents.workspace import HostWorkspace

    temp_host_path: Optional[str] = None
    is_url = file_path.startswith("http")

    try:
        # Anchor relative local paths to workspace_root (URLs pass through).
        if not is_url:
            wr = workspace_root_from_ctx(ctx)
            if wr and not os.path.isabs(file_path):
                file_path = os.path.abspath(os.path.join(wr, file_path))

        # Decide whether we need a host-side bridge file. MarkItDown reads
        # paths directly from the host filesystem, so when the active
        # workspace is a HostWorkspace the file is already where MarkItDown
        # can find it — no bridge needed. For URLs, no local file is
        # involved at all. For any other workspace backend (SandboxWorkspace
        # in particular), we materialise the workspace bytes to a host temp
        # file and clean up in the finally block.
        active_ws = _ws.current_workspace()
        is_host_ws = isinstance(active_ws, HostWorkspace)

        if is_url:
            local_path_for_markitdown = file_path
        elif is_host_ws:
            # Host mode: the file is already where MarkItDown can read it.
            # Use os.path.exists directly — workspace.exists would call into
            # Path.exists under the hood, and existing mocks intercept at
            # the os.path layer.
            if not os.path.exists(file_path):
                error_msg = f"File not found at {file_path}"
                ui_metadata = {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "File not found",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {
                        "error_type": "file_not_found",
                        "file_path": file_path,
                        "success": False,
                    },
                }
                return RichToolOutput(error_msg, ui_metadata)
            local_path_for_markitdown = file_path
        else:
            # Sandbox / non-host workspace: bridge content to host temp.
            if not await _ws.exists(file_path):
                error_msg = f"File not found at {file_path}"
                ui_metadata = {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "File not found",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {
                        "error_type": "file_not_found",
                        "file_path": file_path,
                        "success": False,
                    },
                }
                return RichToolOutput(error_msg, ui_metadata)

            data = await _ws.read_bytes(file_path)
            suffix = os.path.splitext(file_path)[1] or ""
            fd, temp_host_path = tempfile.mkstemp(prefix="convert_", suffix=suffix)
            os.close(fd)
            await asyncio.to_thread(Path(temp_host_path).write_bytes, data)
            local_path_for_markitdown = temp_host_path

        # Convert file to markdown (host-side library call; runs off the
        # event loop so we don't stall other coroutines).
        result = await asyncio.to_thread(
            md_converter.convert, local_path_for_markitdown
        )
        content = result.text_content

        # Create preview (first 500 chars)
        preview = content[:500] if content else "No content extracted"
        truncated = len(content) > 500 if content else False

        # Determine source type
        if file_path.startswith("http"):
            if "youtube.com" in file_path or "youtu.be" in file_path:
                source_type = "youtube"
            else:
                source_type = "web"
        else:
            ext = os.path.splitext(file_path)[1].lower()
            source_type = ext[1:] if ext else "unknown"

        ui_metadata = {
            "value": content,  # The actual markdown content
            "display_type": "conversion",
            "summary": f"Converted {os.path.basename(file_path) if not file_path.startswith('http') else file_path}",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "success": True,
                "file_path": file_path,
                "source_type": source_type,
                "content_length": len(content) if content else 0,
            },
        }
        return RichToolOutput(content, ui_metadata)
    except Exception as e:
        error_msg = f"Error converting file: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Conversion error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "conversion_error",
                "file_path": file_path,
                "error": str(e),
                "success": False,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
    finally:
        # Clean up the host-side bridge file if we created one.
        if temp_host_path is not None:
            try:
                os.unlink(temp_host_path)
            except OSError:
                pass
