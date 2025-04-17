import requests
from typing import Dict, Any
from agents import function_tool

@function_tool
def geocode(city_name: str) -> Dict[str, Any]:
    """
    Geocodes a city name into latitude and longitude data using the Open-Meteo Geocoding API.
    
    Args:
        city_name: ONLY the name of the city to geocode. Do not include state, country,
                 or any additional location information. For example, use "New York" 
                 not "New York, NY" or "New York, USA".
        
    Returns:
        Dictionary containing geocoding results with the following structure:
        {
            "results": [
                {
                    "id": 123456,
                    "name": "City Name",
                    "latitude": 40.7128,
                    "longitude": -74.0060,
                    "country": "Country Name",
                    "country_code": "CC",
                    ... other location metadata
                },
                ... other matching locations
            ]
        }
        
    Usage:
        When looking for weather, first use this function to get coordinates, then 
        pass the latitude and longitude to the get_weather function.
        
        IMPORTANT: Only pass the city name alone, without state or country. The API will 
        return an error if given complex location strings.
        
    Example:
        To get weather for New York:
        1. geo_data = geocode("New York")  # CORRECT
           NOT geocode("New York, NY")     # INCORRECT
           NOT geocode("New York, USA")    # INCORRECT
        2. lat = geo_data["results"][0]["latitude"]
        3. lon = geo_data["results"][0]["longitude"]
        4. weather = get_weather(latitude=lat, longitude=lon)
    """
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=10&language=en&format=json"

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if "results" not in data:
            return {"error": "No results found for the given city"}
        return data
    except requests.HTTPError as http_err:
        return {"error": f"HTTP error occurred: {http_err}"}
    except Exception as err:
        return {"error": f"Other error occurred: {err}"}
