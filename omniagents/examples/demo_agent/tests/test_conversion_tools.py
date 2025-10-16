import pytest
from unittest.mock import patch, MagicMock

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to just return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield

# Mock MarkItDown result class
class MockMarkdownResult:
    """Mock object returned by MarkItDown.convert."""
    def __init__(self, text_content):
        self.text_content = text_content

# Tests
def test_convert_to_markdown_local_file_success(mock_function_tool):
    """Test successful conversion of a local file."""
    # Mock data
    file_path = "/path/to/document.pdf"
    markdown_content = "# Document Title\n\nThis is the content of the document."
    
    # Setup mocks
    with patch('tools.conversion_tools.os.path.exists', return_value=True), \
         patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(file_path)
        
        # Verify results
        assert result["success"] is True
        assert result["content"] == markdown_content
        assert result["error"] is None
        
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(file_path)

def test_convert_to_markdown_url_success(mock_function_tool):
    """Test successful conversion of a URL."""
    # Mock data
    url = "https://example.com/document.pdf"
    markdown_content = "# Web Document\n\nThis is the content from a URL."
    
    # Setup mocks
    with patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(url)
        
        # Verify results
        assert result["success"] is True
        assert result["content"] == markdown_content
        assert result["error"] is None
        
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(url)

def test_convert_to_markdown_youtube_success(mock_function_tool):
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
    with patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(youtube_url)
        
        # Verify results
        assert result["success"] is True
        assert result["content"] == markdown_content
        assert result["error"] is None
        
        # Verify convert was called correctly
        mock_convert.assert_called_once_with(youtube_url)

def test_convert_to_markdown_file_not_found(mock_function_tool):
    """Test conversion when file is not found."""
    # Mock data
    file_path = "/path/to/nonexistent.pdf"
    
    # Setup mocks
    with patch('tools.conversion_tools.os.path.exists', return_value=False):
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(file_path)
        
        # Verify results
        assert result["success"] is False
        assert result["content"] is None
        assert "File not found" in result["error"]
        assert file_path in result["error"]

def test_convert_to_markdown_conversion_error(mock_function_tool):
    """Test handling of conversion errors."""
    # Mock data
    file_path = "/path/to/document.pdf"
    
    # Setup mocks
    with patch('tools.conversion_tools.os.path.exists', return_value=True), \
         patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to raise an exception
        mock_convert.side_effect = Exception("Unsupported file format")
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(file_path)
        
        # Verify results
        assert result["success"] is False
        assert result["content"] is None
        assert "Error converting file" in result["error"]
        assert "Unsupported file format" in result["error"]

def test_convert_to_markdown_word_document(mock_function_tool):
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
    with patch('tools.conversion_tools.os.path.exists', return_value=True), \
         patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(file_path)
        
        # Verify results
        assert result["success"] is True
        assert result["content"] == markdown_content
        assert "## Section 1" in result["content"]
        assert "* Bullet point" in result["content"]

def test_convert_to_markdown_image_with_ocr(mock_function_tool):
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
    with patch('tools.conversion_tools.os.path.exists', return_value=True), \
         patch('tools.conversion_tools.md_converter.convert') as mock_convert:
        
        # Configure mock to return successful result
        mock_result = MockMarkdownResult(markdown_content)
        mock_convert.return_value = mock_result
        
        # Import here after mocking
        from tools.conversion_tools import convert_to_markdown
        
        # Call the function
        result = convert_to_markdown(file_path)
        
        # Verify results
        assert result["success"] is True
        assert "## EXIF Metadata" in result["content"]
        assert "## OCR Text" in result["content"]
        assert "The quick brown fox" in result["content"] 