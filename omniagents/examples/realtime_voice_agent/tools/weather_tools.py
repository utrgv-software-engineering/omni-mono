"""Weather tools for the realtime voice agent."""

from omniagents import function_tool


@function_tool
def get_current_weather(location: str) -> str:
    """Get the current weather for a location.

    Args:
        location: City name, address, or zip code

    Returns:
        Current weather conditions including temperature, conditions, and wind
    """
    # In a real implementation, this would call a weather API
    # For this example, we return mock data

    mock_weather = {
        "san francisco": {
            "temperature": 65,
            "celsius": 18,
            "conditions": "Partly cloudy",
            "humidity": 72,
            "wind": "10 mph from the west",
        },
        "new york": {
            "temperature": 45,
            "celsius": 7,
            "conditions": "Clear",
            "humidity": 45,
            "wind": "5 mph from the north",
        },
        "london": {
            "temperature": 52,
            "celsius": 11,
            "conditions": "Light rain",
            "humidity": 85,
            "wind": "12 mph from the southwest",
        },
        "tokyo": {
            "temperature": 70,
            "celsius": 21,
            "conditions": "Sunny",
            "humidity": 55,
            "wind": "8 mph from the east",
        },
    }

    # Normalize location
    location_lower = location.lower()

    # Check if we have data for this location
    for known_location, data in mock_weather.items():
        if known_location in location_lower or location_lower in known_location:
            return (
                f"Current weather in {location}:\n"
                f"Temperature: {data['temperature']}°F ({data['celsius']}°C)\n"
                f"Conditions: {data['conditions']}\n"
                f"Humidity: {data['humidity']}%\n"
                f"Wind: {data['wind']}"
            )

    # Default response for unknown locations
    return (
        f"I don't have current weather data for {location}. "
        "Please try a major city like San Francisco, New York, London, or Tokyo."
    )


@function_tool
def get_weather_forecast(location: str, days: int = 3) -> str:
    """Get the weather forecast for a location.

    Args:
        location: City name, address, or zip code
        days: Number of days to forecast (1-7)

    Returns:
        Weather forecast for the specified number of days
    """
    if days < 1:
        days = 1
    if days > 7:
        days = 7

    # Mock forecast data
    forecast_templates = {
        "san francisco": [
            "Partly cloudy, 67°F/19°C",
            "Sunny, 70°F/21°C",
            "Partly cloudy, 65°F/18°C",
            "Light rain, 60°F/15°C",
            "Cloudy, 62°F/17°C",
            "Sunny, 68°F/20°C",
            "Partly cloudy, 66°F/19°C",
        ],
        "new york": [
            "Clear, 48°F/9°C",
            "Sunny, 52°F/11°C",
            "Cloudy, 45°F/7°C",
            "Rain, 42°F/6°C",
            "Partly cloudy, 50°F/10°C",
            "Clear, 55°F/13°C",
            "Sunny, 58°F/14°C",
        ],
        "london": [
            "Rain, 54°F/12°C",
            "Cloudy, 52°F/11°C",
            "Light rain, 50°F/10°C",
            "Partly cloudy, 55°F/13°C",
            "Cloudy, 53°F/12°C",
            "Rain, 51°F/11°C",
            "Overcast, 54°F/12°C",
        ],
        "tokyo": [
            "Sunny, 72°F/22°C",
            "Partly cloudy, 70°F/21°C",
            "Cloudy, 68°F/20°C",
            "Light rain, 65°F/18°C",
            "Sunny, 73°F/23°C",
            "Clear, 75°F/24°C",
            "Partly cloudy, 71°F/22°C",
        ],
    }

    # Find matching location
    location_lower = location.lower()
    forecast_data = None

    for known_location, forecasts in forecast_templates.items():
        if known_location in location_lower or location_lower in known_location:
            forecast_data = forecasts
            break

    if not forecast_data:
        return (
            f"I don't have forecast data for {location}. "
            "Please try a major city like San Francisco, New York, London, or Tokyo."
        )

    # Build forecast string
    forecast_lines = [f"{days}-day forecast for {location}:"]
    for i in range(days):
        forecast_lines.append(f"Day {i + 1}: {forecast_data[i]}")

    return "\n".join(forecast_lines)


@function_tool
def get_weather_alerts(location: str) -> str:
    """Check for weather alerts or warnings in a location.

    Args:
        location: City name, address, or zip code

    Returns:
        Active weather alerts or "No alerts" if none
    """
    # Mock alert data
    alerts = {
        "san francisco": [],
        "new york": ["Wind Advisory until 8 PM EST"],
        "london": ["Flood Watch until midnight"],
        "tokyo": [],
    }

    location_lower = location.lower()

    for known_location, location_alerts in alerts.items():
        if known_location in location_lower or location_lower in known_location:
            if location_alerts:
                return f"Weather alerts for {location}:\n" + "\n".join(
                    f"- {alert}" for alert in location_alerts
                )
            else:
                return f"No weather alerts for {location}."

    return f"Unable to check alerts for {location}. Please try a major city."
