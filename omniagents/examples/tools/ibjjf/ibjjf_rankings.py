import requests
from typing import Dict, Any
from omniagents.core import function_tool

# IBJJF Event tool
@function_tool
def ibjjf_events() -> Dict[str, Any]:
    """
    Fetch IBJJF events. This includes upcoming, ongoing, and recently completed events with no distinction provided by the API.

    Returns:
        Dictionary containing all IBJJF events with id and name fields
    """
    url = "https://ibjjfrankings.com/api/brackets/events"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        events = data.get("events", [])

        # Return all events with basic info
        return {"events": [{"id": e.get("id"), "name": e.get("name")} for e in events]}
    except requests.HTTPError as error:
        return {"error": f"HTTP error: {str(error)}"}
    except Exception as error:
        return {"error": f"Error fetching IBJJF events: {str(error)}"}

# IBJJF Brackets tool
@function_tool
def ibjjf_brackets(event_id: str) -> Dict[str, Any]:
    """
    Fetch IBJJF bracket categories for a specific event

    Args:
        event_id: The numeric ID of the IBJJF event

    Returns:
        Dictionary containing bracket categories (age, belt, gender, weight)
    """
    url = f"https://ibjjfrankings.com/api/brackets/categories/{event_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        # Return the raw JSON, agent should format it
        return response.json()
    except requests.HTTPError as error:
        return {"error": f"HTTP error: {str(error)}"}
    except Exception as error:
        return {"error": f"Error fetching IBJJF brackets: {str(error)}"}

# IBJJF Competitors tool
@function_tool
def ibjjf_competitors(link: str, age: str, gender: str, belt: str, weight: str, gi_competition: str) -> Dict[str, Any]:
    """
    Fetch competitors for a specific IBJJF division

    Args:
        link: The category link (format: /tournaments/EVENT_ID/categories/CATEGORY_ID)
        age: Age category (e.g., "Adult", "Master 1")
        gender: Gender category ("Male" or "Female")
        belt: Belt color (e.g., "WHITE", "BLUE", "PURPLE", "BROWN", "BLACK")
        weight: Weight class (e.g., "Rooster", "Light Feather", "Middle", "Heavy")
        gi_competition: Whether this is a gi ("true") or no-gi ("false") competition

    Returns:
        Dictionary containing competitor information
    """
    url = "https://ibjjfrankings.com/api/brackets/competitors"

    # Convert string "true"/"false" to boolean for the API
    gi_param = gi_competition.lower() == "true"

    params = {
        "link": link,
        "age": age,
        "gender": gender,
        "gi": str(gi_param).lower(), # API expects string 'true' or 'false'
        "belt": belt,
        "weight": weight
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        # Return raw JSON, agent formats it
        return response.json()
    except requests.HTTPError as error:
        return {"error": f"HTTP error: {str(error)}"}
    except Exception as error:
        return {"error": f"Error fetching IBJJF competitors: {str(error)}"} 