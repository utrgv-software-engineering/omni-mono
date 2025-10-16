import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException, Timeout
from tools.web_fetch import web_fetch

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
def test_web_fetch_html_with_extraction(invoke_tool):
    """Test successful HTML fetch with text extraction."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
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
        result = invoke_tool(web_fetch, url="https://example.com", extract_text=True)
        value = result.ui_metadata["value"]
        meta = result.ui_metadata["metadata"]

        assert "Hello World" in value
        assert "This is a test paragraph." in value
        assert "console.log" not in value
        assert "color: red" not in value
        assert meta["title"] == "Test Page"
        assert meta["meta_tags"]["description"] == "A test page"
        assert meta["link_count"] == 1
        assert meta["content_type"] == "text/html; charset=utf-8"

        # Verify request
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://example.com"
        assert "headers" in kwargs
        assert "timeout" in kwargs

def test_web_fetch_html_without_extraction(invoke_tool):
    """Test successful HTML fetch without text extraction."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        html_content = "<html><body>Test content</body></html>"
        
        # Setup mock
        mock_response = DummySuccessHTMLResponse(html_content)
        mock_requests.get.return_value = mock_response
        
        # Call the function
        result = invoke_tool(web_fetch, url="https://example.com", extract_text=False)
        value = result.ui_metadata["value"]
        meta = result.ui_metadata["metadata"]

        assert value == html_content
        assert meta["content_type"] == "text/html; charset=utf-8"
        assert meta["extracted_text"] is False

def test_web_fetch_non_html_content(invoke_tool):
    """Test fetching non-HTML content."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        json_content = '{"key": "value"}'
        
        # Setup mock
        mock_response = DummySuccessNonHTMLResponse(json_content)
        mock_requests.get.return_value = mock_response
        
        # Call the function with extract_text=True, but it should still return raw content
        result = invoke_tool(web_fetch, url="https://api.example.com/data.json", extract_text=True)
        value = result.ui_metadata["value"]
        meta = result.ui_metadata["metadata"]

        assert value == json_content
        assert meta["content_type"] == "application/json; charset=utf-8"
        assert meta["extracted_text"] is False

def test_web_fetch_http_error(invoke_tool):
    """Test HTTP error handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        # Setup mock
        mock_response = DummyErrorResponse(404)
        mock_requests.get.return_value = mock_response
        
        # Call the function
        result = invoke_tool(web_fetch, url="https://example.com/not-found", extract_text=True)
        assert "Request failed with status code 404" in result.ui_metadata["value"]

def test_web_fetch_request_exception(invoke_tool):
    """Test RequestException handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        # Setup mock to raise an exception
        mock_requests.get.side_effect = RequestException("Connection refused")
        
        # Call the function
        result = invoke_tool(web_fetch, url="https://example.com", extract_text=True)
        assert "Network error occurred" in result.ui_metadata["value"]
        assert "Connection refused" in result.ui_metadata["value"]

def test_web_fetch_general_exception(invoke_tool):
    """Test general exception handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        # Setup mock with BeautifulSoup to raise exception
        mock_response = DummySuccessHTMLResponse("<html>Invalid markup</not-html>")
        mock_requests.get.return_value = mock_response
        
        # Mock BeautifulSoup to raise an exception
        with patch('tools.web_fetch.BeautifulSoup', side_effect=Exception("Parse error")):
            # Call the function
            result = invoke_tool(web_fetch, url="https://example.com", extract_text=True)
            assert "Error during web fetch" in result.ui_metadata["value"]
            assert "Parse error" in result.ui_metadata["value"]

def test_web_fetch_timeout(invoke_tool):
    """Test timeout handling."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):
        
        # Setup mock to raise timeout
        mock_requests.get.side_effect = Timeout("Request timed out")
        
        # Call the function
        result = invoke_tool(web_fetch, url="https://example.com", extract_text=True)
        assert "Network error occurred" in result.ui_metadata["value"]
        assert "Request timed out" in result.ui_metadata["value"]

def test_web_fetch_non_html_without_extraction(invoke_tool):
    """Test fetching non-HTML content with extract_text disabled."""
    mock_requests = setup_mock_requests()
    with patch('tools.web_fetch.requests', mock_requests):

        json_content = '{"key": "value"}'
        # Setup mock
        mock_response = DummySuccessNonHTMLResponse(json_content)
        mock_requests.get.return_value = mock_response

        # Call the function with extract_text=False and non-HTML
        result = invoke_tool(web_fetch, url="https://api.example.com/data.json", extract_text=False)
        assert result.ui_metadata["value"] == json_content
        assert result.ui_metadata["metadata"]["extracted_text"] is False
