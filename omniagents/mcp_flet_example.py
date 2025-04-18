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
from agents.mcp import MCPServerSse # Import for type hinting build_agent

from agents import (
    Agent, # Import base Agent
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled
)
# MCP Server is now handled by AgentApp, no direct import needed here
# from agents.mcp import MCPServerSse
from agents.model_settings import ModelSettings
# SafeAgent is now handled by core, no direct import needed here

# Import core components
from flet_agent_core.app_base import AgentApp, AgentSpec, AgentLike, SafeAgentOptions # Added AgentLike, SafeAgentOptions
from flet_agent_core.settings import SettingField
# UI Components (ToolApprovalRequest is now in core.ui)
# No direct imports needed unless customizing beyond base chat
# from flet_agent_core.ui import ChatMessage, ToolCallDisplay, ToolOutputDisplay, Message


# --- Configuration & Setup ---

# Load environment variables first
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# Parse command line arguments
parser = argparse.ArgumentParser(description="80/20 Jiu-Jitsu Gym Support Agent with MCP and Flet UI")
parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode to show detailed tool outputs")
parser.add_argument("--skip-approvals", action="store_true", help="Skip all approval prompts (uses SafeAgent)")
parser.add_argument("--pre-approve", type=str, help="Comma-separated list of tool names to pre-approve (uses SafeAgent)")
parser.add_argument("--safe-patterns", type=str, help="Comma-separated list of regex patterns for safe tools (uses SafeAgent)")
parser.add_argument("--no-halt", action="store_true", help="Don't halt execution when a tool is rejected (uses SafeAgent)")
args = parser.parse_args()

# Flags from arguments
DEBUG_MODE = args.debug
SKIP_APPROVALS_ARG = args.skip_approvals
HALT_ON_REJECTION_ARG = not args.no_halt
safe_tool_names_arg = [tool.strip() for tool in args.pre_approve.split(",")] if args.pre_approve else []
safe_tool_patterns_arg = [pattern.strip() for pattern in args.safe_patterns.split(",")] if args.safe_patterns else []

if DEBUG_MODE:
    print("\n--- Indie - 80/20 Jiu-Jitsu Assistant ---")
    print(f"Debug mode: {DEBUG_MODE}")
    print(f"(Safe Agent Options if enabled) Skip approvals: {SKIP_APPROVALS_ARG}")
    print(f"(Safe Agent Options if enabled) Halt on rejection: {HALT_ON_REJECTION_ARG}")
    print(f"(Safe Agent Options if enabled) Pre-approved tools: {safe_tool_names_arg}")
    print(f"(Safe Agent Options if enabled) Safe tool patterns: {safe_tool_patterns_arg}")
    print("-----------------------------------------")

# Disable tracing
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# --- MCP & Agent Specific Components ---

# Global state for MCP connection REMOVED - handled by AgentApp
# mcp_server_instance: Optional[MCPServerSse] = None

# ToolApprovalRequest UI REMOVED - handled by flet_agent_core.ui
# flet_approval_callback logic REMOVED - handled by AgentApp._request_tool_approval
# Global approval_state REMOVED - handled internally by AgentApp

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

# build_agent now returns the *core* agent (AgentLike protocol)
# AgentApp will handle wrapping it in SafeAgent if spec.use_safe_agent is True
# AgentApp will handle adding the MCP server instance
async def build_agent(settings: Dict[str, Any], mcp_server: Optional[MCPServerSse], app: AgentApp) -> AgentLike:
    """Builds the Indie Agent instance (core logic), using the provided MCP server."""
    print("Building Indie Agent (Core)...")

    api_key = settings.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    base_url = settings.get("openai_api_base") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
    model_name = settings.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o"

    if not api_key:
        raise ValueError("OpenAI API key is missing. Please configure in settings.")

    # Configure OpenAI client
    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        set_default_openai_client(client=client, use_for_tracing=False)
        print(f"OpenAI client configured: base_url={base_url}, model={model_name}")
    except Exception as e:
        raise ValueError(f"Failed to initialize OpenAI client: {e}")

    # --- REMOVED MCP Server Connection Logic --- 
    # AgentApp now manages the MCPServerSse instance and connection.

    # Create the base Agent instance. 
    # Explicitly pass the managed mcp_server instance (if provided) to the Agent.
    agent = Agent(
        name="Indie",
        mcp_servers=[mcp_server] if mcp_server else [], # Pass the provided server instance
        model=model_name,
        model_settings=ModelSettings(tool_choice="auto"),
        instructions=get_agent_instructions(),
        tools=[], # Base agent doesn't need static tools if MCP is used
    )

    # AgentApp will handle wrapping this instance with SafeAgent based on AgentSpec config.
    print(f"Indie core Agent created. MCP configuration will be handled by AgentApp.")
    return agent

# --- App Specific UI/Lifecycle --- 
# REMOVED - These are now handled by AgentApp based on AgentSpec configuration
# connection_status_indicator = ft.Container(...)
# def update_mcp_status_indicator(): ...
# def build_header_controls(app: AgentApp) -> List[ft.Control]: ...
# async def cleanup_handler(): ...

# --- Application Specification --- 

# Define settings fields specific to the MCP app
mcp_settings_fields = [
    SettingField(key="openai_api_base", label="OpenAI API Base URL", group="OpenAI API", default="https://api.openai.com/v1", hint="e.g., https://api.openai.com/v1"),
    SettingField(key="openai_api_key", label="OpenAI API Key", group="OpenAI API", secret=True, hint="Required"),
    SettingField(key="model_name", label="Model Name", group="OpenAI API", default="gpt-4o", hint="e.g., gpt-4o, gpt-3.5-turbo"),
    # Ensure these keys match the defaults in AgentSpec or override in AgentSpec
    SettingField(key="mcp_url", label="MCP Server URL", group="MCP Server", default="", hint="e.g., http://localhost:3000/mcp/sse"),
    SettingField(key="mcp_auth_token", label="MCP Auth Token", group="MCP Server", secret=True, hint="Optional, if MCP server requires auth"),
]

# Define icon map for MCP tools (prefix typically indicates source)
mcp_icon_map = {
    # Add known MCP tool prefixes or specific tool names here
    "mcp__search": ft.Icons.SEARCH,
    "mcp__fetch": ft.Icons.CLOUD_DOWNLOAD,
    "mcp__weather": ft.Icons.CLOUD,
    "mcp__calendar": ft.Icons.CALENDAR_TODAY,
    "mcp__location": ft.Icons.LOCATION_ON,
    "read_file": ft.Icons.DESCRIPTION, # Example standard tool icon
    # Add more...
}

# Construct welcome text
welcome_text = "👋 Welcome to 80/20 Jiu-Jitsu! I'm Indie, your gym assistant. How can I assist you today?"
if DEBUG_MODE:
    welcome_text += " (Debug mode active)"

# Configure SafeAgent options from command line args
safe_agent_options = SafeAgentOptions(
    skip_approvals=SKIP_APPROVALS_ARG,
    halt_on_rejection=HALT_ON_REJECTION_ARG,
    safe_tool_names=safe_tool_names_arg,
    safe_tool_patterns=safe_tool_patterns_arg
)

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
    build_agent=build_agent, # Core agent builder
    available_tools=[], # Tools are dynamically provided by MCP

    # Agent Safety Config
    use_safe_agent=True, # Enable SafeAgent wrapping provided by AgentApp
    safe_agent_options=safe_agent_options, # Pass configured options

    # MCP Config (NEW - Enable MCP handling by AgentApp)
    uses_mcp=True,
    # mcp_url_setting_key="mcp_url", # Default, no need to specify unless different
    # mcp_token_setting_key="mcp_auth_token", # Default
    # show_mcp_status_indicator=True, # Default

    # UI/UX Config
    icon_map=mcp_icon_map,
    settings_fields=mcp_settings_fields,
    debug_flag=DEBUG_MODE,
    page_title_suffix=" (Debug Mode)" if DEBUG_MODE else "", # Conditional suffix
    theme_color=ft.Colors.INDIGO_700, # Specific theme color

    # App-Specific Customizations (REMOVED - MCP handlers are now core)
    header_controls_builder=None,
    cleanup_handler=None,
)

# --- Main Execution ---

if __name__ == "__main__":
    # Create the app instance with the MCP spec
    app = AgentApp(spec=mcp_agent_spec)

    # Run the Flet app using the main method of the app instance
    # No need to pass app via page.data anymore
    ft.app(target=app.main)
