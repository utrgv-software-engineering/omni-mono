"""Tests for image_tools module."""

import os
import tempfile
import base64
import types
import pytest
from omniagents.builtin.tools.image_tools import read_image
from agents.tool import ToolOutputText, ToolOutputImage


def unwrap(tool):
    """Extract the underlying function from a FunctionTool."""
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


def create_test_png(path):
    """Create a minimal valid PNG file."""
    # Minimal 1x1 red PNG
    png_data = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"  # IHDR chunk
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x9a{\xe2\xb8"  # IDAT chunk
        b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND chunk
    )
    with open(path, "wb") as f:
        f.write(png_data)


def create_test_jpeg(path):
    """Create a minimal valid JPEG file."""
    # Minimal valid JPEG
    jpeg_data = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08"
        b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\x00"
        b"\xff\xd9"  # EOI marker
    )
    with open(path, "wb") as f:
        f.write(jpeg_data)


def test_read_image_success():
    """Test successfully reading an image file."""
    # Create a temporary image file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        create_test_png(tmp_path)

        # Read the image
        result = unwrap(read_image)(None, tmp_path)

        # Verify we got a list with 2 items
        assert isinstance(result, list)
        assert len(result) == 2

        # Verify first item is text
        assert isinstance(result[0], ToolOutputText)
        assert "Image loaded" in result[0].text
        assert "png" in result[0].text.lower()

        # Verify second item is image
        assert isinstance(result[1], ToolOutputImage)
        assert result[1].image_url is not None
        assert result[1].image_url.startswith("data:image/png;base64,")

        # Verify the image data can be decoded
        _, base64_data = result[1].image_url.split(",", 1)
        decoded = base64.b64decode(base64_data)
        assert len(decoded) > 0

    finally:
        # Clean up
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_read_image_with_detail():
    """Test reading an image with specific detail level."""
    # Create a temporary image file
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        create_test_jpeg(tmp_path)

        # Read the image with high detail
        result = unwrap(read_image)(None, tmp_path, detail="high")

        # Verify the detail level is set
        assert isinstance(result[1], ToolOutputImage)
        assert result[1].detail == "high"

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_read_image_file_not_found():
    """Test reading a non-existent image file."""
    result = unwrap(read_image)(None, "/nonexistent/image.png")

    # Should return text output with error
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], ToolOutputText)
    assert "File not found" in result[0].text


def test_read_image_not_a_file():
    """Test reading a directory instead of a file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = unwrap(read_image)(None, tmpdir)

        # Should return text output with error
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ToolOutputText)
        assert "not a file" in result[0].text


def test_read_image_various_formats():
    """Test reading images in various formats."""
    test_cases = [
        (".png", create_test_png, "image/png"),
        (".jpg", create_test_jpeg, "image/jpeg"),
    ]

    for extension, create_func, expected_mime in test_cases:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            create_func(tmp_path)
            result = unwrap(read_image)(None, tmp_path)

            # Verify we got valid output
            assert isinstance(result, list)
            assert len(result) == 2
            assert isinstance(result[0], ToolOutputText)
            assert isinstance(result[1], ToolOutputImage)
            assert result[1].image_url.startswith(f"data:image/")

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
