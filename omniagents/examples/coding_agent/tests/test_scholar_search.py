import pytest
import os
import json
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException
from typing import Literal

# Mock classes and responses
class DummySuccessResponse:
    """A minimal dummy response object for a 200 OK request with Google Scholar results."""
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

# Sample Google Scholar search results for mocking
SAMPLE_SCHOLAR_RESULTS = {
    "search_metadata": {
        "id": "12345",
        "status": "Success",
        "total_time_taken": 0.5
    },
    "organic_results": [
        {
            "title": "Machine learning approaches in COVID-19 diagnosis, mortality, and severity risk prediction",
            "link": "https://www.sciencedirect.com/science/article/pii/S2589004222001481",
            "snippet": "The COVID-19 pandemic has overwhelmed healthcare systems globally. Accurate and rapid diagnosis is essential to limit the spread...",
            "publication_info": {
                "summary": "H Alimadadi, I Aryal, I Manandhar - Cell Reports Medicine, 2022 - Elsevier",
                "authors": [
                    {"name": "H Alimadadi", "link": "https://scholar.google.com/citations?user=abc123"},
                    {"name": "I Aryal", "link": "https://scholar.google.com/citations?user=def456"},
                    {"name": "I Manandhar", "link": "https://scholar.google.com/citations?user=ghi789"}
                ]
            },
            "inline_links": {
                "cited_by": {
                    "total": 127,
                    "link": "https://scholar.google.com/scholar?cites=123456789"
                },
                "versions": {
                    "total": 5,
                    "link": "https://scholar.google.com/scholar?cluster=987654321"
                }
            },
            "resources": [
                {
                    "title": "PDF",
                    "link": "https://www.sciencedirect.com/science/article/pii/S2589004222001481/pdf"
                }
            ]
        },
        {
            "title": "Deep learning in medical image analysis",
            "link": "https://www.nature.com/articles/s41551-018-0315-x",
            "snippet": "Medical image analysis using deep learning techniques has shown tremendous progress in recent years...",
            "publication_info": {
                "summary": "G Litjens, T Kooi, B Ehteshami - Nature Medicine, 2019 - nature.com"
            },
            "inline_links": {
                "cited_by": {
                    "total": 3420,
                    "link": "https://scholar.google.com/scholar?cites=234567890"
                }
            }
        }
    ],
    "citations": [
        {
            "title": "APA citation",
            "snippet": "Alimadadi, H., Aryal, I., & Manandhar, I. (2022). Machine learning approaches in COVID-19 diagnosis, mortality, and severity risk prediction. Cell Reports Medicine, 3(1), 100522."
        }
    ],
    "profiles": [
        {
            "name": "Hamid Alimadadi",
            "link": "https://scholar.google.com/citations?user=abc123",
            "thumbnail": "https://scholar.google.com/citations?view_op=medium_photo&user=abc123",
            "affiliations": "Stanford University",
            "email": "@stanford.edu",
            "cited_by": 3450
        }
    ],
    "related_searches": [
        {"query": "machine learning medical diagnosis", "link": "https://scholar.google.com/scholar?q=machine+learning+medical+diagnosis"},
        {"query": "COVID-19 prediction models", "link": "https://scholar.google.com/scholar?q=COVID-19+prediction+models"}
    ],
    "pagination": {
        "current": 1,
        "next": "https://scholar.google.com/scholar?start=10&q=machine+learning+covid"
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
def test_scholar_search_basic(mock_function_tool):
    """Test basic Google Scholar search with minimal parameters."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify results
        assert result["status"] == "success"
        assert result["query"] == "machine learning covid"
        assert len(result["organic_results"]) == 2
        assert len(result["citation_results"]) == 1
        assert len(result["profiles"]) == 1
        assert "related_searches" in result
        assert result["search_metadata"]["engine"] == "Google Scholar"
        
        # Verify first paper
        paper = result["organic_results"][0]
        assert paper["title"] == "Machine learning approaches in COVID-19 diagnosis, mortality, and severity risk prediction"
        assert paper["link"] == "https://www.sciencedirect.com/science/article/pii/S2589004222001481"
        assert "snippet" in paper
        assert "publication_info" in paper
        assert "cited_by" in paper
        assert paper["cited_by"]["total"] == 127
        assert "pdf_link" in paper
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert args[0] == "https://serpapi.com/search"
        assert kwargs["params"]["q"] == "machine learning covid"
        assert kwargs["params"]["num"] == 10
        assert kwargs["params"]["engine"] == "google_scholar"
        assert kwargs["params"]["hl"] == "en"
        assert "as_user" not in kwargs["params"]  # No author
        assert "as_ylo" not in kwargs["params"]  # No publication date
        assert "as_sdt" not in kwargs["params"]  # Default sort by relevance

def test_scholar_search_sort_by_date(mock_function_tool):
    """Test Google Scholar search sorted by date."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="date",
            publication_date=None,
            author=None
        )
        
        # Verify results
        assert result["status"] == "success"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "as_sdt" in kwargs["params"]
        assert kwargs["params"]["as_sdt"] == "0,5"  # Parameter for sort by date

def test_scholar_search_with_publication_date(mock_function_tool):
    """Test Google Scholar search with publication date filter."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date="since_2020",
            author=None
        )
        
        # Verify results
        assert result["status"] == "success"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "as_ylo" in kwargs["params"]
        assert kwargs["params"]["as_ylo"] == "2020"  # Since 2020

def test_scholar_search_with_author(mock_function_tool):
    """Test Google Scholar search with author filter."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author="John Smith"
        )
        
        # Verify results
        assert result["status"] == "success"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert "as_user" in kwargs["params"]
        assert kwargs["params"]["as_user"] == "John Smith"

def test_scholar_search_with_combined_filters(mock_function_tool):
    """Test Google Scholar search with multiple filters combined."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="date",
            publication_date="since_2023",
            author="John Smith"
        )
        
        # Verify results
        assert result["status"] == "success"
        
        # Verify request parameters
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        
        # Check all filters are applied
        assert "as_sdt" in kwargs["params"]  # Sort by date
        assert kwargs["params"]["as_sdt"] == "0,5"
        
        assert "as_ylo" in kwargs["params"]  # Since 2023
        assert kwargs["params"]["as_ylo"] == "2023"
        
        assert "as_user" in kwargs["params"]  # Author filter
        assert kwargs["params"]["as_user"] == "John Smith"

def test_scholar_search_result_limit(mock_function_tool):
    """Test Google Scholar search with result limit."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function with max allowed
        result = scholar_search(
            query="machine learning covid",
            num_results=30,  # Over the max of 20
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify the limit was applied
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert kwargs["params"]["num"] == 20  # Should be capped at 20

def test_scholar_search_missing_api_key(mock_function_tool):
    """Test Google Scholar search with missing API key."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {}, clear=True):  # Empty environment
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "SERPAPI_API_KEY not found" in result["error"]
        
        # Verify no request was made
        mock_requests.get.assert_not_called()

def test_scholar_search_request_error(mock_function_tool):
    """Test Google Scholar search with HTTP error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_response = DummyErrorResponse(403)  # Forbidden
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "403" in result["error"]

def test_scholar_search_network_error(mock_function_tool):
    """Test Google Scholar search with network error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    mock_requests.get.side_effect = RequestException("Connection refused")
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Network error" in result["error"]
        assert "Connection refused" in result["error"]

def test_scholar_search_json_decode_error(mock_function_tool):
    """Test Google Scholar search with JSON decode error."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    # Create a response with invalid JSON
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_requests.get.return_value = mock_response
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Failed to parse" in result["error"]

def test_scholar_search_general_exception(mock_function_tool):
    """Test Google Scholar search with general exception."""
    # Setup mocks
    mock_requests = setup_mock_requests()
    
    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}), \
         patch('tools.scholar_search.requests.get', side_effect=Exception("Unexpected error")):
        # Import here after mocking
        from tools.scholar_search import scholar_search
        
        # Call the function
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=None,
            author=None
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "Error during Google Scholar search" in result["error"]
        assert "Unexpected error" in result["error"]

@pytest.mark.parametrize("publication_date,expected_year", [
    ("since_2017", "2017"),
    ("since_2014", "2014"),
])
def test_scholar_search_additional_publication_dates(mock_function_tool, publication_date, expected_year):
    """Test scholar search with additional publication date filters."""
    mock_requests = setup_mock_requests()
    mock_response = DummySuccessResponse(SAMPLE_SCHOLAR_RESULTS)
    mock_requests.get.return_value = mock_response

    with patch('tools.scholar_search.requests', mock_requests), \
         patch.dict(os.environ, {"SERPAPI_API_KEY": "test_api_key"}):
        from tools.scholar_search import scholar_search
        result = scholar_search(
            query="machine learning covid",
            num_results=10,
            sort_by="relevance",
            publication_date=publication_date,
            author=None
        )
        assert result["status"] == "success"
        mock_requests.get.assert_called_once()
        args, kwargs = mock_requests.get.call_args
        assert kwargs["params"]["as_ylo"] == expected_year 