import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock, mock_open, call
from requests.exceptions import RequestException

# Mock classes and responses
class DummySuccessResponse:
    """A minimal dummy response object for a 200 OK request with file content."""
    status_code = 200
    
    def __init__(self, content=b"file content", content_type="application/pdf", filename=None):
        self.content = content
        self._content_chunks = [content[i:i+8192] for i in range(0, len(content), 8192)]
        self.headers = {
            'Content-Type': content_type,
            'Content-Length': str(len(content))
        }
        
        # Add Content-Disposition header if filename is provided
        if filename:
            self.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        self.text = "file content"
        
    def raise_for_status(self):
        return None
        
    def iter_content(self, chunk_size=8192):
        return iter(self._content_chunks)

class DummyErrorResponse:
    """A dummy response object for HTTP errors."""
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = f"Error {status_code}"
        
    def raise_for_status(self):
        raise RequestException(f"{self.status_code} Error")

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to just return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield

# Setup mock requests that correctly includes RequestException
def setup_mock_requests():
    """Create a properly configured mock requests module with exception classes."""
    mock_req = MagicMock()
    mock_req.RequestException = RequestException
    mock_req.exceptions = MagicMock()
    mock_req.exceptions.RequestException = RequestException
    return mock_req

# Tests
def test_download_file_temp_path(mock_function_tool):
    """Test downloading a file to a temporary location."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(content=b"sample PDF content", content_type="application/pdf")
    mock_requests.get.return_value = mock_response
    
    # Mock tempfile functions
    mock_temp_dir = "/tmp/download_123456"
    mock_temp_path = "/tmp/download_123456/document.pdf"
    
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkdtemp', return_value=mock_temp_dir), \
         patch('tools.download_file.tempfile.mkstemp', return_value=(123, mock_temp_path)), \
         patch('tools.download_file.os.makedirs'), \
         patch('tools.download_file.os.close'), \
         patch('tools.download_file.os.path.getsize', return_value=18), \
         patch('builtins.open', mock_open()) as mock_file:
        
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/document.pdf")
        
        # Verify results
        assert result["success"] is True
        assert result["file_path"] == mock_temp_path
        assert result["url"] == "https://example.com/document.pdf"
        assert result["content_type"] == "application/pdf"
        assert result["file_size"] == 18
        assert result["is_temporary"] is True
        
        # Verify file was written correctly
        mock_file.assert_called_once_with(mock_temp_path, 'wb')
        handle = mock_file()
        handle.write.assert_called_once_with(b"sample PDF content")
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://example.com/document.pdf"
        assert "headers" in kwargs
        assert "stream" in kwargs
        assert kwargs["stream"] is True

def test_download_file_custom_path(mock_function_tool):
    """Test downloading a file to a custom path."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(content=b"sample PDF content", content_type="application/pdf")
    mock_requests.get.return_value = mock_response
    
    custom_path = "/custom/path/document.pdf"
    
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.os.path.dirname', return_value="/custom/path"), \
         patch('tools.download_file.os.path.basename', return_value="document.pdf"), \
         patch('tools.download_file.os.makedirs'), \
         patch('tools.download_file.os.path.getsize', return_value=18), \
         patch('builtins.open', mock_open()) as mock_file:
        
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/document.pdf", output_path=custom_path)
        
        # Verify results
        assert result["success"] is True
        assert result["file_path"] == custom_path
        assert result["url"] == "https://example.com/document.pdf"
        assert result["content_type"] == "application/pdf"
        assert result["file_size"] == 18
        assert result["is_temporary"] is False
        
        # Verify directory was created
        from tools.download_file import os
        os.makedirs.assert_called_once_with("/custom/path", exist_ok=True)
        
        # Verify file was written correctly
        mock_file.assert_called_once_with(custom_path, 'wb')
        handle = mock_file()
        handle.write.assert_called_once_with(b"sample PDF content")

def test_download_file_content_types(mock_function_tool):
    """Test downloading files with different content types."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    # Tests for different content types
    content_types = [
        ("application/pdf", ".pdf"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        ("application/msword", ".doc"),
        ("text/plain", ".txt"),
        ("text/html", ".html"),
        ("image/jpeg", ".jpg"),
        ("image/png", ".png")
    ]
    
    for content_type, expected_ext in content_types:
        mock_response = DummySuccessResponse(content=b"file content", content_type=content_type)
        mock_requests.get.return_value = mock_response
        
        # Mock tempfile functions
        mock_temp_path = f"/tmp/temp_file{expected_ext}"
        
        with patch('tools.download_file.requests', mock_requests), \
             patch('tools.download_file.tempfile.mkstemp', return_value=(123, mock_temp_path)), \
             patch('tools.download_file.os.close'), \
             patch('tools.download_file.os.path.getsize', return_value=12), \
             patch('builtins.open', mock_open()) as mock_file:
            
            # Import here after mocking
            from tools.download_file import download_file
            
            # Call the function
            result = download_file(url="https://example.com/file")
            
            # Verify results
            assert result["success"] is True
            assert result["file_path"] == mock_temp_path
            assert result["content_type"] == content_type
            
            # Reset mock for next iteration
            mock_requests.reset_mock()

def test_download_file_content_disposition(mock_function_tool):
    """Test downloading a file with Content-Disposition header."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(
        content=b"file content", 
        content_type="application/octet-stream",
        filename="report.pdf"
    )
    mock_requests.get.return_value = mock_response
    
    # Mock path manipulation functions
    mock_temp_dir = "/tmp/download_123456"
    mock_temp_path = "/tmp/download_123456/report.pdf"
    temp_path_mapper = {
        mock_temp_dir: {
            "report.pdf": mock_temp_path
        }
    }
    
    def mock_path_join(dir_path, filename):
        try:
            return temp_path_mapper[dir_path][filename]
        except KeyError:
            return os.path.join(dir_path, filename)
    
    # Mock all relevant functions
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkdtemp', return_value=mock_temp_dir), \
         patch('tools.download_file.os.path.join', side_effect=mock_path_join), \
         patch('tools.download_file.os.path.splitext', return_value=('report', '.pdf')), \
         patch('tools.download_file.os.path.getsize', return_value=12), \
         patch('builtins.open', mock_open()) as mock_file:
        
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/download?id=123")
        
        # Verify results
        assert result["success"] is True
        assert result["file_path"] == mock_temp_path
        
        # Verify file was written
        mock_file.assert_called_once_with(mock_temp_path, 'wb')

def test_download_file_http_error(mock_function_tool):
    """Test download with HTTP error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummyErrorResponse(404)  # Not Found
    mock_requests.get.return_value = mock_response
    
    with patch('tools.download_file.requests', mock_requests):
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/not-found.pdf")
        
        # Verify error response
        assert result["success"] is False
        assert "error" in result
        assert "404" in result["error"]
        assert result["file_path"] is None

def test_download_file_network_error(mock_function_tool):
    """Test download with network error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_requests.get.side_effect = RequestException("Connection refused")
    
    with patch('tools.download_file.requests', mock_requests):
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/document.pdf")
        
        # Verify error response
        assert result["success"] is False
        assert "error" in result
        assert "Network error" in result["error"]
        assert "Connection refused" in result["error"]
        assert result["file_path"] is None

def test_download_file_file_error(mock_function_tool):
    """Test download with file system error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(content=b"file content", content_type="application/pdf")
    mock_requests.get.return_value = mock_response
    
    # Mock tempfile functions
    mock_temp_path = "/tmp/temp_file.pdf"
    file_error = IOError("Permission denied")
    
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkstemp', return_value=(123, mock_temp_path)), \
         patch('tools.download_file.os.close'), \
         patch('builtins.open', side_effect=file_error):
        
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/document.pdf")
        
        # Verify error response
        assert result["success"] is False
        assert "error" in result
        assert "Error during file download" in result["error"]
        assert "Permission denied" in result["error"]

def test_download_file_large_file(mock_function_tool):
    """Test downloading a large file in chunks."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    # Create a large file content with multiple chunks
    large_content = b"chunk1" * 4000 + b"chunk2" * 4000  # Multiple chunks
    mock_response = DummySuccessResponse(content=large_content, content_type="application/pdf")
    mock_requests.get.return_value = mock_response
    
    # Mock tempfile functions and path handling
    mock_fd = 123
    mock_temp_path = "/tmp/large_file.pdf"
    
    # Extensive mocking to intercept all file-related operations
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkstemp', return_value=(mock_fd, mock_temp_path)), \
         patch('tools.download_file.os.close'), \
         patch('tools.download_file.os.path.splitext', return_value=('large_file', '.pdf')), \
         patch('tools.download_file.os.path.getsize', return_value=len(large_content)), \
         patch('builtins.open', mock_open()) as mock_file:
        
        # Import here after mocking
        from tools.download_file import download_file
        
        # Call the function
        result = download_file(url="https://example.com/large.pdf")
        
        # Verify results
        assert result["success"] is True
        assert result["file_path"] == mock_temp_path
        assert result["file_size"] == len(large_content)
        
        # Verify the file was opened for writing
        mock_file.assert_called_once_with(mock_temp_path, 'wb')
        
        # Verify write was called multiple times (once per chunk)
        handle = mock_file()
        assert handle.write.call_count > 1 

def test_download_file_content_disposition_no_extension(mock_function_tool):
    """Test Content-Disposition filename without extension triggers header path."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(
        content=b"file content",
        content_type="application/pdf",
        filename="report"  # No extension
    )
    mock_requests.get.return_value = mock_response

    # Mock path handling so we can assert final path
    mock_temp_dir = "/tmp/download_654321"
    mock_temp_path = "/tmp/download_654321/report"  # No extension expected

    def mock_path_join(dir_path, filename):
        if dir_path == mock_temp_dir and filename == "report":
            return mock_temp_path
        return os.path.join(dir_path, filename)

    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkdtemp', return_value=mock_temp_dir), \
         patch('tools.download_file.os.path.join', side_effect=mock_path_join), \
         patch('tools.download_file.os.path.splitext', return_value=("report", "")), \
         patch('tools.download_file.os.path.getsize', return_value=12), \
         patch('builtins.open', mock_open()) as mock_file:

        from tools.download_file import download_file

        result = download_file(url="https://example.com/download?id=456")

        # Verify success and filename without extension
        assert result["success"] is True
        assert result["file_path"] == mock_temp_path
        assert result["content_type"] == "application/pdf"
        mock_file.assert_called_once_with(mock_temp_path, 'wb')


def test_download_file_infer_extension_from_content_type(mock_function_tool):
    """Test inferring extension when URL filename lacks extension and no header."""
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(content=b"pdf bytes", content_type="application/pdf")
    mock_requests.get.return_value = mock_response

    # Mock mkstemp to see chosen suffix
    mock_temp_path = "/tmp/temp_file.pdf"
    with patch('tools.download_file.requests', mock_requests), \
         patch('tools.download_file.tempfile.mkstemp', return_value=(123, mock_temp_path)) as mock_mkstemp, \
         patch('tools.download_file.os.close'), \
         patch('tools.download_file.os.path.splitext', return_value=("123", "")), \
         patch('tools.download_file.os.path.getsize', return_value=len(b"pdf bytes")), \
         patch('builtins.open', mock_open()) as mock_file:

        from tools.download_file import download_file

        result = download_file(url="https://example.com/download/123")

        # Verify success and that inferred extension is .pdf
        assert result["success"] is True
        assert result["file_path"] == mock_temp_path
        # Ensure mkstemp was called with .pdf suffix
        mock_mkstemp.assert_called_once()
        _, kwargs = mock_mkstemp.call_args
        assert kwargs.get('suffix', '') == '.pdf'
        mock_file.assert_called_once_with(mock_temp_path, 'wb') 