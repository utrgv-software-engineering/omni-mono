# Initialize MarkItDown
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput
from markitdown import MarkItDown
import os

md_converter = MarkItDown(enable_plugins=True)  # Enable plugins with all features


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    try:
        if ctx is None:
            return None
        c = getattr(ctx, "context", None)
        if c is None:
            return None
        if isinstance(c, dict):
            wr = c.get("workspace_root")
        else:
            wr = getattr(c, "workspace_root", None)
        if isinstance(wr, str) and wr.strip():
            return os.path.abspath(wr)
    except Exception:
        pass
    return None


# Define tools
@rich_function_tool
def convert_to_markdown(
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
    try:
        # Anchor relative local paths to workspace_root (URLs pass through).
        if not file_path.startswith('http'):
            wr = _workspace_root_from_ctx(ctx)
            if wr and not os.path.isabs(file_path):
                file_path = os.path.abspath(os.path.join(wr, file_path))
        # Check if file exists (skip for URLs)
        if not file_path.startswith('http') and not os.path.exists(file_path):
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
                    "success": False
                }
            }
            return RichToolOutput(error_msg, ui_metadata)
        
        # Convert file to markdown
        result = md_converter.convert(file_path)
        content = result.text_content
        
        # Create preview (first 500 chars)
        preview = content[:500] if content else "No content extracted"
        truncated = len(content) > 500 if content else False
        
        # Determine source type
        if file_path.startswith('http'):
            if 'youtube.com' in file_path or 'youtu.be' in file_path:
                source_type = 'youtube'
            else:
                source_type = 'web'
        else:
            ext = os.path.splitext(file_path)[1].lower()
            source_type = ext[1:] if ext else 'unknown'
        
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
                "content_length": len(content) if content else 0
            }
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
                "success": False
            }
        }
        return RichToolOutput(error_msg, ui_metadata)
