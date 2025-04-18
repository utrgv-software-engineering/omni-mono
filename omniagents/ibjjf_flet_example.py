import flet as ft
import os
import sys # To read command line args
import argparse
from dotenv import load_dotenv
from datetime import datetime
from openai import AsyncOpenAI
from typing import Dict, Any, List, Optional

from agents import (
    Agent,
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled
)
from agents.model_settings import ModelSettings

# Import standard tools
from tools import geocode, get_weather, web_search, youtube_search, google_flights_search
# Import IBJJF tools
from tools.ibjjf_rankings import ibjjf_events, ibjjf_brackets, ibjjf_competitors

# Import core components
from flet_agent_core.app_base import AgentApp, AgentSpec
from flet_agent_core.settings import SettingField

# --- Configuration & Setup ---

# Load environment variables first
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Parse command line arguments for debug mode
parser = argparse.ArgumentParser(description="IBJJF Information Assistant with Flet UI")
parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode")
args = parser.parse_args()
DEBUG_MODE = args.debug

if DEBUG_MODE:
    print("\n--- IBJJF Assistant --- Debug mode enabled ---")

# Disable tracing
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# List of all available tools for this agent
ALL_AVAILABLE_TOOLS = [
    geocode, get_weather, web_search, youtube_search,
    google_flights_search, ibjjf_events, ibjjf_brackets, ibjjf_competitors
]

# --- Agent Specific Functions ---

def get_agent_instructions() -> str:
    """Get agent instructions tailored for IBJJF competitors and coaches."""
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""
    Current date and time: {current_datetime}

    You are an assistant designed to help IBJJF competitors and coaches with competition planning, logistics, and research using the available tools.

    **IBJJF Competition Tools:**

    *   `ibjjf_events()`: Call this with no arguments to get a list of all available IBJJF events (upcoming, ongoing, recent - the API doesn't distinguish). Each event will have an `id` and `name`.
        *   Use this list to find the specific `event_id` required for other tools.
    *   `ibjjf_brackets(event_id: str)`: Provide the numeric `event_id` (from `ibjjf_events`) to get the raw category data for that tournament.
        *   You should process this data to show the user the available divisions (age, belt, gender, weight, gi/no-gi) clearly.
    *   `ibjjf_competitors(link: str, age: str, gender: str, belt: str, weight: str, gi_competition: str)`: Use this to find competitors in a specific division. It requires:
        *   `link`: The category link (e.g., '/tournaments/EVENT_ID/categories/CATEGORY_ID') found in the `ibjjf_brackets` output.
        *   `age`: Age group (e.g., "Adult", "Master 1").
        *   `gender`: "Male" or "Female".
        *   `belt`: Belt rank (e.g., "BLACK", "BROWN").
        *   `weight`: Weight class (e.g., "Middle", "Heavy").
        *   `gi_competition`: A string, "true" for gi or "false" for no-gi.
        *   You should process the returned competitor list, showing names, teams, and seeds clearly to the user.
        *   Note: The 'seed' value in the results comes directly from the official IBJJF data for that specific event. Separately, the ibjjfrankings.com website ([https://ibjjfrankings.com/about](https://ibjjfrankings.com/about)) computes its own ELO-based rankings, which are distinct from this official seeding.

    **Travel & Location Tools:**

    *   `geocode(city_name: str)`: **IMPORTANT:** Use ONLY the city name (e.g., "Orlando", NOT "Orlando, FL"). This finds geographic coordinates (latitude, longitude).
    *   `get_weather(latitude: float, longitude: float)`: After using `geocode`, provide the latitude and longitude from its results to get the current weather forecast for that location.
    *   `google_flights_search(...)`: Searches for flights. Requires `SERPAPI_API_KEY` (set in settings). This tool takes parameters for:
        *   `origin` & `destination` (airport/city codes like "LAX", "CDG").
        *   `departure_date` (YYYY-MM-DD).
        *   `return_date` (optional, YYYY-MM-DD).
        *   `adults`, `children`, `infants` (passenger counts).
        *   `stops` (optional: "any", "nonstop", "1stop", "2stops").
        *   `flight_class` ("economy", "premium_economy", "business", "first").
        *   `max_price` (optional).
        *   `currency` (e.g., "USD").
        *   `airlines` (optional list of IATA codes).

    **Research Tools:**

    *   `web_search(query: str, num_results: int, include_news: bool, time_period: Optional[str])`: Performs a Google search via SerpApi. Requires `SERPAPI_API_KEY`. Takes parameters for the query, number of results (max 100, recommend 10), whether to include news, and optional time filter ("past_day", "past_week", "past_month", "past_year").
    *   `youtube_search(query: str, num_results: int, sort_by: str, upload_date: Optional[str], duration: Optional[str])`: Searches YouTube via SerpApi. Requires `SERPAPI_API_KEY`. Takes parameters for the query, number of results (max 100), sorting ("relevance", "upload_date", "view_count", "rating"), optional upload date filter ("last_hour", "today", "this_week", "this_month", "this_year"), and optional duration filter ("short", "medium", "long").
        *   **Recommend using `num_results=10` for most searches, especially for competitors.**
        *   **When searching for competitor footage:** Don't just use the full name from the bracket. Try multiple searches with variations:
            *   Full Name + "BJJ" or "IBJJF" (good for official videos).
            *   First Name + Last Name + Event Name (e.g., "Worlds 2023").
            *   Nickname (if known) + Last Name + "BJJ".
            *   Common variations or potential misspellings of the name.
            *   You should formulate approximately 5 different search queries based on these variations (and any other reasonable combinations).
            *   Execute *all* of these `youtube_search` calls.
            *   Synthesize the findings from all searches before reporting back to the user. Mention which queries yielded useful results if possible. Do not just return the results from the first successful search.

    **General Notes:**
    *   If a tool requiring `SERPAPI_API_KEY` is used and the key isn't configured in Settings, you must inform the user.
    *   You should structure the information from the IBJJF tools for clarity before presenting it.
    *   Remember the specific input requirements for tools, especially `geocode`.
    """

# Update signature to accept AgentApp instance (app argument is unused but required by protocol)
# Make the function async to match the Awaitable type hint in AgentSpec
# Add mcp_server argument to match the calling signature in AgentApp
async def build_agent(settings: Dict[str, Any], mcp_server: Optional[Any], app: AgentApp) -> Agent:
    """Builds the IBJJF Agent instance using current settings."""
    print("Building IBJJF Agent...")
    # Add a check/log if mcp_server is unexpectedly passed (optional)
    if mcp_server:
        print("Warning: build_agent received an mcp_server instance, but IBJJF agent does not use it.")

    # Use settings passed from AgentApp
    api_key = settings.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    base_url = settings.get("openai_api_base") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
    model_name = settings.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o"
    serpapi_key = settings.get("serpapi_api_key") or os.getenv("SERPAPI_API_KEY")

    if not api_key:
        # Let AgentApp handle displaying the error via exception
        raise ValueError("OpenAI API key is missing. Please configure in settings.")

    # Configure the OpenAI client (important for the agent)
    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        set_default_openai_client(client=client, use_for_tracing=False)
        print(f"OpenAI client configured: base_url={base_url}, model={model_name}")
    except Exception as e:
        raise ValueError(f"Failed to initialize OpenAI client: {e}")

    # Set SerpApi key in environment if provided, as tools might expect it there
    if serpapi_key:
        os.environ["SERPAPI_API_KEY"] = serpapi_key
        print("SERPAPI_API_KEY set in environment.")
    elif "SERPAPI_API_KEY" in os.environ:
        del os.environ["SERPAPI_API_KEY"]

    agent = Agent(
        name="IBJJF Assistant",
        tools=ALL_AVAILABLE_TOOLS,
        model=model_name,
        model_settings=ModelSettings(tool_choice="auto"),
        instructions=get_agent_instructions(),
    )
    print(f"IBJJF Agent created with model: {model_name}")
    return agent

# --- Application Specification ---

# Define the settings fields specific to this app
ibjjf_settings_fields = [
    SettingField(key="openai_api_base", label="OpenAI API Base URL", group="OpenAI API", default="https://api.openai.com/v1", hint="e.g., https://api.openai.com/v1"),
    SettingField(key="openai_api_key", label="OpenAI API Key", group="OpenAI API", secret=True, hint="Required"),
    SettingField(key="model_name", label="Model Name", group="OpenAI API", default="gpt-4o", hint="e.g., gpt-4o, gpt-3.5-turbo"),
    SettingField(key="serpapi_api_key", label="SerpApi API Key", group="SerpApi", secret=True, hint="Required for web/YouTube/Flights"),
]

# Define the icon map for tool calls
ibjjf_icon_map = {
    "geocode": ft.Icons.MAP,
    "get_weather": ft.Icons.CLOUD,
    "web_search": ft.Icons.SEARCH,
    "youtube_search": ft.Icons.VIDEO_LIBRARY,
    "google_flights_search": ft.Icons.FLIGHT,
    "ibjjf_events": ft.Icons.EVENT,
    "ibjjf_brackets": ft.Icons.LIST_ALT,
    "ibjjf_competitors": ft.Icons.PEOPLE,
}

# Construct the welcome text
welcome_text = "👋 Hi! I'm the IBJJF Assistant. Ask me about upcoming tournaments, specific event divisions, or competitors. You can also ask me about weather, flights, or general web search."
if DEBUG_MODE:
    welcome_text += " (Debug mode is active - tool outputs will be shown)"
# Note: We can add the SerpApi key check warning within AgentApp based on loaded settings later

# Create the Agent Specification
ibjjf_agent_spec = AgentSpec(
    # Basic Info
    name="IBJJF Assistant",
    header_title="IBJJF Assistant",
    page_title="IBJJF Assistant",
    welcome_text=welcome_text,
    client_storage_prefix="ibjjf_flet_agent.",

    # Agent Config
    get_agent_instructions=get_agent_instructions,
    build_agent=build_agent,
    available_tools=ALL_AVAILABLE_TOOLS,

    # UI/UX Config
    icon_map=ibjjf_icon_map,
    settings_fields=ibjjf_settings_fields,
    debug_flag=DEBUG_MODE,
    page_title_suffix=" (Debug Mode)",
    theme_color=ft.Colors.BLUE_900, # Specific theme color for this app

    # Optional Customizations (None needed for this app)
    header_controls_builder=None,
    cleanup_handler=None,
)

# --- Main Execution ---

if __name__ == "__main__":
    # Create the app instance with the spec
    app = AgentApp(spec=ibjjf_agent_spec)
    # Run the Flet app, targeting the main method of the app instance
    ft.app(target=app.main)