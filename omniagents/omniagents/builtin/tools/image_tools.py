import os
import base64
import mimetypes
from typing import Optional, Any, List, Union
from omniagents.core.tools import function_tool
from agents.run_context import RunContextWrapper
from agents.tool import ToolOutputText, ToolOutputImage


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    """Extract workspace root from context if available."""
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


def _resolve_path(ctx: RunContextWrapper[Any] | None, path: Optional[str]) -> str:
    """Resolve a path relative to workspace root or make absolute."""
    wr = _workspace_root_from_ctx(ctx)
    if path is None or (isinstance(path, str) and path.strip() == ""):
        return wr or "."
    p = path if isinstance(path, str) else str(path)
    if os.path.isabs(p):
        return p
    if wr:
        return os.path.abspath(os.path.join(wr, p))
    return p


@function_tool(client_status="Loading image...")
def read_image(
    ctx: RunContextWrapper[Any],
    image_path: str,
    detail: Optional[str] = None,
) -> List[Union[ToolOutputText, ToolOutputImage]]:
    """
    Reads an image file and returns it for the LLM to process using vision capabilities.

    Args:
        image_path: Path to the image file to read
        detail: Optional detail level for image processing: "low", "high", or "auto" (default)

    Returns:
        A list containing both a text description and the image itself, allowing the LLM
        to see and analyze the image content.

    Supported formats:
        - PNG (.png)
        - JPEG (.jpg, .jpeg)
        - GIF (.gif)
        - WebP (.webp)
        - BMP (.bmp)
        - And other formats supported by the model

    Usage:
        Use this tool to read images and make them available to the LLM for analysis.
        The LLM can then describe the image, extract text from it, analyze visual content, etc.

    Examples:
        # Read a screenshot
        read_image("/path/to/screenshot.png")

        # Read a diagram with high detail
        read_image("/path/to/diagram.png", detail="high")

        # Read a photo with auto detail level
        read_image("/path/to/photo.jpg", detail="auto")
    """
    try:
        # Resolve the path
        image_path = _resolve_path(ctx, image_path)

        # Check if file exists
        if not os.path.exists(image_path):
            error_text = f"Error reading image: File not found at {image_path}"
            return [ToolOutputText(type="text", text=error_text)]

        if not os.path.isfile(image_path):
            error_text = f"Error reading image: Path is not a file: {image_path}"
            return [ToolOutputText(type="text", text=error_text)]

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None or not mime_type.startswith("image/"):
            # Try to detect from extension
            ext = os.path.splitext(image_path)[1].lower()
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".svg": "image/svg+xml",
            }
            mime_type = mime_map.get(ext, "image/png")

        # Read the image file
        with open(image_path, "rb") as f:
            image_data = f.read()

        # Convert to base64
        base64_data = base64.b64encode(image_data).decode("utf-8")

        # Create data URL
        data_url = f"data:{mime_type};base64,{base64_data}"

        # Get file size for the description
        file_size = len(image_data)
        size_kb = file_size / 1024

        # Create text description
        text_description = (
            f"Image loaded from {os.path.basename(image_path)} "
            f"({size_kb:.1f} KB, {mime_type}). "
            f"The image is now available for analysis."
        )

        # Create the outputs
        text_output = ToolOutputText(type="text", text=text_description)

        # Set detail level (default to "auto" if not specified)
        detail_level = detail if detail in ["low", "high", "auto"] else "auto"

        image_output = ToolOutputImage(
            type="image", image_url=data_url, detail=detail_level
        )

        # Return both text and image
        return [text_output, image_output]

    except Exception as e:
        error_text = f"Error reading image: {str(e)}"
        return [ToolOutputText(type="text", text=error_text)]
