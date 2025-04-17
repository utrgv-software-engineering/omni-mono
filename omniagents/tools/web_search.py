import os
import json
import requests
from typing import Dict, Any, Optional
from agents import function_tool

@function_tool
def web_search(query: str, 
               num_results: int, 
               include_news: bool,
               time_period: Optional[str]) -> Dict[str, Any]:
    """
    Performs a web search using Google via SerpAPI.
    
    Args:
        query: Search query string.
        num_results: Number of results to return (max 100). Recommend using 10 for most queries.
        include_news: Whether to include news results (true or false).
        time_period: Time filter for results (null, "past_day", "past_week", "past_month", or "past_year").
    
    Returns:
        Dictionary with search results and metadata.
    
    Usage:
        This function requires all parameters to be explicitly provided.
        
    Example:
        web_search(
            query="climate change solutions",
            num_results=10,
            include_news=false,
            time_period=null
        )
    """
    # Default to 10 if not explicitly set
    if num_results is None:
        num_results = 10
        
    # Default values for optional parameters
    if include_news is None:
        include_news = False
        
    if time_period is None:
        time_period = None
    try:
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")
        
        if not api_key:
            return {
                "status": "error",
                "error": "SERPAPI_API_KEY not found in environment variables. Please set this variable to use web search."
            }
        
        # Base URL for SerpAPI
        base_url = "https://serpapi.com/search"
        
        # Prepare parameters with fixed values:
        # - region="us"
        # - language="en"
        # - safe_search="active"
        params = {
            "q": query,
            "api_key": api_key,
            "num": min(num_results, 100),  # Limit to 100 max
            "safe": "active",  # Always use safe search
            "gl": "us",        # Always use "us" region
            "hl": "en"         # Always use "en" language
        }
        
        # Add time period if specified
        if time_period:
            time_map = {
                "past_day": "d",
                "past_week": "w",
                "past_month": "m",
                "past_year": "y"
            }
            if time_period in time_map:
                params["tbs"] = f"qdr:{time_map[time_period]}"
        
        # Make the request to SerpAPI
        response = requests.get(base_url, params=params)
        
        if response.status_code != 200:
            return {
                "status": "error",
                "error": f"SerpAPI request failed with status code {response.status_code}: {response.text}"
            }
        
        # Parse the response
        search_results = response.json()
        
        # Extract and structure the results
        result = {
            "status": "success",
            "query": query,
            "organic_results": []
        }
        
        # Add organic search results
        if "organic_results" in search_results:
            for item in search_results["organic_results"][:num_results]:
                result["organic_results"].append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "position": item.get("position", 0),
                    "displayed_link": item.get("displayed_link", "")
                })
        
        # Add answer box if available
        if "answer_box" in search_results:
            result["answer_box"] = search_results["answer_box"]
        
        # Add knowledge graph if available
        if "knowledge_graph" in search_results:
            result["knowledge_graph"] = search_results["knowledge_graph"]
        
        # Add related questions if available
        if "related_questions" in search_results:
            result["related_questions"] = search_results["related_questions"]
        
        # Add news if requested and available
        if include_news and "news_results" in search_results:
            result["news"] = search_results["news_results"][:num_results]
        
        # Add pagination information
        if "pagination" in search_results:
            result["pagination"] = search_results["pagination"]
        
        # Add search metadata
        result["search_metadata"] = {
            "id": search_results.get("search_metadata", {}).get("id", ""),
            "status": search_results.get("search_metadata", {}).get("status", ""),
            "total_time_taken": search_results.get("search_metadata", {}).get("total_time_taken", 0),
            "engine": "Google"
        }
        
        return result
        
    except requests.RequestException as e:
        return {
            "status": "error",
            "error": f"Network error occurred: {str(e)}"
        }
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "Failed to parse the search results."
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Error during web search: {str(e)}"
        }