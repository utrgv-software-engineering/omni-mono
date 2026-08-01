import yaml
import os
import re
import importlib
import fnmatch
import sys
import warnings

from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Set

_SAFE_AGENT_DEPRECATION_WARNED: Set[str] = set()


def _emit_safe_agent_deprecations(safe_options_config: Any, *, source: str) -> None:
    """Emit one-shot deprecation warnings for SafeAgent-era YAML keys whose
    semantics changed under the SDK 0.16+ interruption model.

    * ``halt_on_rejection`` is no longer honored: when a tool is rejected
      the SDK feeds the rejection_message back to the model and lets it
      decide what to do next (continue with another approach, ask the
      user, etc). The legacy "halt the entire run" behavior is gone.

    The other keys (``use_safe_agent``, ``safe_tool_names``,
    ``safe_tool_patterns``, ``skip_approvals``) still work exactly as
    before — they configure the per-tool ``needs_approval`` predicates
    that AgentService wires per session.
    """

    if not isinstance(safe_options_config, dict):
        return
    if "halt_on_rejection" not in safe_options_config:
        return
    val = safe_options_config.get("halt_on_rejection")
    # Only warn when the user explicitly opted out of the (former) default.
    # The default was True, so a literal True isn't worth warning about.
    if val is False and source not in _SAFE_AGENT_DEPRECATION_WARNED:
        _SAFE_AGENT_DEPRECATION_WARNED.add(source)
        warnings.warn(
            f"safe_agent_options.halt_on_rejection is deprecated and ignored "
            f"in {source}. Under the SDK 0.16+ interruption model, rejected "
            f"tool calls return their rejection_message to the model rather "
            f"than terminating the run. Remove the key to silence this "
            f"warning.",
            DeprecationWarning,
            stacklevel=3,
        )


# Core imports
from omniagents.core.agents.specs import (
    AgentSpec,
    AgentLike,
    SafeAgentOptions,
    HandoffConfig,
    AgentToolConfig,
    MCPServerConfig,
    SettingField,
    RealtimeSettings,
)
from omniagents.core.tools.discovery import discover_functions_in_dir
from omniagents.core.tools.hosted import build_hosted_tool
from omniagents.core.guardrails.discovery import discover_guardrails_in_dir
from omniagents.core.context.discovery import (
    discover_context_factories,
    discover_model_config_resolvers,
    discover_realtime_settings_resolvers,
)
from omniagents.core.debug import Debug
from omniagents.core.server_functions.discovery import (
    discover_server_functions_in_dir,
    build_server_functions_registrar,
)
from omniagents.core.runtime_hooks.discovery import (
    discover_runtime_hooks_in_dir,
    build_runtime_hooks_registrar,
)
from omniagents.core.predicates.discovery import (
    discover_predicates_in_dir,
    resolve_predicate,
)

DEFAULT_SETTINGS_FIELDS = [
    SettingField(
        key="openai_base_url",
        label="OpenAI Base URL",
        group="OpenAI API",
        default="https://api.openai.com/v1",
        hint="e.g., https://api.openai.com/v1",
    ),
    SettingField(
        key="openai_api_key",
        label="OpenAI API Key",
        group="OpenAI API",
        secret=True,
        hint="Required",
    ),
]


def _parse_mcp_servers_config(
    config: Dict[str, Any],
    *,
    config_path: str,
) -> List[MCPServerConfig]:
    raw_servers = config.get("mcp_servers")
    if raw_servers is None:
        return []

    if not isinstance(raw_servers, list):
        print(
            f"Warning: 'mcp_servers' in {config_path} should be a list of mappings. Ignoring."
        )
        return []

    servers: List[MCPServerConfig] = []
    seen_names: Set[str] = set()

    for idx, entry in enumerate(raw_servers):
        if not isinstance(entry, dict):
            print(
                f"Warning: Entry {idx} of 'mcp_servers' in {config_path} is not a mapping. Skipping."
            )
            continue

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            print(
                f"Warning: MCP server entry {idx} in {config_path} is missing a valid 'name'. Skipping."
            )
            continue
        if name in seen_names:
            print(
                f"Warning: Duplicate MCP server name '{name}' in {config_path}. Skipping duplicate."
            )
            continue

        server_type = entry.get("type")
        if not isinstance(server_type, str) or not server_type.strip():
            print(
                f"Warning: MCP server '{name}' in {config_path} is missing a valid 'type'. Skipping."
            )
            continue

        params = entry.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            print(
                f"Warning: MCP server '{name}' in {config_path} has non-dict 'params'. Skipping."
            )
            continue

        options = entry.get("options")
        if options is not None and not isinstance(options, dict):
            print(
                f"Warning: MCP server '{name}' in {config_path} has non-dict 'options'. Ignoring options."
            )
            options = None

        servers.append(
            MCPServerConfig(
                name=name,
                type=server_type,
                params=params,
                options=options,
            )
        )
        seen_names.add(name)

    return servers


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute environment variables in strings.

    Supports:
    - ${VAR_NAME} - Simple substitution, errors if not found
    - ${VAR_NAME:-default} - With default value if not set
    - ${VAR_NAME:?error message} - With custom error message if not set

    For dicts and lists, recursively processes all values.
    """
    if isinstance(value, str):
        # Pattern to match ${VAR} or ${VAR:-default} or ${VAR:?error}
        pattern = r"\$\{([^}:]+)(?::([?-])([^}]*))?\}"

        def replace_var(match):
            var_name = match.group(1)
            operator = match.group(2)
            default_or_error = match.group(3) or ""

            env_value = os.getenv(var_name)

            if env_value is not None:
                return env_value
            elif operator == "-":
                # Use default value
                return default_or_error
            elif operator == "?":
                # Error with custom message
                error_msg = (
                    default_or_error or f"Environment variable {var_name} is not set"
                )
                raise ValueError(error_msg)
            else:
                # No operator, error if not found
                raise ValueError(f"Environment variable {var_name} is not set")

        # Only do substitution if the pattern is found
        if re.search(pattern, value):
            return re.sub(pattern, replace_var, value)
        return value

    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]

    else:
        # For other types (int, bool, None, etc), return as-is
        return value


def _load_instructions(config: Dict[str, Any], base_dir: Path) -> Callable[[], str]:
    """Creates a function to load instructions based on YAML config, resolving relative to base_dir."""
    instructions_content = config.get("instructions")
    instructions_file_relative = config.get(
        "instructions_file"
    )  # Keep original key name

    if instructions_content:
        # Use literal instructions if provided
        return lambda: str(instructions_content)
    elif instructions_file_relative:
        # Create function to read from file relative to base_dir
        file_path = (
            base_dir / instructions_file_relative
        ).resolve()  # Calculate absolute path and resolve symlinks/..

        def read_instructions_file():
            try:
                if not file_path.is_file():
                    # Show path relative to CWD for clearer error messages if run from workspace root
                    try:
                        display_path = file_path.relative_to(Path.cwd())
                    except ValueError:
                        display_path = file_path  # Show absolute if not relative to CWD
                    print(f"Warning: Instructions file not found: {file_path}")
                    return f"Error: Instructions file '{display_path}' not found."
                return file_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading instructions file {file_path}: {e}")
                return f"Error reading instructions file: {e}"

        return read_instructions_file
    else:
        # Default instructions if neither is specified
        print(
            "Warning: No instructions or instructions_file specified in config. Using default."
        )
        return lambda: "You are a helpful assistant."


def _instruction_search_paths(
    config: Dict[str, Any],
    base_dir: Path,
    project_defaults: Optional[Dict[str, Any]] = None,
) -> List[Path]:
    """Compute ``{% include %}`` search paths for an agent's instructions.

    Anchored at the instructions file's own directory when
    ``instructions_file`` is set, otherwise at the agent YAML's directory
    (inline ``instructions:``). Adds the ``_shared/`` conventions and any
    ``paths.instruction_fragments`` directories forwarded by the project
    loader via ``project_defaults``.
    """
    from omniagents.core.utils.jinja_instructions import instruction_search_paths

    anchor_dir = base_dir
    instructions_file_relative = config.get("instructions_file")
    if instructions_file_relative:
        anchor_dir = (base_dir / str(instructions_file_relative)).resolve().parent

    extra_dirs: List[Path] = []
    if project_defaults:
        for d in project_defaults.get("instruction_fragments") or []:
            extra_dirs.append(Path(d))

    return instruction_search_paths(anchor_dir, extra_dirs)


def _parse_inline_agent_spec(
    config: Dict[str, Any], base_dir: Path, parent_loading_stack: Set[Path]
) -> "AgentSpec":
    """Parse an inline agent configuration into an AgentSpec.

    This is a simplified version of load_agent_spec_from_yaml for inline agents.
    """
    # Get basic agent info
    agent_name = config.get("name", "Inline Agent")
    model_name = config.get("model", "gpt-5.2")

    # Handle instructions
    instructions_func = _load_instructions(config, base_dir)

    # Parse tools (inline agents reference tools by name only)
    tool_names = config.get("tools", [])
    if not isinstance(tool_names, list):
        print(f"Warning: 'tools' in inline agent should be a list. Using empty list.")
        tool_names = []

    # Discover tools from parent's directory
    all_discovered_functions = {}
    tools_dir_path = (base_dir / "tools").resolve()
    if tools_dir_path.is_dir():
        from omniagents.core.tools.discovery import discover_functions_in_dir

        tools_found = discover_functions_in_dir(str(tools_dir_path), False)
        all_discovered_functions.update(tools_found)

    selected_tool_functions = []
    for tool_entry in tool_names:
        # Hosted tool entry (dict with 'hosted' key) — build via SDK factory
        if isinstance(tool_entry, dict) and "hosted" in tool_entry:
            try:
                selected_tool_functions.append(build_hosted_tool(tool_entry))
            except Exception as e:
                print(
                    f"Warning: Failed to build hosted tool {tool_entry!r} for inline agent '{agent_name}': {e}"
                )
            continue
        # Function tool reference (string name, or dict without 'hosted')
        tool_name = tool_entry if isinstance(tool_entry, str) else None
        if tool_name and tool_name in all_discovered_functions:
            selected_tool_functions.append(all_discovered_functions[tool_name])
        else:
            print(
                f"Warning: Tool {tool_entry!r} not found for inline agent '{agent_name}'"
            )

    # Parse model settings if provided
    model_settings_config = config.get("model_settings", {})
    if not isinstance(model_settings_config, dict):
        model_settings_config = {}

    # Safety configuration
    use_safe = config.get("use_safe_agent", True)
    safe_options_config = config.get("safe_agent_options", {})
    safe_options = SafeAgentOptions(
        skip_approvals=safe_options_config.get("skip_approvals", False),
        halt_on_rejection=safe_options_config.get("halt_on_rejection", True),
        safe_tool_names=safe_options_config.get("safe_tool_names", []),
        safe_tool_patterns=safe_options_config.get("safe_tool_patterns", []),
    )
    _emit_safe_agent_deprecations(safe_options_config, source=f"agent '{agent_name}'")

    # Derive agent_slug from agent name for inline agents
    inline_agent_slug = agent_name.lower().replace(" ", "_").replace("-", "_")

    handoff_configs_list: List[HandoffConfig] = []
    handoffs_config = config.get("handoffs", [])
    if handoffs_config:
        if not isinstance(handoffs_config, list):
            print(
                f"Warning: 'handoffs' in inline agent '{agent_name}' should be a list. Skipping."
            )
            handoffs_config = []
        for handoff_item in handoffs_config:
            if not isinstance(handoff_item, dict):
                print(
                    f"Warning: Invalid handoff item in inline agent '{agent_name}'. Expected dict. Skipping."
                )
                continue
            if "yaml" in handoff_item:
                yaml_path = handoff_item.get("yaml")
                if not isinstance(yaml_path, str) or not yaml_path.strip():
                    print(
                        f"Warning: Inline handoff for '{agent_name}' is missing a valid 'yaml' path. Skipping."
                    )
                    continue
                resolved_yaml = (base_dir / yaml_path).resolve()
                if not resolved_yaml.is_file():
                    try:
                        display_path = resolved_yaml.relative_to(Path.cwd())
                    except ValueError:
                        display_path = resolved_yaml
                    print(
                        f"Warning: Handoff agent YAML file not found for inline agent '{agent_name}': {display_path}. Skipping."
                    )
                    continue
                handoff_configs_list.append(
                    HandoffConfig(
                        yaml_path=str(resolved_yaml),
                        tool_name_override=handoff_item.get("tool_name_override"),
                        tool_description_override=handoff_item.get(
                            "tool_description_override"
                        ),
                    )
                )
            elif "inline" in handoff_item:
                inline_config = handoff_item.get("inline")
                if not isinstance(inline_config, dict):
                    print(
                        f"Warning: 'inline' value in handoff for '{agent_name}' should be a dictionary. Skipping."
                    )
                    continue
                try:
                    nested_spec = _parse_inline_agent_spec(
                        inline_config, base_dir, parent_loading_stack
                    )
                except Exception as e:
                    print(
                        f"Warning: Failed to parse nested inline agent for '{agent_name}': {e}. Skipping."
                    )
                    continue
                handoff_configs_list.append(
                    HandoffConfig(
                        inline_spec=nested_spec,
                        tool_name_override=handoff_item.get("tool_name_override"),
                        tool_description_override=handoff_item.get(
                            "tool_description_override"
                        ),
                    )
                )
            else:
                print(
                    f"Warning: Inline handoff item for '{agent_name}' must include 'yaml' or 'inline'. Skipping."
                )

    # Create minimal AgentSpec for inline agent
    return AgentSpec(
        # Basic Info
        name=agent_name,
        welcome_text=config.get("welcome_text", f"Hello from {agent_name}"),
        # Agent Config
        get_agent_instructions=instructions_func,
        build_agent=None,
        available_tools=selected_tool_functions,
        model_name=model_name,
        max_turns=config.get("max_turns", 20),
        handoff_description=config.get("handoff_description"),
        tool_description=config.get("tool_description"),
        reset_tool_choice=config.get("reset_tool_choice", True),
        tool_use_behavior=config.get("tool_use_behavior", "run_llm_again"),
        model_settings=model_settings_config if model_settings_config else None,
        output_type=None,  # Simplified for inline agents
        output_type_strict=True,  # Default to strict for inline agents
        prompt=None,  # Simplified for inline agents
        # Nested handoffs for inline agents (if provided)
        handoff_configs=handoff_configs_list,
        # Guardrails (simplified for inline agents)
        input_guardrails=[],
        output_guardrails=[],
        # Safety
        use_safe_agent=use_safe,
        safe_agent_options=safe_options,
        # MCP (not supported for inline agents)
        # UI/UX (minimal for inline agents)
        settings_fields=[],
        agent_slug=inline_agent_slug,
        # Include search paths for {% include %} fragments in instructions
        instructions_search_paths=_instruction_search_paths(config, base_dir),
    )


def load_agent_spec_from_yaml(
    config_path: str,
    _loading_stack: Optional[Set[Path]] = None,
    *,
    tool_registry: Optional[Dict[str, Any]] = None,
    guardrails_registry: Optional[Dict[str, Dict[str, Any]]] = None,
    server_functions_registry: Optional[Dict[str, Any]] = None,
    agents_map: Optional[Dict[str, Path]] = None,
    capability_registry: Optional[Dict[str, Any]] = None,
    project_defaults: Optional[Dict[str, Any]] = None,
    user_mcp_servers: Optional[List[MCPServerConfig]] = None,
) -> AgentSpec:
    """
    Loads agent configuration from a YAML file and builds an AgentSpec.

    Args:
        config_path: Path to the agent.yml configuration file.
        _loading_stack: Internal parameter for circular reference detection.

    Returns:
        An AgentSpec instance configured according to the YAML file.

    Raises:
        FileNotFoundError: If the config_path does not exist.
        ValueError: If the configuration is invalid or circular references detected.
    """
    config_file = Path(config_path).resolve()

    # --------------------------------------------------------------
    # Ensure .env is loaded for YAML-based agents
    # --------------------------------------------------------------
    # Policy: ALWAYS search upward from the YAML's directory so that
    # the configuration is self-contained and not dependent on the
    # caller's current working directory.
    try:
        try:
            from dotenv import dotenv_values  # type: ignore
        except Exception:
            dotenv_values = None  # type: ignore

        if dotenv_values is not None:
            current = config_file.parent
            while True:
                env_path = current / ".env"
                if env_path.exists():
                    try:
                        loaded_values = dotenv_values(env_path) or {}
                        for key, value in loaded_values.items():
                            if not key:
                                continue
                            existing_value = os.environ.get(key)
                            if existing_value:
                                continue
                            if value is not None:
                                os.environ[key] = value
                    except Exception:
                        pass
                    break
                if current == current.parent:
                    break
                current = current.parent
    except Exception:
        # Never fail YAML loading due to dotenv issues
        pass

    # Initialize loading stack for circular reference detection
    if _loading_stack is None:
        _loading_stack = set()

    # Check for circular references
    if config_file in _loading_stack:
        raise ValueError(
            f"Circular reference detected: {config_file} is already being loaded"
        )

    # Add current file to loading stack
    _loading_stack = _loading_stack.copy()
    _loading_stack.add(config_file)

    if not config_file.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file {config_file.resolve()}: {e}")
    except Exception as e:
        raise ValueError(f"Error reading file {config_file.resolve()}: {e}")

    if not isinstance(config, dict):
        raise ValueError(
            f"Invalid YAML format in {config_file.resolve()}. Expected a dictionary (key-value pairs)."
        )

    # --- Remote Agent preliminary ---
    remote_ws_url: str | None = None
    remote_token_env: str | None = None
    remote_identity_secret_env: str | None = None
    remote_identity_audience: str | None = None
    remote_identity_allowed_roles: list[str] = []
    if isinstance(config.get("remote_agent"), dict):
        remote_cfg_tmp = config["remote_agent"]
        remote_ws_url = remote_cfg_tmp.get("ws_url")
        remote_token_env = remote_cfg_tmp.get("auth_token_env")
        # Per-user identity propagation across the proxy hop. See
        # ``omniagents.rpc.identity_assertion``.
        remote_identity_secret_env = remote_cfg_tmp.get("identity_secret_env")
        remote_identity_audience = remote_cfg_tmp.get("identity_audience")
        from omniagents.rpc.identity_assertion import normalize_role_allowlist

        remote_identity_allowed_roles = normalize_role_allowlist(
            remote_cfg_tmp.get("identity_allowed_roles"),
            field_name="remote_agent.identity_allowed_roles",
        )

    # --- Extract and Derive Basic Info ---
    agent_name = config.get("name", "Unnamed Agent")
    # max_turns: int, or null to disable the limit (SDK 0.16+)
    if "max_turns" in config:
        _raw_max_turns = config.get("max_turns")
        if _raw_max_turns is None:
            max_turns_cfg = None
        else:
            try:
                max_turns_cfg = int(_raw_max_turns)
            except (TypeError, ValueError):
                print(
                    f"Warning: 'max_turns' must be an integer or null. Got {_raw_max_turns!r}. Using 20."
                )
                max_turns_cfg = 20
    else:
        max_turns_cfg = 20
    label_from_yaml = (
        config.get("label") if isinstance(config.get("label"), str) else None
    )
    description_from_yaml = (
        config.get("description")
        if isinstance(config.get("description"), str)
        else None
    )
    # Derive agent_slug from agent name if not provided
    default_agent_slug = agent_name.lower().replace(" ", "_").replace("-", "_")
    agent_slug = config.get("agent_slug", default_agent_slug)
    welcome_text = config.get(
        "welcome_text", f"Hello! I'm {agent_name}. How can I help you?"
    )
    model_name_from_config = config.get("model")  # Get model name from YAML

    # --- New Agent Parameters ---
    handoff_description = config.get(
        "handoff_description"
    )  # Optional description for handoffs
    tool_description = config.get(
        "tool_description"
    )  # Optional description for agent_tools (Agent.as_tool())
    reset_tool_choice = config.get("reset_tool_choice", True)  # Default to True
    tool_use_behavior_config = config.get(
        "tool_use_behavior", "run_llm_again"
    )  # Default behavior

    # Parse tool_use_behavior - can be string or dict (StopAtTools)
    tool_use_behavior = tool_use_behavior_config
    if isinstance(tool_use_behavior_config, str):
        # Validate string behaviors
        valid_behaviors = ["run_llm_again", "stop_on_first_tool"]
        if tool_use_behavior_config not in valid_behaviors:
            print(
                f"Warning: Invalid tool_use_behavior '{tool_use_behavior_config}'. Using default 'run_llm_again'."
            )
            tool_use_behavior = "run_llm_again"
    elif isinstance(tool_use_behavior_config, dict):
        # Handle StopAtTools dict format
        if "stop_at_tool_names" in tool_use_behavior_config:
            stop_at_tools = tool_use_behavior_config.get("stop_at_tool_names", [])
            if not isinstance(stop_at_tools, list):
                print(
                    f"Warning: 'stop_at_tool_names' should be a list. Using default 'run_llm_again'."
                )
                tool_use_behavior = "run_llm_again"
            else:
                # Keep the dict format for StopAtTools
                tool_use_behavior = {"stop_at_tool_names": stop_at_tools}
        else:
            print(
                f"Warning: tool_use_behavior dict must have 'stop_at_tool_names' field. Using default 'run_llm_again'."
            )
            tool_use_behavior = "run_llm_again"
    else:
        print(
            f"Warning: tool_use_behavior must be a string or dict. Got {type(tool_use_behavior_config).__name__}. Using default 'run_llm_again'."
        )
        tool_use_behavior = "run_llm_again"

    # --- Output Type ---
    output_type_config = config.get("output_type")  # Optional output type specification
    output_schema_config = config.get(
        "output_schema"
    )  # Optional explicit JSON schema for output
    output_type = None
    if output_schema_config and isinstance(output_schema_config, dict):
        # Build a simple TypedDict at runtime from JSON schema properties
        try:
            from typing_extensions import TypedDict

            def _map_js_type(t):
                t = (t or "").lower()
                return {
                    "string": str,
                    "boolean": bool,
                    "bool": bool,
                    "number": float,
                    "float": float,
                    "integer": int,
                    "int": int,
                    "object": dict,
                    "array": list,
                }.get(t, str)

            props = output_schema_config.get("properties", {}) or {}
            ann = {k: _map_js_type((v or {}).get("type")) for k, v in props.items()}
            td_name = (agent_name or "Agent").title().replace(" ", "") + "Output"
            output_type = TypedDict(td_name, ann)  # type: ignore
        except Exception as e:
            print(
                f"Warning: Failed to build output_schema TypedDict: {e}. Falling back to output_type"
            )
            output_schema_config = None
    if output_type is None and output_type_config:
        # Map string type names to Python types
        type_mapping = {
            "str": str,
            "string": str,
            "dict": dict,
            "dictionary": dict,
            "list": list,
            "array": list,
            "int": int,
            "integer": int,
            "float": float,
            "number": float,
            "bool": bool,
            "boolean": bool,
            "any": None,  # None means no type constraint
        }
        if isinstance(output_type_config, str):
            output_type = type_mapping.get(output_type_config.lower())
            if output_type_config.lower() not in type_mapping:
                print(
                    f"Warning: Unknown output_type '{output_type_config}'. Supported types: {', '.join(type_mapping.keys())}. Ignoring."
                )
        else:
            print(
                f"Warning: 'output_type' should be a string. Got {type(output_type_config).__name__}. Ignoring."
            )

    # --- Output Type Strict Mode ---
    output_type_strict = config.get(
        "output_type_strict", True
    )  # Default to True for strict JSON schema
    if not isinstance(output_type_strict, bool):
        print(
            f"Warning: 'output_type_strict' should be a boolean. Got {type(output_type_strict).__name__}. Using default True."
        )
        output_type_strict = True

    # --- Prompt Configuration ---
    prompt_config = config.get("prompt")  # Optional prompt configuration
    prompt = None
    if prompt_config:
        if isinstance(prompt_config, dict):
            # Validate required 'id' field
            if "id" not in prompt_config:
                print(
                    f"Warning: 'prompt' configuration must have an 'id' field. Ignoring."
                )
            else:
                # Create a Prompt TypedDict-compatible dictionary
                prompt = {
                    "id": str(prompt_config["id"]),
                }
                # Add optional fields if present
                if "version" in prompt_config:
                    prompt["version"] = str(prompt_config["version"])
                if "variables" in prompt_config:
                    if isinstance(prompt_config["variables"], dict):
                        prompt["variables"] = prompt_config["variables"]
                    else:
                        print(
                            f"Warning: 'prompt.variables' should be a dictionary. Ignoring variables field."
                        )
        else:
            print(
                f"Warning: 'prompt' should be a dictionary with 'id', 'version' (optional), and 'variables' (optional). Got {type(prompt_config).__name__}. Ignoring."
            )

    # --- Model Settings ---
    model_settings_config = config.get("model_settings", {})
    if not isinstance(model_settings_config, dict):
        print(f"Warning: 'model_settings' should be a dictionary. Ignoring.")
        model_settings_config = {}

    # Validate known ModelSettings parameters
    valid_model_settings = {
        "temperature": (float, int),
        "top_p": (float, int),
        "frequency_penalty": (float, int),
        "presence_penalty": (float, int),
        "tool_choice": (
            str,
            type(None),
        ),  # "auto", "required", "none", or specific tool
        "parallel_tool_calls": bool,
        "truncation": str,  # "auto" or "disabled"
        "max_tokens": int,
        "reasoning": dict,  # Configuration for reasoning models
        "verbosity": str,  # "low", "medium", or "high"
        "metadata": dict,  # Metadata to include with model calls
        "store": bool,  # Whether to store responses
        "include_usage": bool,  # Include usage chunks
        "response_include": list,  # Additional output data
        "top_logprobs": int,  # Number of top tokens for logprobs
        "extra_query": dict,  # Additional query fields
        "extra_body": dict,  # Additional body fields
        "extra_headers": dict,  # Additional headers
        "extra_args": dict,  # Arbitrary keyword arguments
        "prompt_cache_key": str,
        "prompt_cache_retention": str,
        # OmniAgents-specific (consumed by run_options.build_model_provider).
        # Not passed to ModelSettings; extracted before construction.
        "reasoning_content_replay": dict,
    }

    # Validate and warn about invalid settings
    keys_to_remove = []
    for key, value in model_settings_config.items():
        if key in valid_model_settings:
            expected_types = valid_model_settings[key]
            if not isinstance(expected_types, tuple):
                expected_types = (expected_types,)
            if not isinstance(value, expected_types):
                type_names = "/".join(t.__name__ for t in expected_types)
                print(
                    f"Warning: 'model_settings.{key}' should be {type_names}, got {type(value).__name__}. Ignoring."
                )
                keys_to_remove.append(key)
        else:
            # Don't warn about unknown settings - they might be provider-specific
            pass

    # Remove invalid keys after iteration
    for key in keys_to_remove:
        del model_settings_config[key]

    # Validate specific enum values
    if "truncation" in model_settings_config and model_settings_config[
        "truncation"
    ] not in ["auto", "disabled"]:
        print(
            f"Warning: 'model_settings.truncation' should be 'auto' or 'disabled', got '{model_settings_config['truncation']}'. Ignoring."
        )
        del model_settings_config["truncation"]

    if "verbosity" in model_settings_config and model_settings_config[
        "verbosity"
    ] not in ["low", "medium", "high"]:
        print(
            f"Warning: 'model_settings.verbosity' should be 'low', 'medium', or 'high', got '{model_settings_config['verbosity']}'. Ignoring."
        )
        del model_settings_config["verbosity"]

    # --- Extract reasoning_content_replay (not a ModelSettings field) ---
    # Lift it out of model_settings_config so ModelSettings(**kwargs) doesn't
    # complain about an unknown keyword. Stash it for the model_provider step.
    _reasoning_replay_cfg = model_settings_config.pop("reasoning_content_replay", None)

    # --- Realtime Agent Configuration ---
    realtime_mode = config.get("realtime_mode", False)
    if not isinstance(realtime_mode, bool):
        print(
            f"Warning: 'realtime_mode' should be a boolean. Got {type(realtime_mode).__name__}. Using False."
        )
        realtime_mode = False

    voice_backend = config.get("voice_backend", "realtime")
    if not isinstance(voice_backend, str):
        print(
            f"Warning: 'voice_backend' should be a string. Got {type(voice_backend).__name__}. Using 'realtime'."
        )
        voice_backend = "realtime"
    voice_backend = voice_backend.strip().lower() or "realtime"
    if voice_backend not in {"realtime", "pipeline"}:
        print(f"Warning: Invalid voice_backend '{voice_backend}'. Using 'realtime'.")
        voice_backend = "realtime"

    realtime_settings = None
    if realtime_mode or voice_backend == "pipeline":
        realtime_config = config.get("realtime_settings", {})
        if not isinstance(realtime_config, dict):
            print(
                f"Warning: 'realtime_settings' should be a dictionary. Using defaults."
            )
            realtime_config = {}

        # Apply environment variable substitution to realtime config
        realtime_config = _substitute_env_vars(realtime_config)

        # Extract and validate realtime settings
        try:
            realtime_settings = RealtimeSettings(
                model_name=realtime_config.get("model_name", "gpt-realtime"),
                voice=realtime_config.get("voice", "alloy"),
                modalities=realtime_config.get("modalities", ["audio"]),
                input_audio_format=realtime_config.get("input_audio_format", "pcm16"),
                output_audio_format=realtime_config.get("output_audio_format", "pcm16"),
                turn_detection=realtime_config.get(
                    "turn_detection",
                    {
                        "type": "server_vad",
                        "threshold": 0.3,
                        "prefix_padding_ms": 100,
                        "silence_duration_ms": 200,
                    },
                ),
                input_audio_transcription=realtime_config.get(
                    "input_audio_transcription", {"model": "whisper-1"}
                ),
                temperature=realtime_config.get("temperature", 0.8),
                max_output_tokens=realtime_config.get("max_output_tokens", 4096),
            )

            # Validate audio formats
            valid_audio_formats = ["pcm16", "g711_ulaw", "g711_alaw"]
            if realtime_settings.input_audio_format not in valid_audio_formats:
                print(
                    f"Warning: Invalid input_audio_format '{realtime_settings.input_audio_format}'. Using 'pcm16'."
                )
                realtime_settings.input_audio_format = "pcm16"
            if realtime_settings.output_audio_format not in valid_audio_formats:
                print(
                    f"Warning: Invalid output_audio_format '{realtime_settings.output_audio_format}'. Using 'pcm16'."
                )
                realtime_settings.output_audio_format = "pcm16"

            # Validate modalities
            if not isinstance(realtime_settings.modalities, list) or not all(
                m in ["text", "audio"] for m in realtime_settings.modalities
            ):
                print(
                    f"Warning: Invalid modalities. Must be a list containing 'text' and/or 'audio'. Using ['audio']."
                )
                realtime_settings.modalities = ["audio"]

        except Exception as e:
            print(f"Warning: Error parsing realtime settings: {e}. Using defaults.")
            realtime_settings = RealtimeSettings()

    # --- Agent Instructions ---
    base_dir = config_file.parent
    if remote_ws_url:
        # When using a remote agent we don't need local instructions
        get_instructions_func = lambda: ""
    else:
        get_instructions_func = _load_instructions(config, base_dir)

    # --- Tool Discovery and Selection ---
    # Prefer project-provided registry when available; otherwise discover relative to agent YAML
    all_discovered_functions = {}
    tools_dir_path = (base_dir / "tools").resolve()
    if tool_registry is not None:
        try:
            all_discovered_functions.update(tool_registry)
            Debug.log(
                f"Using project tool registry with {len(all_discovered_functions)} tools"
            )
        except Exception:
            pass
    else:
        core_root = Path(__file__).resolve().parents[2]
        builtin_tools_dir = (core_root / "builtin" / "tools").resolve()
        if builtin_tools_dir.is_dir():
            try:
                builtin_tools = discover_functions_in_dir(str(builtin_tools_dir))
                all_discovered_functions.update(builtin_tools)
            except Exception:
                pass
        if tools_dir_path.is_dir():
            tools_found = discover_functions_in_dir(str(tools_dir_path))
            all_discovered_functions.update(tools_found)
            Debug.log(f"Discovered tools from 'tools' subdirectory: {tools_dir_path}")
        additional_tools_dirs = config.get("additional_tools_dirs", [])
        if not isinstance(additional_tools_dirs, list):
            print(
                f"Warning: 'additional_tools_dirs' in {config_path} should be a list. Ignoring."
            )
            additional_tools_dirs = []
        for tools_dir in additional_tools_dirs:
            additional_dir_path = (base_dir / tools_dir).resolve()
            if additional_dir_path.is_dir():
                additional_tools = discover_functions_in_dir(str(additional_dir_path))
                all_discovered_functions.update(additional_tools)
                Debug.log(f"Discovered additional tools from: {additional_dir_path}")
            else:
                try:
                    display_path = additional_dir_path.relative_to(Path.cwd())
                except ValueError:
                    display_path = additional_dir_path
                print(
                    f"Warning: Additional tools directory '{display_path}' not found. Skipping."
                )
        if not all_discovered_functions:
            pass

    activated_tool_names = config.get("tools", [])
    selected_tool_functions = []
    missing_tools = []
    if not isinstance(activated_tool_names, list):
        print(
            f"Warning: 'tools' key in {config_path} should be a list of tool names. Ignoring."
        )
        activated_tool_names = []

    if not remote_ws_url:
        for tool_entry in activated_tool_names:
            # Hosted tool entry (dict with 'hosted' key) — build via SDK factory
            if isinstance(tool_entry, dict) and "hosted" in tool_entry:
                try:
                    selected_tool_functions.append(build_hosted_tool(tool_entry))
                except Exception as e:
                    print(
                        f"Warning: Failed to build hosted tool {tool_entry!r} in '{config_path}': {e}"
                    )
                continue
            # Function tool reference: bare string ("bash_kill") OR dict
            # form ({name: bash_kill, is_enabled: my_predicate}). The
            # is_enabled value is applied later in a second pass that
            # mutates the FunctionTool's is_enabled attribute.
            if isinstance(tool_entry, str):
                tool_name = tool_entry
            elif isinstance(tool_entry, dict):
                tool_name = tool_entry.get("name") or tool_entry.get("tool")
            else:
                tool_name = None
            if tool_name and tool_name in all_discovered_functions:
                selected_tool_functions.append(all_discovered_functions[tool_name])
            else:
                missing_tools.append(tool_entry if tool_name is None else tool_name)

    if missing_tools:
        # Updated error message
        try:
            display_path = tools_dir_path.relative_to(Path.cwd())
        except ValueError:
            display_path = tools_dir_path
        print(
            f"Warning: The following tools listed in '{config_path}' were not found in the agent's tools directory '{display_path}' or are not valid marked functions: {', '.join(str(t) for t in missing_tools)}"
        )
        # Continue without missing tools

    # --- Apply project-level tool blocklist ---
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        sec = project_defaults["security"]
        blocked_names = set(sec.get("blocked_tools") or [])
        blocked_patterns = sec.get("blocked_tool_patterns") or []
        if blocked_names or blocked_patterns:
            before_count = len(selected_tool_functions)
            filtered = []
            for tool_func in selected_tool_functions:
                tool_name = getattr(tool_func, "name", "")
                if tool_name in blocked_names:
                    print(
                        f"Security: tool '{tool_name}' blocked by project security policy"
                    )
                    continue
                if any(fnmatch.fnmatch(tool_name, p) for p in blocked_patterns):
                    print(
                        f"Security: tool '{tool_name}' blocked by pattern in project security policy"
                    )
                    continue
                filtered.append(tool_func)
            selected_tool_functions = filtered

    # --- Apply per-tool timeouts ---
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        tool_timeouts = project_defaults["security"].get("tool_timeouts") or {}
        if tool_timeouts and selected_tool_functions:
            from omniagents.core.agents.builder import _apply_tool_timeouts

            selected_tool_functions = _apply_tool_timeouts(
                selected_tool_functions, tool_timeouts
            )

    # --- Server Functions Discovery and Selection ---
    server_functions_registrar = None
    if not remote_ws_url:
        discovered_server_functions = {}
        if server_functions_registry is not None:
            try:
                discovered_server_functions.update(server_functions_registry)
                Debug.log(
                    f"Using project server functions registry with {len(discovered_server_functions)} functions"
                )
            except Exception:
                pass
        else:
            server_functions_dir = (base_dir / "server_functions").resolve()
            if server_functions_dir.is_dir():
                discovered_server_functions = discover_server_functions_in_dir(
                    str(server_functions_dir)
                )
                Debug.log(
                    f"Discovered server functions from 'server_functions' subdirectory: {server_functions_dir}"
                )
            additional_sf_dirs = config.get("additional_server_functions_dirs", [])
            if not isinstance(additional_sf_dirs, list):
                print(
                    f"Warning: 'additional_server_functions_dirs' in {config_path} should be a list. Ignoring."
                )
                additional_sf_dirs = []
            for sf_dir in additional_sf_dirs:
                add_dir_path = (base_dir / sf_dir).resolve()
                if add_dir_path.is_dir():
                    extra = discover_server_functions_in_dir(str(add_dir_path))
                    discovered_server_functions.update(extra)
                    Debug.log(
                        f"Discovered additional server functions from: {add_dir_path}"
                    )
                else:
                    try:
                        display_path = add_dir_path.relative_to(Path.cwd())
                    except ValueError:
                        display_path = add_dir_path
                    print(
                        f"Warning: Additional server functions directory '{display_path}' not found. Skipping."
                    )

        sf_cfg = config.get("server_functions", [])
        register_all = False
        if isinstance(sf_cfg, str) and sf_cfg.strip().lower() in {"*", "all"}:
            register_all = True
        elif isinstance(sf_cfg, bool) and sf_cfg is True:
            register_all = True

        if register_all:
            if discovered_server_functions:
                server_functions_registrar = build_server_functions_registrar(
                    discovered_server_functions
                )
        else:
            sf_names = sf_cfg
            if sf_names and not isinstance(sf_names, list):
                print(
                    f"Warning: 'server_functions' in {config_path} should be a list of names. Ignoring."
                )
                sf_names = []
            selected_funcs = []
            missing_sf = []
            for n in sf_names or []:
                meta = discovered_server_functions.get(n)
                if meta:
                    selected_funcs.append(meta.get("func"))
                else:
                    missing_sf.append(n)
            if missing_sf:
                print(
                    f"Warning: The following server functions listed in '{config_path}' were not found: {', '.join(missing_sf)}"
                )
            if selected_funcs:
                server_functions_registrar = build_server_functions_registrar(
                    selected_funcs
                )

    runtime_hooks_registrar = None
    if not remote_ws_url:
        runtime_hooks_dir = (base_dir / "runtime_hooks").resolve()
        discovered_runtime_hooks: Dict[str, List[Callable]] = {}
        if runtime_hooks_dir.is_dir():
            discovered_runtime_hooks = discover_runtime_hooks_in_dir(
                str(runtime_hooks_dir)
            )
            Debug.log(
                f"Discovered runtime hooks from 'runtime_hooks' subdirectory: {runtime_hooks_dir}"
            )

        rh_cfg = config.get("runtime_hooks", None)
        if rh_cfg is None:
            register_all_hooks = True
        else:
            register_all_hooks = False
            if isinstance(rh_cfg, str) and rh_cfg.strip().lower() in {"*", "all"}:
                register_all_hooks = True
            elif isinstance(rh_cfg, bool) and rh_cfg is True:
                register_all_hooks = True

        if discovered_runtime_hooks:
            if register_all_hooks:
                runtime_hooks_registrar = build_runtime_hooks_registrar(
                    discovered_runtime_hooks
                )
            else:
                hook_names = rh_cfg
                if hook_names and not isinstance(hook_names, list):
                    print(
                        f"Warning: 'runtime_hooks' in {config_path} should be a list of event names. Ignoring."
                    )
                    hook_names = []
                if hook_names:
                    selected: Dict[str, List[Callable]] = {
                        k: v
                        for k, v in discovered_runtime_hooks.items()
                        if k in set([str(x) for x in hook_names])
                    }
                    runtime_hooks_registrar = build_runtime_hooks_registrar(selected)

    # --- Guardrail Discovery and Selection ---
    # Check the 'guardrails' subdirectory for guardrail definitions
    input_guardrails_list = []
    output_guardrails_list = []
    tool_input_guardrails_list = []
    tool_output_guardrails_list = []

    if not remote_ws_url:  # Only discover guardrails for local agents
        if guardrails_registry is not None:
            discovered_input = dict((guardrails_registry.get("input") or {}))
            discovered_output = dict((guardrails_registry.get("output") or {}))
            discovered_tool_input = dict((guardrails_registry.get("tool_input") or {}))
            discovered_tool_output = dict(
                (guardrails_registry.get("tool_output") or {})
            )
            Debug.log(
                f"Using project guardrails registry: input={len(discovered_input)} "
                f"output={len(discovered_output)} tool_input={len(discovered_tool_input)} "
                f"tool_output={len(discovered_tool_output)}"
            )
        else:
            guardrails_dir_path = (base_dir / "guardrails").resolve()
            if guardrails_dir_path.is_dir():
                # Discover guardrails (agent-level + tool-level)
                (
                    discovered_input,
                    discovered_output,
                    discovered_tool_input,
                    discovered_tool_output,
                ) = discover_guardrails_in_dir(str(guardrails_dir_path))
                Debug.log(
                    f"Discovered guardrails from 'guardrails' subdirectory: {guardrails_dir_path}"
                )
                Debug.log(f"  Input guardrails: {list(discovered_input.keys())}")
                Debug.log(f"  Output guardrails: {list(discovered_output.keys())}")
                Debug.log(
                    f"  Tool input guardrails: {list(discovered_tool_input.keys())}"
                )
                Debug.log(
                    f"  Tool output guardrails: {list(discovered_tool_output.keys())}"
                )
            else:
                discovered_input = {}
                discovered_output = {}
                discovered_tool_input = {}
                discovered_tool_output = {}
                Debug.log(
                    f"No 'guardrails' subdirectory found at {guardrails_dir_path}"
                )

        # Process input guardrails from config
        input_guardrail_names = config.get("input_guardrails", [])
        if not isinstance(input_guardrail_names, list):
            print(
                f"Warning: 'input_guardrails' key in {config_path} should be a list of guardrail names. Ignoring."
            )
            input_guardrail_names = []

        missing_input_guardrails = []
        for guardrail_name in input_guardrail_names:
            if guardrail_name in discovered_input:
                input_guardrails_list.append(discovered_input[guardrail_name])
            else:
                missing_input_guardrails.append(guardrail_name)

        if missing_input_guardrails:
            print(
                f"Warning: The following input guardrails listed in '{config_path}' were not found: {', '.join(missing_input_guardrails)}"
            )

        # Process output guardrails from config
        output_guardrail_names = config.get("output_guardrails", [])
        if not isinstance(output_guardrail_names, list):
            print(
                f"Warning: 'output_guardrails' key in {config_path} should be a list of guardrail names. Ignoring."
            )
            output_guardrail_names = []

        missing_output_guardrails = []
        for guardrail_name in output_guardrail_names:
            if guardrail_name in discovered_output:
                output_guardrails_list.append(discovered_output[guardrail_name])
            else:
                missing_output_guardrails.append(guardrail_name)

        if missing_output_guardrails:
            print(
                f"Warning: The following output guardrails listed in '{config_path}' were not found: {', '.join(missing_output_guardrails)}"
            )

        # Process tool-level input guardrails from config (SDK 0.7+)
        tool_input_guardrail_names = config.get("tool_input_guardrails", [])
        if not isinstance(tool_input_guardrail_names, list):
            print(
                f"Warning: 'tool_input_guardrails' key in {config_path} should be a list of guardrail names. Ignoring."
            )
            tool_input_guardrail_names = []

        missing_tool_input = []
        for guardrail_name in tool_input_guardrail_names:
            if guardrail_name in discovered_tool_input:
                tool_input_guardrails_list.append(discovered_tool_input[guardrail_name])
            else:
                missing_tool_input.append(guardrail_name)
        if missing_tool_input:
            print(
                f"Warning: The following tool input guardrails listed in '{config_path}' were not found: {', '.join(missing_tool_input)}"
            )

        # Process tool-level output guardrails from config (SDK 0.7+)
        tool_output_guardrail_names = config.get("tool_output_guardrails", [])
        if not isinstance(tool_output_guardrail_names, list):
            print(
                f"Warning: 'tool_output_guardrails' key in {config_path} should be a list of guardrail names. Ignoring."
            )
            tool_output_guardrail_names = []

        missing_tool_output = []
        for guardrail_name in tool_output_guardrail_names:
            if guardrail_name in discovered_tool_output:
                tool_output_guardrails_list.append(
                    discovered_tool_output[guardrail_name]
                )
            else:
                missing_tool_output.append(guardrail_name)
        if missing_tool_output:
            print(
                f"Warning: The following tool output guardrails listed in '{config_path}' were not found: {', '.join(missing_tool_output)}"
            )

    # --- Content Scanner Guardrails (auto-injected from security providers) ---
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        scanner_cfg = (project_defaults["security"].get("providers") or {}).get(
            "content_scanner"
        )
        if scanner_cfg and scanner_cfg.get("type", "none") != "none":
            try:
                from omniagents.core.providers import resolve_provider
                from omniagents.core.providers.content_scanner.guardrail_adapter import (
                    make_scanner_input_guardrail,
                    make_scanner_output_guardrail,
                )

                scanner = resolve_provider("content_scanner", scanner_cfg)
                input_guardrails_list.insert(0, make_scanner_input_guardrail(scanner))
                output_guardrails_list.insert(0, make_scanner_output_guardrail(scanner))
            except Exception as e:
                print(f"Warning: Failed to set up content scanner guardrails: {e}")

    # --- Predicate Discovery (for is_enabled callables) ---
    # Discovered once per YAML; used to resolve `is_enabled:` references on
    # tools, handoffs, and agent_tools below.
    predicate_registry: Dict[str, Callable] = {}
    if not remote_ws_url:
        try:
            predicate_registry = discover_predicates_in_dir(base_dir)
            if predicate_registry:
                Debug.log(
                    f"Discovered {len(predicate_registry)} is_enabled predicates from {base_dir}"
                )
        except Exception as e:
            print(f"Warning: predicate discovery failed: {e}")

    # --- Apply is_enabled to function tools ---
    # The `tools:` block supports either a bare tool name (str) OR a dict like
    # {"name": "my_tool", "is_enabled": "my_predicate"}. Function tools are
    # already constructed FunctionTool dataclass instances, so we mutate the
    # `is_enabled` attribute in place.
    if not remote_ws_url and isinstance(activated_tool_names, list):
        # Re-walk the original `tools:` list to look for dict-form entries.
        raw_tools_list = config.get("tools", [])
        if isinstance(raw_tools_list, list):
            from agents.tool import FunctionTool as _SDKFunctionTool

            tool_by_name = {
                getattr(t, "name", None): t for t in selected_tool_functions
            }
            for entry in raw_tools_list:
                if not isinstance(entry, dict):
                    continue
                tool_name = entry.get("name") or entry.get("tool")
                if not tool_name:
                    continue
                tool_obj = tool_by_name.get(tool_name)
                if tool_obj is None:
                    continue
                is_enabled_raw = entry.get("is_enabled")
                if is_enabled_raw is None:
                    continue
                resolved = resolve_predicate(is_enabled_raw, predicate_registry)
                if resolved is None:
                    print(
                        f"Warning: tool '{tool_name}' is_enabled='{is_enabled_raw}' "
                        f"could not be resolved. Leaving default."
                    )
                    continue
                # FunctionTool is a @dataclass — mutating `is_enabled` is supported.
                if isinstance(tool_obj, _SDKFunctionTool):
                    try:
                        tool_obj.is_enabled = resolved
                        Debug.log(f"Applied is_enabled predicate to tool '{tool_name}'")
                    except Exception as e:
                        print(
                            f"Warning: failed to set is_enabled on tool '{tool_name}': {e}"
                        )

    # --- Handoffs Configuration ---
    handoff_configs_list = []
    if not remote_ws_url:  # Only process handoffs for local agents
        handoffs_config = config.get("handoffs", [])
        if not isinstance(handoffs_config, list):
            print(
                f"Warning: 'handoffs' key in {config_path} should be a list. Ignoring."
            )
            handoffs_config = []

        for handoff_item in handoffs_config:
            # Bare-string shorthand: `handoffs: [explorer]` is sugar for
            # `handoffs: [{agent: explorer}]`. Mirrors the agent_tools shape.
            if isinstance(handoff_item, str):
                handoff_item = {"agent": handoff_item}

            if isinstance(handoff_item, dict):
                # `agent: <name>` resolves against the project agent registry
                # (built by core/project/runtime.py from paths.agents). Translates
                # to the same yaml_path the `yaml:` branch produces, so downstream
                # HandoffConfig handling is unchanged.
                if "agent" in handoff_item:
                    agent_name = handoff_item.get("agent")
                    if not isinstance(agent_name, str) or not agent_name.strip():
                        print(
                            f"Warning: handoff 'agent' value must be a non-empty string in {config_path}. Skipping."
                        )
                        continue
                    if agents_map is None:
                        print(
                            f"Warning: handoff entry uses 'agent: {agent_name}' but no project agent registry is available "
                            f"(loader was called without agents_map). Skipping. Use 'yaml:' or load via project."
                        )
                        continue
                    handoff_yaml_path = agents_map.get(agent_name)
                    if handoff_yaml_path is None:
                        print(
                            f"Warning: handoff 'agent: {agent_name}' not found in project. "
                            f"Available: {sorted(agents_map.keys())}. Skipping."
                        )
                        continue
                    handoff_config = HandoffConfig(
                        yaml_path=str(handoff_yaml_path),
                        tool_name_override=handoff_item.get("tool_name_override"),
                        tool_description_override=handoff_item.get(
                            "tool_description_override"
                        ),
                        is_enabled=resolve_predicate(
                            handoff_item.get("is_enabled"), predicate_registry
                        ),
                    )
                    handoff_configs_list.append(handoff_config)
                # Check if it's an external YAML reference
                elif "yaml" in handoff_item:
                    yaml_path = handoff_item.get("yaml")

                    # Resolve path relative to current YAML file
                    handoff_yaml_path = (base_dir / yaml_path).resolve()
                    if not handoff_yaml_path.is_file():
                        try:
                            display_path = handoff_yaml_path.relative_to(Path.cwd())
                        except ValueError:
                            display_path = handoff_yaml_path
                        print(
                            f"Warning: Handoff agent YAML file not found: {display_path}. Skipping."
                        )
                        continue

                    # Create HandoffConfig for external agent
                    # The actual YAML will be loaded during agent building
                    handoff_config = HandoffConfig(
                        yaml_path=str(handoff_yaml_path),
                        tool_name_override=handoff_item.get("tool_name_override"),
                        tool_description_override=handoff_item.get(
                            "tool_description_override"
                        ),
                        is_enabled=resolve_predicate(
                            handoff_item.get("is_enabled"), predicate_registry
                        ),
                    )
                    handoff_configs_list.append(handoff_config)

                # Check if it's an inline agent definition
                elif "inline" in handoff_item:
                    inline_config = handoff_item.get("inline")
                    if not isinstance(inline_config, dict):
                        print(
                            f"Warning: 'inline' value in handoff should be a dictionary. Skipping."
                        )
                        continue

                    try:
                        # Parse the inline agent configuration
                        inline_spec = _parse_inline_agent_spec(
                            inline_config, base_dir, _loading_stack
                        )

                        # Create HandoffConfig for inline agent
                        handoff_config = HandoffConfig(
                            inline_spec=inline_spec,
                            tool_name_override=handoff_item.get("tool_name_override"),
                            tool_description_override=handoff_item.get(
                                "tool_description_override"
                            ),
                            is_enabled=resolve_predicate(
                                handoff_item.get("is_enabled"), predicate_registry
                            ),
                        )
                        handoff_configs_list.append(handoff_config)
                    except Exception as e:
                        print(f"Warning: Failed to parse inline agent: {e}. Skipping.")
                        continue

                else:
                    print(
                        f"Warning: Handoff item must have 'agent', 'yaml', or 'inline' field in {config_path}. Skipping."
                    )
            else:
                print(
                    f"Warning: Invalid handoff item in {config_path}. Expected dict or string. Skipping."
                )

    # --- Agent-as-Tool Configuration ---
    # `agent_tools:` exposes other agents as callable tools (Agent.as_tool()).
    # Differs from handoffs: caller stays in control after the sub-agent runs.
    agent_tool_configs_list: List[AgentToolConfig] = []
    if not remote_ws_url:
        agent_tools_config = config.get("agent_tools", [])
        if agent_tools_config and not isinstance(agent_tools_config, list):
            print(
                f"Warning: 'agent_tools' key in {config_path} should be a list. Ignoring."
            )
            agent_tools_config = []

        for at_item in agent_tools_config:
            # Bare-string shorthand: `agent_tools: [explorer]` is sugar for
            # `agent_tools: [{agent: explorer}]`. Mirrors the bare-string
            # form `tools:` already accepts.
            if isinstance(at_item, str):
                at_item = {"agent": at_item}

            if not isinstance(at_item, dict):
                print(
                    f"Warning: Invalid agent_tool item in {config_path}. Expected dict or string. Skipping."
                )
                continue

            tool_name = at_item.get("tool_name")
            tool_description = at_item.get("tool_description")
            is_enabled_resolved = resolve_predicate(
                at_item.get("is_enabled"), predicate_registry
            )
            needs_approval_raw = at_item.get("needs_approval")
            needs_approval_resolved = (
                needs_approval_raw
                if isinstance(needs_approval_raw, bool)
                else resolve_predicate(needs_approval_raw, predicate_registry)
            )
            max_turns_val = at_item.get("max_turns")

            # `agent: <name>` resolves against the project agent registry
            # (built by core/project/runtime.py from paths.agents). Translates
            # to the same yaml_path the `yaml:` branch produces, so downstream
            # AgentToolConfig handling is unchanged.
            if "agent" in at_item:
                agent_name = at_item.get("agent")
                if not isinstance(agent_name, str) or not agent_name.strip():
                    print(
                        f"Warning: agent_tool 'agent' value must be a non-empty string in {config_path}. Skipping."
                    )
                    continue
                if agents_map is None:
                    print(
                        f"Warning: agent_tool entry uses 'agent: {agent_name}' but no project agent registry is available "
                        f"(loader was called without agents_map). Skipping. Use 'yaml:' or load via project."
                    )
                    continue
                resolved_yp = agents_map.get(agent_name)
                if resolved_yp is None:
                    print(
                        f"Warning: agent_tool 'agent: {agent_name}' not found in project. "
                        f"Available: {sorted(agents_map.keys())}. Skipping."
                    )
                    continue
                agent_tool_configs_list.append(
                    AgentToolConfig(
                        yaml_path=str(resolved_yp),
                        tool_name=tool_name,
                        tool_description=tool_description,
                        is_enabled=is_enabled_resolved,
                        needs_approval=needs_approval_resolved,
                        max_turns=max_turns_val,
                    )
                )
            elif "yaml" in at_item:
                yp = at_item.get("yaml")
                if not isinstance(yp, str) or not yp.strip():
                    print(
                        f"Warning: agent_tool entry missing valid 'yaml' path in {config_path}. Skipping."
                    )
                    continue
                resolved_yp = (base_dir / yp).resolve()
                if not resolved_yp.is_file():
                    print(
                        f"Warning: agent_tool YAML not found: {resolved_yp}. Skipping."
                    )
                    continue
                agent_tool_configs_list.append(
                    AgentToolConfig(
                        yaml_path=str(resolved_yp),
                        tool_name=tool_name,
                        tool_description=tool_description,
                        is_enabled=is_enabled_resolved,
                        needs_approval=needs_approval_resolved,
                        max_turns=max_turns_val,
                    )
                )
            elif "inline" in at_item:
                inline_cfg = at_item.get("inline")
                if not isinstance(inline_cfg, dict):
                    print(
                        f"Warning: agent_tool 'inline' value must be a dict in {config_path}. Skipping."
                    )
                    continue
                try:
                    inline_spec = _parse_inline_agent_spec(
                        inline_cfg, base_dir, _loading_stack
                    )
                except Exception as e:
                    print(f"Warning: Failed to parse inline agent_tool: {e}. Skipping.")
                    continue
                agent_tool_configs_list.append(
                    AgentToolConfig(
                        inline_spec=inline_spec,
                        tool_name=tool_name,
                        tool_description=tool_description,
                        is_enabled=is_enabled_resolved,
                        needs_approval=needs_approval_resolved,
                        max_turns=max_turns_val,
                    )
                )
            else:
                print(
                    f"Warning: agent_tool item must have 'agent', 'yaml', or 'inline' in {config_path}. Skipping."
                )

    # --- Capabilities (Phase 0) ---
    # `capabilities:` is a list of references to capabilities defined at
    # project scope (inline in project.yml or under paths.capabilities/).
    # Each entry is either a bare name string or a dict with `name:` plus
    # optional `overrides:` (approvals/instructions/options/sampling_params).
    # The agent builder calls cap.tools() on each resolved capability and
    # merges into agent.tools at build time.
    resolved_capabilities_list: List[Any] = []
    capabilities_config = config.get("capabilities", [])
    if capabilities_config and not isinstance(capabilities_config, list):
        print(f"Warning: 'capabilities' key in {config_path} must be a list. Ignoring.")
        capabilities_config = []

    for cap_entry in capabilities_config:
        # Bare-string shorthand: `capabilities: [filesystem]` references the
        # capability as-defined; no overrides.
        if isinstance(cap_entry, str):
            cap_name = cap_entry
            overrides_block = None
        elif isinstance(cap_entry, dict):
            cap_name = cap_entry.get("name")
            overrides_block = cap_entry.get("overrides")
            if not isinstance(cap_name, str) or not cap_name.strip():
                print(
                    f"Warning: capability entry in {config_path} requires a string 'name'. Skipping."
                )
                continue
            if overrides_block is not None and not isinstance(overrides_block, dict):
                print(
                    f"Warning: capability {cap_name!r} 'overrides' must be a dict in {config_path}. Skipping overrides."
                )
                overrides_block = None
        else:
            print(
                f"Warning: capability entry in {config_path} must be a string or dict. Skipping."
            )
            continue

        if capability_registry is None:
            print(
                f"Warning: agent {config_path} references capability {cap_name!r} but no project "
                "capability_registry is available (loader was called without one). Skipping. "
                "Capabilities require loading via project."
            )
            continue
        cap = capability_registry.get(cap_name)
        if cap is None:
            print(
                f"Warning: capability {cap_name!r} not found in project. "
                f"Available: {sorted(capability_registry.keys())}. Skipping."
            )
            continue

        # Apply per-agent overrides without mutating the shared registry
        # instance. Currently supported: approvals (replace), instructions
        # (append), options (shallow merge for Python-class caps).
        if overrides_block:
            try:
                cap = cap.model_copy(deep=False)  # Pydantic copy
            except Exception:
                # Capability isn't a Pydantic model — skip override and warn.
                print(
                    f"Warning: capability {cap_name!r} does not support overrides "
                    f"(not a Pydantic model). Ignoring overrides in {config_path}."
                )
            else:
                if "approvals" in overrides_block:
                    new_approvals = overrides_block.get("approvals") or {}
                    if isinstance(new_approvals, dict):
                        cap.approvals_spec = new_approvals  # type: ignore[attr-defined]
                if "instructions" in overrides_block:
                    appended = overrides_block.get("instructions")
                    if isinstance(appended, str):
                        existing = getattr(cap, "instructions_text", None) or ""
                        cap.instructions_text = (  # type: ignore[attr-defined]
                            (existing + "\n\n" + appended) if existing else appended
                        )

        resolved_capabilities_list.append(cap)

    # Tool-name conflicts across capabilities used by the same agent are a
    # modeling mistake (see CAPABILITY_PACKS_DESIGN.md §10 Q1). Surface them
    # at load time rather than letting the model see two tools with the same
    # name and pick arbitrarily.
    seen_tool_names: Dict[str, str] = {}
    for cap in resolved_capabilities_list:
        cap_label = getattr(cap, "capability_name", None) or getattr(cap, "type", "?")
        for tool in cap.tools():
            tname = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if tname is None:
                continue
            if tname in seen_tool_names:
                raise ValueError(
                    f"Capability tool name conflict in {config_path}: tool {tname!r} "
                    f"appears in both {seen_tool_names[tname]!r} and {cap_label!r}. "
                    "A tool can only belong to one active capability per agent."
                )
            seen_tool_names[tname] = cap_label

    # Capabilities can also bundle agent_tools (subagent-as-tool refs).
    # Flatten them into the agent's own agent_tool_configs so the existing
    # builder path (_build_agent_tools_from_configs) handles them uniformly.
    for cap in resolved_capabilities_list:
        cap_at = getattr(cap, "agent_tool_configs", None) or []
        agent_tool_configs_list.extend(cap_at)

    # --- Model Provider Configuration ---
    # Optional `model_provider:` block that controls how the run-level
    # ModelProvider is constructed. Currently used to opt into the
    # Responses-over-WebSocket transport.
    model_provider_config = config.get("model_provider")
    if model_provider_config is not None and not isinstance(
        model_provider_config, dict
    ):
        print(f"Warning: 'model_provider' in {config_path} should be a dict. Ignoring.")
        model_provider_config = None
    if isinstance(model_provider_config, dict):
        # Apply env var substitution so users can keep ${OPENAI_API_KEY} etc.
        try:
            model_provider_config = _substitute_env_vars(model_provider_config)
        except ValueError as e:
            print(
                f"Warning: env substitution failed for model_provider: {e}. Ignoring."
            )
            model_provider_config = None

    # If reasoning_content_replay was set under model_settings, fold it into the
    # model_provider config so the runtime wiring sees it in one place.
    if _reasoning_replay_cfg is not None:
        if not isinstance(model_provider_config, dict):
            model_provider_config = {}
        model_provider_config["reasoning_content_replay"] = _reasoning_replay_cfg

    # --- Safety Configuration ---
    use_safe = config.get("use_safe_agent", True)  # Default to safe
    safe_options_config = config.get("safe_agent_options", {})
    safe_options = SafeAgentOptions(
        skip_approvals=safe_options_config.get("skip_approvals", False),
        halt_on_rejection=safe_options_config.get("halt_on_rejection", True),
        safe_tool_names=safe_options_config.get("safe_tool_names", []),
        safe_tool_patterns=safe_options_config.get("safe_tool_patterns", []),
    )
    _emit_safe_agent_deprecations(safe_options_config, source=str(config_path))

    # Apply project-level safety mode override
    _safety_mode = None
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        _safety_mode = project_defaults["security"].get("safety_mode")
    if _safety_mode == "enforced":
        if safe_options.skip_approvals:
            print(
                "Security: safety_mode is 'enforced' — overriding skip_approvals=True to False"
            )
            safe_options.skip_approvals = False
        if not use_safe:
            print(
                "Security: safety_mode is 'enforced' — overriding use_safe_agent=False to True"
            )
            use_safe = True

    # --- MCP Configuration ---
    mcp_servers = _parse_mcp_servers_config(config, config_path=str(config_file))

    # Merge user MCP servers if provided (user configs override project configs)
    if user_mcp_servers:
        servers_by_name = {s.name: s for s in mcp_servers}
        for user_server in user_mcp_servers:
            servers_by_name[user_server.name] = user_server
        mcp_servers = list(servers_by_name.values())

    # Apply project-level MCP allowlist
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        sec = project_defaults["security"]
        if sec.get("mcp_require_allowlist"):
            allowed = set(sec.get("mcp_allowed_servers") or [])
            if allowed:
                filtered = []
                for srv in mcp_servers:
                    if srv.name in allowed:
                        filtered.append(srv)
                    else:
                        print(
                            f"Security: MCP server '{srv.name}' not in allowlist — blocked"
                        )
                mcp_servers = filtered
            else:
                # Allowlist required but empty = block all MCP servers
                if mcp_servers:
                    print(
                        "Security: mcp_require_allowlist is enabled but mcp_allowed_servers is empty — blocking all MCP servers"
                    )
                    mcp_servers = []

    # Extended MCP config
    mcp_config_from_yaml = config.get("mcp_config", {})
    if not isinstance(mcp_config_from_yaml, dict):
        print(f"Warning: 'mcp_config' should be a dictionary. Using empty dict.")
        mcp_config_from_yaml = {}

    # --- Settings ---
    # Start with default OpenAI settings
    combined_settings_fields_dict = {
        field.key: field for field in DEFAULT_SETTINGS_FIELDS
    }
    # Project-level settings fields (optional)
    if project_defaults and isinstance(project_defaults.get("settings"), list):
        for setting_dict in project_defaults.get("settings"):
            if (
                isinstance(setting_dict, dict)
                and "key" in setting_dict
                and "label" in setting_dict
            ):
                field = SettingField(
                    key=setting_dict["key"],
                    label=setting_dict["label"],
                    group=setting_dict.get("group"),
                    default=setting_dict.get("default", ""),
                    secret=setting_dict.get("secret", False),
                    hint=setting_dict.get("hint", ""),
                )
                combined_settings_fields_dict[field.key] = field
    # Add default MCP settings if MCP is used
    # Add/override with custom settings from YAML
    custom_settings = config.get("settings", [])
    if isinstance(custom_settings, list):
        for setting_dict in custom_settings:
            if (
                isinstance(setting_dict, dict)
                and "key" in setting_dict
                and "label" in setting_dict
            ):
                # Use SettingField constructor for validation/defaults
                field = SettingField(
                    key=setting_dict["key"],
                    label=setting_dict["label"],
                    group=setting_dict.get("group"),
                    default=setting_dict.get("default", ""),
                    secret=setting_dict.get("secret", False),
                    hint=setting_dict.get("hint", ""),
                )
                combined_settings_fields_dict[field.key] = field
            else:
                print(
                    f"Warning: Invalid item in 'settings' list in {config_path}. Skipping: {setting_dict}"
                )
    else:
        print(
            f"Warning: 'settings' key in {config_path} should be a list of setting definitions. Ignoring."
        )

    final_settings_fields = list(combined_settings_fields_dict.values())

    # --- Tracing Configuration ---
    tracing_config = config.get("tracing")
    if tracing_config and not isinstance(tracing_config, dict):
        print(f"Warning: 'tracing' in {config_path} should be a dictionary. Ignoring.")
        tracing_config = None
    if (
        tracing_config is None
        and project_defaults
        and isinstance(project_defaults.get("tracing"), dict)
    ):
        tracing_config = project_defaults.get("tracing")

    # --- Variables and Context Factory for dynamic instructions ---
    # Variables support environment variable substitution
    # Context factory is specified by name via `context:` field and discovered from agent dir
    variables_config = config.get("variables")
    context_factory_name = config.get("context")

    # Discover context factories from agent directory (decorated with @context_factory)
    build_context_func: Optional[Callable] = None
    if context_factory_name:
        context_factories = discover_context_factories(base_dir)
        if context_factory_name in context_factories:
            build_context_func = context_factories[context_factory_name]
            Debug.log(f"Using context factory: {context_factory_name}")
        else:
            available = list(context_factories.keys()) if context_factories else []
            print(
                f"Warning: Context factory '{context_factory_name}' not found in {base_dir}. "
                f"Available: {available}"
            )

    # --- Model Config Resolver Discovery ---
    # Discover model config resolver from agent directory (decorated with @model_config_resolver)
    model_config_resolver_name = config.get("model_config_resolver")
    model_config_resolver_func: Optional[Callable] = None
    if model_config_resolver_name:
        model_config_resolvers = discover_model_config_resolvers(base_dir)
        if model_config_resolver_name in model_config_resolvers:
            model_config_resolver_func = model_config_resolvers[
                model_config_resolver_name
            ]
            Debug.log(f"Using model config resolver: {model_config_resolver_name}")
        else:
            available = (
                list(model_config_resolvers.keys()) if model_config_resolvers else []
            )
            print(
                f"Warning: Model config resolver '{model_config_resolver_name}' not found in {base_dir}. "
                f"Available: {available}"
            )

    realtime_settings_resolver_name = config.get("realtime_settings_resolver")
    realtime_settings_resolver_func: Optional[Callable] = None
    if realtime_settings_resolver_name:
        realtime_settings_resolvers = discover_realtime_settings_resolvers(base_dir)
        if realtime_settings_resolver_name in realtime_settings_resolvers:
            realtime_settings_resolver_func = realtime_settings_resolvers[
                realtime_settings_resolver_name
            ]
            Debug.log(
                f"Using realtime settings resolver: {realtime_settings_resolver_name}"
            )
        else:
            available = (
                list(realtime_settings_resolvers.keys())
                if realtime_settings_resolvers
                else []
            )
            print(
                f"Warning: Realtime settings resolver '{realtime_settings_resolver_name}' not found in {base_dir}. "
                f"Available: {available}"
            )

    # Apply environment variable substitution if variables are provided
    context_config = None
    variables_for_spec = None

    if variables_config is not None:
        try:
            # Apply environment variable substitution to variables
            substituted_vars = _substitute_env_vars(variables_config)
            Debug.log(f"Variables after substitution: {substituted_vars}")

            if build_context_func:
                # When context factory is specified:
                # - Store substituted variables as spec.variables
                # - Leave spec.context as None
                # - The factory will be called at runtime to build context
                variables_for_spec = substituted_vars
                context_config = None
            else:
                # No factory: use variables directly as context (original behavior)
                context_config = substituted_vars

        except ValueError as e:
            print(f"Error substituting environment variables: {e}")
            raise

    # --- Build Agent (Placeholder/Default) ---
    # The actual Agent/SafeAgent creation is deferred to AgentApp using a default builder.
    # We pass None here; AgentApp will use its internal builder.
    build_agent_func = None

    # --- Run-level toggles (SDK 0.16+) ---
    nest_handoff_history_cfg = bool(config.get("nest_handoff_history", False))
    tool_execution_cfg = config.get("tool_execution") or None
    if tool_execution_cfg is not None and not isinstance(tool_execution_cfg, dict):
        print("Warning: 'tool_execution' must be a dict. Ignoring.")
        tool_execution_cfg = None

    # error_handlers: resolve names via discovery (similar to guardrails),
    # falling back to dotted-path import.
    error_handlers_raw = config.get("error_handlers") or None
    if error_handlers_raw is not None and not isinstance(error_handlers_raw, dict):
        print("Warning: 'error_handlers' must be a dict. Ignoring.")
        error_handlers_raw = None
    error_handlers_cfg = None
    if error_handlers_raw:
        from omniagents.core.agents.run_options import resolve_error_handlers

        try:
            # Pull the project-level error-handler registry off the spec
            # (populated by project loader) when available; fall back to None
            # so dotted-path resolution kicks in.
            _eh_registry = None
            try:
                if isinstance(project_defaults, dict):
                    _eh_registry = (project_defaults or {}).get(
                        "error_handlers_registry"
                    )
            except Exception:
                _eh_registry = None
            error_handlers_cfg = resolve_error_handlers(
                error_handlers_raw, registry=_eh_registry
            )
        except Exception as e:
            print(f"Warning: Failed to resolve error_handlers: {e}. Ignoring.")
            error_handlers_cfg = None

    # SDK lifecycle hooks (RunHooks / AgentHooks). Discover from
    # ``lifecycle_hooks/`` subdirectory and pick by name from YAML.
    agent_hooks_obj: Optional[Any] = None
    run_hooks_obj: Optional[Any] = None
    if not remote_ws_url:
        try:
            from omniagents.core.lifecycle_hooks.discovery import (
                discover_lifecycle_hooks_in_dir,
            )

            lifecycle_hooks_dir = (base_dir / "lifecycle_hooks").resolve()
            run_hooks_map: Dict[str, Any] = {}
            agent_hooks_map: Dict[str, Any] = {}
            if lifecycle_hooks_dir.is_dir():
                run_hooks_map, agent_hooks_map = discover_lifecycle_hooks_in_dir(
                    str(lifecycle_hooks_dir)
                )
            requested_agent_hook = config.get("agent_hooks")
            requested_run_hook = config.get("run_hooks")
            if isinstance(requested_agent_hook, str):
                agent_hooks_obj = agent_hooks_map.get(requested_agent_hook)
                if agent_hooks_obj is None:
                    print(
                        f"Warning: agent_hooks '{requested_agent_hook}' not found in lifecycle_hooks/. Ignoring."
                    )
            if isinstance(requested_run_hook, str):
                run_hooks_obj = run_hooks_map.get(requested_run_hook)
                if run_hooks_obj is None:
                    print(
                        f"Warning: run_hooks '{requested_run_hook}' not found in lifecycle_hooks/. Ignoring."
                    )
        except Exception as e:
            print(f"Warning: lifecycle hook discovery failed: {e}. Ignoring.")

    # retry_policy: coerce to ModelRetrySettings stored on spec.retry_settings.
    retry_policy_raw = config.get("retry_policy") or None
    if retry_policy_raw is not None and not isinstance(retry_policy_raw, dict):
        print("Warning: 'retry_policy' must be a dict. Ignoring.")
        retry_policy_raw = None
    retry_settings_cfg: Any = None
    if retry_policy_raw:
        from omniagents.core.agents.run_options import resolve_retry_settings

        try:
            retry_settings_cfg = resolve_retry_settings(retry_policy_raw)
        except Exception as e:
            print(f"Warning: Failed to resolve retry_policy: {e}. Ignoring.")
            retry_settings_cfg = None

    # --- Session-related YAML block (backend + compaction) ---
    # YAML schema:
    #   session:
    #     backend:                  # opt-in SDK-managed backend
    #       type is one of: redis | sqlalchemy | mongodb | encrypted |
    #         openai_conversations | sqlite
    #       # backend-specific kwargs (url, session_id, ttl, inner, ...)
    #     compaction:               # opt-in SDK-native context compaction
    #       enabled: true
    #       # any kwargs forwarded to OpenAIResponsesCompactionSession
    session_backend_obj: Any = None
    session_compaction_cfg: Optional[Dict[str, Any]] = None
    session_block = config.get("session")
    if session_block is not None and not isinstance(session_block, dict):
        print("Warning: 'session' must be a dict. Ignoring.")
        session_block = None
    if isinstance(session_block, dict):
        backend_block = session_block.get("backend")
        if backend_block is not None and not isinstance(backend_block, dict):
            print("Warning: 'session.backend' must be a dict. Ignoring.")
            backend_block = None
        if isinstance(backend_block, dict):
            try:
                from omniagents.core.session.backends import build_session_backend

                session_backend_obj = build_session_backend(backend_block)
            except ImportError as e:
                print(f"Warning: {e}. Falling back to default SQLite storage.")
                session_backend_obj = None
            except Exception as e:
                print(
                    f"Warning: Failed to resolve session.backend: {e}. "
                    f"Falling back to default SQLite storage."
                )
                session_backend_obj = None

        compaction_raw = session_block.get("compaction")
        if compaction_raw is not None and not isinstance(compaction_raw, dict):
            print("Warning: 'session.compaction' must be a dict. Ignoring.")
            compaction_raw = None
        if compaction_raw:
            session_compaction_cfg = dict(compaction_raw)

    # --- Sandbox (SDK 0.14+) ---
    # YAML schema:
    #   sandbox:
    #     use_sandbox_agent: false
    #     run_as: "user"
    #     client:
    #       type is one of: unix_local | docker | e2b | modal | runloop |
    #         daytona | blaxel | vercel | cloudflare
    #     options: { ... }
    #     manifest:
    #       entries: { ... }
    #     snapshot:
    #       type is one of: local | remote | noop
    #     concurrency_limits:
    #       manifest_entries: 4
    #       local_dir_files: 8
    #     capabilities:
    #       - filesystem
    #       - shell
    #       - skills: { ... }
    sandbox_block = config.get("sandbox")
    if sandbox_block is not None and not isinstance(sandbox_block, dict):
        print("Warning: 'sandbox' must be a dict. Ignoring.")
        sandbox_block = None
    sandbox_run_config_obj: Any = None
    sandbox_capabilities_list: Optional[List[Any]] = None
    sandbox_use_agent_flag = False
    sandbox_run_as_value: Optional[str] = None
    if isinstance(sandbox_block, dict):
        sandbox_use_agent_flag = bool(sandbox_block.get("use_sandbox_agent", False))
        run_as_raw = sandbox_block.get("run_as")
        if isinstance(run_as_raw, str):
            sandbox_run_as_value = run_as_raw
        try:
            from omniagents.core.sandbox.factory import (
                build_sandbox_capabilities,
                build_sandbox_run_config,
            )

            sandbox_run_config_obj = build_sandbox_run_config(sandbox_block)
            sandbox_capabilities_list = build_sandbox_capabilities(sandbox_block)
        except ImportError as e:
            print(f"Warning: {e}. Sandbox configuration disabled.")
            sandbox_run_config_obj = None
            sandbox_capabilities_list = None
        except Exception as e:
            print(
                f"Warning: Failed to resolve sandbox configuration: {e}. "
                "Sandbox configuration disabled."
            )
            sandbox_run_config_obj = None
            sandbox_capabilities_list = None

    # --- Construct AgentSpec ---
    spec = AgentSpec(
        # Basic Info
        name=agent_name,
        welcome_text=welcome_text,
        # Agent Config
        get_agent_instructions=get_instructions_func,
        build_agent=build_agent_func,  # Defer to internal builder
        available_tools=selected_tool_functions if not remote_ws_url else [],
        model_name=model_name_from_config if not remote_ws_url else None,
        max_turns=max_turns_cfg,
        handoff_description=handoff_description,
        tool_description=tool_description,
        reset_tool_choice=reset_tool_choice,
        tool_use_behavior=tool_use_behavior,
        model_settings=model_settings_config if model_settings_config else None,
        realtime_mode=realtime_mode,
        realtime_settings=realtime_settings,
        voice_backend=voice_backend,
        mcp_config=mcp_config_from_yaml if mcp_config_from_yaml else None,
        mcp_servers=mcp_servers,
        output_type=output_type,
        output_type_strict=output_type_strict,
        prompt=prompt,
        # Handoffs
        handoff_configs=handoff_configs_list,
        # Agent-as-Tool
        agent_tool_configs=agent_tool_configs_list,
        # Model Provider
        model_provider_config=model_provider_config,
        # Guardrails
        input_guardrails=input_guardrails_list,
        output_guardrails=output_guardrails_list,
        tool_input_guardrails=tool_input_guardrails_list,
        tool_output_guardrails=tool_output_guardrails_list,
        # Agent Safety Config
        use_safe_agent=use_safe,
        safe_agent_options=safe_options,
        # SDK Runner Context
        context=context_config,
        variables=variables_for_spec,
        build_context=build_context_func,
        # Run-level toggles (SDK 0.16+)
        nest_handoff_history=nest_handoff_history_cfg,
        tool_execution_config=tool_execution_cfg,
        error_handlers=error_handlers_cfg,
        retry_settings=(retry_settings_cfg if retry_settings_cfg is not None else None),
        agent_hooks=agent_hooks_obj,
        run_hooks=run_hooks_obj,
        # SDK-native session backend (opt-in; None means use default SQLite)
        session_backend=session_backend_obj,
        session_compaction_config=session_compaction_cfg,
        # Sandbox (SDK 0.14+; opt-in)
        sandbox_config=sandbox_block,
        sandbox_run_config=sandbox_run_config_obj,
        sandbox_capabilities=sandbox_capabilities_list,
        sandbox_use_sandbox_agent=sandbox_use_agent_flag,
        sandbox_run_as=sandbox_run_as_value,
        # Capability Packs (Phase 0)
        resolved_capabilities=resolved_capabilities_list,
        # Tracing Config
        tracing_config=tracing_config,
        # MCP Config
        # UI/UX Config
        settings_fields=final_settings_fields,
        label=label_from_yaml,
        description=description_from_yaml,
        # Remote agent metadata
        remote_ws_url=remote_ws_url,
        remote_auth_token_key=remote_token_env,
        remote_identity_secret_key=remote_identity_secret_env,
        remote_identity_audience=remote_identity_audience,
        remote_identity_allowed_roles=remote_identity_allowed_roles,
        # Storage Metadata
        project_slug=None,
        agent_slug=agent_slug,
        # Include search paths for {% include %} fragments in instructions
        instructions_search_paths=_instruction_search_paths(
            config, base_dir, project_defaults
        ),
    )

    if server_functions_registrar:
        try:
            spec.register_server_functions = server_functions_registrar
        except Exception:
            pass

    if runtime_hooks_registrar:
        try:
            spec.register_runtime_hooks = runtime_hooks_registrar
        except Exception:
            pass

    if model_config_resolver_func:
        try:
            spec.resolve_default_model_config = model_config_resolver_func
        except Exception:
            pass

    if realtime_settings_resolver_func:
        try:
            spec.resolve_realtime_settings = realtime_settings_resolver_func
        except Exception:
            pass

    # Store project-level security config on spec
    if project_defaults and isinstance(project_defaults.get("security"), dict):
        spec.security_config = project_defaults["security"]

    # Store registries in spec for passing to handoff agents
    spec._tool_registry = tool_registry
    spec._guardrails_registry = guardrails_registry
    spec._server_functions_registry = server_functions_registry
    spec._agents_map = agents_map
    spec._capability_registry = capability_registry
    spec._runtime_hooks_registry = None

    return spec
