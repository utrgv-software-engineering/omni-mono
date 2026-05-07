from __future__ import annotations

# No longer need flet import
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Optional, Protocol, Awaitable

# Import necessary components for definitions
# Removed import of SettingField from .settings

# Use TYPE_CHECKING to avoid runtime circular dependency if AgentApp type hint is needed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app_base import AgentApp  # Import only for type hinting
    from agents.mcp import MCPServerSse  # Add the import here
    from agents.guardrail import InputGuardrail, OutputGuardrail


# --- Add SettingField definition here ---
@dataclass
class SettingField:
    key: str
    label: str
    group: Optional[str] = None
    default: str = ""
    secret: bool = False
    hint: str = ""
    # Add other potential field types/properties if needed later


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
class HandoffConfig:
    """Configuration for a handoff to another agent."""

    yaml_path: Optional[str] = (
        None  # Path to the agent's YAML file (for external agents)
    )
    inline_spec: Optional["AgentSpec"] = (
        None  # Inline agent specification (for inline agents)
    )
    tool_name_override: Optional[str] = None  # Override the default tool name
    tool_description_override: Optional[str] = (
        None  # Override the default tool description
    )
    # Optional callable predicate (or static bool) that gates handoff visibility.
    # Resolved by the loader from a `module:func` style string into a real callable.
    is_enabled: Optional[Any] = None

    def __post_init__(self):
        # Validate that exactly one of yaml_path or inline_spec is provided
        if (self.yaml_path is None) == (self.inline_spec is None):
            raise ValueError(
                "HandoffConfig must have exactly one of 'yaml_path' or 'inline_spec'"
            )


# --- Agent-as-Tool ----------------------------------------------------------
# AgentToolConfig mirrors HandoffConfig but is used for agents exposed via
# Agent.as_tool() — the called agent receives generated input (not full
# conversation history) and returns to the caller after running.
@dataclass
class AgentToolConfig:
    """Configuration for exposing another agent as a callable tool."""

    yaml_path: Optional[str] = None  # Path to the agent's YAML file
    inline_spec: Optional["AgentSpec"] = None  # Or an inline spec
    tool_name: Optional[str] = None  # Tool name override (defaults to agent name)
    tool_description: Optional[str] = None  # Tool description override
    is_enabled: Optional[Any] = None  # bool or callable predicate
    needs_approval: Optional[Any] = None  # bool or callable predicate
    max_turns: Optional[int] = None  # Max turns for the nested agent run

    def __post_init__(self):
        if (self.yaml_path is None) == (self.inline_spec is None):
            raise ValueError(
                "AgentToolConfig must have exactly one of 'yaml_path' or 'inline_spec'"
            )


@dataclass
class MCPServerConfig:
    name: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    options: Optional[Dict[str, Any]] = None


@dataclass
class RealtimeSettings:
    """Configuration for realtime voice agent sessions.

    These settings map to the OpenAI Realtime API's session configuration.
    See: https://platform.openai.com/docs/guides/realtime
    """

    model_name: str = "gpt-realtime"
    """The realtime model to use (default: 'gpt-realtime')."""

    voice: Optional[str] = "alloy"
    """Voice to use for audio output. Options: 'alloy', 'echo', 'shimmer', 'ash', 'ballad', 'coral', 'sage', 'verse'."""

    modalities: List[str] = field(default_factory=lambda: ["audio"])
    """Communication modalities. Options: ['text'], ['audio'], or ['text', 'audio']."""

    input_audio_format: str = "pcm16"
    """Input audio format. Options: 'pcm16', 'g711_ulaw', 'g711_alaw'."""

    output_audio_format: str = "pcm16"
    """Output audio format. Options: 'pcm16', 'g711_ulaw', 'g711_alaw'."""

    turn_detection: Optional[Dict[str, Any]] = field(
        default_factory=lambda: {
            "type": "server_vad",
            "threshold": 0.3,
            "silence_duration_ms": 200,
            "prefix_padding_ms": 100,
        }
    )
    """Turn detection configuration for voice activity detection (VAD).

    Example:
        {
            "type": "server_vad",  # or "semantic_vad"
            "threshold": 0.5,
            "silence_duration_ms": 700,
            "prefix_padding_ms": 300,
            "interrupt_response": True
        }
    """

    input_audio_transcription: Optional[Dict[str, Any]] = field(
        default_factory=lambda: {"model": "whisper-1"}
    )
    """Configuration for transcribing user audio input.

    Example:
        {
            "model": "whisper-1",
            "language": "en"
        }
    """

    temperature: Optional[float] = 0.8
    """Sampling temperature (0.0 to 2.0). Lower is more deterministic."""

    max_output_tokens: Optional[int] = 4096
    """Maximum tokens to generate in a single response."""

    tts_instructions: Optional[str] = None


@dataclass
class AgentSpec:
    """Specification for configuring an AgentApp."""

    # --- Basic App Info ---
    name: str
    welcome_text: str

    # --- Agent Configuration ---
    get_agent_instructions: Callable[[], str]
    build_agent: Optional[
        Callable[
            [Dict[str, Any], Optional[MCPServerSse], "AgentApp"], Awaitable[AgentLike]
        ]
    ]
    available_tools: List[Callable] = field(default_factory=list)
    model_name: Optional[str] = None  # Model name used by default builder
    max_turns: int = 20  # Maximum turns per run (overrides Runner default)
    handoff_description: Optional[str] = None  # Description when used as a handoff
    reset_tool_choice: bool = True  # Reset tool choice after tool call
    tool_use_behavior: str | Dict[str, Any] = (
        "run_llm_again"  # How to handle tool use (string or StopAtTools dict)
    )
    model_settings: Optional[Dict[str, Any]] = (
        None  # Model-specific settings (temperature, top_p, etc.)
    )

    # --- Realtime Agent Configuration ---
    realtime_mode: bool = False  # If True, agent runs as realtime voice agent
    realtime_settings: Optional[RealtimeSettings] = (
        None  # Realtime-specific configuration (voice, audio formats, VAD, etc.)
    )

    voice_backend: str = "realtime"

    mcp_config: Optional[Dict[str, Any]] = None  # MCP configuration
    mcp_servers: List[MCPServerConfig] = field(default_factory=list)
    output_type: Optional[type] = None  # Output type for structured responses
    output_type_strict: bool = True  # Whether to use strict JSON schema for output_type
    prompt: Optional[Dict[str, Any]] = None  # Prompt configuration for Responses API

    # --- Handoffs ---
    handoff_configs: List[HandoffConfig] = field(
        default_factory=list
    )  # List of handoff configurations

    # --- Agent-as-Tool ---
    # Sub-agents exposed as callable tools via Agent.as_tool().
    agent_tool_configs: List[AgentToolConfig] = field(default_factory=list)

    # --- Model Provider (optional override of session-derived provider) ---
    # When present, controls how the Runner-level model_provider is built.
    # See loader._parse_model_provider_config and run_options.build_model_provider.
    # Example: {"type": "openai", "use_responses_websocket": True, "websocket_options": {...}}
    model_provider_config: Optional[Dict[str, Any]] = None

    # --- Guardrails ---
    input_guardrails: List[Any] = field(
        default_factory=list
    )  # List of InputGuardrail instances
    output_guardrails: List[Any] = field(
        default_factory=list
    )  # List of OutputGuardrail instances

    # --- Agent Safety Configuration ---
    use_safe_agent: bool = False
    safe_agent_options: SafeAgentOptions = field(default_factory=SafeAgentOptions)

    # --- SDK Runner Context ---
    context: Optional[Any] = None  # Pre-built context passed to SDK Runner
    variables: Optional[Dict[str, Any]] = (
        None  # Variables to be transformed via build_context
    )
    build_context: Optional[Callable[[Dict[str, Any]], Any]] = (
        None  # Factory to build context from variables
    )

    # --- Tracing Configuration ---
    tracing_config: Optional[Dict[str, Any]] = None

    # --- Security Configuration (project-level policy) ---
    security_config: Optional[Dict[str, Any]] = None

    # --- MCP Configuration ---

    # --- UI/UX Configuration ---
    settings_fields: List[SettingField] = field(default_factory=list)

    # --- Remote Agent (optional) --- #
    remote_ws_url: str | None = (
        None  # If provided, UI/CLI will connect to this URL instead of building local agent
    )
    remote_auth_token_key: str | None = (
        None  # Name of env var holding the bearer token (optional)
    )

    # --- Optional hook to register example/server functions on the AgentService --- #
    # A callable that will be invoked by the server on startup to register
    # server functions (via AgentService.register_server_function). This allows
    # examples to inject custom functions without modifying core server code.
    register_server_functions: Optional[Callable[[Any], None]] = None

    register_runtime_hooks: Optional[Callable[[Any], None]] = None

    # --- Optional hook to resolve default model configuration --- #
    # A callable that returns the default model config dict (with keys like
    # 'model', 'provider', 'max_input_tokens', 'max_output_tokens') for a session.
    # Used to initialize session.model_config before the first run if not already set.
    resolve_default_model_config: Optional[Callable[[], Optional[Dict[str, Any]]]] = (
        None
    )

    resolve_realtime_settings: Optional[Callable[[], Optional[Dict[str, Any]]]] = None

    # --- Optional Metadata ---
    label: Optional[str] = None
    description: Optional[str] = None

    # --- Storage Metadata ---
    project_slug: Optional[str] = None
    agent_slug: Optional[str] = None

    # --- Hidden fields for passing registries to handoff agents ---
    # These are populated when loading from project mode and used to pass
    # the same tool/guardrails/server_functions registries to handoff agents
    _tool_registry: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _guardrails_registry: Optional[Dict[str, Dict[str, Any]]] = field(
        default=None, repr=False
    )
    _server_functions_registry: Optional[Dict[str, Any]] = field(
        default=None, repr=False
    )

    _runtime_hooks_registry: Optional[Dict[str, Any]] = field(default=None, repr=False)

    voice_spec: Optional["AgentSpec"] = field(default=None, repr=False)
