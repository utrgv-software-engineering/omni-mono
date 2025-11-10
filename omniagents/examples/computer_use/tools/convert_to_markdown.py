from omniagents.core.tools import rich_function_tool, RichToolOutput
import os


_md_converter = None


def _get_converter():
    global _md_converter
    if _md_converter is not None:
        return _md_converter
    try:
        from markitdown import MarkItDown
    except Exception as e:
        raise ImportError(f"markitdown not available: {e}")
    _md_converter = MarkItDown(enable_plugins=True)
    return _md_converter


@rich_function_tool
def convert_to_markdown(file_path: str) -> RichToolOutput:
    """Convert a local file or URL into Markdown for LLM analysis.

    Args:
        file_path: Path to a local file or a URL (supports many formats, including
                   PDFs, Office docs, HTML, images with OCR/EXIF, audio with
                   transcription, YouTube links for transcripts, and more).

    Returns:
        RichToolOutput whose value is the Markdown content (on success) or an
        error string (on failure). The UI metadata includes source type, content
        length, preview, and success flag.

    Notes:
        - Requires the `markitdown` package. If unavailable, the tool returns a
          helpful error description.
        - For URLs, the tool fetches and converts remote content when supported.
        - For large documents, only a preview is shown in summary; the full
          content is returned in the tool value.

    Example:
        convert_to_markdown("/path/to/file.pdf")
        convert_to_markdown("https://example.com/page.html")
        convert_to_markdown("https://youtu.be/VIDEOID")
    """
    try:
        if not (file_path.startswith("http://") or file_path.startswith("https://")):
            if not os.path.exists(file_path):
                msg = f"File not found at {file_path}"
                ui = {
                    "value": msg,
                    "display_type": "error",
                    "summary": "File not found",
                    "preview": msg,
                    "truncated": False,
                    "metadata": {
                        "error_type": "file_not_found",
                        "file_path": file_path,
                        "success": False,
                    },
                }
                return RichToolOutput(msg, ui)

        conv = _get_converter()
        result = conv.convert(file_path)
        content = result.text_content
        preview = content[:500] if content else "No content extracted"
        truncated = bool(content and len(content) > 500)
        if file_path.startswith("http"):
            source = (
                "youtube"
                if ("youtube.com" in file_path or "youtu.be" in file_path)
                else "web"
            )
        else:
            ext = os.path.splitext(file_path)[1].lower()
            source = ext[1:] if ext else "unknown"
        ui = {
            "value": content,
            "display_type": "conversion",
            "summary": f"Converted {os.path.basename(file_path) if not file_path.startswith('http') else file_path}",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "success": True,
                "file_path": file_path,
                "source_type": source,
                "content_length": len(content) if content else 0,
            },
        }
        return RichToolOutput(content, ui)
    except ImportError as e:
        msg = f"Error converting file: {e}"
        ui = {
            "value": msg,
            "display_type": "error",
            "summary": "Conversion error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "missing_dependency",
                "file_path": file_path,
                "error": str(e),
                "success": False,
            },
        }
        return RichToolOutput(msg, ui)
    except Exception as e:
        msg = f"Error converting file: {str(e)}"
        ui = {
            "value": msg,
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
        return RichToolOutput(msg, ui)
