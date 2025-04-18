import flet as ft
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Optional, Protocol, Awaitable

# Import necessary components for definitions
from .settings import SettingField
from agents.mcp import MCPServerSse # For type hint in build_agent

# Use TYPE_CHECKING to avoid runtime circular dependency if AgentApp type hint is needed
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .app_base import AgentApp # Import only for type hinting

# Define a protocol for Agent-like objects
class AgentLike(Protocol):
    # Define methods/attributes expected from an Agent
    name: str
    tools: List[Any]
    model: str
    instructions: str
    model_settings: Optional[Any]
    mcp_servers: Optional[List[Any]]

    async def get_all_tools(self) -> List[Any]: ...

    # Optional method for resetting state (used by SafeAgent)
    def reset_run_state(self):
        pass

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
    name: str
    header_title: str
    page_title: str
    welcome_text: str
    client_storage_prefix: str

    # --- Agent Configuration ---
    get_agent_instructions: Callable[[], str]
    build_agent: Optional[Callable[[Dict[str, Any], Optional[MCPServerSse], 'AgentApp'], Awaitable[AgentLike]]]
    available_tools: List[Callable] = field(default_factory=list)
    model_name: Optional[str] = None # Model name used by default builder

    # --- Agent Safety Configuration ---
    use_safe_agent: bool = False
    safe_agent_options: SafeAgentOptions = field(default_factory=SafeAgentOptions)

    # --- MCP Configuration ---
    uses_mcp: bool = False
    mcp_url_setting_key: str = "mcp_url"
    mcp_token_setting_key: str = "mcp_auth_token"
    show_mcp_status_indicator: bool = True

    # --- UI/UX Configuration ---
    icon_map: Dict[str, str] = field(default_factory=dict)
    settings_fields: List[SettingField] = field(default_factory=list)
    debug_flag: bool = False
    page_title_suffix: str = ""
    theme_color: str = ft.Colors.BLUE

    # --- Optional App-Specific Customization ---
    header_controls_builder: Optional[Callable[['AgentApp'], List[ft.Control]]] = None
    cleanup_handler: Optional[Callable[[], Awaitable[None]]] = None 