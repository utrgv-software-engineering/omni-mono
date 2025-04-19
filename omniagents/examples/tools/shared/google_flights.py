import os
import json
import requests
from typing import Dict, Any, Optional, Literal
from omniagents.core import function_tool

@function_tool
def google_flights_search(origin: str,
                         destination: str,
                         departure_date: str,
                         return_date: Optional[str] = None,
                         num_results: int = 10) -> Dict[str, Any]:
    """
    Searches Google Flights for flight information using SerpAPI.
    
    Args:
        origin: Origin airport code or city (e.g., "LAX", "New York")
        destination: Destination airport code or city (e.g., "JFK", "London")
        departure_date: Departure date in YYYY-MM-DD format
        return_date: Optional return date in YYYY-MM-DD format for round trips
        num_results: Number of results to return (max 10)
        
    Returns:
        Dictionary containing flight search results and metadata.
    """
    try:
        # Get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")
        
        if not api_key:
            return {
                "status": "error",
                "error": "SERPAPI_API_KEY not found in environment variables"
            }
        
        # Base URL for SerpAPI Google Flights endpoint
        base_url = "https://serpapi.com/search"
        
        # Construct the search query
        query = f"flights from {origin} to {destination} on {departure_date}"
        if return_date:
            query += f" return {return_date}"
        
        # Prepare parameters
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google_flights",
            "hl": "en",  # Language set to English
            "gl": "us",  # Region set to US
            "num": min(num_results, 10)  # Limit results
        }
        
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
            "flights": [],
            "search_metadata": {
                "id": search_results.get("search_metadata", {}).get("id", ""),
                "status": search_results.get("search_metadata", {}).get("status", ""),
                "total_time_taken": search_results.get("search_metadata", {}).get("total_time_taken", 0),
                "engine": "Google Flights"
            }
        }
        
        # Extract flight results
        if "flights" in search_results:
            for flight in search_results["flights"][:num_results]:
                flight_info = {
                    "price": flight.get("price", ""),
                    "duration": flight.get("duration", ""),
                    "departure": {
                        "airport": flight.get("departure", {}).get("airport", ""),
                        "time": flight.get("departure", {}).get("time", "")
                    },
                    "arrival": {
                        "airport": flight.get("arrival", {}).get("airport", ""),
                        "time": flight.get("arrival", {}).get("time", "")
                    },
                    "airline": flight.get("airline", ""),
                    "flight_number": flight.get("flight_number", ""),
                    "stops": flight.get("stops", 0),
                    "layovers": flight.get("layovers", [])
                }
                result["flights"].append(flight_info)
        
        # Add best prices if available
        if "best_prices" in search_results:
            result["best_prices"] = search_results["best_prices"]
        
        # Add price insights if available
        if "price_insights" in search_results:
            result["price_insights"] = search_results["price_insights"]
        
        return result
        
    except requests.RequestException as e:
        return {
            "status": "error",
            "error": f"Network error occurred: {str(e)}"
        }
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "Failed to parse the search results"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Error during Google Flights search: {str(e)}"
        }