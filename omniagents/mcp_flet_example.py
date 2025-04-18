import flet as ft
import os
import asyncio
import argparse
import json
from dotenv import load_dotenv
from datetime import datetime
from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    HandoffOutputItem,
    ItemHelpers,
    MessageOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled
)
from agents.mcp import MCPServerSse
from agents.model_settings import ModelSettings
from safe_agent import SafeAgent


# Load environment variables from .env file
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Get configuration from environment variables
# These will be overridden in the main function if settings are saved in a file
BASE_URL = os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME") or "gpt-4o"
MCP_URL = os.getenv("MCP_URL") or ""
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN")

# Disable tracing
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# Process pre-approved tools if provided
safe_tool_names = []
safe_tool_patterns = []

# Parse command line arguments
parser = argparse.ArgumentParser(description="80/20 Jiu-Jitsu Gym Support Agent with MCP and Flet UI")
parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode to show detailed tool outputs")
parser.add_argument("--skip-approvals", action="store_true", help="Skip all approval prompts while still using SafeAgent")
parser.add_argument("--pre-approve", type=str, help="Comma-separated list of tool names to pre-approve")
parser.add_argument("--safe-patterns", type=str, help="Comma-separated list of regex patterns for safe tools")
parser.add_argument("--no-halt", action="store_true", help="Don't halt execution when a tool is rejected")
args = parser.parse_args()

# Debug mode flag
DEBUG_MODE = args.debug
SKIP_APPROVALS = args.skip_approvals

# Process pre-approved tools if provided
if args.pre_approve:
    safe_tool_names = [tool.strip() for tool in args.pre_approve.split(",")]
    print(f"Pre-approved tools: {', '.join(safe_tool_names)}")

# Process safe tool patterns if provided
if args.safe_patterns:
    safe_tool_patterns = [pattern.strip() for pattern in args.safe_patterns.split(",")]
    print(f"Safe tool patterns: {', '.join(safe_tool_patterns)}")

if DEBUG_MODE:
    print("\n--- Debug Info ---")
    print(f"Safe tools: {safe_tool_names}")
    print(f"Safe patterns: {safe_tool_patterns}")
    print(f"Skip approvals: {SKIP_APPROVALS}")
    print(f"Halt on rejection: {not args.no_halt}")
    print("------------------\n")

def get_agent_instructions() -> str:
    """Get agent instructions with current date and time"""
    # Get current date and time
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""
    Current date and time: {current_datetime}
    
    You are Indie, the virtual assistant for 80/20 Jiu-Jitsu, a martial arts studio in Edinburg, TX. Your main goal is to help staff, students, prospects, and visitors with information and actions related to the gym.
    
    Key facts about 80/20 Jiu-Jitsu:
    - Located in Edinburg, Texas
    - Founded on the principle that 20% of techniques are responsible for 80% of success in Jiu-Jitsu
    - Focuses on teaching effective, practical techniques for self-defense and competition
    - Offers classes for all experience levels, from beginners to advanced practitioners
    
    You have access to various tools and resources through the connected MCP server. The server will provide you with information about what capabilities are available. Use these tools appropriately to assist users.
    
    Always be friendly, encouraging, and respectful. Address yourself as "Indie" when introducing yourself. Use terminology appropriate for Jiu-Jitsu and martial arts. Focus on the studio's 80/20 philosophy of focusing on the most effective techniques.
    
    Your primary job is to help the gym staff manage day-to-day operations, answer questions about the gym for potential customers, and assist existing members.
    
    Answer questions concisely and accurately using your available tools.
    """

# --- ToolCallDisplay: Modified to match IBJJF Styling ---
class ToolCallDisplay(ft.Container):
    def __init__(self, tool_name, args_str):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        # Match IBJJF margin
        self.margin = ft.margin.only(left=20, right=20, top=8, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Keep MCP's icon logic, but use IBJJF's color/layout style
        tool_icons = {
            "mcp__search": ft.Icons.SEARCH,
            "mcp__fetch": ft.Icons.CLOUD_DOWNLOAD,
            "mcp__weather": ft.Icons.CLOUD,
            "mcp__calendar": ft.Icons.CALENDAR_TODAY,
            "mcp__location": ft.Icons.LOCATION_ON,
        }
        icon = tool_icons.get(tool_name, ft.Icons.BUILD) # Default MCP icon

        # Create a card with tool info (layout matches IBJJF)
        tool_card = ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Icon(name=icon, size=16, color=ft.Colors.WHITE),
                    # Match IBJJF icon container style
                    width=28,
                    height=28,
                    border_radius=14,
                    bgcolor=ft.Colors.BLUE, # Match IBJJF color
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
                    ),
                ], spacing=2, tight=True, expand=True), # Added expand=True
            ], spacing=0),
            # Match IBJJF padding
            padding=ft.padding.only(left=12, top=12, bottom=12, right=16),
            border_radius=8,
            # Match IBJJF background
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.BLUE),
            # width=600, # Removed fixed width
        )

        self.content = tool_card

class ToolApprovalRequest(ft.Container):
    def __init__(self, tool_name, args_str, on_approve, on_approve_session, on_reject):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.margin = ft.margin.only(left=42, top=8, bottom=4)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)
        
        # Create a card with tool info and approval buttons
        self.content = ft.Container(
            content=ft.Column([
                ft.Text(
                    f"Approval required to use tool: {tool_name}",
                    weight="w500",
                    size=14,
                    color=ft.Colors.ORANGE,
                ),
                ft.Text(
                    f"Parameters: {args_str}",
                    size=13,
                    color=ft.Colors.BLACK54,
                ),
                ft.Container(height=10),
                ft.Row([
                    ft.ElevatedButton(
                        text="Approve Once",
                        icon=ft.Icons.CHECK,
                        color=ft.Colors.WHITE,
                        bgcolor=ft.Colors.GREEN,
                        on_click=on_approve,
                    ),
                    ft.ElevatedButton(
                        text="Approve For Session",
                        icon=ft.Icons.CHECK_CIRCLE,
                        color=ft.Colors.WHITE,
                        bgcolor=ft.Colors.BLUE,
                        on_click=on_approve_session,
                    ),
                    ft.ElevatedButton(
                        text="Reject",
                        icon=ft.Icons.CANCEL,
                        color=ft.Colors.WHITE,
                        bgcolor=ft.Colors.RED,
                        on_click=on_reject,
                    ),
                ], spacing=10),
            ]),
            padding=16,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ORANGE),
            border=ft.border.all(0.5, ft.Colors.ORANGE),
            width=600,
        )

# --- ToolOutputDisplay: Modified to match IBJJF Styling ---
class ToolOutputDisplay(ft.Container):
    def __init__(self, output_text):
        super().__init__()
        self.padding = 0
        # Match IBJJF margin (left=20)
        self.margin = ft.margin.only(left=20, right=20, top=4, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Truncate very long outputs (same logic)
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
            # Match IBJJF padding
            padding=ft.padding.only(top=12, bottom=12, right=16),
            border_radius=6,
            # Match IBJJF background and border
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
            border=ft.border.all(0.5, ft.Colors.BLACK12),
            # width=560, # Removed fixed width
        )

class SettingsScreen(ft.View):
    def __init__(self, on_save, on_back, initial_values=None):
        # Initialize View
        super().__init__()
        
        # Set View properties with mobile-friendly layout
        self.route = "/settings"
        self.bgcolor = ft.Colors.WHITE
        self.padding = ft.padding.all(0)
        
        # Store callbacks and initial values
        self.on_save = on_save
        self.on_back = on_back
        self.initial_values = initial_values or {
            "openai_api_key": "",
            "openai_api_base": "https://api.openai.com/v1",
            "model_name": "gpt-4o",
            "mcp_url": "http://localhost:3000/mcp/sse",
            "mcp_auth_token": "",
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
        
        self.mcp_url = ft.TextField(
            label="MCP Server URL",
            value=self.initial_values.get("mcp_url", ""),
            border_color=ft.Colors.BLUE_200,
            expand=True,
            hint_text="Enter MCP server URL",
        )
        
        self.mcp_auth_token = ft.TextField(
            label="MCP Auth Token (Optional)",
            value=self.initial_values.get("mcp_auth_token", ""),
            password=True,
            can_reveal_password=True,
            border_color=ft.Colors.BLUE_200,
            expand=True,
            hint_text="Only required if your MCP server needs authentication",
        )
        
        
        # Create solid color header that matches main screen gradient
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
            bgcolor=ft.Colors.INDIGO_900,
            padding=ft.padding.only(left=10, right=10, top=16, bottom=16),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.2, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
        )
        
        # Create scrollable content with padding for app bar
        settings_content = ft.ListView(
            controls=[
                # Top padding to avoid app bar overlap
                ft.Container(height=12),
                
                # Title
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
            
            # iOS-style grouped settings section for OpenAI API
            ft.Container(
                content=ft.Column([
                    # Section header
                    ft.Container(
                        content=ft.Text(
                            "OPENAI API",
                            size=12,
                            weight="w500",
                            color=ft.Colors.BLACK54,
                        ),
                        margin=ft.margin.only(left=16, bottom=6),
                    ),
                    
                    # Settings group with iOS-style appearance
                    ft.Container(
                        content=ft.Column([
                            # API Base URL setting
                            ft.Container(
                                content=self.openai_api_base,
                                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                            ),
                            
                            # API Key setting
                            ft.Container(
                                content=self.openai_api_key,
                                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                            ),
                            
                            # Model Name setting
                            ft.Container(
                                content=self.model_name,
                                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                            ),
                        ]),
                        bgcolor=ft.Colors.WHITE,
                        border_radius=10,
                        shadow=ft.BoxShadow(
                            spread_radius=0,
                            blur_radius=2,
                            color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
                            offset=ft.Offset(0, 1)
                        ),
                    ),
                    
                    # Footer text for section
                    ft.Container(
                        content=ft.Text(
                            "Your OpenAI API key and configuration settings",
                            size=12,
                            color=ft.Colors.BLACK45,
                        ),
                        margin=ft.margin.only(left=16, top=6, bottom=24),
                    ),
                ]),
                expand=True,
            ),
            
            # iOS-style grouped settings section for MCP Server
            ft.Container(
                content=ft.Column([
                    # Section header
                    ft.Container(
                        content=ft.Text(
                            "MCP SERVER",
                            size=12,
                            weight="w500",
                            color=ft.Colors.BLACK54,
                        ),
                        margin=ft.margin.only(left=16, bottom=6),
                    ),
                    
                    # Settings group with iOS-style appearance
                    ft.Container(
                        content=ft.Column([
                            # MCP URL setting
                            ft.Container(
                                content=self.mcp_url,
                                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                                border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                            ),
                            
                            # MCP Auth token setting
                            ft.Container(
                                content=self.mcp_auth_token,
                                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                            ),
                        ]),
                        bgcolor=ft.Colors.WHITE,
                        border_radius=10,
                        shadow=ft.BoxShadow(
                            spread_radius=0,
                            blur_radius=2,
                            color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
                            offset=ft.Offset(0, 1)
                        ),
                    ),
                    
                    # Footer text for section
                    ft.Container(
                        content=ft.Text(
                            "MCP server connection details (Auth Token is optional depending on your server)",
                            size=12,
                            color=ft.Colors.BLACK45,
                        ),
                        margin=ft.margin.only(left=16, top=6, bottom=30),
                    ),
                ]),
                expand=True,
            ),
            
            # Add bottom spacing
            ft.Container(height=30),
        ],
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=20),
        )
        
        # Add content to view with header above the scrollable content
        self.controls = [
            settings_header,
            settings_content
        ]
    
    def _save_settings(self, e):
        # Collect settings
        settings = {
            "openai_api_key": self.openai_api_key.value,
            "openai_api_base": self.openai_api_base.value,
            "model_name": self.model_name.value,
            "mcp_url": self.mcp_url.value,
            "mcp_auth_token": self.mcp_auth_token.value
        }
        
        # Call the save callback with the settings directly (synchronously)
        if self.on_save:
            self.on_save(settings)
    
    def _go_back(self, e):
        if self.on_back:
            self.on_back(e)

# Settings are now stored in client storage rather than in files
# This approach works across all platforms (iOS, Android, Web, Desktop)
# and avoids file system permission issues on mobile devices

class Message:
    def __init__(self, user_name: str, text: str, message_type: str):
        self.user_name = user_name
        self.text = text
        self.message_type = message_type

# --- Modified ChatMessage to match IBJJF styling ---
class ChatMessage(ft.Container):
    def __init__(self, message: Message, page: ft.Page = None):
        super().__init__()
        self.animate = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT)
        # Match IBJJF padding
        self.padding = ft.padding.only(bottom=12)

        is_user = message.user_name == "You"
        is_system = message.user_name == "System"

        # Create content area based on IBJJF logic
        if is_user:
            # User message: Right-aligned speech bubble (like IBJJF)
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=15, color=ft.Colors.BLACK87),
                bgcolor=ft.colors.with_opacity(0.05, ft.colors.BLACK), # Light grey bubble
                border_radius=18,
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
            )
            alignment = ft.MainAxisAlignment.END
            # Use Row for alignment
            self.content = ft.Row([content_area], alignment=alignment)

        elif is_system:
             # System message: Left-aligned, simple text (like IBJJF)
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=14, color=ft.Colors.BLACK54, italic=True),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
            )
            alignment = ft.MainAxisAlignment.START
            # Use Row with expanded inner Container for wrapping
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

        else: # Assistant message ("Indie" in MCP context)
            # Assistant message: Left-aligned, no bubble (like IBJJF)
            content_area = ft.Container(
                 content=ft.Markdown(
                    value=message.text, selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                    code_theme=ft.MarkdownCodeTheme.GITHUB,
                    on_tap_link=lambda e: page.launch_url(e.data) if page else None,
                ),
                # Match IBJJF padding for Assistant
                padding=ft.padding.only(left=20, right=16, top=4, bottom=4),
            )
            alignment = ft.MainAxisAlignment.START
            # Use Row with expanded inner Container for wrapping
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

    # Helper methods removed as they are not needed for IBJJF styling
    # def _get_initials(self, user_name: str): ...
    # def _get_avatar_color(self, user_name: str): ...
    # def _create_shadow(self, opacity=0.1): ...


async def main(page: ft.Page):
    # Set up page properties for a mobile-friendly design
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.title = "Indie - 80/20 Jiu-Jitsu Assistant" + (" (Debug Mode)" if DEBUG_MODE else "")
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window_width = 900
    page.window_height = 750
    page.padding = 0
    page.bgcolor = ft.Colors.with_opacity(0.98, ft.Colors.WHITE)
    page.fonts = {
        "SF Pro": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    }
    page.theme = ft.Theme(font_family="SF Pro, Inter, Helvetica, Arial, sans-serif")
    
    # Enable adaptive UI for mobile-like experience
    page.views.clear()
    page.adaptive = True  # This enables platform-adaptive controls
    
    # Use client storage only - works across all platforms
    # On desktop platforms, Flet prepends "flutter." to all keys
    prefix = "openai_agents."
    saved_settings = {}
    
    # Use the workaround suggested in Discord for getting values with longer timeout
    async def get_with_longer_timeout(key, default_value=""):
        try:
            # Direct invoke with longer timeout (10 seconds)
            result = await page._invoke_method_async(  # noqa: WPS437
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
        nonlocal saved_settings
        # Using the Discord workaround with longer timeout
        saved_settings["openai_api_key"] = await get_with_longer_timeout(f"{prefix}openai_api_key", "")
        saved_settings["openai_api_base"] = await get_with_longer_timeout(f"{prefix}openai_api_base", "https://api.openai.com/v1")
        saved_settings["model_name"] = await get_with_longer_timeout(f"{prefix}model_name", "gpt-4o")
        saved_settings["mcp_url"] = await get_with_longer_timeout(f"{prefix}mcp_url", "")
        saved_settings["mcp_auth_token"] = await get_with_longer_timeout(f"{prefix}mcp_auth_token", "")
        
        # Only consider settings loaded if we have an API key
        if saved_settings["openai_api_key"]:
            print("Loaded settings from client storage")
    
    try:
        # Run the async function to load settings
        await load_settings_async()
    except Exception as e:
        print(f"Error loading from client storage: {e}")
    
    # Initialize with saved settings or environment variables
    API_KEY = saved_settings.get("openai_api_key") or os.getenv("OPENAI_API_KEY", "")
    BASE_URL = saved_settings.get("openai_api_base") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
    MODEL_NAME = saved_settings.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o"
    MCP_URL = saved_settings.get("mcp_url") or os.getenv("MCP_URL", "")
    MCP_AUTH_TOKEN = saved_settings.get("mcp_auth_token") or os.getenv("MCP_AUTH_TOKEN", "")
    
    # Check for required API key
    if not API_KEY:
        print("Warning: No API key found. Please configure settings.")
    
    # Only initialize MCP params if URL is provided
    mcp_params = None
    if MCP_URL:
        print(f"Connecting to MCP server at: {MCP_URL}")
        mcp_params = {
            "url": MCP_URL,
            # Add timeout settings
            "timeout": 30,  # HTTP timeout in seconds (default is 5)
            "sse_read_timeout": 600,  # SSE read timeout in seconds (default is 5 min)
        }
        
        # Add authorization header if token is available
        if MCP_AUTH_TOKEN:
            mcp_params["headers"] = {
                "Authorization": f"Bearer {MCP_AUTH_TOKEN}"
            }
            print(f"Using auth token: {MCP_AUTH_TOKEN}")
        else:
            print("No MCP_AUTH_TOKEN provided - this is OK if your MCP server doesn't require authentication")
    else:
        print("No MCP server URL configured. Running without MCP capabilities.")
    
    # Create a connection status indicator and settings dialog variables
    connection_status = ft.Container(
        width=10,
        height=10,
        border_radius=5,
        bgcolor=ft.Colors.ORANGE
    )
    
    # Create connection status indicator
    connection_status = ft.Container(
        width=10,
        height=10,
        border_radius=5,
        bgcolor=ft.Colors.ORANGE
    )
    
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
    
    # Create tool approval state for the current run
    class ApprovalState:
        def __init__(self):
            self.active_approval_displays = {}
            self.approval_events = {}  # Store Future objects to wait for approvals
            
        def reset(self):
            self.active_approval_displays = {}
            self.approval_events = {}
            
    approval_state = ApprovalState()
    
    # We'll use the same approach as mcp_example.py
    # Creating a new agent for each message with pre-approved tools
    
    # Implement approval callback for SafeAgent
    async def flet_approval_callback(tool_name: str, formatted_args: str):
        """Flet UI implementation for tool approval.
        
        This callback is called by SafeAgent when a tool requires approval.
        
        Returns:
            tuple: (approved, always_approve, error_message)
        """
        # Create a unique ID for this tool call
        tool_call_id = f"tool_call_{len(approval_state.approval_events)}"
        
        # Create a Future to wait for approval
        approval_future = asyncio.get_event_loop().create_future()
        approval_state.approval_events[tool_call_id] = approval_future
        
        # Remove typing indicator before adding approval request
        if typing_indicator in chat.controls:
            chat.controls.remove(typing_indicator)
            
        # Create approval request display handlers
        def approve_once(e):
            # Remove the approval display
            display = approval_state.active_approval_displays.get(tool_call_id)
            if display and display in chat.controls:
                chat.controls.remove(display)
            
            # Update the UI
            page.update()
            
            # Resolve the future with approval status (approved=True, always_approve=False)
            approval_state.approval_events[tool_call_id].set_result((True, False, None))
        
        def approve_session(e):
            # Remove the approval display
            display = approval_state.active_approval_displays.get(tool_call_id)
            if display and display in chat.controls:
                chat.controls.remove(display)
            
            # Update the UI
            page.update()
            
            # Resolve the future with session approval (approved=True, always_approve=True)
            approval_state.approval_events[tool_call_id].set_result((True, True, None))
            
        def reject(e):
            # Remove the approval display
            display = approval_state.active_approval_displays.get(tool_call_id)
            if display and display in chat.controls:
                chat.controls.remove(display)
            
            # Add a rejection notice
            chat.controls.append(ft.Container(
                content=ft.Text(
                    f"❌ Tool call to {tool_name} was rejected",
                    color=ft.Colors.RED,
                    weight="bold",
                    size=14
                ),
                margin=ft.margin.only(left=80, top=4, bottom=12),
            ))
            
            # Update the UI
            page.update()
            
            # Resolve the future with rejection (approved=False)
            approval_state.approval_events[tool_call_id].set_result((False, False, "User rejected tool call"))
        
        # Create approval request UI
        approval_display = ToolApprovalRequest(
            tool_name=tool_name,
            args_str=formatted_args,
            on_approve=approve_once,
            on_approve_session=approve_session,
            on_reject=reject
        )
        
        # Store the display for later removal
        approval_state.active_approval_displays[tool_call_id] = approval_display
        
        # Add approval display
        chat.controls.append(approval_display)
        page.update()
        
        # Wait for user decision and extract the approval status
        result = await approval_future
        
        # Return approval status tuple: (approved, always_approve, error_message)
        return result
    
    # Create persistent objects to maintain state between messages
    agent = None
    mcp_server = None
    
    async def process_message(user_message: str):
        nonlocal agent, mcp_server
        
        print(f"process_message called with: {user_message}")
        
        # Show typing indicator
        typing_indicator.visible = True
        page.update()
        
        # Reset UI approval state for new message
        approval_state.reset()
        
        try:
            # For the first message, create an agent (and optional MCP connection)
            # For subsequent messages, reuse the same agent (preserving approvals)
            if agent is None:
                # Check if API key is available
                if not API_KEY:
                    print("No API key set, raising exception")
                    raise ValueError("OpenAI API key is missing. Please configure your API key in settings.")
                
                # Create custom client with specified base URL using stored settings
                try:
                    client = AsyncOpenAI(
                        base_url=BASE_URL, 
                        api_key=API_KEY,
                    )
                    
                    # Set as default client and configure API method
                    set_default_openai_client(client=client, use_for_tracing=False)
                except Exception as e:
                    raise ValueError(f"Failed to initialize OpenAI client: {str(e)}. Please check your API key and connection.")
                
                # Configure approval callback
                approval_status = "enabled"
                if SKIP_APPROVALS:
                    approval_status = "enabled (but skipped)"
                    approval_callback = SafeAgent.auto_approval_callback
                else:
                    approval_callback = flet_approval_callback
                
                # Initialize MCP server if params are available
                mcp_servers = []
                if mcp_params:
                    try:
                        # Initial creation of MCP server
                        print(f"Connecting to MCP server at: {MCP_URL}")
                        mcp_server = MCPServerSse(
                            name="80/20 Jiu-Jitsu MCP Server",
                            params=mcp_params,
                        )
                        
                        # Enter the MCP server context
                        await mcp_server.__aenter__()
                        
                        # Add to MCP servers list
                        mcp_servers.append(mcp_server)
                        
                        # Update connection status
                        connection_status.bgcolor = ft.Colors.GREEN
                        page.update()
                    except Exception as e:
                        print(f"Failed to connect to MCP server: {e}")
                        connection_status.bgcolor = ft.Colors.RED
                        page.update()
                else:
                    # Set connection status to orange for no MCP
                    connection_status.bgcolor = ft.Colors.ORANGE
                    page.update()
                    
                print(f"Tool approval {approval_status}")
                agent = SafeAgent(
                    name="Indie - 80/20 Jiu-Jitsu Support",
                    mcp_servers=mcp_servers,
                    model=MODEL_NAME,
                    model_settings=ModelSettings(tool_choice="auto"),
                    instructions=get_agent_instructions(),
                    safe_tool_names=safe_tool_names,
                    safe_tool_patterns=safe_tool_patterns,
                    debug_mode=DEBUG_MODE,
                    halt_on_rejection=not args.no_halt,
                    approval_callback=approval_callback,
                    skip_approvals=SKIP_APPROVALS
                )
                
            # For all messages, we use the same agent regardless of whether it's new or existing
            # The agent will remember which tools were approved for the session
                
            # Use the agent with the conversation history
            if conversation:
                # Add the new user message to the existing conversation
                new_input = conversation + [{"role": "user", "content": user_message}]
                result = Runner.run_streamed(agent, new_input)
            else:
                # First message in the conversation
                result = Runner.run_streamed(agent, user_message)
            
            message_output = ""
            final_items = []
            
            # Process the streaming results
            async for event in result.stream_events():
                # We'll ignore certain event types
                if event.type == "raw_response_event":
                    continue
                elif event.type == "agent_updated_stream_event":
                    continue
                elif event.type == "run_item_stream_event":
                    final_items.append(event.item)
                    
                    if event.item.type == "tool_call_item":
                        # Process tool call
                        args_str = event.item.to_input_item()['arguments']
                        try:
                            args_dict = json.loads(args_str)
                            formatted_args = ", ".join([f"{k}: {repr(v)}" for k, v in args_dict.items()])
                        except:
                            formatted_args = args_str
                        
                        tool_name = event.item.to_input_item()['name']
                        
                        # SafeAgent will handle approval via callback, so we just show the tool call
                        # Remove typing indicator before adding tool call
                        if typing_indicator in chat.controls:
                            chat.controls.remove(typing_indicator)
                            
                        # For SafeAgent, the flet_approval_callback will handle creating proper UI 
                        # elements. For non-SafeAgent, or for pre-approved tools, we show tool info directly
                        is_pre_approved = False
                        # Check direct name match
                        if tool_name in agent._safe_tool_names:
                            is_pre_approved = True
                        else:
                            # Check pattern match
                            for pattern in agent._safe_tool_patterns:
                                if pattern.match(tool_name):
                                    is_pre_approved = True
                                    break
                                        
                        # Display all tools - whether they're pre-approved or just approved for this call
                        # We need to show ALL tool calls, not just pre-approved ones
                        # The approval UI only handles getting approval, not displaying the tool call
                        # Add tool call display for all tools
                        tool_call_display = ToolCallDisplay(tool_name, formatted_args)
                        chat.controls.append(tool_call_display)
                        chat.controls.append(typing_indicator)
                        page.update()
                        
                    elif event.item.type == "tool_call_output_item":
                        # Debug mode shows tool outputs
                        if DEBUG_MODE:
                            tool_output = str(event.item.output)
                            
                            # Check if this is a rejection response from SafeAgent
                            try:
                                output_data = json.loads(tool_output)
                                if isinstance(output_data, dict) and output_data.get("error") == "TOOL_REJECTED":
                                    if DEBUG_MODE:
                                        print(f"Detected tool rejection response: {tool_output}")
                                    continue
                            except (json.JSONDecodeError, TypeError, AttributeError):
                                pass  # Not JSON or not our rejection format
                            
                            # Remove typing indicator before adding output
                            if typing_indicator in chat.controls:
                                chat.controls.remove(typing_indicator)
                                
                            # Add output display
                            output_display = ToolOutputDisplay(tool_output)
                            chat.controls.append(output_display)
                            
                            # Add typing indicator back
                            chat.controls.append(typing_indicator)
                            page.update()
                        
                    elif event.item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(event.item)
                        
                    # Check if the run is halted due to rejection
                    if agent._halt_run and event.item.type.endswith("_output_item"):
                        if DEBUG_MODE:
                            print(f"Detected halted run due to tool rejection")
                        break  # Exit the event loop early
            
            # Hide typing indicator
            typing_indicator.visible = False
            if typing_indicator in chat.controls:
                chat.controls.remove(typing_indicator)
            
            # Update the conversation history with new items
            for item in final_items:
                if hasattr(item, 'to_input_item'):
                    input_item = item.to_input_item()
                    if input_item:
                        conversation.append(input_item)
            
            # Display the final output based on whether run was halted
            if agent._halt_run:
                # Reset the halt flag for the next run
                agent._halt_run = False
                agent._halt_reason = None
                
                # Add a message that explains the run was halted
                system_message = Message(
                    user_name="System",
                    text="The assistant stopped responding due to a rejected tool call.",
                    message_type="system_message"
                )
                chat.controls.append(ChatMessage(system_message, page))
                chat.controls.append(ft.Container(height=16))
                page.update()
                
            # Display the final assistant message if available
            elif message_output:
                assistant_message = Message(
                    user_name="Indie",
                    text=message_output,
                    message_type="assistant_message"
                )
                chat.controls.append(ChatMessage(assistant_message, page))
                chat.controls.append(ft.Container(height=16))  # Add space after the last message
                page.update()
                
        except Exception as e:
            # Print error for debugging
            print(f"Error in process_message: {e}")
            
            # Hide typing indicator in case of error
            typing_indicator.visible = False
            if typing_indicator in chat.controls:
                chat.controls.remove(typing_indicator)
            
            # Update connection status
            connection_status.bgcolor = ft.Colors.RED
            
            # Create a simple error message without specific error details
            print(f"Error in process_message: {e}")
            friendly_message = "An error occurred while processing your message. Please check your settings."
            
            # Show error using a visible error banner instead of a snackbar
            print(f"Displaying error: {friendly_message}")
            
            # Create a simple error banner
            error_banner = ft.Container(
                content=ft.Row([
                    ft.Icon(name=ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600, size=24),
                    ft.Text(
                        friendly_message,
                        color=ft.Colors.BLACK87,
                        size=14,
                        expand=True
                    ),
                    ft.OutlinedButton(
                        "Settings",
                        on_click=go_to_settings,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=8),
                            color=ft.Colors.RED_600,
                            side=ft.BorderSide(1, ft.Colors.RED_400),
                            padding=ft.padding.symmetric(horizontal=12, vertical=8),
                            overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.RED),
                        ),
                        height=36
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_color=ft.Colors.RED_400,
                        icon_size=18,
                        tooltip="Dismiss",
                        style=ft.ButtonStyle(
                            shape=ft.CircleBorder(),
                            padding=4,
                            overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.RED),
                        ),
                        on_click=lambda e: chat.controls.remove(error_banner) or page.update()
                    )
                ], 
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=ft.Colors.RED_50,
                padding=ft.padding.symmetric(horizontal=16, vertical=12),
                border_radius=8,
                border=ft.border.all(1, ft.Colors.RED_200),
                margin=ft.margin.symmetric(horizontal=8, vertical=16),
            )
            
            # Add error banner at the top of the chat
            if len(chat.controls) > 0:
                chat.controls.insert(0, error_banner)
            else:
                chat.controls.append(error_banner)
                
            page.update()

    async def send_message_click(e):
        if new_message.value != "":
            # Display user message
            user_message = Message(
                user_name="You",
                text=new_message.value,
                message_type="user_message"
            )
            chat.controls.append(ChatMessage(user_message, page))
            chat.controls.append(typing_indicator)
            
            # Store message text and clear input field
            message_text = new_message.value
            new_message.value = ""
            new_message.focus()
            page.update()
            
            # Process message with the agent
            await process_message(message_text)

    # Navigation functions
    def go_to_settings(e):
        page.go("/settings")
            
    def create_settings_view():
        # Get current settings
        current_settings = {
            "openai_api_key": API_KEY,
            "openai_api_base": BASE_URL,
            "model_name": MODEL_NAME,
            "mcp_url": MCP_URL,
            "mcp_auth_token": MCP_AUTH_TOKEN
        }
        
        # Create settings screen
        settings_view = SettingsScreen(
            on_save=lambda settings: handle_settings_save(settings),
            on_back=lambda _: page.go("/"),
            initial_values=current_settings
        )
        
        # Set the page view
        page.views.clear()
        page.views.append(settings_view)
        page.update()
    
    def go_to_settings(e):
        page.go("/settings")
    
    def handle_settings_save(new_settings):
        nonlocal API_KEY, BASE_URL, MODEL_NAME, MCP_URL, MCP_AUTH_TOKEN
        
        # Update variables with new settings
        API_KEY = new_settings["openai_api_key"]
        BASE_URL = new_settings["openai_api_base"]
        MODEL_NAME = new_settings["model_name"]
        MCP_URL = new_settings["mcp_url"]
        MCP_AUTH_TOKEN = new_settings["mcp_auth_token"]
        
        # Save to client storage - works across all platforms
        try:
            # When saving, just use the base prefix - Flet will automatically add "flutter." on desktop
            save_prefix = "openai_agents."
            page.client_storage.set(f"{save_prefix}openai_api_key", API_KEY)
            page.client_storage.set(f"{save_prefix}openai_api_base", BASE_URL)
            page.client_storage.set(f"{save_prefix}model_name", MODEL_NAME)
            page.client_storage.set(f"{save_prefix}mcp_url", MCP_URL)
            page.client_storage.set(f"{save_prefix}mcp_auth_token", MCP_AUTH_TOKEN)
            print("Settings saved successfully")
        except Exception as e:
            print(f"Error saving settings: {e}")
            
        # Go back to main view
        page.go("/")
    
    # Add a settings button
    settings_button = ft.IconButton(
        icon=ft.Icons.SETTINGS,
        icon_color=ft.Colors.WHITE,
        icon_size=20,
        tooltip="API Settings",
        on_click=go_to_settings
    )
    
    # Create header with fancy gradient
    header = ft.Container(
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text(
                            "Indie",
                            color=ft.Colors.WHITE,
                            size=22,
                            weight="bold",
                        ),
                        ft.Text(
                            "80/20 Jiu-Jitsu Assistant",
                            color=ft.Colors.with_opacity(0.8, ft.Colors.WHITE),
                            size=14,
                            weight="w400",
                        ),
                    ], spacing=2, tight=True),
                    ft.Row([
                        settings_button,
                        connection_status,
                    ], spacing=12),
                ], spacing=12, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=ft.padding.symmetric(horizontal=20, vertical=16),
            ),
            # Create debug indicator at the bottom of the header if enabled
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
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=[
                ft.Colors.INDIGO_900,  # Darker blue for Jiu-Jitsu theme
                ft.Colors.INDIGO_700,
                ft.Colors.BLUE_900,
            ],
        ),
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            color=ft.Colors.with_opacity(0.2, ft.Colors.BLACK),
            offset=ft.Offset(0, 2)
        ),
    )
    
    # Create a banner for notifications like missing API key
    api_key_banner = ft.Banner(
        bgcolor=ft.Colors.AMBER_100,
        leading=ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.AMBER, size=40),
        content=ft.Text(
            "OpenAI API key is missing. Please configure your API key in settings.",
            color=ft.Colors.BLACK87,
        ),
        actions=[
            ft.TextButton("Open Settings", on_click=go_to_settings),
            ft.TextButton("Dismiss", on_click=lambda e: page.close(api_key_banner)),
        ],
    )
    
    # Remove temporary connection status and use the one in the header
    page.controls.clear()
    
    # Set welcome message
    welcome_text = "👋 Welcome to 80/20 Jiu-Jitsu! I'm Indie, your gym assistant. I can help with information about classes, schedules, membership options, and more. How can I assist you today?"
    if DEBUG_MODE:
        welcome_text += " (Debug mode is active - you'll see detailed tool outputs)"
    
    welcome_message = Message(
        user_name="System",
        text=welcome_text,
        message_type="system_message"
    )
    
    # Add welcome message to chat
    chat.controls.append(ChatMessage(welcome_message, page))
    chat.controls.append(ft.Container(height=16))  # Add space after the welcome message

    # Create chat input with modern styling
    new_message = ft.TextField(
        hint_text="Message the assistant...",
        hint_style=ft.TextStyle(color=ft.Colors.BLACK45),
        autofocus=True,
        shift_enter=True,
        min_lines=1,
        max_lines=5,
        filled=True,
        border_color=ft.Colors.BLACK12,
        focused_border_color=ft.Colors.BLUE,
        text_size=15,
        text_style=ft.TextStyle(color=ft.Colors.BLACK),
        cursor_color=ft.Colors.BLUE,
        expand=True,
        border_radius=24,
        content_padding=ft.padding.only(left=20, right=20, top=14, bottom=14),
        on_submit=send_message_click,
    )

    send_button = ft.IconButton(
        icon=ft.Icons.SEND_ROUNDED,
        icon_color=ft.Colors.BLUE,
        icon_size=22,
        tooltip="Send message",
        on_click=send_message_click,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=30),
            padding=12,
            overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.BLUE),
        ),
    )

    # Create the main view function
    def create_main_view():
        # Create the main view with original layout
        main_view = ft.View(
            route="/",
            controls=[
                header,
                ft.Container(
                    content=chat,
                    expand=True,
                    bgcolor=ft.Colors.with_opacity(0.98, ft.Colors.WHITE),
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            new_message,
                            send_button,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.only(left=16, right=16, top=12, bottom=16),
                    bgcolor=ft.Colors.WHITE,
                    border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                ),
            ],
            padding=0,
            bgcolor=ft.Colors.WHITE
        )
        
        # Set the view
        page.views.clear()
        page.views.append(main_view)
        page.update()
    
    # Define route_change handler
    def route_change(e):
        if e.route == "/settings":
            # Show settings view
            create_settings_view()
        elif e.route == "/":
            # Show main view
            create_main_view()
    
    # Configure routing
    page.views.clear()
    page.on_route_change = route_change
    
    # Initialize the app with the main view
    create_main_view()
    
    # No startup warnings - we'll show errors only when exceptions are caught
    
    # Add a cleanup handler to properly close MCP connection when app exits
    async def page_cleanup():
        if mcp_server is not None and hasattr(mcp_server, '__aexit__'):
            try:
                await mcp_server.__aexit__(None, None, None)
                print("MCP server connection closed")
            except Exception as e:
                print(f"Error closing MCP connection: {e}")
    
    page.on_close = page_cleanup

if __name__ == "__main__":
    ft.app(target=main)
