import pytest
import os
import json
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException
from typing import Literal
from tools.youtube_search import youtube_search

# Mock classes and responses
class DummySuccessResponse:
    """A minimal dummy response object for a 200 OK request with YouTube search results."""
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

# Sample YouTube search results for mocking
SAMPLE_YOUTUBE_RESULTS = {
    "search_metadata": {
        "id": "12345",
        "status": "Success",
        "total_time_taken": 0.5
    },
    "search_information": {
        "total_results": "1,200,000",
        "time_taken_displayed": "0.32"
    },
    "video_results": [
        {
            "title": "Test Video 1",
            "link": "https://www.youtube.com/watch?v=abcd1234",
            "thumbnail": {
                "static": "https://i.ytimg.com/vi/abcd1234/hqdefault.jpg"
            },
            "channel": {
                "name": "Test Channel 1",
                "link": "https://www.youtube.com/channel/UC123456"
            },
            "published_date": "2 weeks ago",
            "views": "1.2M views",
            "duration_text": "10:25",
            "description": "This is a test video description.",
            "extensions": ["HD"]
        },
        {
            "title": "Test Video 2",
            "link": "https://www.youtube.com/watch?v=efgh5678",
            "thumbnail": {
                "static": "https://i.ytimg.com/vi/efgh5678/hqdefault.jpg"
            },
            "channel": {
                "name": "Test Channel 2",
                "link": "https://www.youtube.com/channel/UC789012"
            },
            "published_date": "3 days ago",
            "views": "500K views",
            "duration_text": "5:17",
            "description": "Another test video description.",
            "extensions": ["4K"]
        }
    ],
    "related_searches": [
        "related search 1",
        "related search 2"
    ]
}

# Setup mock requests that correctly includes RequestException
def setup_mock_requests():
    """Create a properly configured mock requests module with exception classes."""
    mock_req = MagicMock()
    mock_req.RequestException = RequestException
    mock_req.exceptions = MagicMock()
    mock_req.exceptions.RequestException = RequestException
    return mock_req

# Tests
def test_youtube_search_basic(invoke_tool):
    """Test basic YouTube search with minimal parameters."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        data = result.ui_metadata["value"]

        assert data["status"] == "success"
        assert data["query"] == "test query"
        assert len(data["videos"]) == 2
        assert "related_searches" in data
        assert data["search_metadata"]["engine"] == "YouTube"
        
        # Verify first video
        video = data["videos"][0]
        assert video["title"] == "Test Video 1"
        assert video["link"] == "https://www.youtube.com/watch?v=abcd1234"
        assert video["thumbnail"] == "https://i.ytimg.com/vi/abcd1234/hqdefault.jpg"
        assert video["channel"]["name"] == "Test Channel 1"
        assert video["duration"] == "10:25"
        assert video["video_id"] == "abcd1234"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://serpapi.com/search"
        assert kwargs["params"]["search_query"] == "test query"
        assert kwargs["params"]["num"] == 10
        assert kwargs["params"]["engine"] == "youtube"
        assert kwargs["params"]["gl"] == "us"
        assert kwargs["params"]["hl"] == "en"
        assert kwargs["params"]["safe"] == "active"
        assert "sp" not in kwargs["params"]  # No sort param for relevance (default)

def test_youtube_search_sort_by_upload_date(invoke_tool):
    """Test YouTube search with sort by upload date."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="upload_date",
            upload_date=None,
            duration=None
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        assert kwargs["params"]["sp"] == "CAI%253D"  # Parameter for upload date

def test_youtube_search_sort_by_view_count(invoke_tool):
    """Test YouTube search with sort by view count."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="view_count",
            upload_date=None,
            duration=None
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        assert kwargs["params"]["sp"] == "CAM%253D"  # Parameter for view count

def test_youtube_search_with_upload_date(invoke_tool):
    """Test YouTube search with upload date filter."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date="today",
            duration=None
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        assert "EgQIAhAB" in kwargs["params"]["sp"]  # Parameter for today

def test_youtube_search_with_duration(invoke_tool):
    """Test YouTube search with duration filter."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration="short"
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        assert "EgQQARgB" in kwargs["params"]["sp"]  # Parameter for short duration

def test_youtube_search_with_combined_filters(invoke_tool):
    """Test YouTube search with multiple filters combined."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="view_count",
            upload_date="this_week",
            duration="long"
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        # Check that all three parameters are in the sp parameter
        assert "CAM%253D" in kwargs["params"]["sp"]  # view count
        assert "EgQIAxAB" in kwargs["params"]["sp"]  # this week
        assert "EgQQARgD" in kwargs["params"]["sp"]  # long duration

def test_youtube_search_result_limit(invoke_tool):
    """Test YouTube search with result limit."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=150,  # Over the max of 100
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        
        # Verify the limit was applied
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert kwargs["params"]["num"] == 100  # Should be capped at 100

def test_youtube_search_missing_api_key(invoke_tool):
    """Test YouTube search with missing API key."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {}, clear=True):  # Empty environment
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        assert "SERPAPI_API_KEY not found" in result.ui_metadata["value"]

        # Verify no request was made
        mock_requests.get.assert_not_called()

def test_youtube_search_request_error(invoke_tool):
    """Test YouTube search with HTTP error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummyErrorResponse(403)  # Forbidden
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        assert "SerpAPI request failed with status code 403" in result.ui_metadata["value"]

def test_youtube_search_network_error(invoke_tool):
    """Test YouTube search with network error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_requests.get.side_effect = RequestException("Connection refused")
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        assert "Network error occurred" in result.ui_metadata["value"]
        assert "Connection refused" in result.ui_metadata["value"]

def test_youtube_search_json_decode_error(invoke_tool):
    """Test YouTube search with JSON decode error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    # Create a response with invalid JSON
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        assert "Failed to parse" in result.ui_metadata["value"]

def test_youtube_search_general_exception(invoke_tool):
    """Test YouTube search with general exception."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}), \
         patch('tools.youtube_search.requests.get', side_effect=Exception("Unexpected error")):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="relevance",
            upload_date=None,
            duration=None
        )
        assert "Error during YouTube search" in result.ui_metadata["value"]
        assert "Unexpected error" in result.ui_metadata["value"]

def test_youtube_search_sort_by_rating(invoke_tool):
    """Test YouTube search sorted by rating (previously untested)."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response

    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="rating",
            upload_date=None,
            duration=None
        )

        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "sp" in kwargs["params"]
        assert kwargs["params"]["sp"] == "CAE%253D"  # Parameter for rating

def test_youtube_search_rating_with_filters(invoke_tool):
    """Test rating sort combined with upload_date and duration filters."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_YOUTUBE_RESULTS)
    mock_requests.get.return_value = mock_response

    with patch('tools.youtube_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        result = invoke_tool(
            youtube_search,
            query="test query",
            num_results=10,
            sort_by="rating",
            upload_date="last_hour",
            duration="medium"
        )
        data = result.ui_metadata["value"]
        assert data["status"] == "success"

        # Verify request parameters include all expected filter codes
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        sp_param = kwargs["params"]["sp"]
        assert "CAE%253D" in sp_param  # rating
        assert "EgIIAQ%253D%253D" in sp_param  # last hour upload date
        assert "EgQQARgC" in sp_param  # medium duration 
