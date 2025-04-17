import os
import json
import requests
from typing import Dict, Any, Optional, Literal
from agents import function_tool

@function_tool
def scholar_search(query: str,
                  num_results: int,
                  sort_by: Literal["relevance", "date"],
                  publication_date: Optional[Literal["since_2023", "since_2020", "since_2017", "since_2014"]],
                  author: Optional[str]) -> Dict[str, Any]:
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
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")
        
        if not api_key:
            return {
                "status": "error",
                "error": "SERPAPI_API_KEY not found in environment variables. Please set this variable to use Google Scholar search."
            }
        
        # Base URL for SerpAPI Google Scholar endpoint
        base_url = "https://serpapi.com/search"
        
        # Prepare parameters
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google_scholar",
            "num": min(num_results, 20),  # Google Scholar typically shows 10-20 results per page
            "hl": "en"                    # Language set to English
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
                "since_2014": "2014"
            }
            if publication_date in date_filters:
                params["as_ylo"] = date_filters[publication_date]
        
        # Always include citations, patents, and profile results
        
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
            "organic_results": [],
            "citation_results": [],
            "profiles": [],
            "related_searches": []
        }
        
        # Add organic search results (articles and papers)
        if "organic_results" in search_results:
            for item in search_results["organic_results"]:
                paper_info = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "publication_info": item.get("publication_info", {})
                }
                
                # Extract authors
                if "authors" in item:
                    paper_info["authors"] = item["authors"]
                
                # Extract citations
                if "inline_links" in item and "cited_by" in item["inline_links"]:
                    paper_info["cited_by"] = {
                        "total": item["inline_links"]["cited_by"].get("total", 0),
                        "link": item["inline_links"]["cited_by"].get("link", "")
                    }
                
                # Extract related versions if available
                if "versions" in item.get("inline_links", {}):
                    paper_info["versions"] = {
                        "total": item["inline_links"]["versions"].get("total", 0),
                        "link": item["inline_links"]["versions"].get("link", "")
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
            "total_time_taken": search_results.get("search_metadata", {}).get("total_time_taken", 0),
            "engine": "Google Scholar"
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
            "error": f"Error during Google Scholar search: {str(e)}"
        }