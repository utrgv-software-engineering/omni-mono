"""What a user message carried besides its text, as the transcript shows it.

The model receives full images and files; the transcript keeps a small
thumbnail per image and a name per file, so a chat shows what was sent
without storing (or re-sending to every client) multi-megabyte payloads.
Entries use the client attachment shape: ``{type, url?, filename?, mime?}``.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

THUMBNAIL_PX = 256
_JPEG_QUALITY = 80


def _thumbnail(data_url: str) -> Dict[str, Any]:
    """A small data-URL thumbnail of an inline image, or just its type."""
    from PIL import Image

    header, _, payload = data_url.partition(",")
    with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
        image.thumbnail((THUMBNAIL_PX, THUMBNAIL_PX))
        keep_alpha = image.mode in ("RGBA", "LA", "P")
        out = io.BytesIO()
        if keep_alpha:
            image.save(out, format="PNG", optimize=True)
            mime = "image/png"
        else:
            image.convert("RGB").save(out, format="JPEG", quality=_JPEG_QUALITY)
            mime = "image/jpeg"
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    original = header[5:].split(";", 1)[0] or mime
    return {"type": "image", "url": f"data:{mime};base64,{encoded}", "mime": original}


def describe_attachments(content: Any) -> List[Dict[str, Any]]:
    """Transcript attachment entries for a structured message *content*."""
    if not isinstance(content, list):
        return []
    entries: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "input_image":
            url = part.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            if isinstance(url, str) and url.startswith("data:"):
                try:
                    entries.append(_thumbnail(url))
                except Exception:
                    logger.warning(
                        "could not thumbnail an attached image", exc_info=True
                    )
                    entries.append({"type": "image"})
            elif isinstance(url, str) and url:
                entries.append({"type": "image", "url": url})
            else:
                entries.append({"type": "image"})
        elif kind == "input_file":
            entry: Dict[str, Any] = {"type": "file"}
            filename = part.get("filename")
            if isinstance(filename, str) and filename:
                entry["filename"] = filename
            entries.append(entry)
    return entries


__all__ = ["THUMBNAIL_PX", "describe_attachments"]
