import requests
from typing import Dict, Any
from requests.exceptions import HTTPError
from agents import function_tool

@function_tool
def get_weather(latitude: float, longitude: float) -> Dict[str, Any]:
    """
    Returns the current weather conditions for a given latitude and longitude using the Open-Meteo API.
    
    Args:
        latitude: Latitude coordinate of the location (decimal degrees)
        longitude: Longitude coordinate of the location (decimal degrees)
        
    Returns:
        Dictionary containing weather data with the following structure:
        {
            "latitude": 40.71,
            "longitude": -74.01,
            "timezone": "America/Chicago",
            "current": {
                "time": "2023-08-11T12:00",
                "temperature_2m": 25.3,
                "is_day": 1,
                "precipitation": 0.0,
                "rain": 0.0,
                "showers": 0.0,
                "snowfall": 0.0
            },
            "current_units": {
                "temperature_2m": "°C",
                "precipitation": "mm",
                ...
            }
        }
        
    Usage:
        This function requires precise coordinates. First use the geocode function 
        to convert a city name to coordinates, then call this function.
        
    Example:
        geo_data = geocode("London")
        weather = get_weather(
            latitude=geo_data["results"][0]["latitude"],
            longitude=geo_data["results"][0]["longitude"]
        )
    """
    url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=temperature_2m,is_day,precipitation,rain,showers,snowfall&timezone=America%2FChicago"

    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except HTTPError as http_err:
        return {"error": f"HTTP error occurred: {http_err}"}
    except Exception as err:
        return {"error": f"Other error occurred: {err}"}