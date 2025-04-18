import flet as ft
import asyncio
import json
import os
import uuid # Added for unique approval IDs
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Optional, Protocol, Awaitable, TYPE_CHECKING, Tuple # Added Tuple

# Import from other core modules
# Use absolute imports for robustness within the package
from flet_agent_core.ui import Message, ChatMessage, ToolCallDisplay, ToolOutputDisplay, ToolApprovalRequest # Added ToolApprovalRequest
from flet_agent_core.settings import SettingsScreen, SettingField
from flet_agent_core.storage import load_settings, save_settings

# Import agents types for type hinting (use Protocol for flexibility)
from agents import Agent, ItemHelpers # Added Agent
from agents import Runner # For run_streamed

# Import SafeAgent components directly as they are part of the core package now
from .safe_agent import SafeAgent, ApprovalResult # Added SafeAgent and ApprovalResult

# Use TYPE_CHECKING to avoid circular import issues at runtime
if TYPE_CHECKING:
    # Use absolute import if AgentApp is defined elsewhere in the package,
    # otherwise keep relative if it's defined in this file (which it is).
    from .app_base import AgentApp # Keep relative for self-reference check

# Define a protocol for Agent-like objects to avoid circular dependencies
# and allow for different Agent types (Agent, SafeAgent, etc.)
class AgentLike(Protocol):
    # Define methods/attributes expected from an Agent
    name: str
    tools: List[Any]
    model: str
    instructions: str
    model_settings: Optional[Any]
    mcp_servers: Optional[List[Any]]

    async def get_all_tools(self) -> List[Any]: ...
    # Add other necessary methods/attributes if your agent interaction requires them

    # Optional method for resetting state (used by SafeAgent)
    def reset_run_state(self): ...

@dataclass
class SafeAgentOptions:
    """Options specific to SafeAgent behavior."""
    skip_approvals: bool = False
    halt_on_rejection: bool = True
    safe_tool_names: List[str] = field(default_factory=list)
    safe_tool_patterns: List[str] = field(default_factory=list)

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
    build_agent: Callable[[Dict[str, Any], 'AgentApp'], Awaitable[AgentLike]] # Function to create the *core* agent instance
    available_tools: List[Callable] # List of tool functions available *if not using MCP*

    # --- Agent Safety Configuration ---
    use_safe_agent: bool = False # Simple flag to enable safety features
    safe_agent_options: SafeAgentOptions = field(default_factory=SafeAgentOptions)

    # --- UI/UX Configuration ---
    icon_map: Dict[str, str] = field(default_factory=dict) # Map tool names to Flet icons
    settings_fields: List[SettingField] = field(default_factory=list) # Fields for the settings screen
    debug_flag: bool = False # Whether debug mode is enabled
    page_title_suffix: str = "" # Suffix for page title (e.g., " (Debug Mode)")
    theme_color: str = ft.Colors.BLUE # Primary theme color

    # --- Optional App-Specific Customization ---
    # Function to build extra controls for the header (e.g., MCP status)
    header_controls_builder: Optional[Callable[['AgentApp'], List[ft.Control]]] = None # Updated signature
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

        # Internal state for tool approval UI
        self._approval_state: Dict[str, Any] = {
            "active_displays": {}, # Maps approval_id -> UI control
            "pending_futures": {}  # Maps approval_id -> asyncio.Future
        }

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
            async def cleanup_wrapper(): # Wrap in async if needed
                await self.spec.cleanup_handler()
            self.page.on_disconnect = lambda e: asyncio.create_task(cleanup_wrapper())

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
            print(f"Initial settings loaded: {list(self.current_settings.keys())}")
            # Apply any immediate effects of settings here if needed
            # e.g., setting environment variables, though passing to build_agent is preferred.
        except Exception as e:
            print(f"Error loading initial settings: {e}")
            # Initialize with defaults if loading fails
            self.current_settings = {field.key: field.default for field in self.spec.settings_fields}
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
            margin=ft.margin.only(left=20, bottom=12),
            animate=ft.animation.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
            visible=False
        )

        # Chat View
        self.chat = ft.ListView(
            expand=True,
            spacing=0,
            auto_scroll=True,
            # Controls added later (welcome message)
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
            try:
                # The builder function receives 'self' (AgentApp instance)
                header_right_controls.extend(self.spec.header_controls_builder(self))
            except Exception as e:
                print(f"Error calling header_controls_builder: {e}")

        self.header = ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(self.spec.header_title, color=ft.Colors.WHITE, size=22, weight="bold"),
                        ], spacing=2, tight=True),
                        ft.Row(header_right_controls, spacing=12, alignment=ft.MainAxisAlignment.END), # Align right
                    ], spacing=12, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.symmetric(horizontal=20, vertical=16),
                ),
                # Debug Banner (only if debug_flag is True)
                *( [
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(name=ft.Icons.BUG_REPORT, color=ft.Colors.WHITE, size=16),
                            ft.Text("Debug Mode Enabled", color=ft.Colors.WHITE, size=13),
                        ], spacing=8),
                        padding=ft.padding.symmetric(horizontal=20, vertical=8),
                        bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                    )
                  ] if self.spec.debug_flag else []
                ),
            ]),
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                colors=[ft.Colors.INDIGO_900, ft.Colors.INDIGO_700, self.spec.theme_color], # Reverted change
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
            autofocus=True, shift_enter=True, min_lines=1, max_lines=5, filled=False, # Use default border
            border=ft.InputBorder.OUTLINE, # Use outline border
            border_radius=30, # Rounded border
            border_color=ft.Colors.BLACK26,
            focused_border_color=self.spec.theme_color,
            text_size=15, text_style=ft.TextStyle(color=ft.Colors.BLACK), cursor_color=self.spec.theme_color,
            expand=True, content_padding=ft.padding.only(left=20, right=20, top=14, bottom=14),
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
        self.chat.controls.append(ft.Container(height=12)) # Space at top
        self.chat.controls.append(ChatMessage(welcome_msg, self.page, assistant_name=self.spec.name))
        self.chat.controls.append(ft.Container(height=12)) # Space after welcome

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
            self.page.show_snack_bar(snack_bar)
        # No page.update() here, happens in main

    # --- Event Handlers & Actions ---

    async def _send_message_click(self, e=None): # Added e=None for direct calls
        """Handle clicks on the send button or Enter in the text field."""
        if self.new_message and self.new_message.value and self.new_message.value.strip() != "":
            user_msg_text = self.new_message.value.strip()
            user_msg = Message("You", user_msg_text, "user_message")

            # Add user message bubble immediately
            self.chat.controls.append(ChatMessage(user_msg, self.page))
            self.new_message.value = "" # Clear input field *before* processing

            # Show typing indicator below the user message
            if self.typing_indicator not in self.chat.controls:
                self.chat.controls.append(self.typing_indicator)
            self.typing_indicator.visible = True
            self.page.update() # Update UI to show user message and indicator

            # Disable input while processing
            self.new_message.disabled = True
            self.send_button.disabled = True
            self.page.update()

            try:
                await self._process_message(user_msg_text)
            finally:
                # Ensure UI elements are re-enabled even if an error occurs
                self.new_message.disabled = False
                self.send_button.disabled = False
                if self.typing_indicator.visible: # Hide indicator if still visible
                    self.typing_indicator.visible = False
                if self.typing_indicator in self.chat.controls:
                    self.chat.controls.remove(self.typing_indicator)
                self.new_message.focus() # Return focus to input field
                self.page.update()

    async def _process_message(self, user_message: str):
        """Core logic to interact with the agent and update the UI."""
        print(f"Processing message: {user_message}")
        try:
            # --- Build or reuse agent instance ---
            if self.agent_instance is None:
                print("Building agent instance...")
                # 1. Build the core agent using the user-provided function
                core_agent = await self.spec.build_agent(self.current_settings, self)
                if not core_agent:
                     raise ValueError("Agent builder function failed to return an agent instance.")
                print(f"Core agent built: {type(core_agent)}")

                # 2. Wrap in SafeAgent if configured
                if self.spec.use_safe_agent:
                    print("Wrapping core agent with SafeAgent...")
                    opts = self.spec.safe_agent_options
                    try:
                        # SafeAgent.__init__ now handles passing core components
                        self.agent_instance = SafeAgent(
                            name=getattr(core_agent, 'name', self.spec.name),
                            tools=await core_agent.get_all_tools(),
                            model=getattr(core_agent, 'model', 'unknown'), # Provide default
                            instructions=getattr(core_agent, 'instructions', self.spec.get_agent_instructions()),
                            model_settings=getattr(core_agent, 'model_settings', None),
                            mcp_servers=getattr(core_agent, 'mcp_servers', []),

                            approval_callback=self._request_tool_approval, # The internal method
                            safe_tool_names=opts.safe_tool_names,
                            safe_tool_patterns=opts.safe_tool_patterns,
                            skip_approvals=opts.skip_approvals,
                            halt_on_rejection=opts.halt_on_rejection,
                            debug_mode=self.spec.debug_flag,
                            # Pass any other relevant kwargs if needed
                        )
                        print(f"SafeAgent wrapper created successfully.")
                    except Exception as safe_agent_init_error:
                        error_msg = f"Failed to initialize SafeAgent wrapper: {safe_agent_init_error}"
                        print(f"ERROR: {error_msg}")
                        self.agent_instance = core_agent # Fallback to core agent
                        self._display_error_banner(error_msg)
                else:
                    # Just use the core agent directly
                    self.agent_instance = core_agent

                if not self.agent_instance:
                    raise ValueError("Failed to create or wrap agent instance.")
                print(f"Final agent instance ready: {type(self.agent_instance)}")

            # --- Prepare input and run agent ---
            # Reset SafeAgent state if applicable
            if hasattr(self.agent_instance, 'reset_run_state') and callable(self.agent_instance.reset_run_state):
                self.agent_instance.reset_run_state()

            # Prepare input for the agent
            agent_input = self.conversation + [{"role": "user", "content": user_message}]

            # Run the agent stream
            result = Runner.run_streamed(self.agent_instance, agent_input)

            message_output = ""
            final_items = []
            assistant_message_control: Optional[ChatMessage] = None # To update assistant message live

            async for event in result.stream_events():
                if event.type == "raw_response_event": continue # Ignore raw OpenAI chunks
                elif event.type == "agent_updated_stream_event": continue # Ignore agent updates

                elif event.type == "run_item_stream_event":
                    item = event.item
                    final_items.append(item) # Store raw items for conversation history

                    # --- Handle Tool Calls ---
                    if item.type == "tool_call_item":
                        tool_input = item.to_input_item()
                        args_str = tool_input.get('arguments', '{}')
                        tool_name = tool_input.get('name', 'unknown_tool')
                        try:
                            args_dict = json.loads(args_str) if args_str else {}
                            # Shorter, cleaner arg formatting for display
                            formatted_args = ", ".join([f"{k}={repr(v)[:50]}{'...' if len(repr(v)) > 50 else ''}" for k, v in args_dict.items()])
                        except:
                            formatted_args = args_str[:150] + ('...' if len(args_str) > 150 else '')

                        # Remove typing indicator before showing tool call
                        if self.typing_indicator in self.chat.controls:
                            self.chat.controls.remove(self.typing_indicator)

                        tool_call_display = ToolCallDisplay(
                            tool_name, formatted_args, self.spec.icon_map, self.spec.theme_color
                        )
                        self.chat.controls.append(tool_call_display)
                        # Add indicator back *after* the tool call display
                        self.chat.controls.append(self.typing_indicator)
                        self.page.update()

                    # --- Handle Tool Outputs (Debug Only) ---
                    elif item.type == "tool_call_output_item":
                        # Check if it was a SafeAgent rejection before displaying
                        is_rejection = False
                        try:
                            output_data = json.loads(str(item.output))
                            if isinstance(output_data, dict) and output_data.get("error") == "TOOL_REJECTED":
                                is_rejection = True
                                if self.spec.debug_flag: # Only print rejection in debug
                                    print(f"Debug: Tool call {output_data.get('tool')} rejected: {output_data.get('message')}")
                                # We might still want a subtle UI indicator for rejection?
                                # For now, just skip the output display.
                        except (json.JSONDecodeError, TypeError):
                            pass # Not JSON or not the rejection format

                        # Display non-rejection output only if in debug mode
                        if not is_rejection and self.spec.debug_flag:
                             # Remove indicator before showing output
                            if self.typing_indicator in self.chat.controls:
                                self.chat.controls.remove(self.typing_indicator)

                            tool_output = str(item.output)
                            output_display = ToolOutputDisplay(tool_output)
                            self.chat.controls.append(output_display)
                            # Add indicator back after output
                            self.chat.controls.append(self.typing_indicator)
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
                            self.page.update() # Update UI with new chunk

            # --- Finalize after stream ---
            self.typing_indicator.visible = False # Ensure indicator is hidden
            if self.typing_indicator in self.chat.controls:
                self.chat.controls.remove(self.typing_indicator)

            # Add final padding if a message was generated
            if assistant_message_control:
                 self.chat.controls.append(ft.Container(height=12))

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
                            self.conversation.append(input_item)
                # Handle message output item (final assistant message)
                elif item.type == "message_output_item":
                     # Check if the last item in conversation is already this assistant message
                     final_assistant_content = ItemHelpers.text_message_output(item)
                     if not (self.conversation and
                             self.conversation[-1].get("role") == "assistant" and
                             self.conversation[-1].get("content") == final_assistant_content):
                         self.conversation.append({"role": "assistant", "content": final_assistant_content})


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
        self.page.update()

    def _remove_control(self, control: ft.Control):
        """Safely removes a specific control from the chat list."""
        try:
            if self.chat and control in self.chat.controls:
                self.chat.controls.remove(control)
                # No page update here, caller should handle batching updates
        except Exception as e:
            print(f"Error removing control: {e}")

    # --- Navigation ---

    def _create_main_view(self):
        """Creates the main chat view content."""
        return ft.View(
            route="/",
            controls=[
                self.header,
                ft.Container(
                    content=self.chat, expand=True, bgcolor=ft.colors.with_opacity(0.98, ft.Colors.WHITE),
                ),
                ft.Container(
                    content=ft.Row([self.new_message, self.send_button], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.only(left=16, right=16, top=12, bottom=16),
                    bgcolor=ft.Colors.WHITE, border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.BLACK12)),
                ),
            ],
            padding=0, bgcolor=ft.Colors.WHITE
        )

    def _create_settings_view(self):
        """Creates the settings view dynamically."""
        return SettingsScreen(
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
                 self._build_ui()
             self.page.views.append(self._create_main_view())

        self.page.update()

    def _view_pop(self, view):
        """Handles the back button press, especially from settings."""
        print("View popped")
        self.page.views.pop()
        top_view = self.page.views[-1]
        self.page.go(top_view.route) # Go to the route of the view below

    def _handle_settings_save(self, new_settings: Dict[str, Any]):
        """Save updated settings and navigate back to main view."""
        print("Saving settings...")
        self.current_settings = new_settings
        save_settings(self.page, self.spec.client_storage_prefix, self.current_settings)

        # Reset agent instance to force re-initialization with new settings on next message
        self.agent_instance = None
        print("Agent reset due to settings change.")

        # Rebuild header controls in case they depend on settings
        if self.spec.header_controls_builder and self.header:
             header_right_controls = [self.settings_button]
             try:
                 header_right_controls.extend(self.spec.header_controls_builder(self))
                 # Assuming header structure: Container -> Column -> Container -> Row (for title/buttons)
                 button_row = self.header.content.controls[0].content.controls[1]
                 button_row.controls = header_right_controls # Update controls in place
                 print("Header controls updated.")
             except Exception as e:
                 print(f"Error updating header controls after save: {e}")

        self.page.go("/") # Go back to main chat view
        # Optionally show a confirmation snackbar
        self.page.show_snack_bar(ft.SnackBar(ft.Text("Settings saved!"), open=True))

# End of AgentApp class 