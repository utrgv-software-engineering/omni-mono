import flet as ft
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Optional, Protocol, Awaitable, TYPE_CHECKING

# Import from other core modules
from .ui import Message, ChatMessage, ToolCallDisplay, ToolOutputDisplay
from .settings import SettingsScreen, SettingField
from .storage import load_settings, save_settings

# Import agents types for type hinting (use Protocol for flexibility)
from agents import ItemHelpers
from agents import Runner # For run_streamed

# Use TYPE_CHECKING to avoid circular import issues at runtime
if TYPE_CHECKING:
    from .app_base import AgentApp # Import AgentApp for type hint

# Define a protocol for Agent-like objects to avoid circular dependencies
# and allow for different Agent types (Agent, SafeAgent, etc.)
class AgentLike(Protocol):
    # Define methods expected from an Agent
    # Add other necessary methods/attributes if your agent interaction requires them
    pass

@dataclass
class AgentSpec:
    """Specification for configuring an AgentApp."""
    # --- Basic App Info ---
    name: str # Agent's name (e.g., "Indie", "IBJJF Assistant")
    header_title: str # Title shown in the app header
    page_title: str # Title for the window/tab
    welcome_text: str # Initial system message
    client_storage_prefix: str # Prefix for saving settings (e.g., "ibjjf_flet.")

    # --- Agent Configuration ---
    get_agent_instructions: Callable[[], str] # Function to get agent instructions
    build_agent: Callable[[Dict[str, Any], 'AgentApp'], Awaitable[AgentLike]] # Function to create the agent instance
    available_tools: List[Callable] # List of tool functions available to the agent

    # --- UI/UX Configuration ---
    icon_map: Dict[str, str] = field(default_factory=dict) # Map tool names to Flet icons
    settings_fields: List[SettingField] = field(default_factory=list) # Fields for the settings screen
    debug_flag: bool = False # Whether debug mode is enabled
    page_title_suffix: str = "" # Suffix for page title (e.g., " (Debug Mode)")
    theme_color: str = ft.Colors.BLUE # Primary theme color

    # --- Optional App-Specific Customization ---
    # Function to build extra controls for the header (e.g., MCP status)
    header_controls_builder: Optional[Callable[[Any], List[ft.Control]]] = None
    # Function to run any app-specific cleanup logic
    cleanup_handler: Optional[Callable[[], Awaitable[None]]] = None


class AgentApp:
    """Base class for creating Flet Agent applications."""
    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.page: Optional[ft.Page] = None
        self.chat: Optional[ft.ListView] = None
        self.new_message: Optional[ft.TextField] = None
        self.typing_indicator: Optional[ft.Container] = None
        self.settings_button: Optional[ft.IconButton] = None
        self.header: Optional[ft.Container] = None
        self.send_button: Optional[ft.IconButton] = None

        self.conversation: List[Dict[str, Any]] = []
        self.agent_instance: Optional[AgentLike] = None
        self.current_settings: Dict[str, Any] = {}

    async def main(self, page: ft.Page):
        """Main entry point for the Flet application."""
        self.page = page
        await self._configure_page()
        await self._load_initial_settings()
        self._build_ui()
        self._setup_routing()
        self._show_initial_warnings()

        # Set cleanup handler if provided
        if self.spec.cleanup_handler:
            self.page.on_close = self.spec.cleanup_handler

        # Create the initial main view
        self._create_main_view()

    async def _configure_page(self):
        """Configure basic page properties."""
        self.page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
        self.page.title = self.spec.page_title + (self.spec.page_title_suffix if self.spec.debug_flag else "")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.window_width = 900
        self.page.window_height = 750
        self.page.padding = 0
        self.page.bgcolor = ft.colors.with_opacity(0.98, ft.Colors.WHITE)
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
            print(f"Initial settings loaded: {self.current_settings.keys()}")
            # Optionally update global os.environ or config objects here if tools need them
            # Example: os.environ["SERPAPI_API_KEY"] = self.current_settings.get("serpapi_api_key", "")
            # Be careful with global state; passing settings to build_agent is cleaner.
        except Exception as e:
            print(f"Error loading initial settings: {e}")
            # Initialize with defaults if loading fails
            self.current_settings = {field.key: field.default for field in self.spec.settings_fields}

        self.page.update()

    def _build_ui(self):
        """Build the main UI components."""
        # Typing Indicator
        self.typing_indicator = ft.Container(
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

        # Chat View
        self.chat = ft.ListView(
            expand=True,
            spacing=0,
            auto_scroll=True,
            padding=ft.padding.only(left=20, right=20, top=24, bottom=24),
        )

        # Settings Button
        self.settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            icon_color=ft.Colors.WHITE,
            icon_size=20,
            tooltip="API Settings",
            on_click=self._go_to_settings
        )

        # Header
        header_right_controls = [self.settings_button]
        if self.spec.header_controls_builder:
            # Add app-specific controls (e.g., MCP status indicator)
            # The builder function receives 'self' in case it needs app state
            header_right_controls.extend(self.spec.header_controls_builder(self))

        self.header = ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(self.spec.header_title, color=ft.Colors.WHITE, size=22, weight="bold"),
                            # Optionally add a subtitle here if needed
                        ], spacing=2, tight=True),
                        ft.Row(header_right_controls, spacing=12), # Include optional controls
                    ], spacing=12, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=ft.padding.symmetric(horizontal=20, vertical=16),
                ),
                ft.Container(
                    content=ft.Row([
                        ft.Icon(name=ft.Icons.BUG_REPORT, color=ft.Colors.WHITE, size=16),
                        ft.Text("Debug Mode Enabled", color=ft.Colors.WHITE, size=13),
                    ], spacing=8),
                    padding=ft.padding.symmetric(horizontal=20, vertical=8),
                    bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                    visible=self.spec.debug_flag
                ) if self.spec.debug_flag else ft.Container(height=0),
            ]),
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                colors=[ft.Colors.INDIGO_900, ft.Colors.INDIGO_700, self.spec.theme_color], # Use theme color
            ),
            shadow=ft.BoxShadow(
                spread_radius=0, blur_radius=8,
                color=ft.Colors.with_opacity(0.2, ft.Colors.BLACK), offset=ft.Offset(0, 2)
            ),
        )

        # Input Field
        self.new_message = ft.TextField(
            hint_text="Ask the assistant...",
            hint_style=ft.TextStyle(color=ft.Colors.BLACK45),
            autofocus=True, shift_enter=True, min_lines=1, max_lines=5, filled=True,
            border_color=ft.Colors.BLACK12, focused_border_color=self.spec.theme_color,
            text_size=15, text_style=ft.TextStyle(color=ft.Colors.BLACK), cursor_color=self.spec.theme_color,
            expand=True, border_radius=24, content_padding=ft.padding.only(left=20, right=20, top=14, bottom=14),
            on_submit=self._send_message_click,
        )

        # Send Button
        self.send_button = ft.IconButton(
            icon=ft.Icons.SEND_ROUNDED, icon_color=self.spec.theme_color,
            icon_size=22, tooltip="Send message", on_click=self._send_message_click,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=30), padding=12,
                overlay_color=ft.Colors.with_opacity(0.1, self.spec.theme_color)
            ),
        )

        # Initial Welcome Message
        welcome_msg = Message("System", self.spec.welcome_text, "system_message")
        self.chat.controls.append(ChatMessage(welcome_msg, self.page, assistant_name=self.spec.name))
        self.chat.controls.append(ft.Container(height=12))

    def _setup_routing(self):
        """Set up page routing for main view and settings."""
        self.page.on_route_change = self._route_change

    def _show_initial_warnings(self):
        """Show snackbar warnings based on initial settings (e.g., missing keys)."""
        # Example: Check for a required key
        # required_key = "openai_api_key"
        # if not self.current_settings.get(required_key):
        #     field_label = next((f.label for f in self.spec.settings_fields if f.key == required_key), required_key)
        #     snack_bar = ft.SnackBar(ft.Text(f"Warning: {field_label} missing. Go to Settings."), open=True)
        #     self.page.overlay.append(snack_bar)
        #     self.page.update()
        pass # Add specific checks as needed based on self.current_settings

    # --- Event Handlers & Actions ---

    async def _send_message_click(self, e):
        """Handle clicks on the send button or Enter in the text field."""
        if self.new_message and self.new_message.value != "":
            user_msg_text = self.new_message.value
            user_msg = Message("You", user_msg_text, "user_message")
            self.chat.controls.append(ChatMessage(user_msg, self.page))
            self.chat.controls.append(self.typing_indicator)
            self.typing_indicator.visible = True

            self.new_message.value = ""
            self.new_message.focus()
            self.page.update()

            await self._process_message(user_msg_text)

    async def _process_message(self, user_message: str):
        """Core logic to interact with the agent and update the UI."""
        print(f"Processing message: {user_message}")
        try:
            # Build or reuse agent instance
            # Pass current settings AND the app instance (self) to the builder function
            if self.agent_instance is None:
                print("Building agent instance...")
                # Pass self (the AgentApp instance) to the build_agent function
                self.agent_instance = await self.spec.build_agent(self.current_settings, self)
                if not self.agent_instance:
                     raise ValueError("Agent builder function failed to return an agent instance.")
                print(f"Agent instance created: {type(self.agent_instance)}")

            # Prepare input for the agent
            agent_input = self.conversation + [{"role": "user", "content": user_message}]

            # Run the agent stream
            result = Runner.run_streamed(self.agent_instance, agent_input)

            message_output = ""
            final_items = []
            tool_call_active = False

            async for event in result.stream_events():
                if event.type == "raw_response_event": continue
                elif event.type == "agent_updated_stream_event": continue
                elif event.type == "run_item_stream_event":
                    item = event.item
                    final_items.append(item)

                    if item.type == "tool_call_item":
                        tool_call_active = True
                        args_str = item.to_input_item()['arguments']
                        try:
                            args_dict = json.loads(args_str)
                            formatted_args = ", ".join([f"{k}: {repr(v)[:50]}{'...' if len(repr(v)) > 50 else ''}" for k, v in args_dict.items()])
                        except:
                            formatted_args = args_str[:150] + ('...' if len(args_str) > 150 else '')
                        tool_name = item.to_input_item()['name']

                        if self.typing_indicator in self.chat.controls:
                            self.chat.controls.remove(self.typing_indicator)

                        tool_call_display = ToolCallDisplay(
                            tool_name, formatted_args, self.spec.icon_map, self.spec.theme_color
                        )
                        self.chat.controls.append(tool_call_display)
                        self.chat.controls.append(self.typing_indicator) # Add indicator back
                        self.page.update()

                    elif item.type == "tool_call_output_item":
                        tool_call_active = False # Tool call finished
                        if self.spec.debug_flag:
                            tool_output = str(item.output)
                             # Simple check for SafeAgent rejection output format
                            is_rejection = False
                            try:
                                output_data = json.loads(tool_output)
                                if isinstance(output_data, dict) and output_data.get("error") == "TOOL_REJECTED":
                                    is_rejection = True
                            except (json.JSONDecodeError, TypeError):
                                pass # Not JSON or not the rejection format

                            if not is_rejection: # Don't display the rejection json blob
                                if self.typing_indicator in self.chat.controls:
                                    self.chat.controls.remove(self.typing_indicator)
                                output_display = ToolOutputDisplay(tool_output)
                                self.chat.controls.append(output_display)
                                self.chat.controls.append(self.typing_indicator) # Add indicator back
                                self.page.update()
                            else:
                                print(f"Debug: Tool call rejected, output: {tool_output}")

                    elif item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(item)

            # Hide typing indicator after loop finishes
            self.typing_indicator.visible = False
            if self.typing_indicator in self.chat.controls:
                self.chat.controls.remove(self.typing_indicator)

            # Update conversation history
            for item in final_items:
                if hasattr(item, 'to_input_item'):
                    input_item = item.to_input_item()
                    if input_item:
                        # Avoid adding duplicate user message if already added above
                        # This depends on how Runner structures the final items
                        is_new_user_message = input_item.get("role") == "user" and input_item.get("content") == user_message
                        if not (len(self.conversation) > 0 and is_new_user_message and self.conversation[-1] == input_item):
                             self.conversation.append(input_item)

            # Display final assistant message
            if message_output:
                assistant_message = Message(
                    user_name=self.spec.name, # Use agent's name
                    text=message_output,
                    message_type="assistant_message"
                )
                self.chat.controls.append(ChatMessage(assistant_message, self.page, assistant_name=self.spec.name))
                self.chat.controls.append(ft.Container(height=12))
                self.page.update()

        except Exception as e:
            print(f"Error in process_message: {e}")
            import traceback
            traceback.print_exc() # Print full traceback for debugging

            self.typing_indicator.visible = False
            if self.typing_indicator in self.chat.controls:
                self.chat.controls.remove(self.typing_indicator)

            # Show a user-friendly error message in the chat
            error_message = f"An error occurred: {str(e)}. Please check settings or logs."
            self._display_error_banner(error_message)

    def _display_error_banner(self, message: str):
        """Displays an error banner in the chat list."""
        error_banner = ft.Container(
            content=ft.Row([
                ft.Icon(name=ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600, size=24),
                ft.Text(message, color=ft.Colors.BLACK87, size=14, expand=True),
                ft.IconButton(
                    icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_400, icon_size=18, tooltip="Dismiss",
                    on_click=lambda e, banner=None: self._remove_control(banner) # Capture banner
                )
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ft.Colors.RED_50, padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=8, border=ft.border.all(1, ft.Colors.RED_200),
            margin=ft.margin.symmetric(horizontal=8, vertical=16),
        )
        # Need to assign the banner to its own dismiss function
        error_banner.content.controls[-1].on_click = lambda e, banner=error_banner: self._remove_control(banner)

        # Add error banner (insert at top or append)
        if len(self.chat.controls) > 0: self.chat.controls.insert(0, error_banner)
        else: self.chat.controls.append(error_banner)
        self.page.update()

    def _remove_control(self, control: ft.Control):
        """Safely removes a control from the chat list."""
        if control in self.chat.controls:
            self.chat.controls.remove(control)
            self.page.update()

    # --- Navigation --- 

    def _create_main_view(self):
        """Creates the main chat view."""
        main_view = ft.View(
            route="/",
            controls=[
                self.header,
                ft.Container(
                    content=self.chat, expand=True, bgcolor=ft.colors.with_opacity(0.98, ft.Colors.WHITE),
                ),
                ft.Container(
                    content=ft.Row([self.new_message, self.send_button], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.only(left=16, right=16, top=12, bottom=16),
                    bgcolor=ft.Colors.WHITE, border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                ),
            ],
            padding=0, bgcolor=ft.Colors.WHITE
        )
        self.page.views.clear()
        self.page.views.append(main_view)
        self.page.update()

    def _create_settings_view(self):
        """Creates the settings view dynamically."""
        settings_view = SettingsScreen(
            fields=self.spec.settings_fields,
            initial_values=self.current_settings,
            on_save=self._handle_settings_save,
            on_back=lambda _: self.page.go("/")
        )
        self.page.views.clear()
        self.page.views.append(settings_view)
        self.page.update()

    def _go_to_settings(self, e):
        """Navigate to the settings view."""
        self.page.go("/settings")

    def _route_change(self, e):
        """Handle route changes between main and settings views."""
        if e.route == "/settings": self._create_settings_view()
        elif e.route == "/": self._create_main_view()
        else: self._create_main_view() # Default to main view

    def _handle_settings_save(self, new_settings: Dict[str, Any]):
        """Save updated settings and navigate back to main view."""
        self.current_settings = new_settings
        save_settings(self.page, self.spec.client_storage_prefix, self.current_settings)

        # Reset agent instance to force re-initialization with new settings on next message
        self.agent_instance = None
        print("Agent reset due to settings change.")

        # Optionally update global os.environ or config objects here if needed
        # Example: os.environ["SERPAPI_API_KEY"] = self.current_settings.get("serpapi_api_key", "")

        self.page.go("/") # Go back to main chat view 