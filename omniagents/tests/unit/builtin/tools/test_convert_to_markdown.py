from unittest.mock import MagicMock, patch

from omniagents.builtin.tools.convert_to_markdown import convert_to_markdown


def _patch_converter():
    """Patch the lazy MarkItDown factory with a mock converter; returns the
    context manager and lets callers grab ``mock.convert``."""
    mock_converter = MagicMock()
    return (
        patch(
            "omniagents.builtin.tools.convert_to_markdown._converter",
            return_value=mock_converter,
        ),
        mock_converter,
    )


# Mock MarkItDown result class
class MockMarkdownResult:
    """Mock object returned by MarkItDown.convert."""

    def __init__(self, text_content):
        self.text_content = text_content


# Tests
def test_convert_to_markdown_local_file_success(invoke_tool):
    """Test successful conversion of a local file."""
    # Mock data
    file_path = "/path/to/document.pdf"
    markdown_content = "# Document Title\n\nThis is the content of the document."

    # Setup mocks
    with (
        patch(
            "omniagents.builtin.tools.convert_to_markdown.os.path.exists",
            return_value=True,
        ),
        patch(
            "omniagents.builtin.tools.convert_to_markdown._converter",
        ) as mock_factory,
    ):
        mock_convert = mock_factory.return_value.convert

        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result

        result = invoke_tool(convert_to_markdown, file_path=file_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert result.ui_metadata["value"] == markdown_content
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(file_path)


def test_convert_to_markdown_url_success(invoke_tool):
    """Test successful conversion of a URL."""
    # Mock data
    url = "https://example.com/document.pdf"
    markdown_content = "# Web Document\n\nThis is the content from a URL."

    # Setup mocks
    converter_patch, mock_converter = _patch_converter()
    with converter_patch:
        mock_convert = mock_converter.convert

        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result

        result = invoke_tool(convert_to_markdown, file_path=url)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert result.ui_metadata["value"] == markdown_content
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(url)


def test_convert_to_markdown_youtube_success(invoke_tool):
    """Test successful conversion of a YouTube URL."""
    # Mock data
    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    markdown_content = """# Video Title: Never Gonna Give You Up

## Video Info
- Views: 1,234,567,890
- Duration: 3:32
- Channel: Rick Astley

## Description
This is the official video description.

## Transcript
00:00 We're no strangers to love
00:03 You know the rules and so do I
...
"""

    # Setup mocks
    converter_patch, mock_converter = _patch_converter()
    with converter_patch:
        mock_convert = mock_converter.convert

        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result

        result = invoke_tool(convert_to_markdown, file_path=youtube_url)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert result.ui_metadata["value"] == markdown_content
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(youtube_url)


def test_convert_to_markdown_file_not_found(invoke_tool):
    """Test conversion when file is not found."""
    # Mock data
    file_path = "/path/to/nonexistent.pdf"

    # Setup mocks
    with patch(
        "omniagents.builtin.tools.convert_to_markdown.os.path.exists",
        return_value=False,
    ):
        result = invoke_tool(convert_to_markdown, file_path=file_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is False
        assert "File not found" in result.ui_metadata["value"]
        assert file_path in result.ui_metadata["value"]


def test_convert_to_markdown_conversion_error(invoke_tool):
    """Test handling of conversion errors."""
    # Mock data
    file_path = "/path/to/document.pdf"

    # Setup mocks
    with (
        patch(
            "omniagents.builtin.tools.convert_to_markdown.os.path.exists",
            return_value=True,
        ),
        patch(
            "omniagents.builtin.tools.convert_to_markdown._converter",
        ) as mock_factory,
    ):
        mock_convert = mock_factory.return_value.convert

        # Configure mock to raise an exception
        mock_convert.side_effect = Exception("Unsupported file format")

        result = invoke_tool(convert_to_markdown, file_path=file_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is False
        assert "Error converting file" in result.ui_metadata["value"]
        assert "Unsupported file format" in result.ui_metadata["value"]


def test_convert_to_markdown_word_document(invoke_tool):
    """Test conversion of a Word document."""
    # Mock data
    file_path = "/path/to/document.docx"
    markdown_content = """# Word Document Title

## Section 1
This is the content of section 1.

## Section 2
This is the content of section 2.

* Bullet point 1
* Bullet point 2
"""

    # Setup mocks
    with (
        patch(
            "omniagents.builtin.tools.convert_to_markdown.os.path.exists",
            return_value=True,
        ),
        patch(
            "omniagents.builtin.tools.convert_to_markdown._converter",
        ) as mock_factory,
    ):
        mock_convert = mock_factory.return_value.convert

        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result

        result = invoke_tool(convert_to_markdown, file_path=file_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert result.ui_metadata["value"] == markdown_content
        assert "## Section 1" in result.ui_metadata["value"]
        assert "* Bullet point" in result.ui_metadata["value"]


def test_convert_to_markdown_image_with_ocr(invoke_tool):
    """Test conversion of an image with OCR."""
    # Mock data
    file_path = "/path/to/image.jpg"
    markdown_content = """# Image: image.jpg

## EXIF Metadata
- Camera: Canon EOS 5D Mark IV
- Date: 2023-12-15 14:30:22
- Resolution: 5472 x 3648

## OCR Text
This is the text extracted from the image using OCR.
The quick brown fox jumps over the lazy dog.
"""

    # Setup mocks
    with (
        patch(
            "omniagents.builtin.tools.convert_to_markdown.os.path.exists",
            return_value=True,
        ),
        patch(
            "omniagents.builtin.tools.convert_to_markdown._converter",
        ) as mock_factory,
    ):
        mock_convert = mock_factory.return_value.convert

        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result

        result = invoke_tool(convert_to_markdown, file_path=file_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert "## EXIF Metadata" in result.ui_metadata["value"]
        assert "## OCR Text" in result.ui_metadata["value"]
        assert "The quick brown fox" in result.ui_metadata["value"]
