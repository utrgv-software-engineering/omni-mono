import os
import json
import requests
from typing import Any, Optional, Literal
from omniagents.core.tools import rich_function_tool, RichToolOutput
from agents.run_context import RunContextWrapper


@rich_function_tool(client_status="Searching Google Scholar...")
def scholar_search(
    ctx: RunContextWrapper[Any],
    query: str,
    num_results: Optional[int] = None,
    sort_by: Literal["relevance", "date"] = "relevance",
    publication_date: Optional[
        Literal["since_2023", "since_2020", "since_2017", "since_2014"]
    ] = None,
    author: Optional[str] = None,
) -> RichToolOutput:
    """
    Searches Google Scholar for academic papers and citations.

    Args:
        query: Search query string.
        num_results: Number of results (max 20 per page).
        sort_by: "relevance" or "date".
        publication_date: Filter by: "since_2023", "since_2020", "since_2017", "since_2014".
        author: Filter by specific author.

    Returns:
        Dictionary with Scholar search results and metadata.
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
                "preview": "Please set SERPAPI_API_KEY environment variable to use Google Scholar search.",
                "truncated": False,
                "metadata": {"error_type": "configuration_error"},
            }
            return RichToolOutput(error_msg, ui_metadata)

        # Base URL for SerpAPI Google Scholar endpoint
        base_url = "https://serpapi.com/search"

        # Prepare parameters
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google_scholar",
            "num": min(
                num_results, 20
            ),  # Google Scholar typically shows 10-20 results per page
            "hl": "en",  # Language set to English
        }

        # Add author filter if provided
        if author:
            params["as_user"] = author

        # Add sorting preference
        if sort_by == "date":
            params["as_sdt"] = "0,5"  # Sort by date

        # Add publication date filter
        if publication_date:
            date_filters = {
                "since_2023": "2023",
                "since_2020": "2020",
                "since_2017": "2017",
                "since_2014": "2014",
            }
            if publication_date in date_filters:
                params["as_ylo"] = date_filters[publication_date]

        # Always include citations, patents, and profile results

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
        result = {
            "status": "success",
            "query": query,
            "organic_results": [],
            "citation_results": [],
            "profiles": [],
            "related_searches": [],
        }

        # Add organic search results (articles and papers)
        if "organic_results" in search_results:
            for item in search_results["organic_results"]:
                paper_info = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "publication_info": item.get("publication_info", {}),
                }

                # Extract authors
                if "authors" in item:
                    paper_info["authors"] = item["authors"]

                # Extract citations
                if "inline_links" in item and "cited_by" in item["inline_links"]:
                    paper_info["cited_by"] = {
                        "total": item["inline_links"]["cited_by"].get("total", 0),
                        "link": item["inline_links"]["cited_by"].get("link", ""),
                    }

                # Extract related versions if available
                if "versions" in item.get("inline_links", {}):
                    paper_info["versions"] = {
                        "total": item["inline_links"]["versions"].get("total", 0),
                        "link": item["inline_links"]["versions"].get("link", ""),
                    }

                # Extract PDF link if available
                if "resources" in item:
                    for resource in item["resources"]:
                        if resource.get("title") == "PDF":
                            paper_info["pdf_link"] = resource.get("link", "")
                            break

                result["organic_results"].append(paper_info)

        # Add citation results if present
        if "citations" in search_results:
            result["citation_results"] = search_results["citations"]

        # Add profiles if present
        if "profiles" in search_results:
            result["profiles"] = search_results["profiles"]

        # Add related searches if available
        if "related_searches" in search_results:
            result["related_searches"] = search_results["related_searches"]

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
            "engine": "Google Scholar",
        }

        # Create LLM-friendly output
        llm_output = json.dumps(result, indent=2)[:5000]  # Limit to 5000 chars for LLM

        # Create preview for UI
        preview_lines = []
        for i, paper in enumerate(result["organic_results"][:5], 1):
            preview_lines.append(f"{i}. {paper['title']}")
            if paper.get("authors"):
                authors_str = ", ".join(
                    [a.get("name", "") for a in paper["authors"][:3]]
                )
                preview_lines.append(f"   Authors: {authors_str}")
            if paper.get("cited_by", {}).get("total"):
                preview_lines.append(f"   Citations: {paper['cited_by']['total']}")
            if paper.get("link"):
                preview_lines.append(f"   {paper['link']}")
        preview = "\n".join(preview_lines) if preview_lines else "No papers found"

        ui_metadata = {
            "value": result,  # The actual search results
            "display_type": "search_results",
            "summary": f"Found {len(result['organic_results'])} academic papers for '{query}'",
            "preview": preview,
            "truncated": len(result["organic_results"]) > 5,
            "metadata": {
                "query": query,
                "result_count": len(result["organic_results"]),
                "sort_by": sort_by,
                "publication_date": publication_date,
                "author_filter": author,
                "search_engine": "Google Scholar",
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
        error_msg = f"Error during Google Scholar search: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Scholar search error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "search_error", "query": query, "error": str(e)},
        }
        return RichToolOutput(error_msg, ui_metadata)
