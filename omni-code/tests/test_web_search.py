import pytest
import os
import json
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException

# Mock classes and responses
class DummySuccessResponse:
    """A minimal dummy response object for a 200 OK request with search results."""
    status_code = 200
    
    def __init__(self, search_data):
        self._search_data = search_data
        
    def raise_for_status(self):
        return None
        
    def json(self):
        return self._search_data

class DummyErrorResponse:
    """A dummy response object for error responses."""
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = f"Error {status_code}"
        
    def raise_for_status(self):
        raise RequestException(f"{self.status_code} Error")

# Sample search results for mocking
SAMPLE_SEARCH_RESULTS = {
    "search_metadata": {
        "id": "12345",
        "status": "Success",
        "total_time_taken": 0.5
    },
    "organic_results": [
        {
            "title": "Test Result 1",
            "link": "https://example.com/1",
            "snippet": "This is the first test result.",
            "position": 1,
            "displayed_link": "example.com/1"
        },
        {
            "title": "Test Result 2",
            "link": "https://example.com/2",
            "snippet": "This is the second test result.",
            "position": 2,
            "displayed_link": "example.com/2"
        }
    ],
    "answer_box": {
        "title": "Answer to your question",
        "snippet": "This is a direct answer to your question."
    },
    "knowledge_graph": {
        "title": "Knowledge Graph",
        "description": "This is a knowledge graph item."
    },
    "related_questions": [
        "Related question 1?",
        "Related question 2?"
    ],
    "news_results": [
        {
            "title": "News Item 1",
            "link": "https://news.example.com/1",
            "snippet": "This is a news item.",
            "position": 1,
            "source": "Example News"
        }
    ],
    "pagination": {
        "current": 1,
        "next": "https://example.com/page/2"
    }
}

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
def test_web_search_basic(mock_function_tool):
    """Test basic web search with minimal parameters."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify results
        assert result["status"] == "success"
        assert result["query"] == "test query"
        assert len(result["organic_results"]) == 2
        assert "answer_box" in result
        assert "knowledge_graph" in result
        assert "related_questions" in result
        assert "news" not in result  # News not included
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://serpapi.com/search"
        assert kwargs["params"]["q"] == "test query"
        assert kwargs["params"]["num"] == 10
        assert kwargs["params"]["safe"] == "active"
        assert kwargs["params"]["gl"] == "us"
        assert kwargs["params"]["hl"] == "en"
        assert "tbs" not in kwargs["params"]  # No time period

def test_web_search_with_news(mock_function_tool):
    """Test web search including news results."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=True,
            time_period=None
        )
        
        # Verify results
        assert result["status"] == "success"
        assert "news" in result
        assert len(result["news"]) == 1

def test_web_search_with_time_period(mock_function_tool):
    """Test web search with time period filtering."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period="past_day"
        )
        
        # Verify results
        assert result["status"] == "success"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "tbs" in kwargs["params"]
        assert kwargs["params"]["tbs"] == "qdr:d"  # 'd' for past_day

def test_web_search_result_limit(mock_function_tool):
    """Test web search with result limit."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function with max allowed
        result = web_search(
            query="test query",
            num_results=150,  # Over the max of 100
            include_news=False,
            time_period=None
        )
        
        # Verify the limit was applied
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert kwargs["params"]["num"] == 100  # Should be capped at 100

def test_web_search_missing_api_key(mock_function_tool):
    """Test web search with missing API key."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {}, clear=True):  # Empty environment
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "SERPAPI_API_KEY not found" in result["error"]
        
        # Verify no request was made
        mock_requests.get.assert_not_called()

def test_web_search_request_error(mock_function_tool):
    """Test web search with HTTP error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummyErrorResponse(403)  # Forbidden
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "403" in result["error"]

def test_web_search_network_error(mock_function_tool):
    """Test web search with network error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_requests.get.side_effect = RequestException("Connection refused")
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Network error" in result["error"]
        assert "Connection refused" in result["error"]

def test_web_search_json_decode_error(mock_function_tool):
    """Test web search with JSON decode error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    # Create a response with invalid JSON
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Failed to parse" in result["error"]

def test_web_search_general_exception(mock_function_tool):
    """Test web search with general exception."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}), \
         patch('tools.web_search.requests.get', side_effect=Exception("Unexpected error")):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Error during web search" in result["error"]
        assert "Unexpected error" in result["error"]

def test_web_search_default_values(mock_function_tool):
    """Test web search with default values for optional parameters."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.web_search import web_search
        
        # Call the function with None for optional parameters
        result = web_search(
            query="test query",
            num_results=None,  # Should default to 10
            include_news=None,  # Should default to False
            time_period=None
        )
        
        # Verify defaults were applied
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert kwargs["params"]["num"] == 10
        assert "news" not in result  # No news with default False 

def _run_web_search_time_period(mock_requests, time_period):
    """Helper to reduce duplication when testing different time periods."""
    with patch('tools.web_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        from tools.web_search import web_search
        result = web_search(
            query="test query",
            num_results=10,
            include_news=False,
            time_period=time_period
        )
        assert result["status"] == "success"
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "tbs" in kwargs["params"]
        return kwargs["params"]["tbs"]


@pytest.mark.parametrize("time_period,expected_code", [
    ("past_week", "qdr:w"),
    ("past_month", "qdr:m"),
    ("past_year", "qdr:y"),
])
def test_web_search_additional_time_periods(mock_function_tool, time_period, expected_code):
    """Test other time period filters not previously covered."""
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SEARCH_RESULTS)
    mock_requests.get.return_value = mock_response

    tbs_value = _run_web_search_time_period(mock_requests, time_period)
    assert tbs_value == expected_code 