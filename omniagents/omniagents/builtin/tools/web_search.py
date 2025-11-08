import os
import json
import requests
from typing import Any, Optional
from omniagents.core.tools import rich_function_tool, RichToolOutput
from agents.run_context import RunContextWrapper


@rich_function_tool(client_status="Searching the web...")
def web_search(
    ctx: RunContextWrapper[Any],
    query: str,
    num_results: Optional[int] = None,
    include_news: Optional[bool] = None,
    time_period: Optional[str] = None,
) -> RichToolOutput:
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
    if num_results is None:
        num_results = 10
    if include_news is None:
        include_news = False
    try:
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")

        if not api_key:
            error_msg = "SERPAPI_API_KEY not found in environment variables"
            ui_metadata = {
                "value": error_msg,
                "display_type": "error",
                "summary": "API key missing",
                "preview": "Please set SERPAPI_API_KEY environment variable to use web search.",
                "truncated": False,
                "metadata": {"error_type": "configuration_error"},
            }
            return RichToolOutput(error_msg, ui_metadata)

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
            "gl": "us",  # Always use "us" region
            "hl": "en",  # Always use "en" language
        }

        # Add time period if specified
        if time_period:
            time_map = {
                "past_day": "d",
                "past_week": "w",
                "past_month": "m",
                "past_year": "y",
            }
            if time_period in time_map:
                params["tbs"] = f"qdr:{time_map[time_period]}"

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
        result = {"status": "success", "query": query, "organic_results": []}

        # Add organic search results
        if "organic_results" in search_results:
            for item in search_results["organic_results"][:num_results]:
                result["organic_results"].append(
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "position": item.get("position", 0),
                        "displayed_link": item.get("displayed_link", ""),
                    }
                )

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
            "total_time_taken": search_results.get("search_metadata", {}).get(
                "total_time_taken", 0
            ),
            "engine": "Google",
        }

        # Create LLM-friendly output
        llm_output = json.dumps(result, indent=2)[:5000]  # Limit to 5000 chars for LLM

        # Create preview for UI
        preview_lines = []
        for i, res in enumerate(result["organic_results"][:5], 1):
            preview_lines.append(f"{i}. {res['title']}")
            preview_lines.append(f"   {res['link']}")
            if res["snippet"]:
                preview_lines.append(f"   {res['snippet'][:100]}...")
        preview = "\n".join(preview_lines) if preview_lines else "No results found"

        ui_metadata = {
            "value": result,  # The actual search results
            "display_type": "search_results",
            "summary": f"Found {len(result['organic_results'])} results for '{query}'",
            "preview": preview,
            "truncated": len(result["organic_results"]) > 5,
            "metadata": {
                "query": query,
                "result_count": len(result["organic_results"]),
                "has_answer_box": "answer_box" in result,
                "has_knowledge_graph": "knowledge_graph" in result,
                "has_news": "news" in result,
                "time_period": time_period,
                "search_engine": "Google",
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
        error_msg = f"Error during web search: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Search error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "search_error", "query": query, "error": str(e)},
        }
        return RichToolOutput(error_msg, ui_metadata)
