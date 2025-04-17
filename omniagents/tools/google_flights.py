import os
import json
import requests
from datetime import datetime, date
from typing import Dict, Any, Optional, List, Literal
from agents import function_tool

@function_tool
def google_flights_search(origin: str,
                         destination: str,
                         departure_date: str,
                         return_date: Optional[str],
                         adults: int,
                         children: int,
                         infants: int,
                         stops: Optional[Literal["any", "nonstop", "1stop", "2stops"]],
                         flight_class: Literal["economy", "premium_economy", "business", "first"],
                         max_price: Optional[int],
                         currency: str,
                         airlines: Optional[List[str]]) -> Dict[str, Any]:
    """
    Searches for flights between specified locations and dates.
    
    Args:
        origin: Origin airport/city code (e.g., "NYC", "SFO").
        destination: Destination airport/city code (e.g., "LHR", "PAR").
        departure_date: Departure date (YYYY-MM-DD).
        return_date: Optional return date for round trips.
        adults: Number of adult passengers (12+).
        children: Number of child passengers (2-11).
        infants: Number of infant passengers (under 2).
        stops: "any", "nonstop", "1stop", or "2stops".
        flight_class: "economy", "premium_economy", "business", or "first".
        max_price: Maximum price in specified currency.
        currency: Three-letter currency code (default "USD").
        airlines: List of preferred airlines (IATA codes).
    
    Returns:
        Dictionary with flight search results and metadata.
    """
    try:
        # Try to get the SerpAPI key from environment variables
        api_key = os.environ.get("SERPAPI_API_KEY")
        
        if not api_key:
            return {
                "status": "error",
                "error": "SERPAPI_API_KEY not found in environment variables. Please set this variable to use flight search."
            }
        
        # Validate input dates
        try:
            departure = datetime.strptime(departure_date, "%Y-%m-%d").date()
            today = date.today()
            
            if departure < today:
                return {
                    "status": "error",
                    "error": f"Departure date {departure_date} is in the past. Please provide a future date."
                }
            
            if return_date:
                return_day = datetime.strptime(return_date, "%Y-%m-%d").date()
                if return_day < departure:
                    return {
                        "status": "error",
                        "error": f"Return date {return_date} cannot be before departure date {departure_date}."
                    }
        except ValueError:
            return {
                "status": "error",
                "error": "Invalid date format. Please use YYYY-MM-DD format."
            }
        
        # Base URL for SerpAPI Google Flights endpoint
        base_url = "https://serpapi.com/search"
        
        # Prepare parameters
        params = {
            "engine": "google_flights",
            "api_key": api_key,
            "departure_id": origin.upper(),
            "arrival_id": destination.upper(),
            "outbound_date": departure_date,
            "currency": currency,
            "adults": adults
        }
        
        # Add return date for round trips
        if return_date:
            params["return_date"] = return_date
        
        # Add passengers
        if children > 0:
            params["children"] = children
        
        if infants > 0:
            params["infants_in_seat"] = infants
        
        # Add class preference
        class_mapping = {
            "economy": "ECONOMY",
            "premium_economy": "PREMIUM_ECONOMY",
            "business": "BUSINESS",
            "first": "FIRST"
        }
        if flight_class in class_mapping:
            params["flight_class"] = class_mapping[flight_class]
        
        # Add stops filter
        if stops == "nonstop":
            params["max_stops"] = 0
        elif stops == "1stop":
            params["max_stops"] = 1
        elif stops == "2stops":
            params["max_stops"] = 2
        
        # Add price filter
        if max_price:
            params["price_max"] = max_price
        
        # Add airlines filter
        if airlines and len(airlines) > 0:
            params["airlines"] = ",".join(airlines)
        
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
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "return_date": return_date,
            "best_flights": [],
            "other_flights": [],
            "airlines_information": [],
            "price_insights": {}
        }
        
        # Add best flights if available
        if "best_flights" in search_results:
            for flight in search_results["best_flights"]:
                result["best_flights"].append({
                    "flight_type": flight.get("flight_type", ""),
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
                    "stops": flight.get("stops", 0),
                    "layovers": flight.get("layovers", []),
                    "carbon_emissions": flight.get("carbon_emissions", "")
                })
        
        # Add other flights if available
        if "other_flights" in search_results:
            for flight in search_results["other_flights"]:
                result["other_flights"].append({
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
                    "stops": flight.get("stops", 0),
                    "layovers": flight.get("layovers", [])
                })
        
        # Add airlines information if available
        if "airlines_information" in search_results:
            result["airlines_information"] = search_results["airlines_information"]
        
        # Add price insights if available
        if "price_insights" in search_results:
            result["price_insights"] = search_results["price_insights"]
        
        # Add search metadata
        result["search_metadata"] = {
            "id": search_results.get("search_metadata", {}).get("id", ""),
            "status": search_results.get("search_metadata", {}).get("status", ""),
            "total_time_taken": search_results.get("search_metadata", {}).get("total_time_taken", 0),
            "engine": "Google Flights"
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
            "error": f"Error during flight search: {str(e)}"
        }