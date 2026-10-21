from __future__ import annotations

from typing import Dict, Any
from omniagents import function_tool
import json
import re


@function_tool
def geocode(city_name: str) -> Dict[str, Any]:
    """
    Geocodes a city name into latitude and longitude using a MOCK service.

    Args:
        city_name: City name only (e.g., "New York", "Paris").

    Returns:
        Dictionary with a "results" list.
    """
    print("Calling Geocode")
    if not city_name or not isinstance(city_name, str):
        return {"error": "city_name must be a non-empty string"}

    name = str(city_name).strip()
    name_lower = name.lower()

    results = []

    if "manchester" in name_lower:
        results = [
            {
                "id": 2643123,
                "name": "Manchester",
                "latitude": 53.48095,
                "longitude": -2.23743,
                "elevation": 38.0,
                "feature_code": "PPLA",
                "country_code": "GB",
                "admin1_id": 6269131,
                "admin2_id": 2643123,
                "timezone": "Europe/London",
                "population": 395515,
                "country_id": 2635167,
                "country": "United Kingdom",
                "admin1": "England",
                "admin2": "Manchester",
            },
            {
                "id": 5089178,
                "name": "Manchester",
                "latitude": 42.99564,
                "longitude": -71.45479,
                "elevation": 64.0,
                "feature_code": "PPLA",
                "country_code": "US",
                "admin1_id": 5090174,
                "admin2_id": 5087463,
                "timezone": "America/New_York",
                "population": 110229,
                "country_id": 6252001,
                "country": "United States",
                "admin1": "New Hampshire",
                "admin2": "Hillsborough",
            },
        ]
    elif "richmond" in name_lower:
        results = [
            {
                "id": 4781756,
                "name": "Richmond",
                "latitude": 37.55376,
                "longitude": -77.46026,
                "elevation": 45.0,
                "feature_code": "PPLA",
                "country_code": "US",
                "admin1_id": 6254928,
                "timezone": "America/New_York",
                "population": 220289,
                "country_id": 6252001,
                "country": "United States",
                "admin1": "Virginia",
            },
            {
                "id": 2638864,
                "name": "Richmond",
                "latitude": 51.46171,
                "longitude": -0.30579,
                "elevation": 19.0,
                "feature_code": "PPL",
                "country_code": "GB",
                "admin1_id": 6269131,
                "admin2_id": 2648110,
                "timezone": "Europe/London",
                "population": 214605,
                "country_id": 2635167,
                "country": "United Kingdom",
                "admin1": "England",
                "admin2": "Greater London",
            },
        ]
    else:
        # Generic fallback
        results = [
            {
                "id": 12345,
                "name": name.title(),
                "latitude": 40.7128,
                "longitude": -74.0060,
                "country": "Mock Country",
                "admin1": "Mock State",
            }
        ]

    return json.dumps({"results": results})


@function_tool
def get_weather(
    latitude: float,
    longitude: float,
    timezone: str = "auto",
    temperature_unit: str = "celsius",
) -> Dict[str, Any]:
    """
    Returns MOCK current weather for given coordinates.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            return json.dumps({"error": "Invalid coordinates"})
    except Exception:
        return json.dumps({"error": "Invalid coordinate types"})

    # Return static nice weather
    mock_data = {
        "latitude": lat,
        "longitude": lon,
        "generationtime_ms": 0.1,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "elevation": 10.0,
        "current_units": {
            "time": "iso8601",
            "interval": "seconds",
            "temperature_2m": "°C" if temperature_unit == "celsius" else "°F",
            "relative_humidity_2m": "%",
            "is_day": "",
            "precipitation": "mm",
            "rain": "mm",
            "showers": "mm",
            "snowfall": "cm",
            "weather_code": "wmo code",
            "wind_speed_10m": "km/h",
            "wind_direction_10m": "°",
            "wind_gusts_10m": "km/h",
        },
        "current": {
            "time": "2023-10-27T12:00",
            "interval": 900,
            "temperature_2m": 20.5,
            "relative_humidity_2m": 45,
            "is_day": 1,
            "precipitation": 0.0,
            "rain": 0.0,
            "showers": 0.0,
            "snowfall": 0.0,
            "weather_code": 0,  # Clear sky
            "wind_speed_10m": 10.5,
            "wind_direction_10m": 180,
            "wind_gusts_10m": 25.0,
        },
    }
    return json.dumps(mock_data)
