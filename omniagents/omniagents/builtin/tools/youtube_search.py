import os
import json
import requests
from typing import Any, Optional, Literal
from omniagents.core.tools import rich_function_tool, RichToolOutput
from agents.run_context import RunContextWrapper


@rich_function_tool(client_status="Searching YouTube...")
def youtube_search(
    ctx: RunContextWrapper[Any],
    query: str,
    num_results: Optional[int] = None,
    sort_by: Literal["relevance", "upload_date", "view_count", "rating"] = "relevance",
    upload_date: Optional[
        Literal["last_hour", "today", "this_week", "this_month", "this_year"]
    ] = None,
    duration: Optional[Literal["short", "medium", "long"]] = None,
) -> RichToolOutput:
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
        if num_results is None:
            num_results = 10
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")

        if not api_key:
            error_msg = "SERPAPI_API_KEY not found in environment variables"
            ui_metadata = {
                "value": error_msg,
                "display_type": "error",
                "summary": "API key missing",
                "preview": "Please set SERPAPI_API_KEY environment variable to use YouTube search.",
                "truncated": False,
                "metadata": {"error_type": "configuration_error"},
            }
            return RichToolOutput(error_msg, ui_metadata)

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
            "safe": "active",  # Always enable safe search
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
                "this_year": "EgQIBRAB",
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
                "medium": "EgQQARgC",  # 4-20 minutes
                "long": "EgQQARgD",  # Over 20 minutes
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
            error_msg = (
                f"SerpAPI request failed with status code {response.status_code}"
            )
            ui_metadata = {
                "value": error_msg,
                "display_type": "error",
                "summary": f"HTTP {response.status_code} error",
                "preview": response.text[:500] if response.text else error_msg,
                "truncated": len(response.text) > 500 if response.text else False,
                "metadata": {
                    "error_type": "api_error",
                    "status_code": response.status_code,
                    "query": query,
                },
            }
            return RichToolOutput(error_msg, ui_metadata)

        # Parse the response
        search_results = response.json()

        # Extract and structure the results
        result = {"status": "success", "query": query, "videos": []}

        # Add video results
        if "video_results" in search_results:
            for item in search_results["video_results"][:num_results]:
                video_info = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "thumbnail": (
                        item.get("thumbnail", {}).get("static", "")
                        if item.get("thumbnail")
                        else ""
                    ),
                    "channel": {
                        "name": (
                            item.get("channel", {}).get("name", "")
                            if item.get("channel")
                            else ""
                        ),
                        "link": (
                            item.get("channel", {}).get("link", "")
                            if item.get("channel")
                            else ""
                        ),
                    },
                    "published_date": item.get("published_date", ""),
                    "views": item.get("views", ""),
                    "duration": item.get("duration_text", ""),
                    "description": item.get("description", ""),
                    "extensions": item.get("extensions", []),
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
            "total_results": search_results.get("search_information", {}).get(
                "total_results", ""
            ),
            "time_taken_displayed": search_results.get("search_information", {}).get(
                "time_taken_displayed", ""
            ),
        }

        # Add search metadata
        result["search_metadata"] = {
            "id": search_results.get("search_metadata", {}).get("id", ""),
            "status": search_results.get("search_metadata", {}).get("status", ""),
            "total_time_taken": search_results.get("search_metadata", {}).get(
                "total_time_taken", 0
            ),
            "engine": "YouTube",
        }

        # Create LLM-friendly output
        llm_output = json.dumps(result, indent=2)[:5000]  # Limit to 5000 chars for LLM

        # Create preview for UI
        preview_lines = []
        for i, video in enumerate(result["videos"][:5], 1):
            preview_lines.append(f"{i}. {video['title']}")
            preview_lines.append(f"   Channel: {video['channel']['name']}")
            preview_lines.append(
                f"   Duration: {video['duration']} | Views: {video['views']}"
            )
            if video.get("link"):
                preview_lines.append(f"   {video['link']}")
        preview = "\n".join(preview_lines) if preview_lines else "No videos found"

        ui_metadata = {
            "value": result,  # The actual search results
            "display_type": "search_results",
            "summary": f"Found {len(result['videos'])} YouTube videos for '{query}'",
            "preview": preview,
            "truncated": len(result["videos"]) > 5,
            "metadata": {
                "query": query,
                "result_count": len(result["videos"]),
                "sort_by": sort_by,
                "upload_date": upload_date,
                "duration": duration,
                "search_engine": "YouTube",
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except requests.RequestException as e:
        error_msg = f"Network error occurred: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Network error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "network_error",
                "query": query,
                "error": str(e),
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
    except json.JSONDecodeError:
        error_msg = "Failed to parse the search results"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Parse error",
            "preview": "The search results could not be parsed as JSON.",
            "truncated": False,
            "metadata": {"error_type": "parse_error", "query": query},
        }
        return RichToolOutput(error_msg, ui_metadata)
    except Exception as e:
        error_msg = f"Error during YouTube search: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "YouTube search error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "search_error", "query": query, "error": str(e)},
        }
        return RichToolOutput(error_msg, ui_metadata)
