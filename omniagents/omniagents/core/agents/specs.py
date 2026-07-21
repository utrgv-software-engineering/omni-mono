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
    # Maximum turns per run. ``None`` disables the limit (SDK 0.16+).
    max_turns: Optional[int] = 20
    handoff_description: Optional[str] = None  # Description when used as a handoff
    # Description when used as a function tool via Agent.as_tool() / agent_tools.
    # Distinct from handoff_description because handoffs transfer conversation
    # control while as_tool is a function call with structured input — the LLM
    # contracts differ. agent_tools entries fall back to this when their own
    # tool_description is omitted.
    tool_description: Optional[str] = None
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
    # Tool-level guardrails (SDK 0.7+). Applied to every tool the agent uses.
    tool_input_guardrails: List[Any] = field(default_factory=list)
    tool_output_guardrails: List[Any] = field(default_factory=list)

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

    # --- Run-level toggles (SDK 0.16+) ---
    nest_handoff_history: bool = False
    """RunConfig.nest_handoff_history — opt-in collapsed transcript on handoff."""

    tool_execution_config: Optional[Dict[str, Any]] = None
    """RunConfig.tool_execution — currently supports max_function_tool_concurrency."""

    error_handlers: Optional[Dict[str, Callable[[Any], Any]]] = None
    """RunErrorHandlers — maps 'max_turns' and 'model_refusal' to handler callables."""

    retry_settings: Optional[Any] = None
    """Pre-built ``ModelRetrySettings`` applied to ``ModelSettings.retry``."""

    model_provider_config: Optional[Dict[str, Any]] = None
    """Optional spec-level ``model_provider`` block (type + creds).

    Resolved at runtime via ``build_model_provider`` and passed to
    ``RunConfig.model_provider``. When unset, the SDK default
    ``MultiProvider`` handles prefix routing (``litellm/...``,
    ``any-llm/...``, ``openai/...``) automatically.
    """

    agent_hooks: Optional[Any] = None
    """SDK ``AgentHooks`` instance assigned to ``Agent.hooks``."""

    run_hooks: Optional[Any] = None
    """SDK ``RunHooks`` instance forwarded to ``Runner.run(...)``."""

    # --- Session Backend (SDK-native, opt-in) ---
    session_backend: Optional[Any] = None
    """Pre-built SDK ``Session`` instance for SDK-managed model-context history.

    Resolved from the YAML ``session.backend`` block via
    :func:`omniagents.core.session.backends.build_session_backend`. ``None``
    means the default OmniAgents SQLite session storage handles history.
    Wired to ``Runner.run_streamed(session=...)`` in the Phase 3 refactor.
    """

    session_compaction_config: Optional[Dict[str, Any]] = None
    """SDK-native compaction config (``session.compaction`` in YAML).

    When present and ``enabled`` is truthy, the OmniAgents Session is wrapped
    with ``agents.memory.OpenAIResponsesCompactionSession`` before being passed
    to the runtime so the OpenAI Responses ``compact`` endpoint manages
    context-window pressure. All keys other than ``enabled`` are forwarded as
    kwargs to ``OpenAIResponsesCompactionSession`` (e.g. ``model``,
    ``compaction_mode``).
    """

    # --- Sandbox (SDK 0.14+) ---
    sandbox_config: Optional[Dict[str, Any]] = None
    """Raw ``sandbox:`` YAML block, kept verbatim for downstream consumers.

    The resolved SDK objects live alongside in ``sandbox_run_config``,
    ``sandbox_capabilities``, and ``sandbox_use_sandbox_agent``.
    """

    sandbox_run_config: Optional[Any] = None
    """Pre-built ``agents.run_config.SandboxRunConfig`` resolved from
    ``sandbox_config``.

    Forwarded to ``RunConfig(sandbox=...)`` by the runtime bridge when set.
    ``None`` (the default) means no sandbox is configured and OmniAgents
    behaves exactly as it did before SDK 0.14.
    """

    sandbox_capabilities: Optional[List[Any]] = None
    """Resolved sandbox ``Capability`` instances from
    ``sandbox.capabilities``.

    Only consulted by the agent builder when
    ``sandbox_use_sandbox_agent`` is true; passed to ``SandboxAgent(...)``
    via the ``capabilities=`` kwarg. ``None`` means the SDK default set
    (Filesystem + Shell + Compaction) is used.
    """

    sandbox_use_sandbox_agent: bool = False
    """When true, the agent builder constructs a
    :class:`agents.sandbox.SandboxAgent` instead of a plain
    :class:`agents.Agent`. Default off; opt-in only.
    """

    sandbox_run_as: Optional[str] = None
    """Optional ``run_as`` user identity for sandbox tools. Forwarded to
    ``SandboxAgent.run_as`` when ``sandbox_use_sandbox_agent`` is true.
    """

    # --- Capability Packs (Phase 0) ---
    resolved_capabilities: List[Any] = field(default_factory=list)
    """Capability instances resolved from the YAML ``capabilities:`` list
    against the project's capability registry. Each entry is an ``agents.sandbox.
    capabilities.Capability`` (subclass) instance. The agent builder calls
    ``cap.tools()`` on each and merges results into ``agent.tools``, appends
    ``cap.instructions(None)`` to the system prompt, and translates the
    capability's ``approvals_spec`` into per-tool ``needs_approval`` predicates.
    Empty list when no capabilities are configured (default behavior unchanged).
    """

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

    # --- Instruction template includes ---
    instructions_search_paths: Optional[List[Any]] = None
    """Directories searched, in order, for ``{% include %}`` fragments when the
    agent's Jinja2 instructions are rendered.

    Populated by the YAML loader via
    :func:`omniagents.core.utils.jinja_instructions.instruction_search_paths`:
    the instructions file's own directory, its ``_shared/`` subdirectory, the
    ``_shared/`` sibling of that directory (the ``agents/_shared/`` convention),
    any ``paths.instruction_fragments`` directories from project.yml, and —
    last — the framework's builtin fragments
    (``omniagents/builtin/instructions/``), so project fragments shadow
    builtins by name. ``None`` (e.g. for agents built in Python) leaves
    includes unsupported, matching pre-include behavior.
    """
