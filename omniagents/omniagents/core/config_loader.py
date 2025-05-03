import yaml
import flet as ft # For theme colors if specified as strings
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional

# Core imports
from .specs import AgentSpec, AgentLike, SafeAgentOptions
from .settings import SettingField
from .tooling import discover_functions_in_dir

DEFAULT_SETTINGS_FIELDS = [
    SettingField(key="openai_api_base", label="OpenAI API Base URL", group="OpenAI API", default="https://api.openai.com/v1", hint="e.g., https://api.openai.com/v1"),
    SettingField(key="openai_api_key", label="OpenAI API Key", group="OpenAI API", secret=True, hint="Required"),
    SettingField(key="model_name", label="Model Name", group="OpenAI API", default="gpt-4o", hint="e.g., gpt-4o, gpt-3.5-turbo"),
]

DEFAULT_MCP_SETTINGS_FIELDS = [
    SettingField(key="mcp_url", label="MCP Server URL", group="MCP Server", default="", hint="e.g., http://localhost:3000/mcp/sse"),
    SettingField(key="mcp_auth_token", label="MCP Auth Token", group="MCP Server", secret=True, hint="Optional, if MCP server requires auth"),
]

def _load_instructions(config: Dict[str, Any], base_dir: Path) -> Callable[[], str]:
    """Creates a function to load instructions based on YAML config, resolving relative to base_dir."""
    instructions_content = config.get('instructions')
    instructions_file_relative = config.get('instructions_file') # Keep original key name

    if instructions_content:
        # Use literal instructions if provided
        return lambda: str(instructions_content)
    elif instructions_file_relative:
        # Create function to read from file relative to base_dir
        file_path = (base_dir / instructions_file_relative).resolve() # Calculate absolute path and resolve symlinks/..
        def read_instructions_file():
            try:
                if not file_path.is_file():
                    # Show path relative to CWD for clearer error messages if run from workspace root
                    try:
                        display_path = file_path.relative_to(Path.cwd())
                    except ValueError:
                        display_path = file_path # Show absolute if not relative to CWD
                    print(f"Warning: Instructions file not found: {file_path}")
                    return f"Error: Instructions file '{display_path}' not found."
                return file_path.read_text(encoding='utf-8')
            except Exception as e:
                print(f"Error reading instructions file {file_path}: {e}")
                return f"Error reading instructions file: {e}"
        return read_instructions_file
    else:
        # Default instructions if neither is specified
        print("Warning: No instructions or instructions_file specified in config. Using default.")
        return lambda: "You are a helpful assistant."

def _parse_theme_color(color_str: Optional[str], default: str) -> str:
    """Parses a theme color string, allowing Flet color constants."""
    if not color_str:
        return default
    # Check if it matches a Flet color constant (e.g., "ft.Colors.BLUE_900")
    if color_str.startswith("ft.Colors."):
        try:
            # Caution: eval can be risky if the string isn't controlled
            # Assuming YAML is trusted source here.
            return eval(color_str, {"ft": ft}) # Pass ft module context
        except Exception as e:
            print(f"Warning: Could not evaluate theme_color '{color_str}': {e}. Using default.")
            return default
    # Otherwise, assume it's a hex code or standard color name Flet understands
    return color_str

def load_agent_spec_from_yaml(config_path: str) -> AgentSpec:
    """
    Loads agent configuration from a YAML file and builds an AgentSpec.

    Args:
        config_path: Path to the agent.yaml configuration file.

    Returns:
        An AgentSpec instance configured according to the YAML file.

    Raises:
        FileNotFoundError: If the config_path does not exist.
        ValueError: If the configuration is invalid.
    """
    config_file = Path(config_path)
    if not config_file.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_file.resolve()}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file {config_file.resolve()}: {e}")
    except Exception as e:
        raise ValueError(f"Error reading file {config_file.resolve()}: {e}")

    if not isinstance(config, dict):
        raise ValueError(f"Invalid YAML format in {config_file.resolve()}. Expected a dictionary (key-value pairs).")

    # --- Extract and Derive Basic Info ---
    agent_name = config.get('name', 'Unnamed Agent')
    header_title = config.get('header_title', agent_name)
    page_title = config.get('page_title', agent_name)
    # Derive default storage prefix if not provided
    default_storage_prefix = agent_name.lower().replace(' ', '_').replace('-','_') + '.'
    storage_prefix = config.get('client_storage_prefix', default_storage_prefix)
    welcome_text = config.get('welcome_text', f"Hello! I'm {agent_name}. How can I help you?")
    debug_flag = config.get('debug', False)
    page_title_suffix = config.get('page_title_suffix', " (Debug Mode)" if debug_flag else "")
    model_name_from_config = config.get('model') # Get model name from YAML

    # --- Agent Instructions ---
    base_dir = config_file.parent # Get the directory containing the YAML file
    get_instructions_func = _load_instructions(config, base_dir) # Pass base_dir

    # --- Tool Discovery and Selection ---
    # Always check the 'tools' subdirectory first, then any additional directories
    all_discovered_functions = {}
    
    # First check the default 'tools' subdirectory
    tools_dir_path = (base_dir / "tools").resolve()
    if tools_dir_path.is_dir():
        # Discover tools (only verbose in debug mode)
        tools_found = discover_functions_in_dir(str(tools_dir_path), debug_flag)
        all_discovered_functions.update(tools_found)
        if debug_flag:
            print(f"Discovered tools from 'tools' subdirectory: {tools_dir_path}")
    
    # Then check any additional directories specified in the config
    additional_tools_dirs = config.get('additional_tools_dirs', [])
    if not isinstance(additional_tools_dirs, list):
        print(f"Warning: 'additional_tools_dirs' in {config_path} should be a list. Ignoring.")
        additional_tools_dirs = []
    
    for tools_dir in additional_tools_dirs:
        additional_dir_path = (base_dir / tools_dir).resolve()
        if additional_dir_path.is_dir():
            # Discover additional tools (only verbose in debug mode)
            additional_tools = discover_functions_in_dir(str(additional_dir_path), debug_flag)
            # Update with any new tools found (existing tools take precedence)
            all_discovered_functions.update(additional_tools)
            if debug_flag:
                print(f"Discovered additional tools from: {additional_dir_path}")
        else:
            # Show path relative to CWD for clearer error messages if run from workspace root
            try:
                display_path = additional_dir_path.relative_to(Path.cwd())
            except ValueError:
                display_path = additional_dir_path # Show absolute if not relative to CWD
            print(f"Warning: Additional tools directory '{display_path}' not found. Skipping.")
    
    if not all_discovered_functions:
        print(f"Warning: No tools discovered in any of the specified directories.")

    activated_tool_names = config.get('tools', [])
    selected_tool_functions = []
    missing_tools = []
    if not isinstance(activated_tool_names, list):
        print(f"Warning: 'tools' key in {config_path} should be a list of tool names. Ignoring.")
        activated_tool_names = []

    for tool_name in activated_tool_names:
        if tool_name in all_discovered_functions:
            selected_tool_functions.append(all_discovered_functions[tool_name])
        else:
            missing_tools.append(tool_name)

    if missing_tools:
        # Updated error message
        try:
            display_path = tools_dir_path.relative_to(Path.cwd())
        except ValueError:
            display_path = tools_dir_path
        print(f"Warning: The following tools listed in '{config_path}' were not found in the agent's tools directory '{display_path}' or are not valid marked functions: {', '.join(missing_tools)}")
        # Continue without missing tools

    # --- Safety Configuration ---
    use_safe = config.get('use_safe_agent', True) # Default to safe
    safe_options_config = config.get('safe_agent_options', {})
    safe_options = SafeAgentOptions(
        skip_approvals=safe_options_config.get('skip_approvals', False),
        halt_on_rejection=safe_options_config.get('halt_on_rejection', True),
        safe_tool_names=safe_options_config.get('safe_tool_names', []),
        safe_tool_patterns=safe_options_config.get('safe_tool_patterns', []),
    )

    # --- MCP Configuration ---
    uses_mcp = config.get('uses_mcp', False)
    mcp_url_key = config.get('mcp_url_setting_key', 'mcp_url')
    mcp_token_key = config.get('mcp_token_setting_key', 'mcp_auth_token')
    show_mcp_indicator = config.get('show_mcp_status_indicator', True)

    # --- Settings ---
    # Start with default OpenAI settings
    combined_settings_fields_dict = {field.key: field for field in DEFAULT_SETTINGS_FIELDS}
    # Add default MCP settings if MCP is used
    if uses_mcp:
        for field in DEFAULT_MCP_SETTINGS_FIELDS:
            combined_settings_fields_dict[field.key] = field

    # Add/override with custom settings from YAML
    custom_settings = config.get('settings', [])
    if isinstance(custom_settings, list):
        for setting_dict in custom_settings:
            if isinstance(setting_dict, dict) and 'key' in setting_dict and 'label' in setting_dict:
                # Use SettingField constructor for validation/defaults
                field = SettingField(
                    key=setting_dict['key'],
                    label=setting_dict['label'],
                    group=setting_dict.get('group'),
                    default=setting_dict.get('default', ''),
                    secret=setting_dict.get('secret', False),
                    hint=setting_dict.get('hint', ''),
                )
                combined_settings_fields_dict[field.key] = field
            else:
                print(f"Warning: Invalid item in 'settings' list in {config_path}. Skipping: {setting_dict}")
    else:
        print(f"Warning: 'settings' key in {config_path} should be a list of setting definitions. Ignoring.")

    final_settings_fields = list(combined_settings_fields_dict.values())

    # --- UI/UX ---
    icon_map_config = config.get('icon_map', {})
    processed_icon_map = {} # Create a new dict for processed icons
    if not isinstance(icon_map_config, dict):
        print(f"Warning: 'icon_map' key in {config_path} should be a dictionary. Using empty map.")
    else:
        # --- Evaluate icon strings into Flet constants --- #
        for tool_name, icon_str in icon_map_config.items():
            if isinstance(icon_str, str) and icon_str.startswith("ft.Icons."):
                try:
                    # Evaluate the string like "ft.Icons.MAP" to get the constant
                    icon_value = eval(icon_str, {"ft": ft})
                    processed_icon_map[tool_name] = icon_value
                except Exception as e:
                    print(f"Warning: Could not evaluate icon string '{icon_str}' for tool '{tool_name}': {e}. Default icon will be used.")
                    # Let ToolCallDisplay handle the default icon
            else:
                 # If it's not a string starting with ft.Icons., log warning and skip
                 print(f"Warning: Invalid icon format '{icon_str}' for tool '{tool_name}'. Expected string like 'ft.Icons.ICON_NAME'. Default icon will be used.")
        # ---------------------------------------------- #

    theme_color = _parse_theme_color(config.get('theme_color'), ft.Colors.BLUE) # Default Blue

    # --- Build Agent (Placeholder/Default) ---
    # The actual Agent/SafeAgent creation is deferred to AgentApp using a default builder.
    # We pass None here; AgentApp will use its internal builder.
    build_agent_func = None

    # --- Construct AgentSpec ---
    spec = AgentSpec(
        # Basic Info
        name=agent_name,
        header_title=header_title,
        page_title=page_title,
        welcome_text=welcome_text,
        client_storage_prefix=storage_prefix,

        # Agent Config
        get_agent_instructions=get_instructions_func,
        build_agent=build_agent_func, # Defer to internal builder
        available_tools=selected_tool_functions, # Pass the discovered & selected functions
        model_name=model_name_from_config, # Pass model name from YAML

        # Agent Safety Config
        use_safe_agent=use_safe,
        safe_agent_options=safe_options,

        # MCP Config
        uses_mcp=uses_mcp,
        mcp_url_setting_key=mcp_url_key,
        mcp_token_setting_key=mcp_token_key,
        show_mcp_status_indicator=show_mcp_indicator,

        # UI/UX Config
        icon_map=processed_icon_map, # Pass the processed map
        settings_fields=final_settings_fields,
        debug_flag=debug_flag,
        page_title_suffix=page_title_suffix,
        theme_color=theme_color,

        # Optional Customizations (Not typically set via simple YAML)
        header_controls_builder=None,
        cleanup_handler=None,
    )

    return spec 