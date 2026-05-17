"""Tests for image_tools module."""

import base64
import os
import tempfile

import pytest
from agents.tool import ToolOutputImage, ToolOutputText

from omniagents.builtin.tools.image_tools import read_image
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


def unwrap(tool):
    """Return the original async function attached by the tool decorator."""
    return tool._original_func


@pytest.fixture
def routed(tmp_path):
    """Install a HostWorkspace rooted at tmp_path and reset on teardown.

    All tests in this file operate within tmp_path so absolute paths the
    tests pass to ``read_image`` stay inside the workspace root.
    """
    workspace = HostWorkspace(root=str(tmp_path))
    token = set_workspace(workspace)
    yield workspace
    reset_workspace(token)


def create_test_png(path):
    """Create a minimal valid PNG file."""
    png_data = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x9a{\xe2\xb8"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with open(path, "wb") as f:
        f.write(png_data)


def create_test_jpeg(path):
    """Create a minimal valid JPEG file."""
    jpeg_data = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08"
        b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\x00"
        b"\xff\xd9"
    )
    with open(path, "wb") as f:
        f.write(jpeg_data)


@pytest.mark.asyncio
async def test_read_image_success(routed, tmp_path):
    """Test successfully reading an image file."""
    img_path = tmp_path / "test.png"
    create_test_png(str(img_path))

    result = await unwrap(read_image)(None, "test.png")

    assert isinstance(result, list)
    assert len(result) == 2
    assert isinstance(result[0], ToolOutputText)
    assert "Image loaded" in result[0].text
    assert "png" in result[0].text.lower()
    assert isinstance(result[1], ToolOutputImage)
    assert result[1].image_url is not None
    assert result[1].image_url.startswith("data:image/png;base64,")

    _, base64_data = result[1].image_url.split(",", 1)
    decoded = base64.b64decode(base64_data)
    assert len(decoded) > 0


@pytest.mark.asyncio
async def test_read_image_with_detail(routed, tmp_path):
    """Test reading an image with specific detail level."""
    img_path = tmp_path / "test.jpg"
    create_test_jpeg(str(img_path))

    result = await unwrap(read_image)(None, "test.jpg", detail="high")

    assert isinstance(result[1], ToolOutputImage)
    assert result[1].detail == "high"


@pytest.mark.asyncio
async def test_read_image_file_not_found(routed):
    """Test reading a non-existent image file."""
    result = await unwrap(read_image)(None, "missing.png")

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], ToolOutputText)
    assert "File not found" in result[0].text


@pytest.mark.asyncio
async def test_read_image_not_a_file(routed, tmp_path):
    """Test reading a directory instead of a file."""
    (tmp_path / "subdir").mkdir()
    result = await unwrap(read_image)(None, "subdir")

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], ToolOutputText)
    assert "not a file" in result[0].text


@pytest.mark.asyncio
async def test_read_image_various_formats(routed, tmp_path):
    """Test reading images in various formats."""
    test_cases = [
        (".png", create_test_png, "image/png"),
        (".jpg", create_test_jpeg, "image/jpeg"),
    ]

    for extension, create_func, _expected_mime in test_cases:
        rel = f"sample{extension}"
        full = tmp_path / rel
        create_func(str(full))

        result = await unwrap(read_image)(None, rel)

        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], ToolOutputText)
        assert isinstance(result[1], ToolOutputImage)
        assert result[1].image_url.startswith("data:image/")
