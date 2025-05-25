from __future__ import annotations

import flet as ft
from flet import animation as animation  # ensure ft.animation submodule is loaded
import asyncio
import json
import os
import uuid # Added for unique approval IDs
from dataclasses import dataclass, field
from enum import Enum # Added Enum
from typing import List, Dict, Any, Callable, Optional, Protocol, Awaitable, TYPE_CHECKING, Tuple # Added Tuple

# Import from other core modules
# Use absolute imports for robustness within the package
from omniagents.core.ui import (
    Message,
    ChatMessage,
    ToolInteractionDisplay,
    ToolApprovalRequest,
)
from omniagents.core.settings import SettingsScreen, SettingField
from omniagents.core.storage import load_settings, save_settings
from omniagents.core.local_sessions import LocalSession
from omniagents.core.config_loader import load_agent_spec_from_yaml, DEFAULT_SETTINGS_FIELDS # <-- Import the loader and default settings
from omniagents.core.specs import AgentSpec, AgentLike, SafeAgentOptions # <-- Import moved specs

# Import agents types for type hinting (use Protocol for flexibility)
from agents import Agent, ItemHelpers # Added Agent
from agents import Runner # For run_streamed
from agents.model_settings import ModelSettings # <-- Import ModelSettings
from agents import ( # <-- Imports for default builder
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled
)
from openai import AsyncOpenAI # <-- Import for default builder

# Import SafeAgent components directly as they are part of the core package now
from .safe_agent import SafeAgent, ApprovalResult # Added SafeAgent and ApprovalResult

# Use TYPE_CHECKING to avoid circular import issues at runtime
if TYPE_CHECKING:
    # Use absolute import if AgentApp is defined elsewhere in the package,
    # otherwise keep relative if it's defined in this file (which it is).
    from .app_base import AgentApp # Keep relative for self-reference check
    from agents.mcp import MCPServerSse # <-- Add import here

# Define a protocol for Agent-like objects to avoid circular dependencies
# and allow for different Agent types (Agent, SafeAgent, etc.)
# --- MOVED AgentLike protocol to specs.py --- 

# --- MOVED SafeAgentOptions dataclass to specs.py --- 

class MCPConnectionStatus(Enum):
    """Enum for MCP connection status."""
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"

# --- MOVED AgentSpec dataclass to specs.py --- 

# --- Default Agent Builder (Internal) --- #
async def _default_build_agent(
    settings: Dict[str, Any],
    mcp_server: Optional['MCPServerSse'], # Use string literal
    app: 'AgentApp' # Pass the app instance to access spec and callback
) -> AgentLike:
    """Internal default function to build the agent instance."""
    # --- ADDED LOGGING --- 
    print(f"BUILDER ENTRY: Received settings dict id: {id(settings)}")
    # ---------------------
    spec = app.spec # Get the spec from the app instance
    print("Building agent using internal default builder...")

    # --- Get Core Config from Spec/Settings --- 
    agent_name = spec.name
    # Get model from spec (set by config loader) or fallback from settings
    model_name = spec.model_name or settings.get("model_name", "gpt-4o") # Ensure fallback
    instructions = spec.get_agent_instructions() # Use the spec's instruction loader
    # Tools are provided directly in the spec if loaded from config
    tools = spec.available_tools
    # MCP server instance is passed directly
    mcp_servers_list = [mcp_server] if mcp_server else []

    # --- Configure OpenAI Client --- 
    # Attempt to get key from settings first, then environment
    api_key_from_settings = settings.get("openai_api_key")
    api_key_from_env = os.getenv("OPENAI_API_KEY")
    
    print(f"BUILDER CHECK: API Key from settings dict passed to builder: '{api_key_from_settings}'")

    # Prioritize settings value if it exists and is not empty
    api_key = api_key_from_settings if api_key_from_settings else api_key_from_env

    # Explicitly check if the final key is None or empty *after* trying both sources
    if not api_key:
        raise ValueError("OpenAI API key is missing. Please configure in settings or set OPENAI_API_KEY environment variable.")

    base_url = settings.get("openai_api_base") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"

    try:
        # Use default settings if not otherwise specified
        # Disable tracing/default API type - assuming this is handled globally or not needed here
        # set_tracing_disabled(disabled=True)
        # set_default_openai_api("chat_completions")

        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        # Set client globally so the agents framework can pick it up
        # Check agents-python docs: Agent might not need global client if passed implicitly via model_settings or env vars
        set_default_openai_client(client=client, use_for_tracing=False)
        print(f"OpenAI client configured for default builder: base_url={base_url}, model={model_name}")
    except Exception as e:
        raise ValueError(f"Failed to initialize OpenAI client: {e}")

    # Set SERPAPI_API_KEY if provided in settings (for tools that need it)
    serpapi_key = settings.get("serpapi_api_key") or os.getenv("SERPAPI_API_KEY")
    if serpapi_key:
        os.environ["SERPAPI_API_KEY"] = serpapi_key
        print("SERPAPI_API_KEY set in environment from settings.")
    elif "SERPAPI_API_KEY" in os.environ:
         # Clean up env var if not in settings to avoid using stale key
         del os.environ["SERPAPI_API_KEY"]
         print("SERPAPI_API_KEY removed from environment as it's not in current settings.")

    # --- Create Base Agent --- 
    print(f"Creating base Agent: name='{agent_name}', model='{model_name}', tools_count={len(tools)}, mcp_count={len(mcp_servers_list)}")
    
    # Create ModelSettings with tool_choice only if tools are available
    if tools:
        model_settings = ModelSettings(tool_choice="auto")
    else:
        # Create ModelSettings without specifying tool_choice when no tools are available
        model_settings = ModelSettings()
    
    base_agent = Agent(
        name=agent_name,
        tools=tools, # Pass the discovered/selected function objects
        mcp_servers=mcp_servers_list,
        model=model_name,
        model_settings=model_settings, # Always pass a ModelSettings object
        instructions=instructions,
    )

    # --- Optionally Wrap with SafeAgent --- 
    if spec.use_safe_agent:
        print(f"Wrapping '{agent_name}' with SafeAgent...")
        opts = spec.safe_agent_options
        try:
            safe_agent = SafeAgent(
                # Pass parameters from the base agent we just created
                name=base_agent.name,
                tools=await base_agent.get_all_tools(), # Get tools resolved by base agent (incl MCP)
                instructions=base_agent.instructions,
                model=base_agent.model,
                model_settings=base_agent.model_settings,
                mcp_servers=base_agent.mcp_servers,

                # SafeAgent specific config from spec
                approval_callback=app._request_tool_approval, # Use app's callback
                safe_tool_names=opts.safe_tool_names,
                safe_tool_patterns=opts.safe_tool_patterns,
                skip_approvals=opts.skip_approvals,
                halt_on_rejection=opts.halt_on_rejection,
                debug_mode=spec.debug_flag,
            )
            print("SafeAgent wrapper created successfully.")
            return safe_agent # Return the wrapped agent
        except Exception as safe_agent_init_error:
            error_msg = f"Failed to initialize SafeAgent wrapper: {safe_agent_init_error}"
            print(f"ERROR: {error_msg}")
            # Fallback to returning the base agent if wrapping fails
            return base_agent
    else:
        # Return the base agent directly if not using SafeAgent
        print(f"Using base Agent '{agent_name}' without SafeAgent wrapper.")
        return base_agent


class AgentApp:
    """Base class for creating Flet Agent applications."""
    @classmethod
    def from_agent(
        cls,
        agent: AgentLike,
        *,
        welcome_text: Optional[str] = None,
        prefix: Optional[str] = None,
        safe: bool = False,
        max_turns: int | None = None,
        # --- SafeAgent customisation ------------------
        skip_approvals: bool = False,
        halt_on_rejection: bool = True,
        safe_tool_names: Optional[List[str]] = None,
        safe_tool_patterns: Optional[List[str]] = None,
        layout: str = "classic",
    ) -> AgentApp:
        """
        Construct an AgentApp directly from an existing agents.Agent (or Agent-like).
        This builds an internal AgentSpec that wraps the given agent without YAML.
        """
        name = getattr(agent, 'name', 'agent')
        # Instructions may be a callable or a string
        instr = agent.instructions() if callable(agent.instructions) else agent.instructions
        # Determine storage prefix
        storage_prefix = prefix or name.lower().replace(' ', '_') + '.'

        # Decide how the Agent will be built downstream:
        # • If `safe` is True we leave build_agent=None so the internal
        #   _default_build_agent will run (it applies SafeAgent wrapping).
        # • If `safe` is False we embed a trivial builder that just returns
        #   the concrete Agent instance supplied by the caller.

        _builder = None
        if not safe:
            async def _builder(
                settings: Dict[str, Any], mcp, app
            ) -> AgentLike:  # type: ignore
                return agent

        spec = AgentSpec(
            name=name,
            header_title=name,
            page_title=name,
            welcome_text=welcome_text or instr,
            client_storage_prefix=storage_prefix,
            get_agent_instructions=lambda: instr,
            build_agent=_builder,
            available_tools=getattr(agent, 'tools', []),
            model_name=getattr(agent, 'model', None),
            use_safe_agent=safe,
            safe_agent_options=SafeAgentOptions(
                skip_approvals=skip_approvals,
                halt_on_rejection=halt_on_rejection,
                safe_tool_names=safe_tool_names or [],
                safe_tool_patterns=safe_tool_patterns or [],
            ),
            settings_fields=list(DEFAULT_SETTINGS_FIELDS),  # include OpenAI settings by default
            max_turns=max_turns if max_turns is not None else 20,
            layout=layout,
        )
        return cls(spec=spec)
    # Modify init to accept config_path OR spec
    def __init__(self, spec: Optional[AgentSpec] = None, config_path: Optional[str] = None):

        if config_path:
            print(f"Loading AgentSpec from configuration file: {config_path}")
            self.spec = load_agent_spec_from_yaml(config_path)
        elif spec:
            print("Using provided AgentSpec object.")
            self.spec = spec
        else:
            raise ValueError("Must provide either 'spec' or 'config_path' to AgentApp constructor.")

        # Ensure build_agent is set if loaded from config
        if self.spec.build_agent is None:
             print("Using internal default agent builder.")
             # We don't assign it here, we check for None later during agent creation
             # self.spec.build_agent = _default_build_agent # This modifies the spec, maybe not ideal
             pass # We'll check for None in _process_message

        self.page: Optional[ft.Page] = None
        self.chat: Optional[ft.ListView] = None
        self.logs: Optional[ft.ListView] = None
        self.new_message: Optional[ft.TextField] = None
        self.typing_indicator: Optional[ft.Container] = None
        self.settings_button: Optional[ft.IconButton] = None
        self.header: Optional[ft.Container] = None
        self.send_button: Optional[ft.IconButton] = None

        self.conversation: List[Dict[str, Any]] = []
        self.local_session: Optional[LocalSession] = None
        self.agent_instance: Optional[AgentLike] = None
        self.current_settings: Dict[str, Any] = {}

        # MCP State
        self._mcp_server_instance: Optional['MCPServerSse'] = None # Use string literal
        self._mcp_connection_status: MCPConnectionStatus = MCPConnectionStatus.UNKNOWN
        self._mcp_connection_status_indicator: Optional[ft.Container] = None # UI element

        # Internal state for tool approval UI
        self._approval_state: Dict[str, Any] = {
            "active_displays": {}, # Maps approval_id -> UI control
            "pending_futures": {}  # Maps approval_id -> asyncio.Future
        }

        # Track active tool interaction cards for pairing call and result
        self._interactions_by_call_id: Dict[str, ToolInteractionDisplay] = {}
        self._pending_interactions: List[ToolInteractionDisplay] = []
    def run(self, mode: str = "gui", **ft_kwargs):
        """
        Convenience method to start the agent app.
        mode: "gui" for Flet-based GUI (only supported).
        Additional keyword args are passed to ft.app.
        """
        if mode == "cli":
            # Run in CLI mode via the CLI runner
            from omniagents.cli_runner import run_cli
            import asyncio, argparse
            # Build minimal args for overrides (all defaults)
            cli_args = argparse.Namespace()
            # Execute CLI loop with in-memory spec
            asyncio.run(run_cli(config_path=None, args=cli_args, spec=self.spec))
            return
        elif mode == "server":
            # ------------------------------------------------------------------
            #  JSON-RPC / WebSocket server (remote agent) -----------------------
            # ------------------------------------------------------------------
            host = ft_kwargs.pop("host", "0.0.0.0")  # remove so uvicorn gets a clean dict
            port = ft_kwargs.pop("port", 8000)
            auth_token = ft_kwargs.pop("auth_token", None)

            # Defer import of heavy deps until needed
            from omniagents.server.app import build_app  # local import to avoid startup cost
            import uvicorn

            rpc_app = build_app(spec=self.spec, auth_token=auth_token)
            print(f"Starting OmniAgents RPC server on {host}:{port} (programmatic spec: {self.spec.name})")
            uvicorn.run(rpc_app, host=host, port=port, **ft_kwargs)
        elif mode == "gui":
            # Ensure OpenAI settings for GUI mode
            set_tracing_disabled(disabled=True)
            set_default_openai_api("chat_completions")
            ft.app(target=self.main, **ft_kwargs)
        else:
            raise ValueError(f"Unsupported run mode: {mode}. Choose 'gui', 'cli', or 'server'.")

    async def main(self, page: ft.Page):
        """Main entry point for the Flet application."""
        self.page = page
        await self._configure_page()
        await self._load_initial_settings()

        # Start a fresh local session each time the app launches
        self.local_session = LocalSession()
        self.conversation = list(self.local_session.history)

        # Attempt initial MCP connection if configured
        if self.spec.uses_mcp:
            await self._update_mcp_connection()

        self._build_ui() # UI depends on initial MCP status potentially
        self._setup_routing()
        self._show_initial_warnings()

        # Set combined cleanup handler
        async def combined_cleanup():
            # Disconnect MCP first
            await self._disconnect_mcp()
            # Then run custom cleanup if provided
            if self.spec.cleanup_handler:
                await self.spec.cleanup_handler()

        self.page.on_disconnect = lambda e: asyncio.create_task(combined_cleanup())

        # Create the initial main view AND add it to the page's views
        initial_view = self._create_main_view()
        self.page.views.append(initial_view) # Ensure the first view is added

        self.page.update() # Ensure initial view is rendered

    async def _configure_page(self):
        """Configure basic page properties."""
        self.page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
        self.page.title = self.spec.page_title + (self.spec.page_title_suffix if self.spec.debug_flag else "")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.window_width = 900
        self.page.window_height = 750
        self.page.padding = 0
        self.page.bgcolor = ft.Colors.with_opacity(0.98, ft.Colors.WHITE)
        self.page.fonts = {
            "SF Pro": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        }
        self.page.theme = ft.Theme(font_family="SF Pro, Inter, Helvetica, Arial, sans-serif")
        self.page.views.clear()
        self.page.adaptive = True

    async def _load_initial_settings(self):
        """Load settings from client storage."""
        try:
            self.current_settings = await load_settings(
                self.page,
                self.spec.client_storage_prefix,
                self.spec.settings_fields
            )
            print(f"Initial settings loaded: {list(self.current_settings.keys())}")
            # --- ADDED LOGGING --- 
            print(f"LOAD SUCCESS: self.current_settings id: {id(self.current_settings)}")
            # ---------------------
            # Apply any immediate effects of settings here if needed
            # e.g., setting environment variables, though passing to build_agent is preferred.
        except Exception as e:
            # 3. If ANY error happens *after* step 2 but before the function fully returns...
            print(f"Error loading initial settings: {e}")
            # 4. ...it resets self.current_settings to defaults!
            self.current_settings = {field.key: field.default for field in self.spec.settings_fields}
            # --- ADDED LOGGING --- 
            print(f"LOAD EXCEPTION: Resetting settings to defaults due to error. Keys now: {list(self.current_settings.keys())}")
            print(f"LOAD EXCEPTION: Default API key value: '{self.current_settings.get('openai_api_key')}'")
            # ---------------------
        # No page.update() here, happens in main

    def _build_ui(self):
        """Build the main UI components."""
        # Typing Indicator
        self.typing_indicator = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.ProgressRing(width=16, height=16, stroke_width=2, color=self.spec.theme_color), # Use theme color
                        margin=ft.margin.only(right=8)
                    ),
                    ft.Text("Assistant is thinking...", size=14, color=ft.Colors.BLACK54),
                ],
                alignment=ft.MainAxisAlignment.START,
            ),
            # margin removed: spacing now handled by ListView
            animate=ft.animation.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
            visible=False
        )

        # Chat View
        self.chat = ft.ListView(
            expand=True,
            spacing=20,  # increased vertical spacing for more breathing room
            auto_scroll=True,
            padding=ft.padding.symmetric(horizontal=20),  # consistent left/right inset for all items
            # Controls added later (welcome message)
        )

        if self.spec.layout == "tabs":
            self.logs = ft.ListView(
                expand=True,
                spacing=20,
                auto_scroll=True,
                padding=ft.padding.symmetric(horizontal=20),
            )

        # Settings Button
        self.settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            icon_color=ft.Colors.WHITE,
            icon_size=20,
            tooltip="API Settings",
            on_click=self._go_to_settings,
            style=ft.ButtonStyle(
                padding=8,  # Consistent padding inside the button
                shape=ft.CircleBorder(),  # Ensures the clickable area is circular
            ),
            # Ensure button is contained within header height
            height=40,
            width=40,
        )

        # Header
        header_right_controls = []

        # Add default MCP status indicator if configured
        if self.spec.uses_mcp and self.spec.show_mcp_status_indicator:
            self._mcp_connection_status_indicator = ft.Container(
                width=12, height=12, border_radius=6,
                bgcolor=self._get_mcp_status_color(), # Initial color based on status
                tooltip=self._get_mcp_status_tooltip(),
                margin=ft.margin.only(right=8), # Add some margin
                alignment=ft.alignment.center,
            )
            header_right_controls.append(self._mcp_connection_status_indicator)

        # Add settings button
        header_right_controls.append(self.settings_button)

        # Add custom header controls if provided by the spec
        if self.spec.header_controls_builder:
            try:
                header_right_controls.extend(self.spec.header_controls_builder(self))
            except Exception as e:
                print(f"Error calling header_controls_builder: {e}")

        self.header = ft.Container(
            content=ft.SafeArea(
                content=ft.Column([
                    ft.Container(
                        content=ft.Row([
                            ft.Column([
                                ft.Text(
                                    self.spec.header_title, 
                                    color=ft.Colors.WHITE, 
                                    size=20,  # Reduced from 24 to a more moderate size
                                    weight="w500",  # Keeping the weight the same
                                ),
                            ], spacing=2, tight=True),
                            ft.Row(
                                header_right_controls, 
                                spacing=12, 
                                alignment=ft.MainAxisAlignment.END,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ), # Align right
                        ], 
                        spacing=12, 
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN, 
                        vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        padding=ft.padding.symmetric(horizontal=20, vertical=0),  # Reduce vertical padding
                        height=64,  # Material 3 standard app bar height
                        alignment=ft.alignment.center,  # Center container content vertically
                    ),
                    # Debug Banner (only if debug_flag is True)
                    *([
                        ft.Container(
                            content=ft.Row([
                                ft.Icon(name=ft.Icons.BUG_REPORT, color=ft.Colors.WHITE, size=16),
                                ft.Text("Debug Mode Enabled", color=ft.Colors.WHITE, size=13),
                            ], spacing=8),
                            padding=ft.padding.symmetric(horizontal=20, vertical=8),  # Matched with header padding
                            bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                        )
                      ] if self.spec.debug_flag else []
                    ),
                ]),
                bottom=False
            ),
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                # Use theme color in gradient
                colors=[ft.Colors.INDIGO_900, self.spec.theme_color, ft.Colors.with_opacity(0.8, self.spec.theme_color)],
            ),
            shadow=ft.BoxShadow(
                spread_radius=0, blur_radius=4,  # Material 3 standard elevation
                color=ft.Colors.with_opacity(0.3, ft.Colors.BLACK), offset=ft.Offset(0, 2)
            ),
        )

        # Input Field
        self.new_message = ft.TextField(
            hint_text="Ask the assistant...",
            hint_style=ft.TextStyle(color=ft.Colors.BLACK45),
            autofocus=True, shift_enter=True, min_lines=1, max_lines=5, filled=False,
            border=ft.InputBorder.OUTLINE,
            border_radius=30,
            border_color=ft.Colors.BLACK26,
            focused_border_color=self.spec.theme_color,
            text_size=15, text_style=ft.TextStyle(color=ft.Colors.BLACK), cursor_color=self.spec.theme_color,
            expand=True, content_padding=ft.padding.symmetric(horizontal=24, vertical=16),
            on_submit=self._send_message_click,
        )

        # Send Button
        self.send_button = ft.IconButton(
            icon=ft.Icons.SEND_ROUNDED, icon_color=self.spec.theme_color,
            icon_size=24, tooltip="Send message", on_click=self._send_message_click,
            style=ft.ButtonStyle(
                shape=ft.CircleBorder(), padding=12, # Make it circular
                overlay_color=ft.Colors.with_opacity(0.1, self.spec.theme_color)
            ),
        )

        # Initial Welcome Message
        welcome_msg = Message("System", self.spec.welcome_text, "system_message")
        self.chat.controls.append(ChatMessage(welcome_msg, self.page, assistant_name=self.spec.name))

    def _setup_routing(self):
        """Set up page routing for main view and settings."""
        self.page.on_route_change = self._route_change
        self.page.on_view_pop = self._view_pop # Handle back button in settings

    def _show_initial_warnings(self):
        """Show snackbar warnings based on initial settings (e.g., missing keys)."""
        missing_keys = []
        for field in self.spec.settings_fields:
            # Check if required field is missing (simple check: empty and has 'Required' in hint)
            if not self.current_settings.get(field.key) and field.hint and "Required" in field.hint:
                 # Special case: Don't warn about MCP token if URL is also missing
                 if field.key == self.spec.mcp_token_setting_key and not self.current_settings.get(self.spec.mcp_url_setting_key):
                      continue
                 missing_keys.append(field.label)

        if missing_keys:
            keys_str = ", ".join(missing_keys)
            snack_bar = ft.SnackBar(
                content=ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.AMBER_600),
                    ft.Text(f"Missing required setting(s): {keys_str}. Go to Settings.", color=ft.Colors.BLACK87),
                ]),
                bgcolor=ft.Colors.AMBER_50,
                open=True
            )
            self.page.overlay.append(snack_bar)
            self.page.update()
        # Warn if MCP is configured but connection failed
        elif self.spec.uses_mcp and self._mcp_connection_status == MCPConnectionStatus.ERROR:
             snack_bar = ft.SnackBar(
                 content=ft.Row([
                     ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600),
                     ft.Text(f"MCP Connection Failed. Check URL/Token in Settings.", color=ft.Colors.BLACK87),
                 ]),
                 bgcolor=ft.Colors.RED_50,
                 open=True
             )
             self.page.overlay.append(snack_bar)
             self.page.update()
        # No page.update() here, happens in main

    # --- Event Handlers & Actions ---

    async def _send_message_click(self, e=None): # Added e=None for direct calls
        """Handle clicks on the send button or Enter in the text field."""
        if self.new_message and self.new_message.value and self.new_message.value.strip() != "":
            user_msg_text = self.new_message.value.strip()
            user_msg = Message("You", user_msg_text, "user_message")

            # Add user message bubble and refresh chat only
            self.chat.controls.append(ChatMessage(user_msg, self.page))
            self.chat.update()

            # Clear input field immediately and update it
            self.new_message.value = ""
            self.new_message.update()

            # Show typing indicator and refresh chat
            if self.typing_indicator not in self.chat.controls:
                self.chat.controls.append(self.typing_indicator)
            self.typing_indicator.visible = True
            self.chat.update()

            # Disable only the send button while processing
            self.send_button.disabled = True
            self.send_button.update()

            try:
                await self._process_message(user_msg_text)
            finally:
                # Re-enable only the send button
                self.send_button.disabled = False
                self.send_button.update()

                # Hide typing indicator and refresh chat
                self.typing_indicator.visible = False
                if self.typing_indicator in self.chat.controls:
                    self.chat.controls.remove(self.typing_indicator)
                    self.chat.update()

                # Re-focus and update the text field so focus sticks
                self.new_message.focus()
                self.new_message.update()

    async def _process_message(self, user_message: str):
        """Core logic to interact with the agent and update the UI."""
        print(f"Processing message: {user_message}")
        try:
            # --- Ensure MCP is connected if needed --- 
            if self.spec.uses_mcp and self._mcp_connection_status != MCPConnectionStatus.CONNECTED:
                print("MCP not connected, attempting to connect before processing message...")
                await self._update_mcp_connection() # Try to connect/reconnect
                if self._mcp_connection_status != MCPConnectionStatus.CONNECTED:
                     # Display error and potentially stop if connection failed
                     error_msg = "Cannot process request: Failed to connect to required MCP Server. Check Settings."
                     print(f"ERROR: {error_msg}")
                     self._display_error_banner(error_msg)
                     self.page.update()
                     return # Stop processing if MCP is required but unavailable


            # --- Build or reuse agent instance --- 
            if self.agent_instance is None:
                print("Building agent instance...")
                # --- Remote AgentProxy path ---
                if self.spec.remote_ws_url:
                    try:
                        from omniagents.remote_proxy import RemoteAgentProxy
                    except ImportError as e:
                        raise RuntimeError(f"Remote mode requested but remote_proxy could not be imported: {e}")

                    token = os.getenv(self.spec.remote_auth_token_key) if self.spec.remote_auth_token_key else None
                    self.agent_instance = RemoteAgentProxy(self.spec.remote_ws_url, token)
                    print(f"RemoteAgentProxy configured for {self.spec.remote_ws_url}")
                else:
                    # --- Use default builder if spec.build_agent is None --- #
                    if self.spec.build_agent is None:
                        print("Using internal agent builder...")
                        builder_func = _default_build_agent
                    else:
                        # Use the builder provided in the spec (legacy/custom mode)
                        print("Using custom agent builder provided in AgentSpec...")
                        builder_func = self.spec.build_agent

                    # --- Call the selected builder --- #
                    self.agent_instance = await builder_func(
                        self.current_settings,
                        self._mcp_server_instance,
                        self # Pass the app instance itself
                    )

                # After either remote or local path ensure instance exists
                
                if not self.agent_instance:
                    raise ValueError("Agent builder function failed to return an agent instance.")
                print(f"Agent instance ready: {type(self.agent_instance)}")

            # --- Prepare input and run agent --- 
            # Reset SafeAgent state if applicable
            if hasattr(self.agent_instance, 'reset_run_state') and callable(self.agent_instance.reset_run_state):
                self.agent_instance.reset_run_state()

            # Prepare input for the agent and persist user message
            user_msg = {"role": "user", "content": user_message}
            self.conversation.append(user_msg)
            if self.local_session:
                self.local_session.append_message(user_msg)
            agent_input = self.conversation

            # Run the agent stream
            if self.spec.remote_ws_url and hasattr(self.agent_instance, "run_streamed"):
                # Use the proxy's built-in streamed runner (bypasses local Runner logic)
                result = self.agent_instance.run_streamed(agent_input)
            else:
                result = Runner.run_streamed(self.agent_instance, agent_input, max_turns=self.spec.max_turns)

            message_output = ""
            final_items = []
            assistant_message_control: Optional[ChatMessage] = None # To update assistant message live

            async for event in result.stream_events():
                if event.type == "raw_response_event": continue # Ignore raw OpenAI chunks
                elif event.type == "agent_updated_stream_event": continue # Ignore agent updates

                elif event.type == "run_item_stream_event":
                    # --------------------------------------------------
                    # Intercept approval requests (from remote SafeAgent)
                    # --------------------------------------------------
                    if getattr(event, "name", None) == "tool_approval_request":
                        payload = event.item if isinstance(event.item, dict) else {}
                        approval_id = payload.get("approval_id")
                        tool_name = payload.get("tool", "<tool>")
                        args_str = payload.get("arguments", "")

                        # Show approval UI and wait for user decision
                        approved, always_approve, err_msg = await self._request_tool_approval(tool_name, args_str)

                        # Send decision back to server (remote mode only)
                        try:
                            if self.spec.remote_ws_url and hasattr(self.agent_instance, "_client") and self.agent_instance._client:
                                await self.agent_instance._client.other.tool_approval_response(
                                    approval_id=approval_id,
                                    approved=approved,
                                    always_approve=always_approve,
                                    error_message=err_msg,
                                )
                        except Exception as _resp_err:
                            print(f"Error sending tool_approval_response: {_resp_err}")

                        # Continue to next streamed event
                        continue

                    item = event.item
                    final_items.append(item) # Store raw items for conversation history

                    # --- Handle Tool Calls --- 
                    if item.type == "tool_call_item":
                        tool_input = item.to_input_item()
                        args_str = tool_input.get('arguments', '{}')
                        tool_name = tool_input.get('name', 'unknown_tool')
                        call_id = tool_input.get('call_id')

                        if self.typing_indicator in self.chat.controls:
                            self.chat.controls.remove(self.typing_indicator)
                            self.chat.update()

                        interaction = ToolInteractionDisplay(
                            tool_name,
                            args_str,
                            self.spec.icon_map,
                            self.spec.theme_color,
                        )
                        target_list = self.logs if self.spec.layout == "tabs" and self.logs is not None else self.chat
                        target_list.controls.append(interaction)
                        if call_id:
                            self._interactions_by_call_id[call_id] = interaction
                        else:
                            self._pending_interactions.append(interaction)

                        self.chat.controls.append(self.typing_indicator)
                        if target_list is not self.chat:
                            target_list.update()
                        self.chat.update()
                        self.page.update()
                        self.page.update()

                    # --- Handle Tool Outputs (Debug Only) --- 
                    elif item.type == "tool_call_output_item":
                        is_rejection = False
                        try:
                            output_data = json.loads(str(item.output))
                            if isinstance(output_data, dict) and output_data.get("error") == "TOOL_REJECTED":
                                is_rejection = True
                                if self.spec.debug_flag:
                                    print(
                                        f"Debug: Tool call {output_data.get('tool')} rejected: {output_data.get('message')}"
                                    )
                        except (json.JSONDecodeError, TypeError):
                            pass

                        if not is_rejection:
                            if self.typing_indicator in self.chat.controls:
                                self.chat.controls.remove(self.typing_indicator)

                            tool_output = str(item.output)
                            call_id = None
                            if hasattr(item, "raw_item"):
                                raw = item.raw_item
                                if isinstance(raw, dict):
                                    call_id = raw.get("call_id")
                                else:
                                    call_id = getattr(raw, "call_id", None)

                            interaction = None
                            if call_id and call_id in self._interactions_by_call_id:
                                interaction = self._interactions_by_call_id.pop(call_id)
                            else:
                                for idx in range(len(self._pending_interactions) - 1, -1, -1):
                                    cand = self._pending_interactions[idx]
                                    if not cand.has_output():
                                        interaction = cand
                                        del self._pending_interactions[idx]
                                        break

                            if interaction is None:
                                interaction = ToolInteractionDisplay(
                                    getattr(item, "name", "tool"),
                                    "",
                                    self.spec.icon_map,
                                    self.spec.theme_color,
                                    tool_output,
                                )
                                target_list = self.logs if self.spec.layout == "tabs" and self.logs is not None else self.chat
                                target_list.controls.append(interaction)
                            else:
                                interaction.set_output(tool_output)

                            self.chat.controls.append(self.typing_indicator)
                            if self.spec.layout == "tabs" and self.logs is not None:
                                self.logs.update()
                            self.chat.update()
                            self.page.update()

                    # --- Handle Assistant Message Stream --- 
                    elif item.type == "message_output_item":
                        chunk = ItemHelpers.text_message_output(item)
                        if chunk:
                            message_output += chunk
                            # Create or update the assistant message bubble
                            if assistant_message_control is None:
                                # Remove indicator when first text arrives
                                if self.typing_indicator in self.chat.controls:
                                    self.chat.controls.remove(self.typing_indicator)
                                assistant_message = Message(self.spec.name, message_output, "assistant_message")
                                assistant_message_control = ChatMessage(assistant_message, self.page, assistant_name=self.spec.name)
                                self.chat.controls.append(assistant_message_control)
                            else:
                                # Update existing bubble's text
                                assistant_message_control.content.controls[0].content.value = message_output # Access Markdown value
                            self.chat.update()
                            self.page.update() # Update UI with new chunk

            # --- Finalize after stream --- 
            self.typing_indicator.visible = False # Ensure indicator is hidden
            if self.typing_indicator in self.chat.controls:
                self.chat.controls.remove(self.typing_indicator)

            # Update conversation history with structured items
            for item in final_items:
                # Convert items to the dictionary format expected by the agent
                if hasattr(item, 'to_input_item'):
                    input_item = item.to_input_item()
                    # Ensure it's a valid dict and avoid duplicates
                    if isinstance(input_item, dict) and input_item:
                         # Avoid adding the initial user message again
                        is_initial_user_msg = input_item.get("role") == "user" and input_item.get("content") == user_message
                        if not is_initial_user_msg:
                            # Prevent adding duplicate tool calls/outputs if history already has them?
                            # This might be tricky, requires comparing complex dicts. Let's keep it simple for now.
                            self.conversation.append(input_item)
                            if self.local_session:
                                self.local_session.append_message(input_item)
                # Handle message output item (final assistant message)
                elif item.type == "message_output_item":
                     # Check if the last item in conversation is already this assistant message
                     final_assistant_content = ItemHelpers.text_message_output(item)
                     if not (self.conversation and
                             self.conversation[-1].get("role") == "assistant" and
                             self.conversation[-1].get("content") == final_assistant_content):
                         # Add only if it's not a duplicate of the last message
                         msg = {"role": "assistant", "content": final_assistant_content}
                         self.conversation.append(msg)
                         if self.local_session:
                             self.local_session.append_message(msg)

            # Debug: Print final conversation state
            # print("--- Final Conversation History ---")
            # for msg in self.conversation:
            #     print(f"- Role: {msg.get('role')}, Type: {msg.get('type')}, Content/Name: {msg.get('content', msg.get('name'))}")
            # print("----------------------------------")

            if self.spec.layout == "tabs" and self.logs is not None:
                self.logs.update()
            self.chat.update()
            self.page.update() # Final update after processing

        except Exception as e:
            print(f"Error in process_message: {e}")
            import traceback
            traceback.print_exc() # Print full traceback for debugging

            # Ensure indicator is hidden on error
            self.typing_indicator.visible = False
            if self.typing_indicator in self.chat.controls:
                self.chat.controls.remove(self.typing_indicator)

            # Show a user-friendly error message in the chat
            error_message = f"An error occurred: {str(e)}. Check logs or settings."
            self._display_error_banner(error_message)
            self.page.update() # Ensure error banner is shown

    async def _request_tool_approval(self, tool_name: str, formatted_args: str) -> ApprovalResult:
        """Displays the tool approval UI and waits for user interaction."""
        if not self.page or not self.chat:
            print("Error: Flet page/chat context not available for approval callback.")
            return False, False, "Internal UI error during approval."

        approval_id = f"approval_{uuid.uuid4()}"
        approval_future = asyncio.get_event_loop().create_future()
        self._approval_state['pending_futures'][approval_id] = approval_future

        # Ensure typing indicator is removed before showing prompt
        if self.typing_indicator in self.chat.controls:
            self.chat.controls.remove(self.typing_indicator)
            self.chat.update()

        def create_handler(approved: bool, always_approve: bool, rejection_message: Optional[str] = None):
            async def handler(e): # Make handler async
                display = self._approval_state['active_displays'].get(approval_id)
                if display and display in self.chat.controls:
                    self.chat.controls.remove(display)
                    del self._approval_state['active_displays'][approval_id]

                # Add rejection message if tool was rejected
                if not approved:
                    rejection_ui = ft.Container(
                        content=ft.Text(
                            f"❌ Tool call ({tool_name}) rejected by user.",
                            color=ft.Colors.RED_700, italic=True, size=13
                        ),
                        margin=ft.margin.only(left=20, top=0, bottom=8),
                    )
                    self.chat.controls.append(rejection_ui)

                # Add typing indicator back if processing continues (i.e., approved)
                # Or if agent needs to respond after rejection (depends on halt setting)
                # We add it back generally, _process_message handles final removal.
                if self.typing_indicator not in self.chat.controls:
                    self.chat.controls.append(self.typing_indicator)
                self.typing_indicator.visible = True # Show indicator while agent potentially processes rejection/approval

                self.chat.update()
                self.page.update()

                # Set the future result if it's not already done
                if not approval_future.done():
                     approval_future.set_result((approved, always_approve, rejection_message))

                # Clean up future reference
                if approval_id in self._approval_state['pending_futures']:
                    del self._approval_state['pending_futures'][approval_id]

            # Use page.run_task to schedule the async handler on the main event loop
            return lambda e: self.page.run_task(handler, e) # <-- Changed this line

        # Use the ToolApprovalRequest UI component from core.ui
        approval_display = ToolApprovalRequest(
            tool_name=tool_name,
            args_str=formatted_args,
            on_approve=create_handler(True, False),
            on_approve_session=create_handler(True, True),
            on_reject=create_handler(False, False, "User rejected tool call")
        )

        self._approval_state['active_displays'][approval_id] = approval_display
        self.chat.controls.append(approval_display)
        self.chat.update()
        self.page.update()

        # Wait for user decision
        result = await approval_future
        return result

    def _display_error_banner(self, message: str):
        """Displays a persistent error banner in the chat list."""
        # Check if a similar error banner already exists
        for control in self.chat.controls:
             if isinstance(control, ft.Container) and hasattr(control, 'data') and control.data == 'error_banner':
                 # Optionally update existing banner text or just return
                 # For simplicity, let's just return to avoid duplicate banners
                 print("Skipping duplicate error banner.")
                 return

        error_banner = ft.Container(
            content=ft.Row([
                ft.Icon(name=ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600, size=20),
                ft.Text(message, color=ft.Colors.BLACK87, size=14, expand=True, weight="w500"),
                # Removed close button to make it persistent until next successful run or restart
            ], alignment=ft.MainAxisAlignment.START, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ft.Colors.RED_50, padding=ft.padding.symmetric(horizontal=16, vertical=10),
            border_radius=8, border=ft.border.all(1, ft.Colors.RED_200),
            margin=ft.margin.only(left=8, right=8, top=8, bottom=8),
            data='error_banner' # Add data attribute for identification
        )

        # Add error banner (insert near the top, after welcome maybe?)
        insert_index = 1 # After initial padding/welcome
        if len(self.chat.controls) > insert_index:
            self.chat.controls.insert(insert_index, error_banner)
        else:
            self.chat.controls.append(error_banner)
        self.chat.update()
        self.page.update()

    def _remove_control(self, control: ft.Control):
        """Safely removes a specific control from the chat list."""
        try:
            if self.chat and control in self.chat.controls:
                self.chat.controls.remove(control)
                # No page update here, caller should handle batching updates
        except Exception as e:
            print(f"Error removing control: {e}")

    # --- MCP Management --- 

    def _get_mcp_status_color(self) -> str:
        """Returns the color for the MCP status indicator."""
        status_map = {
            MCPConnectionStatus.UNKNOWN: ft.Colors.ORANGE,
            MCPConnectionStatus.NOT_CONFIGURED: ft.Colors.GREY_400,
            MCPConnectionStatus.CONNECTING: ft.Colors.YELLOW_700,
            MCPConnectionStatus.CONNECTED: ft.Colors.GREEN,
            MCPConnectionStatus.ERROR: ft.Colors.RED,
            MCPConnectionStatus.DISCONNECTED: ft.Colors.ORANGE,
        }
        return status_map.get(self._mcp_connection_status, ft.Colors.ORANGE)

    def _get_mcp_status_tooltip(self) -> str:
        """Returns the tooltip text for the MCP status indicator."""
        status_map = {
            MCPConnectionStatus.UNKNOWN: "MCP Status: Unknown",
            MCPConnectionStatus.NOT_CONFIGURED: "MCP Not Configured",
            MCPConnectionStatus.CONNECTING: "MCP Connecting...",
            MCPConnectionStatus.CONNECTED: "MCP Connected",
            MCPConnectionStatus.ERROR: "MCP Connection Error",
            MCPConnectionStatus.DISCONNECTED: "MCP Disconnected",
        }
        return status_map.get(self._mcp_connection_status, "MCP Status: Unknown")

    def _update_mcp_ui_indicator(self):
        """Updates the UI element for MCP status."""
        if self.page and self._mcp_connection_status_indicator:
            self._mcp_connection_status_indicator.bgcolor = self._get_mcp_status_color()
            self._mcp_connection_status_indicator.tooltip = self._get_mcp_status_tooltip()
            # Consider if self.page.update() is needed here or batched later
            # It's usually safer to batch updates, but for immediate feedback:
            self.page.update()

    async def _update_mcp_connection(self):
        """Connects, disconnects, or updates the MCP server instance based on settings."""
        if not self.spec.uses_mcp:
            if self._mcp_server_instance: # Disconnect if MCP was used but now disabled
                await self._disconnect_mcp()
            self._mcp_connection_status = MCPConnectionStatus.NOT_CONFIGURED
            self._update_mcp_ui_indicator()
            return

        mcp_url = self.current_settings.get(self.spec.mcp_url_setting_key)
        mcp_auth_token = self.current_settings.get(self.spec.mcp_token_setting_key)

        if not mcp_url:
            print("MCP URL not configured in settings.")
            if self._mcp_server_instance:
                await self._disconnect_mcp()
            self._mcp_connection_status = MCPConnectionStatus.NOT_CONFIGURED
            self._update_mcp_ui_indicator()
            return

        # --- Prepare connection parameters --- 
        mcp_params = {
            "url": mcp_url,
            "timeout": 30,
            "sse_read_timeout": 600,
            "headers": {},
        }
        if mcp_auth_token:
            mcp_params["headers"]["Authorization"] = f"Bearer {mcp_auth_token}"

        # --- Determine if reconnection or update is needed --- 
        should_connect = False
        needs_recreate = False
        if self._mcp_server_instance is None:
            print("No existing MCP instance, creating new one.")
            should_connect = True
            needs_recreate = True
        elif self._mcp_server_instance.params.get("url") != mcp_url or \
             self._mcp_server_instance.params.get("headers", {}).get("Authorization") != mcp_params["headers"].get("Authorization"):
            print("MCP configuration changed, recreating connection.")
            await self._disconnect_mcp() # Disconnect old one first
            should_connect = True
            needs_recreate = True
        elif self._mcp_server_instance._sse_client is None or self._mcp_server_instance._sse_client.is_closed:
             print("Existing MCP instance found but connection is closed, attempting to reconnect.")
             should_connect = True # Try to reconnect existing instance
        else:
             print("Reusing existing and connected MCP server instance.")
             self._mcp_connection_status = MCPConnectionStatus.CONNECTED # Already connected
             self._update_mcp_ui_indicator()
             return # Nothing more to do

        # --- Attempt connection/reconnection --- 
        if should_connect:
            print("Attempting to connect/reconnect to MCP server...")
            self._mcp_connection_status = MCPConnectionStatus.CONNECTING
            self._update_mcp_ui_indicator()
            try:
                if needs_recreate or self._mcp_server_instance is None:
                    # Dynamically import here if needed for instantiation
                    from agents.mcp import MCPServerSse
                    self._mcp_server_instance = MCPServerSse(
                        name=f"{self.spec.name}_MCP_Server", # Generic name based on app
                        params=mcp_params,
                    )
                else: # Update params if reconnecting existing instance
                    self._mcp_server_instance.params = mcp_params

                await self._mcp_server_instance.__aenter__() # Connects/reconnects
                self._mcp_connection_status = MCPConnectionStatus.CONNECTED
                print(f"MCP Server connected successfully to {mcp_url}")
            except Exception as e_conn:
                print(f"############# MCP CONNECTION FAILED #############")
                print(f"Error connecting to MCP server at {mcp_url}: {e_conn}")
                print(f"###############################################")
                self._mcp_connection_status = MCPConnectionStatus.ERROR
                # Don't keep the failed instance
                if self._mcp_server_instance:
                    try: # Attempt cleanup even on connection error
                         await self._mcp_server_instance.__aexit__(None, None, None)
                    except: pass # Ignore cleanup errors here
                self._mcp_server_instance = None
                # Reset agent if connection fails? Depends on desired behavior.
                # For now, just show error. Agent build/run will handle missing MCP.
            finally:
                 self._update_mcp_ui_indicator() # Update UI with final status


    async def _disconnect_mcp(self):
        """Gracefully disconnects the MCP server instance."""
        if self._mcp_server_instance:
            print("Disconnecting MCP server...")
            try:
                await self._mcp_server_instance.__aexit__(None, None, None)
                print("MCP connection closed.")
                self._mcp_connection_status = MCPConnectionStatus.DISCONNECTED
            except Exception as e_close:
                print(f"Error closing MCP connection: {e_close}")
                self._mcp_connection_status = MCPConnectionStatus.ERROR # Error state if close fails
            finally:
                self._mcp_server_instance = None # Clear instance ref
                self._update_mcp_ui_indicator()


    # --- Navigation --- 

    def _create_main_view(self):
        """Creates the main chat view content."""
        conversation_column = ft.Column(
            [
                ft.Container(
                    content=self.chat,
                    expand=True,
                    bgcolor=ft.Colors.with_opacity(0.98, ft.Colors.WHITE),
                ),
                ft.Container(
                    content=ft.Row(
                        [self.new_message, self.send_button],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.only(left=16, right=16, top=12, bottom=16),
                    bgcolor=ft.Colors.WHITE,
                    border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                ),
            ],
            spacing=12,
            expand=True,
        )

        if self.spec.layout == "tabs" and self.logs is not None:
            logs_container = ft.Container(
                content=self.logs,
                expand=True,
                bgcolor=ft.Colors.with_opacity(0.98, ft.Colors.WHITE),
            )
            tabs = ft.Tabs(
                tabs=[
                    ft.Tab(text="Conversation", content=conversation_column),
                    ft.Tab(text="Logs", content=logs_container),
                ],
                expand=True,
            )
            main_content = tabs
        else:
            main_content = conversation_column

        return ft.View(
            route="/",
            controls=[self.header, main_content],
            spacing=12,
            padding=0,
            bgcolor=ft.Colors.WHITE,
        )

    def _create_settings_view(self):
        """Creates the settings view dynamically."""
        return SettingsScreen(
            spec=self.spec,
            fields=self.spec.settings_fields,
            initial_values=self.current_settings,
            on_save=self._handle_settings_save,
            on_back=lambda _: self.page.go("/") # Use lambda for simplicity
        )

    def _go_to_settings(self, e):
        """Navigate to the settings view."""
        self.page.go("/settings")

    def _route_change(self, route):
        """Handle route changes between main and settings views."""
        print(f"Route changing to: {self.page.route}")
        self.page.views.clear()
        if self.page.route == "/settings":
            self.page.views.append(self._create_settings_view())
        else: # Default to main view
             # Ensure main view components are created if they weren't
             if not self.header or not self.chat or not self.new_message:
                 self._build_ui() # Rebuild UI ensures header updates if needed
             self.page.views.append(self._create_main_view())

        self.page.update()

    def _view_pop(self, view):
        """Handles the back button press, especially from settings."""
        print("View popped")
        self.page.views.pop()
        top_view = self.page.views[-1]
        self.page.go(top_view.route) # Go to the route of the view below

    async def _handle_settings_save(self, new_settings: Dict[str, Any]):
        """Save updated settings, update MCP, and navigate back."""
        print("Saving settings...")
        old_mcp_url = self.current_settings.get(self.spec.mcp_url_setting_key)
        old_mcp_token = self.current_settings.get(self.spec.mcp_token_setting_key)

        self.current_settings = new_settings
        # --- AWAIT the async save_settings --- #
        await save_settings(self.page, self.spec.client_storage_prefix, self.current_settings)

        # Check if MCP settings changed and update connection if needed
        mcp_settings_changed = (
            self.spec.uses_mcp and
            (new_settings.get(self.spec.mcp_url_setting_key) != old_mcp_url or
             new_settings.get(self.spec.mcp_token_setting_key) != old_mcp_token)
        )

        if mcp_settings_changed:
            print("MCP settings changed, updating connection...")
            await self._update_mcp_connection() # Reconnect/disconnect based on new settings

        # Reset agent instance to force re-initialization with new settings
        self.agent_instance = None
        print("Agent reset due to settings change.")

        # Rebuild header controls *after* potential MCP update to reflect status
        if self.header:
            header_right_controls = []
            if self.spec.uses_mcp and self.spec.show_mcp_status_indicator:
                # Ensure indicator exists and reflects current status
                if not self._mcp_connection_status_indicator:
                     self._mcp_connection_status_indicator = ft.Container(
                         width=12, height=12, border_radius=6, margin=ft.margin.only(right=8)
                     )
                self._mcp_connection_status_indicator.bgcolor = self._get_mcp_status_color()
                self._mcp_connection_status_indicator.tooltip = self._get_mcp_status_tooltip()
                header_right_controls.append(self._mcp_connection_status_indicator)

            header_right_controls.append(self.settings_button) # Add settings button back

            if self.spec.header_controls_builder:
                 try:
                     header_right_controls.extend(self.spec.header_controls_builder(self))
                 except Exception as e:
                     print(f"Error calling header_controls_builder after save: {e}")

            # Update controls in place (assuming header structure)
            try:
                button_row = self.header.content.controls[0].content.controls[1] # Title/Button Row
                button_row.controls = header_right_controls
                print("Header controls updated after save.")
            except (IndexError, AttributeError) as e:
                 print(f"Error accessing header structure to update controls: {e}")

        self.page.go("/") # Go back to main chat view
        # Optionally show a confirmation snackbar
        settings_saved_snackbar = ft.SnackBar(ft.Text("Settings saved!"), open=True)
        self.page.overlay.append(settings_saved_snackbar)
        self.page.update() # Update needed to show snackbar
        # self.page.update() # Update is implicitly handled by page.go() and snackbar

# End of AgentApp class 