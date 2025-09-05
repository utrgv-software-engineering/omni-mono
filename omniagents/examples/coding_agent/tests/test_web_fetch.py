import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException, Timeout

# Mock classes and responses
class DummySuccessHTMLResponse:
    """A minimal dummy response object for a 200 OK HTML request."""
    status_code = 200
    
    def __init__(self, html_content):
        self.text = html_content
        self.content = html_content.encode('utf-8')
        self.headers = {
            'Content-Type': 'text/html; charset=utf-8',
            'Content-Length': str(len(self.content))
        }
        
    def raise_for_status(self):
        return None

class DummySuccessNonHTMLResponse:
    """A minimal dummy response object for a 200 OK non-HTML request."""
    status_code = 200
    
    def __init__(self, content):
        self.text = content
        self.content = content.encode('utf-8')
        self.headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(self.content))
        }
        
    def raise_for_status(self):
        return None

class DummyErrorResponse:
    """A dummy response object for HTTP errors."""
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = f"Error {status_code}"
        self.content = self.text.encode('utf-8')
        self.headers = {}
        
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
    # Add exception classes to the mock
    mock_req.RequestException = RequestException
    mock_req.exceptions = MagicMock()
    mock_req.exceptions.RequestException = RequestException
    mock_req.exceptions.Timeout = Timeout
    return mock_req

# Now we can test the actual function
def test_web_fetch_html_with_extraction(mock_function_tool):
    """Test successful HTML fetch with text extraction."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        # Import here after the decorator is mocked
        from tools.web_fetch import web_fetch
        
        # Sample HTML content
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Test Page</title>
            <meta name="description" content="A test page">
            <script>console.log('hello');</script>
            <style>body { color: red; }</style>
        </head>
        <body>
            <h1>Hello World</h1>
            <p>This is a test paragraph.</p>
            <a href="https://example.com/page">Link Text</a>
            <a href="#anchor">Anchor</a>
            <a href="javascript:void(0)">JS Link</a>
        </body>
        </html>
        """
        
        # Setup mock
        mock_response = DummySuccessHTMLResponse(html_content)
        mock_requests.get.return_value = mock_response
        
        # Call the function
        result = web_fetch("https://example.com", extract_text=True)
        
        # Verify results
        assert result["status"] == "success"
        assert result["url"] == "https://example.com"
        assert result["content_type"] == "text/html; charset=utf-8"
        assert result["title"] == "Test Page"
        assert "Hello World" in result["text_content"]
        assert "This is a test paragraph." in result["text_content"]
        assert "console.log" not in result["text_content"]  # Scripts should be removed
        assert "color: red" not in result["text_content"]  # Styles should be removed
        
        # Check metadata
        assert "description" in result["meta_tags"]
        assert result["meta_tags"]["description"] == "A test page"
        
        # Check links
        assert len(result["links"]) == 1  # Only one valid external link
        assert result["links"][0]["href"] == "https://example.com/page"
        assert result["links"][0]["text"] == "Link Text"
        
        # Verify request
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://example.com"
        assert "headers" in kwargs
        assert "timeout" in kwargs

def test_web_fetch_html_without_extraction(mock_function_tool):
    """Test successful HTML fetch without text extraction."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        html_content = "<html><body>Test content</body></html>"
        
        # Setup mock
        mock_response = DummySuccessHTMLResponse(html_content)
        mock_requests.get.return_value = mock_response
        
        # Call the function
        result = web_fetch("https://example.com", extract_text=False)
        
        # Verify results
        assert result["status"] == "success"
        assert result["url"] == "https://example.com"
        assert result["content_type"] == "text/html; charset=utf-8"
        assert "text_content" not in result
        assert "raw_content" in result
        assert result["raw_content"] == html_content

def test_web_fetch_non_html_content(mock_function_tool):
    """Test fetching non-HTML content."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        json_content = '{"key": "value"}'
        
        # Setup mock
        mock_response = DummySuccessNonHTMLResponse(json_content)
        mock_requests.get.return_value = mock_response
        
        # Call the function with extract_text=True, but it should still return raw content
        result = web_fetch("https://api.example.com/data.json", extract_text=True)
        
        # Verify results
        assert result["status"] == "success"
        assert result["content_type"] == "application/json; charset=utf-8"
        assert "text_content" not in result
        assert "raw_content" in result
        assert result["raw_content"] == json_content

def test_web_fetch_http_error(mock_function_tool):
    """Test HTTP error handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        # Setup mock
        mock_response = DummyErrorResponse(404)
        mock_requests.get.return_value = mock_response
        
        # Call the function
        result = web_fetch("https://example.com/not-found", extract_text=True)
        
        # Verify results
        assert result["status"] == "error"
        assert "error" in result
        assert "404" in result["error"]

def test_web_fetch_request_exception(mock_function_tool):
    """Test RequestException handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        # Setup mock to raise an exception
        mock_requests.get.side_effect = RequestException("Connection refused")
        
        # Call the function
        result = web_fetch("https://example.com", extract_text=True)
        
        # Verify results
        assert result["status"] == "error"
        assert "error" in result
        assert "Network error" in result["error"]
        assert "Connection refused" in result["error"]

def test_web_fetch_general_exception(mock_function_tool):
    """Test general exception handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        # Setup mock with BeautifulSoup to raise exception
        mock_response = DummySuccessHTMLResponse("<html>Invalid markup</not-html>")
        mock_requests.get.return_value = mock_response
        
        # Mock BeautifulSoup to raise an exception
        with patch('tools.web_fetch.BeautifulSoup', side_effect=Exception("Parse error")):
            # Call the function
            result = web_fetch("https://example.com", extract_text=True)
            
            # Verify results
            assert result["status"] == "error"
            assert "error" in result
            assert "Error during web fetch" in result["error"]
            assert "Parse error" in result["error"]

def test_web_fetch_timeout(mock_function_tool):
    """Test timeout handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch
        
        # Setup mock to raise timeout
        mock_requests.get.side_effect = Timeout("Request timed out")
        
        # Call the function
        result = web_fetch("https://example.com", extract_text=True)
        
        # Verify results
        assert result["status"] == "error"
        assert "error" in result
        assert "Network error" in result["error"]
        assert "Request timed out" in result["error"]

def test_web_fetch_non_html_without_extraction(mock_function_tool):
    """Test fetching non-HTML content with extract_text disabled."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        from tools.web_fetch import web_fetch

        json_content = '{"key": "value"}'
        # Setup mock
        mock_response = DummySuccessNonHTMLResponse(json_content)
        mock_requests.get.return_value = mock_response

        # Call the function with extract_text=False and non-HTML
        result = web_fetch("https://api.example.com/data.json", extract_text=False)

        # Verify results
        assert result["status"] == "success"
        assert "raw_content" in result
        assert result["raw_content"] == json_content
        assert "text_content" not in result 