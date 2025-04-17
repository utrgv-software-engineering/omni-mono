import flet as ft
import os
import asyncio
import argparse
import json
import requests
from dotenv import load_dotenv
from datetime import datetime
from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    function_tool,
    HandoffOutputItem,
    ItemHelpers,
    MessageOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
    set_default_openai_client,
    set_tracing_disabled
)
from agents.model_settings import ModelSettings
from typing import List, Dict, Any, Optional

# Import standard tools
from tools import geocode, get_weather, web_search, youtube_search, scholar_search, google_flights_search

# Load environment variables from .env file
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Get configuration from environment variables
# These will be overridden in the main function if settings are saved in client storage
BASE_URL = os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME") or "gpt-4o"
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

# Disable tracing
set_tracing_disabled(disabled=True)

# Parse command line arguments
parser = argparse.ArgumentParser(description="IBJJF Information Assistant with Flet UI")
parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode to show detailed tool outputs")
args = parser.parse_args()

# Debug mode flag
DEBUG_MODE = args.debug

if DEBUG_MODE:
    print("\n--- Debug Info ---")
    print(f"Debug mode enabled")
    print("------------------\n")

# IBJJF Event tool
@function_tool
def ibjjf_events(keyword: str) -> Dict[str, Any]:
    """
    Fetch IBJJF events and optionally filter by name

    Args:
        keyword: Text to filter events by name (pass empty string to get all events)

    Returns:
        Dictionary containing IBJJF events with id and name fields
    """
    url = "https://ibjjfrankings.com/api/brackets/events"
    print(f"Tool: ibjjf_events called with keyword='{keyword}'")
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        events = data.get("events", [])

        # Filter results if keyword is provided
        if keyword and keyword.strip():
            filtered = []
            for event in events:
                if keyword.lower() in event.get("name", "").lower():
                    # Return only basic info for brevity in chat
                    filtered.append({"id": event.get("id"), "name": event.get("name")})
            # Limit results to avoid flooding chat
            return {"events": filtered[:15]}

        # Limit results to avoid flooding chat
        return {"events": [{"id": e.get("id"), "name": e.get("name")} for e in events[:15]]}
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
    print(f"Tool: ibjjf_brackets called with event_id='{event_id}'")
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
    print(f"Tool: ibjjf_competitors called for link='{link}', age='{age}', belt='{belt}', weight='{weight}'")

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

# --- Agent Instructions ---

def get_agent_instructions() -> str:
    """Get agent instructions with current date and time"""
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""
    Current date and time: {current_datetime}

    You are a helpful assistant that can:
    1. Find geographic coordinates for locations using geocode
    2. Get weather information for a location using latitude and longitude
    3. Search the web for information
    4. Search YouTube for videos
    5. Search Google Scholar for academic papers
    6. Search for flights using Google Flights
    7. Find IBJJF (International Brazilian Jiu-Jitsu Federation) events
    8. Get bracket categories for specific IBJJF events
    9. Get competitor information for specific IBJJF divisions

    When asked about weather in a location:
    1. First use the geocode tool with ONLY the city name (do not include state or country)
        Example: geocode("Paris") is correct, geocode("Paris, France") will fail
    2. Then use the get_weather tool with the coordinates from the first result

    When asked for general information, use the web_search tool. Ensure SERPAPI_API_KEY is configured.

    When asked to search for videos on YouTube, use the youtube_search tool. Ensure SERPAPI_API_KEY is configured.

    When asked to search for academic papers or research, use the scholar_search tool. Ensure SERPAPI_API_KEY is configured.

    When asked to search for flights, use the google_flights_search tool. Ensure SERPAPI_API_KEY is configured.
    - Use airport or city codes for origin and destination (e.g., "NYC", "SFO", "LHR", "PAR")
    - Format dates as YYYY-MM-DD

    When asked about IBJJF events, use the ibjjf_events tool to:
    - Get all upcoming IBJJF events by passing an empty string as the keyword parameter
    - Filter events by keyword (e.g., "Miami", "World", "2025") by providing the search term
    - Always list the events in a clear, numbered format with the event ID and name

    When asked for details about an IBJJF event's brackets/divisions:
    - First use ibjjf_events to find the event ID if not already known
    - Then use ibjjf_brackets with the event_id to get categories
    - Organize the returned data to show available divisions by age, belt, gender, and weight
    - Format the information in a clear, structured way

    When asked about competitors in a specific division:
    - First use ibjjf_events to find the event
    - Then use ibjjf_brackets to find the division's link and category details
    - Finally use ibjjf_competitors with the following parameters:
        - link: The category link (format: /tournaments/EVENT_ID/categories/CATEGORY_ID)
        - age: The age category (e.g., "Adult", "Master 1")
        - gender: The gender category ("Male" or "Female")
        - belt: The belt color (e.g., "WHITE", "BLUE", "PURPLE", "BROWN", "BLACK")
        - weight: The weight class (e.g., "Rooster", "Light Feather", "Middle", "Heavy")
        - gi_competition: "true" for gi competitions or "false" for no-gi competitions (as a string)
    - Present the competitor information in a clear, organized format including names, teams, and seeds

    Always be polite, informative and concise in your responses. If a tool requires an API key (like SerpApi) and it's missing, inform the user they need to configure it in the settings.
    """

# --- Flet UI Components (Adapted from mcp_flet_example.py) ---

class ToolCallDisplay(ft.Container):
    def __init__(self, tool_name, args_str):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.margin = ft.margin.only(left=20, right=20, top=8, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        tool_icons = {
            # Default tool icons - customize based on actual tool names
            "geocode": ft.Icons.MAP,
            "get_weather": ft.Icons.CLOUD,
            "web_search": ft.Icons.SEARCH,
            "youtube_search": ft.Icons.VIDEO_LIBRARY,
            "scholar_search": ft.Icons.SCHOOL,
            "google_flights_search": ft.Icons.FLIGHT,
            "ibjjf_events": ft.Icons.EVENT,
            "ibjjf_brackets": ft.Icons.LIST_ALT,
            "ibjjf_competitors": ft.Icons.PEOPLE,
        }

        icon = tool_icons.get(tool_name, ft.Icons.BUILD) # Default icon

        # Create a card with tool info
        tool_card = ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Icon(name=icon, size=16, color=ft.Colors.WHITE),
                    width=28,
                    height=28,
                    border_radius=14,
                    bgcolor=ft.Colors.BLUE_GREY, # Different color scheme
                    alignment=ft.alignment.center,
                ),
                ft.Container(width=10),
                ft.Column([
                    ft.Text(
                        f"Using {tool_name}",
                        weight="w500",
                        size=13,
                        color=ft.Colors.BLACK87
                    ),
                    ft.Text(
                        f"{args_str}",
                        size=12,
                        color=ft.Colors.BLACK54,
                        # max_lines=2, # Limit arg length display
                        # overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ], spacing=2, tight=True, expand=True),
            ], spacing=0),
            padding=ft.padding.only(left=12, top=12, bottom=12, right=16),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.BLUE_GREY),
        )

        self.content = tool_card

class ToolOutputDisplay(ft.Container):
    def __init__(self, output_text):
        super().__init__()
        self.padding = 0
        # Standardized left margin, consistent bottom margin - Adjusted for no avatar
        self.margin = ft.margin.only(left=20, right=20, top=4, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Truncate very long outputs
        if len(output_text) > 1000:
            display_text = output_text[:1000] + "... [output truncated]"
        else:
            display_text = output_text

        self.content = ft.Container(
            content=ft.Text(
                display_text,
                selectable=True,
                size=13,
                color=ft.Colors.BLACK87,
                font_family="SF Mono, Menlo, Monaco, Consolas, monospace"
            ),
            # Removed horizontal padding to align text with margin
            padding=ft.padding.only(top=12, bottom=12, right=16),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
            border=ft.border.all(0.5, ft.Colors.BLACK12),
        )

class SettingsScreen(ft.View):
    def __init__(self, on_save, on_back, initial_values=None):
        super().__init__()

        self.route = "/settings"
        self.bgcolor = ft.colors.with_opacity(0.98, ft.Colors.WHITE) # Match main screen bg
        self.padding = ft.padding.all(0)

        self.on_save = on_save
        self.on_back = on_back
        self.initial_values = initial_values or {
            "openai_api_key": "",
            "openai_api_base": "https://api.openai.com/v1",
            "model_name": "gpt-4o",
            "serpapi_api_key": "", # Add SerpApi key
        }

        # Create form fields
        self.openai_api_key = ft.TextField(
            label="OpenAI API Key",
            value=self.initial_values.get("openai_api_key", ""),
            password=True,
            can_reveal_password=True,
            border_color=ft.Colors.BLUE_200,
            expand=True,
        )

        self.openai_api_base = ft.TextField(
            label="OpenAI API Base URL",
            value=self.initial_values.get("openai_api_base", "https://api.openai.com/v1"),
            border_color=ft.Colors.BLUE_200,
            expand=True,
            hint_text="https://api.openai.com/v1",
        )

        self.model_name = ft.TextField(
            label="Model Name",
            value=self.initial_values.get("model_name", "gpt-4o"),
            border_color=ft.Colors.BLUE_200,
            expand=True,
            hint_text="gpt-4o",
        )

        # Add SerpApi Key field
        self.serpapi_api_key = ft.TextField(
            label="SerpApi API Key",
            value=self.initial_values.get("serpapi_api_key", ""),
            password=True,
            can_reveal_password=True,
            border_color=ft.Colors.BLUE_200,
            expand=True,
            hint_text="Required for web, YouTube, Scholar, Flights",
        )

        # Create solid color header
        settings_header = ft.Container(
            content=ft.Row([
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color=ft.Colors.WHITE,
                    on_click=self._go_back,
                ),
                ft.Text(
                    "Settings",
                    color=ft.Colors.WHITE,
                    size=20,
                    weight="bold",
                ),
                ft.TextButton(
                    text="Save",
                    on_click=self._save_settings,
                    style=ft.ButtonStyle(
                        color=ft.Colors.WHITE,
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            bgcolor=ft.Colors.BLUE_GREY_700, # Match new theme
            padding=ft.padding.only(left=10, right=10, top=16, bottom=16),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.2, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
        )

        # Create scrollable content
        settings_content = ft.ListView(
            controls=[
                ft.Container(height=12),
                ft.Container(
                    content=ft.Text(
                        "Configure API credentials",
                        size=14,
                        color=ft.Colors.BLACK54,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    margin=ft.margin.only(bottom=15, top=10),
                    expand=True,
                ),

                # OpenAI API Section
                ft.Container(
                    content=ft.Column([
                        ft.Container(
                            content=ft.Text("OPENAI API", size=12, weight="w500", color=ft.Colors.BLACK54),
                            margin=ft.margin.only(left=16, bottom=6),
                        ),
                        ft.Container(
                            content=ft.Column([
                                ft.Container(
                                    content=self.openai_api_base,
                                    padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                    border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                                ),
                                ft.Container(
                                    content=self.openai_api_key,
                                    padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                    border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                                ),
                                ft.Container(
                                    content=self.model_name,
                                    padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                ),
                            ]),
                            bgcolor=ft.Colors.WHITE,
                            border_radius=10,
                            shadow=ft.BoxShadow(
                                spread_radius=0, blur_radius=2,
                                color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK), offset=ft.Offset(0, 1)
                            ),
                        ),
                        ft.Container(
                            content=ft.Text("Your OpenAI API key and configuration settings", size=12, color=ft.Colors.BLACK45),
                            margin=ft.margin.only(left=16, top=6, bottom=24),
                        ),
                    ]),
                    expand=True,
                ),

                # SerpApi Section
                ft.Container(
                    content=ft.Column([
                        ft.Container(
                            content=ft.Text("SERPAPI", size=12, weight="w500", color=ft.Colors.BLACK54),
                            margin=ft.margin.only(left=16, bottom=6),
                        ),
                        ft.Container(
                            content=ft.Column([
                                ft.Container(
                                    content=self.serpapi_api_key,
                                    padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                ),
                            ]),
                            bgcolor=ft.Colors.WHITE,
                            border_radius=10,
                            shadow=ft.BoxShadow(
                                spread_radius=0, blur_radius=2,
                                color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK), offset=ft.Offset(0, 1)
                            ),
                        ),
                        ft.Container(
                            content=ft.Text("API key for web search, YouTube, Scholar, and Flights tools.", size=12, color=ft.Colors.BLACK45),
                            margin=ft.margin.only(left=16, top=6, bottom=30),
                        ),
                    ]),
                    expand=True,
                ),

                ft.Container(height=30),
            ],
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=20),
        )

        self.controls = [
            settings_header,
            settings_content
        ]

    def _save_settings(self, e):
        settings = {
            "openai_api_key": self.openai_api_key.value,
            "openai_api_base": self.openai_api_base.value,
            "model_name": self.model_name.value,
            "serpapi_api_key": self.serpapi_api_key.value, # Add SerpApi key
        }
        if self.on_save:
            self.on_save(settings)

    def _go_back(self, e):
        if self.on_back:
            self.on_back(e)

# --- Chat Message UI (Mostly unchanged) ---
class Message:
    def __init__(self, user_name: str, text: str, message_type: str):
        self.user_name = user_name
        self.text = text
        self.message_type = message_type

class ChatMessage(ft.Container):
    def __init__(self, message: Message, page: ft.Page = None):
        super().__init__()
        self.animate = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT)
        self.padding = ft.padding.only(bottom=12)

        is_user = message.user_name == "You"
        is_system = message.user_name == "System"

        # Create content area
        if is_user:
            # User message: Right-aligned speech bubble
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=15, color=ft.Colors.BLACK87),
                bgcolor=ft.colors.with_opacity(0.05, ft.colors.BLACK), # Light grey bubble
                border_radius=18,
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
                # margin=ft.margin.only(left=60) # Removed unnecessary left margin for right-aligned item
            )
            alignment = ft.MainAxisAlignment.END
            self.content = ft.Row([content_area], alignment=alignment)

        elif is_system:
             # System message: Left-aligned, simple text
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=14, color=ft.Colors.BLACK54, italic=True),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                # margin=ft.margin.only(right=60) # Removed right margin
            )
            alignment = ft.MainAxisAlignment.START # Explicitly start
            # Set expand=True on the container within the Row
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

        else: # Assistant message
            # Assistant message: Left-aligned, no bubble
            content_area = ft.Container(
                 content=ft.Markdown(
                    value=message.text, selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                    code_theme=ft.MarkdownCodeTheme.GITHUB,
                    on_tap_link=lambda e: page.launch_url(e.data) if page else None,
                ),
                # Adjusted padding: left=20 to align with tool output box
                padding=ft.padding.only(left=20, right=16, top=4, bottom=4),
                # margin=ft.margin.only(right=60) # Removed right margin
            )
            alignment = ft.MainAxisAlignment.START
            # Set expand=True on the container within the Row to enable wrapping
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

# --- Main Application Logic ---

async def main(page: ft.Page):
    # Set up page properties
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.title = "IBJJF Assistant" + (" (Debug Mode)" if DEBUG_MODE else "")
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window_width = 900
    page.window_height = 750
    page.padding = 0
    page.bgcolor = ft.colors.with_opacity(0.98, ft.Colors.WHITE)
    page.fonts = {
        "SF Pro": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    }
    page.theme = ft.Theme(font_family="SF Pro, Inter, Helvetica, Arial, sans-serif")
    page.views.clear()
    page.adaptive = True

    # Client storage prefix
    prefix = "ibjjf_flet_agent."
    saved_settings = {}

    # Use the workaround for getting values with longer timeout
    async def get_with_longer_timeout(key, default_value=""):
        try:
            result = await page._invoke_method_async( # noqa: WPS437
                method_name="clientStorage:get",
                arguments={"key": key},
                wait_timeout=10,
                wait_for_result=True,
            )
            if result:
                return json.loads(json.loads(result))
            return default_value
        except Exception as e:
            print(f"Error getting {key}: {e}")
            return default_value

    # Load settings asynchronously
    async def load_settings_async():
        nonlocal saved_settings # saved_settings is local to main, so nonlocal is correct here
        global API_KEY, BASE_URL, MODEL_NAME, SERPAPI_API_KEY # Use global for module-level vars

        saved_settings["openai_api_key"] = await get_with_longer_timeout(f"{prefix}openai_api_key", "")
        saved_settings["openai_api_base"] = await get_with_longer_timeout(f"{prefix}openai_api_base", "https://api.openai.com/v1")
        saved_settings["model_name"] = await get_with_longer_timeout(f"{prefix}model_name", "gpt-4o")
        saved_settings["serpapi_api_key"] = await get_with_longer_timeout(f"{prefix}serpapi_api_key", "") # Load SerpApi key

        # Update global vars if settings were loaded
        if saved_settings["openai_api_key"]:
            API_KEY = saved_settings["openai_api_key"]
            print("Loaded OpenAI API Key from client storage")
        if saved_settings["openai_api_base"]:
            BASE_URL = saved_settings["openai_api_base"]
            print(f"Loaded OpenAI Base URL: {BASE_URL}")
        if saved_settings["model_name"]:
            MODEL_NAME = saved_settings["model_name"]
            print(f"Loaded Model Name: {MODEL_NAME}")
        if saved_settings["serpapi_api_key"]:
            SERPAPI_API_KEY = saved_settings["serpapi_api_key"] # Update global SerpApi key
            os.environ["SERPAPI_API_KEY"] = SERPAPI_API_KEY # Set environment variable
            print("Loaded SerpApi API Key from client storage and set environment variable")
        # Ensure env var is unset if key is empty/not loaded
        elif "SERPAPI_API_KEY" in os.environ:
            del os.environ["SERPAPI_API_KEY"]

        page.update()

    try:
        await load_settings_async()
    except Exception as e:
        print(f"Error loading from client storage: {e}")

    # Check for required API keys after loading
    if not API_KEY:
        print("Warning: No OpenAI API key found. Please configure settings.")
    if not SERPAPI_API_KEY:
        print("Warning: No SerpApi API key found. Web/YouTube/Scholar/Flights tools may not work. Please configure settings.")

    # Initialize conversation history
    conversation = []

    # Create a typing indicator
    typing_indicator = ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.ProgressRing(width=16, height=16, stroke_width=2),
                    margin=ft.margin.only(right=8)
                ),
                ft.Text("Assistant is thinking...", size=14, color=ft.Colors.BLACK54),
            ],
            alignment=ft.MainAxisAlignment.START,
        ),
        margin=ft.margin.only(left=20, bottom=12),
        animate=ft.animation.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        visible=False
    )

    # Create chat controls
    chat = ft.ListView(
        expand=True,
        spacing=0,
        auto_scroll=True,
        padding=ft.padding.only(left=20, right=20, top=24, bottom=24),
    )

    # Agent instance (created on first message)
    agent = None

    # List of available tools - Include imported and local tools
    available_tools = [
        geocode, get_weather, web_search, youtube_search, scholar_search,
        google_flights_search, ibjjf_events, ibjjf_brackets, ibjjf_competitors
    ]

    async def process_message(user_message: str):
        nonlocal agent # Allow modification of the agent instance

        print(f"process_message called with: {user_message}")

        typing_indicator.visible = True
        page.update()

        try:
            # Create agent on first message or if settings changed requiring re-init
            if agent is None:
                if not API_KEY:
                    raise ValueError("OpenAI API key is missing. Please configure your API key in settings.")
                # No check for SERPAPI_API_KEY here, agent instructions handle informing the user

                try:
                    # Use globally updated BASE_URL and API_KEY
                    client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
                    set_default_openai_client(client=client, use_for_tracing=False)
                except Exception as e:
                    raise ValueError(f"Failed to initialize OpenAI client: {str(e)}. Please check your API key and connection.")

                # Using standard Agent, no approval logic needed for now
                agent = Agent(
                    name="IBJJF Information Assistant",
                    tools=available_tools,
                    model=MODEL_NAME, # Use globally updated MODEL_NAME
                    model_settings=ModelSettings(tool_choice="auto"),
                    instructions=get_agent_instructions(),
                )
                print(f"Agent initialized with model: {MODEL_NAME}")


            # Run the agent
            if conversation:
                new_input = conversation + [{"role": "user", "content": user_message}]
                result = Runner.run_streamed(agent, new_input)
            else:
                result = Runner.run_streamed(agent, user_message)

            message_output = ""
            final_items = []

            # Process streaming results
            async for event in result.stream_events():
                if event.type == "raw_response_event": continue
                elif event.type == "agent_updated_stream_event": continue
                elif event.type == "run_item_stream_event":
                    final_items.append(event.item)

                    if event.item.type == "tool_call_item":
                        args_str = event.item.to_input_item()['arguments']
                        try:
                            args_dict = json.loads(args_str)
                            # Limit length of displayed args
                            formatted_args = ", ".join([f"{k}: {repr(v)[:50]}{'...' if len(repr(v)) > 50 else ''}" for k, v in args_dict.items()])
                        except:
                            formatted_args = args_str[:150] + ('...' if len(args_str) > 150 else '') # Limit raw string too

                        tool_name = event.item.to_input_item()['name']

                        if typing_indicator in chat.controls:
                            chat.controls.remove(typing_indicator)

                        tool_call_display = ToolCallDisplay(tool_name, formatted_args)
                        chat.controls.append(tool_call_display)
                        chat.controls.append(typing_indicator) # Add indicator back
                        page.update()

                    elif event.item.type == "tool_call_output_item":
                        if DEBUG_MODE:
                            tool_output = str(event.item.output)

                            if typing_indicator in chat.controls:
                                chat.controls.remove(typing_indicator)

                            output_display = ToolOutputDisplay(tool_output)
                            chat.controls.append(output_display)
                            chat.controls.append(typing_indicator) # Add indicator back
                            page.update()

                    elif event.item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(event.item)

            # Hide typing indicator
            typing_indicator.visible = False
            if typing_indicator in chat.controls:
                chat.controls.remove(typing_indicator)

            # Update conversation history
            for item in final_items:
                if hasattr(item, 'to_input_item'):
                    input_item = item.to_input_item()
                    if input_item:
                        conversation.append(input_item)

            # Display final assistant message
            if message_output:
                assistant_message = Message(
                    user_name="Assistant",
                    text=message_output,
                    message_type="assistant_message"
                )
                chat.controls.append(ChatMessage(assistant_message, page))
                chat.controls.append(ft.Container(height=12))
                page.update()

        except Exception as e:
            print(f"Error in process_message: {e}")
            typing_indicator.visible = False
            if typing_indicator in chat.controls:
                chat.controls.remove(typing_indicator)

            friendly_message = f"An error occurred: {str(e)}. Please check settings or logs."
            print(f"Displaying error: {friendly_message}")

            # Simple error banner
            error_banner = ft.Container(
                content=ft.Row([
                    ft.Icon(name=ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600, size=24),
                    ft.Text(
                        friendly_message, color=ft.Colors.BLACK87, size=14, expand=True
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_400, icon_size=18, tooltip="Dismiss",
                        on_click=lambda e: chat.controls.remove(error_banner) or page.update()
                    )
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=ft.Colors.RED_50, padding=ft.padding.symmetric(horizontal=16, vertical=12),
                border_radius=8, border=ft.border.all(1, ft.Colors.RED_200),
                margin=ft.margin.symmetric(horizontal=8, vertical=16),
            )

            # Add error banner at the top
            if len(chat.controls) > 0: chat.controls.insert(0, error_banner)
            else: chat.controls.append(error_banner)
            page.update()

    async def send_message_click(e):
        if new_message.value != "":
            user_msg = Message("You", new_message.value, "user_message")
            chat.controls.append(ChatMessage(user_msg, page))
            chat.controls.append(typing_indicator)

            message_text = new_message.value
            new_message.value = ""
            new_message.focus()
            page.update()

            await process_message(message_text)

    # --- Navigation and Settings Handling ---

    def create_settings_view():
        nonlocal agent # Allow agent reset on settings change
        current_settings = {
            "openai_api_key": API_KEY,
            "openai_api_base": BASE_URL,
            "model_name": MODEL_NAME,
            "serpapi_api_key": SERPAPI_API_KEY,
        }
        settings_view = SettingsScreen(
            on_save=lambda settings: handle_settings_save(settings),
            on_back=lambda _: page.go("/"),
            initial_values=current_settings
        )
        page.views.clear()
        page.views.append(settings_view)
        page.update()

    def go_to_settings(e):
        page.go("/settings")

    def handle_settings_save(new_settings):
        nonlocal agent # agent is local to main, so nonlocal is correct here
        global API_KEY, BASE_URL, MODEL_NAME, SERPAPI_API_KEY # Use global for module-level vars

        # Update global variables
        API_KEY = new_settings["openai_api_key"]
        BASE_URL = new_settings["openai_api_base"]
        MODEL_NAME = new_settings["model_name"]
        SERPAPI_API_KEY = new_settings["serpapi_api_key"]

        # Update environment variables for tools that read from os.environ
        os.environ["OPENAI_API_KEY"] = API_KEY # Also update OpenAI env var just in case
        os.environ["OPENAI_API_BASE"] = BASE_URL # Also update OpenAI env var just in case
        if SERPAPI_API_KEY:
            os.environ["SERPAPI_API_KEY"] = SERPAPI_API_KEY
            print("Set SERPAPI_API_KEY environment variable")
        elif "SERPAPI_API_KEY" in os.environ:
            # Remove env var if key is cleared in settings
            del os.environ["SERPAPI_API_KEY"]
            print("Cleared SERPAPI_API_KEY environment variable")

        # Save to client storage
        try:
            save_prefix = "ibjjf_flet_agent."
            page.client_storage.set(f"{save_prefix}openai_api_key", API_KEY)
            page.client_storage.set(f"{save_prefix}openai_api_base", BASE_URL)
            page.client_storage.set(f"{save_prefix}model_name", MODEL_NAME)
            page.client_storage.set(f"{save_prefix}serpapi_api_key", SERPAPI_API_KEY)
            print("Settings saved successfully")
            # Reset agent to force re-initialization with new settings on next message
            agent = None
            print("Agent reset due to settings change.")
        except Exception as e:
            print(f"Error saving settings: {e}")

        page.go("/") # Go back to main chat view

    # Settings button
    settings_button = ft.IconButton(
        icon=ft.Icons.SETTINGS,
        icon_color=ft.Colors.WHITE,
        icon_size=20,
        tooltip="API Settings",
        on_click=go_to_settings
    )

    # Create header
    header = ft.Container(
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text("IBJJF Assistant", color=ft.Colors.WHITE, size=22, weight="bold"),
                        ft.Text("Ask about events, brackets, or competitors", color=ft.Colors.with_opacity(0.8, ft.Colors.WHITE), size=14, weight="w400"),
                    ], spacing=2, tight=True),
                    settings_button, # Only settings button in header now
                ], spacing=12, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=ft.padding.symmetric(horizontal=20, vertical=16),
            ),
            # Debug indicator
            ft.Container(
                content=ft.Row([
                    ft.Icon(name=ft.Icons.BUG_REPORT, color=ft.Colors.WHITE, size=16),
                    ft.Text("Debug Mode Enabled", color=ft.Colors.WHITE, size=13),
                ], spacing=8),
                padding=ft.padding.symmetric(horizontal=20, vertical=8),
                bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                visible=DEBUG_MODE
            ) if DEBUG_MODE else ft.Container(height=0),
        ]),
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
            colors=[ft.Colors.BLUE_GREY_700, ft.Colors.BLUE_GREY_500, ft.Colors.TEAL_700], # New gradient
        ),
        shadow=ft.BoxShadow(
            spread_radius=0, blur_radius=8, color=ft.Colors.with_opacity(0.2, ft.Colors.BLACK), offset=ft.Offset(0, 2)
        ),
    )

    # Initial Welcome Message
    welcome_text = "👋 Hi! I'm the IBJJF Assistant. Ask me about upcoming tournaments, specific event divisions, or competitors. You can also ask me about weather, flights, or general web search."
    if DEBUG_MODE:
        welcome_text += " (Debug mode is active - tool outputs will be shown)"
    if not SERPAPI_API_KEY:
         welcome_text += "\n\n⚠️ Note: SerpApi key is not configured. Web search, YouTube, Scholar, and Flights tools might not work. Configure it in Settings."


    welcome_message = Message("System", welcome_text, "system_message")
    chat.controls.append(ChatMessage(welcome_message, page))
    chat.controls.append(ft.Container(height=12))

    # Chat input field
    new_message = ft.TextField(
        hint_text="Ask about IBJJF events or other info...",
        hint_style=ft.TextStyle(color=ft.Colors.BLACK45),
        autofocus=True, shift_enter=True, min_lines=1, max_lines=5, filled=True,
        border_color=ft.Colors.BLACK12, focused_border_color=ft.Colors.TEAL, # Theme color
        text_size=15, text_style=ft.TextStyle(color=ft.Colors.BLACK), cursor_color=ft.Colors.TEAL,
        expand=True, border_radius=24, content_padding=ft.padding.only(left=20, right=20, top=14, bottom=14),
        on_submit=send_message_click,
    )

    # Send button
    send_button = ft.IconButton(
        icon=ft.Icons.SEND_ROUNDED, icon_color=ft.Colors.TEAL, # Theme color
        icon_size=22, tooltip="Send message", on_click=send_message_click,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=30), padding=12, overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.TEAL)),
    )

    # --- View Creation and Routing ---

    def create_main_view():
        main_view = ft.View(
            route="/",
            controls=[
                header,
                ft.Container(
                    content=chat, expand=True, bgcolor=ft.colors.with_opacity(0.98, ft.Colors.WHITE),
                ),
                ft.Container(
                    content=ft.Row([new_message, send_button], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.only(left=16, right=16, top=12, bottom=16),
                    bgcolor=ft.Colors.WHITE, border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                ),
            ],
            padding=0, bgcolor=ft.Colors.WHITE
        )
        page.views.clear()
        page.views.append(main_view)
        page.update()

    def route_change(e):
        if e.route == "/settings": create_settings_view()
        elif e.route == "/": create_main_view()

    page.on_route_change = route_change
    create_main_view() # Initial view

    # Show initial warnings if keys are missing
    if not API_KEY:
        snack_bar = ft.SnackBar(ft.Text("Warning: OpenAI API Key missing. Go to Settings."), open=True)
        page.overlay.append(snack_bar)
        page.update()
    elif not SERPAPI_API_KEY: # Show only if OpenAI key is present but SerpApi is missing
        snack_bar = ft.SnackBar(ft.Text("Warning: SerpApi Key missing. Some tools may fail. Go to Settings."), open=True)
        page.overlay.append(snack_bar)
        page.update()


if __name__ == "__main__":
    ft.app(target=main)