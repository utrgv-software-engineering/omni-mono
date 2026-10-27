"""Transcript attachments: a small thumbnail per image, a name per file."""

import base64
import io

from PIL import Image

from omniagents.core.conversation.attachments import THUMBNAIL_PX, describe_attachments


def _data_url(size, mode="RGB", fmt="JPEG"):
    out = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else (255, 0, 0, 128)).save(
        out, format=fmt
    )
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(out.getvalue()).decode()


def _decoded_size(url):
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        return image.size


def test_images_become_small_thumbnails_and_files_keep_their_names():
    photo = _data_url((4000, 3000))
    entries = describe_attachments(
        [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": photo, "detail": "auto"},
            {"type": "input_image", "image_url": _data_url((300, 300), "RGBA", "PNG")},
            {"type": "input_file", "filename": "notes.pdf", "file_data": "AAAA"},
        ]
    )
    assert [entry["type"] for entry in entries] == ["image", "image", "file"]
    assert entries[0]["mime"] == "image/jpeg"
    assert entries[0]["url"].startswith("data:image/jpeg;base64,")
    assert max(_decoded_size(entries[0]["url"])) == THUMBNAIL_PX
    assert len(entries[0]["url"]) < len(photo) // 20
    # Transparency survives as PNG.
    assert entries[1]["url"].startswith("data:image/png;base64,")
    assert entries[2] == {"type": "file", "filename": "notes.pdf"}


def test_unreadable_images_and_plain_text_are_tolerated():
    assert describe_attachments("just text") == []
    assert describe_attachments(
        [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]
    ) == [{"type": "image"}]
    assert describe_attachments(
        [{"type": "input_image", "image_url": "https://example.com/cat.png"}]
    ) == [{"type": "image", "url": "https://example.com/cat.png"}]
