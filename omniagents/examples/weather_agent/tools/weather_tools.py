from __future__ import annotations

import requests
from typing import Dict, Any
from omniagents import function_tool
import re
import json


@function_tool
def geocode(city_name: str) -> Dict[str, Any]:
    """
    Geocodes a city name into latitude and longitude using Open‑Meteo Geocoding API.

    Args:
        city_name: City name only (e.g., "New York", "Paris").

    Returns:
        Dictionary with a "results" list. Each item may contain name, country, latitude, longitude.

    Example:
        data = geocode("London")
        lat = data["results"][0]["latitude"]
        lon = data["results"][0]["longitude"]
    """
    if not city_name or not isinstance(city_name, str):
        return {"error": "city_name must be a non-empty string"}

    # Sanitize trailing punctuation that often leaks from prompts
    name = str(city_name).strip()
    name = re.sub(r"[\s.,!?;:]+$", "", name)

    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {
        "name": name,
        "count": 10,
        "language": "en",
        "format": "json",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data or "results" not in data or not data["results"]:
            return json.dumps({"error": "No results found for the given city"})
        return json.dumps(data)
    except requests.HTTPError as e:
        return json.dumps({"error": f"HTTP error: {e}"})
    except requests.Timeout:
        return json.dumps({"error": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {e}"})


@function_tool
def get_weather(latitude: float, longitude: float, timezone: str = "auto", temperature_unit: str = "celsius") -> Dict[str, Any]:
    """
    Returns current weather for given coordinates using Open‑Meteo.

    Args:
        latitude: Decimal latitude (−90..90)
        longitude: Decimal longitude (−180..180)
        timezone: IANA timezone or "auto"
        temperature_unit: "celsius" or "fahrenheit"

    Returns:
        Dictionary containing current weather, units, and metadata from the API.

    Example:
        geo = geocode("Berlin")
        lat = geo["results"][0]["latitude"]
        lon = geo["results"][0]["longitude"]
        weather = get_weather(lat, lon)
    """
    try:
        if not (-90.0 <= float(latitude) <= 90.0) or not (-180.0 <= float(longitude) <= 180.0):
            return json.dumps({"error": "Invalid coordinates"})
    except Exception:
        return json.dumps({"error": "Invalid coordinate types"})

    fields = [
        "temperature_2m",
        "apparent_temperature",
        "relative_humidity_2m",
        "is_day",
        "precipitation",
        "rain",
        "showers",
        "snowfall",
        "weather_code",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
    ]
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(fields),
        "timezone": timezone,
        "temperature_unit": temperature_unit,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return json.dumps(r.json())
    except requests.HTTPError as e:
        return json.dumps({"error": f"HTTP error: {e}"})
    except requests.Timeout:
        return json.dumps({"error": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {e}"})
