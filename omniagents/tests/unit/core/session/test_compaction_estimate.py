"""The compaction trigger prices inline images by size, not base64 length."""

from __future__ import annotations

import base64
import struct

from omniagents.core.session.compaction import (
    _candidate_token_estimate,
    _image_token_estimate,
)


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", width, height)
    )


def _jpeg(width: int, height: int, exif_bytes: int = 0) -> bytes:
    exif = b"\xff\xe1" + struct.pack(">H", exif_bytes + 2) + b"\x00" * exif_bytes
    frame = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width)
    return b"\xff\xd8" + exif + frame


def _data_url(mime: str, data: bytes, padding: int = 0) -> str:
    return f"data:{mime};base64," + base64.b64encode(data + b"\x00" * padding).decode()


def test_images_cost_their_pixel_area():
    assert _image_token_estimate(_data_url("image/png", _png(1280, 800))) == 40 * 25
    # A large photo, with its frame header behind a big EXIF block.
    photo = _data_url("image/jpeg", _jpeg(4000, 3000, exif_bytes=60_000))
    assert _image_token_estimate(photo) == 125 * 94


def test_unreadable_images_cost_a_typical_screenshot():
    assert _image_token_estimate("data:image/png;base64,AAAA") == 1_500


def test_a_large_image_does_not_look_like_millions_of_tokens():
    # ~8M characters of base64 behind a 4000x3000 header.
    photo = _data_url("image/jpeg", _jpeg(4000, 3000), padding=6_000_000)
    message = {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "what is this?"},
            {"type": "input_image", "image_url": photo, "detail": "auto"},
        ],
    }
    assert 11_750 <= _candidate_token_estimate([message]) < 12_000
    tool_output = {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [{"type": "input_image", "image_url": photo}],
    }
    assert _candidate_token_estimate([tool_output]) < 12_000
