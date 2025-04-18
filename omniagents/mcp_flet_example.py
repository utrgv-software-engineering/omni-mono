import flet as ft
import os
import sys # To read command line args
import asyncio
import argparse
import json
from dotenv import load_dotenv
from datetime import datetime
from openai import AsyncOpenAI
from typing import Dict, Any, List, Optional, Tuple
from functools import partial # Import partial

from agents import (
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled
)
from agents.mcp import MCPServerSse
from agents.model_settings import ModelSettings
from safe_agent import SafeAgent # Use SafeAgent

# Import core components
from flet_agent_core.app_base import AgentApp, AgentSpec
from flet_agent_core.settings import SettingField
# Import only necessary UI components (AppBase handles others)
from flet_agent_core.ui import ChatMessage, ToolCallDisplay, ToolOutputDisplay, Message


# --- Configuration & Setup ---

# Load environment variables first
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Parse command line arguments
parser = argparse.ArgumentParser(description="80/20 Jiu-Jitsu Gym Support Agent with MCP and Flet UI")
parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode to show detailed tool outputs")
parser.add_argument("--skip-approvals", action="store_true", help="Skip all approval prompts while still using SafeAgent")
parser.add_argument("--pre-approve", type=str, help="Comma-separated list of tool names to pre-approve")
parser.add_argument("--safe-patterns", type=str, help="Comma-separated list of regex patterns for safe tools")
parser.add_argument("--no-halt", action="store_true", help="Don't halt execution when a tool is rejected")
args = parser.parse_args()

# Flags from arguments
DEBUG_MODE = args.debug
SKIP_APPROVALS = args.skip_approvals
HALT_ON_REJECTION = not args.no_halt

# Process pre-approved tools/patterns
safe_tool_names = [tool.strip() for tool in args.pre_approve.split(",")] if args.pre_approve else []
safe_tool_patterns = [pattern.strip() for pattern in args.safe_patterns.split(",")] if args.safe_patterns else []

if DEBUG_MODE:
    print("\n--- Indie - 80/20 Jiu-Jitsu Assistant ---")
    print(f"Debug mode: {DEBUG_MODE}")
    print(f"Skip approvals: {SKIP_APPROVALS}")
    print(f"Halt on rejection: {HALT_ON_REJECTION}")
    print(f"Pre-approved tools: {safe_tool_names}")
    print(f"Safe tool patterns: {safe_tool_patterns}")
    print("-----------------------------------------")

# Disable tracing
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# --- MCP & Agent Specific Components ---

# Global state for MCP connection and approval flow (managed by AgentApp lifecycle)
mcp_server_instance: Optional[MCPServerSse] = None
approval_state = {
    "active_approval_displays": {},
    "approval_events": {}
}

# UI for Tool Approval Request (Specific to SafeAgent/MCP flow)
class ToolApprovalRequest(ft.Container):
    def __init__(self, tool_name, args_str, on_approve, on_approve_session, on_reject):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        # Use standard margins consistent with other UI elements
        self.margin = ft.margin.only(left=20, right=20, top=8, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)
        
        self.content = ft.Container(
            content=ft.Column([
                ft.Text(
                    f"Approval required: {tool_name}",
                    weight="w500", size=14, color=ft.Colors.ORANGE_800,
                ),
                ft.Text(
                    f"Parameters: {args_str}",
                    size=13, color=ft.Colors.BLACK54, selectable=True,
                ),
                ft.Container(height=10),
                ft.Row([
                    ft.ElevatedButton(
                        text="Approve Once", icon=ft.Icons.CHECK,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.GREEN_600,
                        on_click=on_approve,
                        height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                    ft.ElevatedButton(
                        text="Approve Session", icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.BLUE_600,
                        on_click=on_approve_session,
                         height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                    ft.ElevatedButton(
                        text="Reject", icon=ft.Icons.CANCEL_OUTLINED,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_600,
                        on_click=on_reject,
                         height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                ], spacing=10, wrap=True), # Allow buttons to wrap on smaller screens
            ]),
            padding=16,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ORANGE),
            border=ft.border.all(1, ft.Colors.ORANGE_200),
            # Removed fixed width for responsiveness
        )

async def flet_approval_callback(tool_name: str, formatted_args: str, app: AgentApp) -> Tuple[bool, bool, Optional[str]]:
    """Flet UI implementation for SafeAgent tool approval."""
    global approval_state
    app_instance = app

    if not app_instance or not app_instance.chat or not app_instance.page:
        print("Error: Flet page/app context not available for approval callback.")
        return False, False, "Internal UI error during approval."

    tool_call_id = f"tool_call_{len(approval_state['approval_events'])}"
    approval_future = asyncio.get_event_loop().create_future()
    approval_state['approval_events'][tool_call_id] = approval_future

    # Access chat and typing_indicator via app_instance
    chat_list = app_instance.chat
    typing_indicator = app_instance.typing_indicator

    if typing_indicator in chat_list.controls:
        chat_list.controls.remove(typing_indicator)

    def create_handler(approved: bool, always_approve: bool, rejection_message: Optional[str] = None):
        def handler(e):
            display = approval_state['active_approval_displays'].get(tool_call_id)
            if display and display in chat_list.controls:
                chat_list.controls.remove(display)
                del approval_state['active_approval_displays'][tool_call_id]

            if not approved:
                 chat_list.controls.append(ft.Container(
                content=ft.Text(
                        f"❌ Tool call ({tool_name}) rejected by user.",
                        color=ft.Colors.RED_700, italic=True, size=13
                    ),
                    margin=ft.margin.only(left=20, top=0, bottom=8),
                 ))

            app_instance.page.update()
            if not approval_future.done():
                 approval_future.set_result((approved, always_approve, rejection_message))
            # Clean up event reference
            if tool_call_id in approval_state['approval_events']:
                del approval_state['approval_events'][tool_call_id]
        return handler

    approval_display = ToolApprovalRequest(
        tool_name=tool_name,
        args_str=formatted_args,
        on_approve=create_handler(True, False),
        on_approve_session=create_handler(True, True),
        on_reject=create_handler(False, False, "User rejected tool call")
    )

    approval_state['active_approval_displays'][tool_call_id] = approval_display
    chat_list.controls.append(approval_display)
    app_instance.page.update()

    # Wait for user decision
    result = await approval_future
    return result

def get_agent_instructions() -> str:
    """Get agent instructions for Indie."""
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

async def build_agent(settings: Dict[str, Any], app: AgentApp) -> SafeAgent:
    """Builds the Indie SafeAgent instance using current settings."""
    global mcp_server_instance
    print("Building Indie Agent (SafeAgent)...")

    api_key = settings.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    base_url = settings.get("openai_api_base") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
    model_name = settings.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o"
    mcp_url = settings.get("mcp_url") or os.getenv("MCP_URL")
    mcp_auth_token = settings.get("mcp_auth_token") or os.getenv("MCP_AUTH_TOKEN")

    if not api_key:
        raise ValueError("OpenAI API key is missing. Please configure in settings.")

    # Configure OpenAI client
    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        set_default_openai_client(client=client, use_for_tracing=False)
        print(f"OpenAI client configured: base_url={base_url}, model={model_name}")
    except Exception as e:
        raise ValueError(f"Failed to initialize OpenAI client: {e}")

    # Configure MCP Server connection if URL is provided
    mcp_servers = []
    if mcp_url:
        print(f"MCP URL found: {mcp_url}")
        if mcp_server_instance is None or mcp_server_instance.params.get("url") != mcp_url:
            # Close existing connection if URL changed
            if mcp_server_instance:
                try:
                    await mcp_server_instance.__aexit__(None, None, None)
                    print("Closed previous MCP connection.")
                except Exception as e_close:
                    print(f"Error closing previous MCP connection: {e_close}")
                mcp_server_instance = None

            # Create new connection
            print("Attempting to connect to MCP server...")
            mcp_params = {
                "url": mcp_url,
                "timeout": 30,
                "sse_read_timeout": 600,
                "headers": {},
            }
            if mcp_auth_token:
                mcp_params["headers"]["Authorization"] = f"Bearer {mcp_auth_token}"
                print("Using MCP Auth Token.")

            try:
                mcp_server_instance = MCPServerSse(
                    name="80/20_Jiu-Jitsu_MCP_Server",
                    params=mcp_params,
                )
                await mcp_server_instance.__aenter__() # Enter context
                print("MCP Server connected successfully.")
                mcp_servers.append(mcp_server_instance)
            except Exception as e_conn:
                print(f"############# MCP CONNECTION FAILED #############")
                print(f"Error connecting to MCP server at {mcp_url}: {e_conn}")
                print(f"###############################################")
                mcp_server_instance = None # Ensure it's None if connection failed
                # Optionally raise an error or show a persistent warning in UI
        elif mcp_server_instance:
             # Reuse existing connection
             print("Reusing existing MCP server connection.")
             mcp_servers.append(mcp_server_instance)
        else:
            print("No MCP URL configured. Running without MCP capabilities.")
            # Ensure any existing connection is closed if URL is removed
            if mcp_server_instance:
                try:
                    await mcp_server_instance.__aexit__(None, None, None)
                    print("Closed previous MCP connection as URL was removed.")
                except Exception as e_close:
                    print(f"Error closing previous MCP connection: {e_close}")
                mcp_server_instance = None

    # Create a partial function that includes the 'app' instance
    approval_callback_with_app = partial(flet_approval_callback, app=app)
    approval_callback_to_use = approval_callback_with_app if not SKIP_APPROVALS else SafeAgent.auto_approval_callback

    agent = SafeAgent(
        name="Indie",
        mcp_servers=mcp_servers, # Pass the connected server instance
        model=model_name,
        model_settings=ModelSettings(tool_choice="auto"),
        instructions=get_agent_instructions(),
        safe_tool_names=safe_tool_names,
        safe_tool_patterns=safe_tool_patterns,
        debug_mode=DEBUG_MODE,
        halt_on_rejection=HALT_ON_REJECTION,
        approval_callback=approval_callback_to_use, # Use the partial callback
        skip_approvals=SKIP_APPROVALS
    )
    print(f"Indie SafeAgent created. Skip Approvals: {SKIP_APPROVALS}")
    return agent

# --- App Specific UI/Lifecycle --- 

# MCP Connection Status Indicator (Example)
connection_status_indicator = ft.Container(
    width=10, height=10, border_radius=5,
    bgcolor=ft.Colors.ORANGE, # Start as Orange (unknown/disconnected)
    tooltip="MCP Connection Status"
)

def update_mcp_status_indicator():
    """Updates the color based on mcp_server_instance state."""
    if mcp_server_instance and mcp_server_instance._sse_client and not mcp_server_instance._sse_client.is_closed:
         connection_status_indicator.bgcolor = ft.Colors.GREEN
         connection_status_indicator.tooltip = "MCP Connected"
    elif mcp_server_instance: # Instance exists but not connected
        connection_status_indicator.bgcolor = ft.Colors.RED
        connection_status_indicator.tooltip = "MCP Connection Error"
    else: # No MCP URL or instance
        connection_status_indicator.bgcolor = ft.Colors.ORANGE # Disconnected/Not configured
        connection_status_indicator.tooltip = "MCP Not Configured"
    # Need to trigger page update from where this is called

def build_header_controls(app: AgentApp) -> List[ft.Control]:
    """Builds extra controls for the header (MCP status)."""
    # Update status when building controls
    update_mcp_status_indicator()
    # Periodically update status (example: every 30 seconds)
    # This requires running an async task - might be complex to manage lifecycle
    # Simple approach: update status when building agent or saving settings
    return [connection_status_indicator]

async def cleanup_handler():
    """Closes MCP connection when the app exits."""
    global mcp_server_instance
    if mcp_server_instance:
        print("Closing MCP connection...")
        try:
            await mcp_server_instance.__aexit__(None, None, None)
            print("MCP connection closed.")
        except Exception as e:
            print(f"Error closing MCP connection during cleanup: {e}")
        mcp_server_instance = None

# --- Application Specification ---

# Define settings fields specific to the MCP app
mcp_settings_fields = [
    SettingField(key="openai_api_base", label="OpenAI API Base URL", group="OpenAI API", default="https://api.openai.com/v1", hint="e.g., https://api.openai.com/v1"),
    SettingField(key="openai_api_key", label="OpenAI API Key", group="OpenAI API", secret=True, hint="Required"),
    SettingField(key="model_name", label="Model Name", group="OpenAI API", default="gpt-4o", hint="e.g., gpt-4o, gpt-3.5-turbo"),
    SettingField(key="mcp_url", label="MCP Server URL", group="MCP Server", default="", hint="e.g., http://localhost:3000/mcp/sse"),
    SettingField(key="mcp_auth_token", label="MCP Auth Token", group="MCP Server", secret=True, hint="Optional, if MCP server requires auth"),
]

# Define icon map for MCP tools (prefix typically indicates source)
mcp_icon_map = {
    "mcp__search": ft.Icons.SEARCH,
    "mcp__fetch": ft.Icons.CLOUD_DOWNLOAD,
    "mcp__weather": ft.Icons.CLOUD,
    "mcp__calendar": ft.Icons.CALENDAR_TODAY,
    "mcp__location": ft.Icons.LOCATION_ON,
    # Add more MCP tool icons as needed
}

# Construct welcome text
welcome_text = "👋 Welcome to 80/20 Jiu-Jitsu! I'm Indie, your gym assistant. How can I assist you today?"
if DEBUG_MODE:
    welcome_text += " (Debug mode active)"

# Create the Agent Specification
mcp_agent_spec = AgentSpec(
    # Basic Info
    name="Indie",
    header_title="Indie - 80/20 Jiu-Jitsu",
    page_title="Indie - 80/20 Assistant",
    welcome_text=welcome_text,
    client_storage_prefix="mcp_flet_agent.", # Unique prefix

    # Agent Config
    get_agent_instructions=get_agent_instructions,
    build_agent=build_agent, # Uses the MCP-specific builder
    available_tools=[], # Tools are dynamically provided by MCP

    # UI/UX Config
    icon_map=mcp_icon_map,
    settings_fields=mcp_settings_fields,
    debug_flag=DEBUG_MODE,
    page_title_suffix=" (Debug Mode)",
    theme_color=ft.Colors.INDIGO_700, # Specific theme color

    # App-Specific Customizations
    header_controls_builder=build_header_controls,
    cleanup_handler=cleanup_handler,
)

# --- Main Execution ---

if __name__ == "__main__":
    # Create the app instance with the MCP spec
    app = AgentApp(spec=mcp_agent_spec)

    # Run the Flet app
    # Store the app instance in page.data so the approval callback can access it
    def run_app(page: ft.Page):
        page.data = app # Make app instance accessible
        asyncio.run(app.main(page))

    ft.app(target=run_app)
