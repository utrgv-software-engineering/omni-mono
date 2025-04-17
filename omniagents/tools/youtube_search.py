import os
import json
import requests
from typing import Dict, Any, Optional, List, Literal
from agents import function_tool

@function_tool
def youtube_search(query: str,
                 num_results: int,
                 sort_by: Literal["relevance", "upload_date", "view_count", "rating"],
                 upload_date: Optional[Literal["last_hour", "today", "this_week", "this_month", "this_year"]],
                 duration: Optional[Literal["short", "medium", "long"]]) -> Dict[str, Any]:
    """
    Searches YouTube for videos based on query and filters.
    
    Args:
        query: Search query string.
        num_results: Number of results to return (max 100).
        sort_by: Sort order: "relevance", "upload_date", "view_count", "rating".
        upload_date: Filter by: "last_hour", "today", "this_week", "this_month", "this_year".
        duration: Filter by: "short" (<4min), "medium" (4-20min), "long" (>20min).
    
    Returns:
        Dictionary with YouTube search results and metadata.
    """
    try:
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")
        
        if not api_key:
            return {
                "status": "error",
                "error": "SERPAPI_API_KEY not found in environment variables. Please set this variable to use YouTube search."
            }
        
        # Base URL for SerpAPI YouTube endpoint
        base_url = "https://serpapi.com/search"
        
        # Prepare parameters
        params = {
            "search_query": query,  # YouTube engine uses search_query instead of q
            "api_key": api_key,
            "engine": "youtube",
            "num": min(num_results, 100),  # Limit to 100 max
            "gl": "us",  # Always use "us" region
            "hl": "en",  # Default to English
            "safe": "active"  # Always enable safe search
        }
        
        # Add sorting preference
        if sort_by == "relevance":
            # Default, no parameter needed
            pass
        elif sort_by == "upload_date":
            params["sp"] = "CAI%253D"  # URL-encoded parameter for upload date
        elif sort_by == "view_count":
            params["sp"] = "CAM%253D"  # URL-encoded parameter for view count
        elif sort_by == "rating":
            params["sp"] = "CAE%253D"  # URL-encoded parameter for rating
        
        # Add upload date filter
        if upload_date:
            date_filters = {
                "last_hour": "EgIIAQ%253D%253D",
                "today": "EgQIAhAB",
                "this_week": "EgQIAxAB",
                "this_month": "EgQIBBAB",
                "this_year": "EgQIBRAB"
            }
            if upload_date in date_filters:
                date_param = date_filters[upload_date]
                # Append to existing sp parameter or create new one
                if "sp" in params:
                    params["sp"] += f",{date_param}"
                else:
                    params["sp"] = date_param
        
        # Add duration filter
        if duration:
            duration_filters = {
                "short": "EgQQARgB",  # Under 4 minutes
                "medium": "EgQQARgC", # 4-20 minutes
                "long": "EgQQARgD"    # Over 20 minutes
            }
            if duration in duration_filters:
                duration_param = duration_filters[duration]
                # Append to existing sp parameter or create new one
                if "sp" in params:
                    params["sp"] += f",{duration_param}"
                else:
                    params["sp"] = duration_param
        
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
            "videos": []
        }
        
        # Add video results
        if "video_results" in search_results:
            for item in search_results["video_results"][:num_results]:
                video_info = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "thumbnail": item.get("thumbnail", {}).get("static", "") if item.get("thumbnail") else "",
                    "channel": {
                        "name": item.get("channel", {}).get("name", "") if item.get("channel") else "",
                        "link": item.get("channel", {}).get("link", "") if item.get("channel") else ""
                    },
                    "published_date": item.get("published_date", ""),
                    "views": item.get("views", ""),
                    "duration": item.get("duration_text", ""),
                    "description": item.get("description", ""),
                    "extensions": item.get("extensions", [])
                }
                
                # Extract video ID from the link
                if "link" in item and "v=" in item["link"]:
                    video_id = item["link"].split("v=")[1].split("&")[0]
                    video_info["video_id"] = video_id
                
                result["videos"].append(video_info)
        
        # Add related searches if available
        if "related_searches" in search_results:
            result["related_searches"] = search_results["related_searches"]
        
        # Add search information
        result["search_information"] = {
            "total_results": search_results.get("search_information", {}).get("total_results", ""),
            "time_taken_displayed": search_results.get("search_information", {}).get("time_taken_displayed", "")
        }
        
        # Add search metadata
        result["search_metadata"] = {
            "id": search_results.get("search_metadata", {}).get("id", ""),
            "status": search_results.get("search_metadata", {}).get("status", ""),
            "total_time_taken": search_results.get("search_metadata", {}).get("total_time_taken", 0),
            "engine": "YouTube"
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
            "error": f"Error during YouTube search: {str(e)}"
        }