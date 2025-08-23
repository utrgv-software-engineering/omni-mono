import yaml
# import flet as ft # For theme colors if specified as strings - moved to Flet backend
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Set

# Core imports
from omniagents.core.agents.specs import AgentSpec, AgentLike, SafeAgentOptions, HandoffConfig
from omniagents.core.agents.specs import SettingField
from omniagents.core.tools.discovery import discover_functions_in_dir
from omniagents.core.guardrails.discovery import discover_guardrails_in_dir

DEFAULT_SETTINGS_FIELDS = [
    SettingField(
        key="openai_api_base",
        label="OpenAI API Base URL",
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
    """Parses a theme color string."""
    if not color_str:
        return default
    # For now, just return the color string as-is (hex codes, color names)
    # Flet-specific color parsing should be handled by the Flet backend
    if color_str.startswith("ft.Colors."):
        print(f"Warning: Flet color constants like '{color_str}' are no longer supported in YAML. Please use hex codes (e.g., '#2196F3') instead.")
        return default
    return color_str

def _parse_inline_agent_spec(config: Dict[str, Any], base_dir: Path, parent_loading_stack: Set[Path]) -> 'AgentSpec':
    """Parse an inline agent configuration into an AgentSpec.
    
    This is a simplified version of load_agent_spec_from_yaml for inline agents.
    """
    # Get basic agent info
    agent_name = config.get('name', 'Inline Agent')
    model_name = config.get('model', 'gpt-4o')
    
    # Handle instructions
    instructions_func = _load_instructions(config, base_dir)
    
    # Parse tools (inline agents reference tools by name only)
    tool_names = config.get('tools', [])
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
    for tool_name in tool_names:
        if tool_name in all_discovered_functions:
            selected_tool_functions.append(all_discovered_functions[tool_name])
        else:
            print(f"Warning: Tool '{tool_name}' not found for inline agent '{agent_name}'")
    
    # Parse model settings if provided
    model_settings_config = config.get('model_settings', {})
    if not isinstance(model_settings_config, dict):
        model_settings_config = {}
    
    # Safety configuration
    use_safe = config.get('use_safe_agent', True)
    safe_options_config = config.get('safe_agent_options', {})
    safe_options = SafeAgentOptions(
        skip_approvals=safe_options_config.get('skip_approvals', False),
        halt_on_rejection=safe_options_config.get('halt_on_rejection', True),
        safe_tool_names=safe_options_config.get('safe_tool_names', []),
        safe_tool_patterns=safe_options_config.get('safe_tool_patterns', []),
    )
    
    # Create minimal AgentSpec for inline agent
    return AgentSpec(
        # Basic Info
        name=agent_name,
        header_title=agent_name,
        page_title=agent_name,
        welcome_text=config.get('welcome_text', f"Hello from {agent_name}"),
        client_storage_prefix=agent_name.lower().replace(' ', '_') + '.',
        
        # Agent Config
        get_agent_instructions=instructions_func,
        build_agent=None,
        available_tools=selected_tool_functions,
        model_name=model_name,
        max_turns=config.get('max_turns', 20),
        handoff_description=config.get('handoff_description'),
        reset_tool_choice=config.get('reset_tool_choice', True),
        tool_use_behavior=config.get('tool_use_behavior', 'run_llm_again'),
        model_settings=model_settings_config if model_settings_config else None,
        output_type=None,  # Simplified for inline agents
        prompt=None,  # Simplified for inline agents
        
        # Nested handoffs for inline agents (if provided)
        handoff_configs=[],  # Will be populated if inline agent has handoffs
        
        # Guardrails (simplified for inline agents)
        input_guardrails=[],
        output_guardrails=[],
        
        # Safety
        use_safe_agent=use_safe,
        safe_agent_options=safe_options,
        
        # MCP (not supported for inline agents)
        uses_mcp=False,
        
        # UI/UX (minimal for inline agents)
        settings_fields=[],
        debug_flag=False,
        theme_color="#2196F3",
        layout="classic",
    )

def load_agent_spec_from_yaml(config_path: str, _loading_stack: Optional[Set[Path]] = None) -> AgentSpec:
    """
    Loads agent configuration from a YAML file and builds an AgentSpec.

    Args:
        config_path: Path to the agent.yaml configuration file.
        _loading_stack: Internal parameter for circular reference detection.

    Returns:
        An AgentSpec instance configured according to the YAML file.

    Raises:
        FileNotFoundError: If the config_path does not exist.
        ValueError: If the configuration is invalid or circular references detected.
    """
    config_file = Path(config_path).resolve()
    
    # Initialize loading stack for circular reference detection
    if _loading_stack is None:
        _loading_stack = set()
    
    # Check for circular references
    if config_file in _loading_stack:
        raise ValueError(f"Circular reference detected: {config_file} is already being loaded")
    
    # Add current file to loading stack
    _loading_stack = _loading_stack.copy()
    _loading_stack.add(config_file)
    
    if not config_file.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file {config_file.resolve()}: {e}")
    except Exception as e:
        raise ValueError(f"Error reading file {config_file.resolve()}: {e}")

    if not isinstance(config, dict):
        raise ValueError(f"Invalid YAML format in {config_file.resolve()}. Expected a dictionary (key-value pairs).")

    # --- Remote Agent preliminary ---
    remote_ws_url: str | None = None
    remote_token_env: str | None = None
    if isinstance(config.get('remote_agent'), dict):
        remote_cfg_tmp = config['remote_agent']
        remote_ws_url = remote_cfg_tmp.get('ws_url')
        remote_token_env = remote_cfg_tmp.get('auth_token_env')

    # --- Extract and Derive Basic Info ---
    agent_name = config.get('name', 'Unnamed Agent')
    header_title = config.get('header_title', agent_name)
    page_title = config.get('page_title', agent_name)
    max_turns_cfg = config.get('max_turns', 20)
    # Derive default storage prefix if not provided
    default_storage_prefix = agent_name.lower().replace(' ', '_').replace('-','_') + '.'
    storage_prefix = config.get('client_storage_prefix', default_storage_prefix)
    welcome_text = config.get('welcome_text', f"Hello! I'm {agent_name}. How can I help you?")
    debug_flag = config.get('debug', False)
    page_title_suffix = config.get('page_title_suffix', " (Debug Mode)" if debug_flag else "")
    model_name_from_config = config.get('model') # Get model name from YAML
    
    # --- New Agent Parameters ---
    handoff_description = config.get('handoff_description')  # Optional description for handoffs
    reset_tool_choice = config.get('reset_tool_choice', True)  # Default to True
    tool_use_behavior = config.get('tool_use_behavior', 'run_llm_again')  # Default behavior
    
    # Validate tool_use_behavior
    valid_behaviors = ['run_llm_again', 'stop_on_first_tool']
    if tool_use_behavior not in valid_behaviors:
        print(f"Warning: Invalid tool_use_behavior '{tool_use_behavior}'. Using default 'run_llm_again'.")
        tool_use_behavior = 'run_llm_again'
    
    # --- Output Type ---
    output_type_config = config.get('output_type')  # Optional output type specification
    output_type = None
    if output_type_config:
        # Map string type names to Python types
        type_mapping = {
            'str': str,
            'string': str,
            'dict': dict,
            'dictionary': dict,
            'list': list,
            'array': list,
            'int': int,
            'integer': int,
            'float': float,
            'number': float,
            'bool': bool,
            'boolean': bool,
            'any': None,  # None means no type constraint
        }
        if isinstance(output_type_config, str):
            output_type = type_mapping.get(output_type_config.lower())
            if output_type_config.lower() not in type_mapping:
                print(f"Warning: Unknown output_type '{output_type_config}'. Supported types: {', '.join(type_mapping.keys())}. Ignoring.")
        else:
            print(f"Warning: 'output_type' should be a string. Got {type(output_type_config).__name__}. Ignoring.")
    
    # --- Prompt Configuration ---
    prompt_config = config.get('prompt')  # Optional prompt configuration
    prompt = None
    if prompt_config:
        if isinstance(prompt_config, dict):
            # Validate required 'id' field
            if 'id' not in prompt_config:
                print(f"Warning: 'prompt' configuration must have an 'id' field. Ignoring.")
            else:
                # Create a Prompt TypedDict-compatible dictionary
                prompt = {
                    'id': str(prompt_config['id']),
                }
                # Add optional fields if present
                if 'version' in prompt_config:
                    prompt['version'] = str(prompt_config['version'])
                if 'variables' in prompt_config:
                    if isinstance(prompt_config['variables'], dict):
                        prompt['variables'] = prompt_config['variables']
                    else:
                        print(f"Warning: 'prompt.variables' should be a dictionary. Ignoring variables field.")
        else:
            print(f"Warning: 'prompt' should be a dictionary with 'id', 'version' (optional), and 'variables' (optional). Got {type(prompt_config).__name__}. Ignoring.")
    
    # --- Model Settings ---
    model_settings_config = config.get('model_settings', {})
    if not isinstance(model_settings_config, dict):
        print(f"Warning: 'model_settings' should be a dictionary. Ignoring.")
        model_settings_config = {}
    
    # Validate known ModelSettings parameters
    valid_model_settings = {
        'temperature': (float, int),
        'top_p': (float, int),
        'frequency_penalty': (float, int),
        'presence_penalty': (float, int),
        'tool_choice': (str, type(None)),  # "auto", "required", "none", or specific tool
        'parallel_tool_calls': bool,
        'truncation': str,  # "auto" or "disabled"
        'max_tokens': int,
        'reasoning': dict,  # Configuration for reasoning models
        'verbosity': str,  # "low", "medium", or "high"
        'metadata': dict,  # Metadata to include with model calls
        'store': bool,  # Whether to store responses
        'include_usage': bool,  # Include usage chunks
        'response_include': list,  # Additional output data
        'top_logprobs': int,  # Number of top tokens for logprobs
        'extra_query': dict,  # Additional query fields
        'extra_body': dict,  # Additional body fields
        'extra_headers': dict,  # Additional headers
        'extra_args': dict,  # Arbitrary keyword arguments
    }
    
    # Validate and warn about invalid settings
    keys_to_remove = []
    for key, value in model_settings_config.items():
        if key in valid_model_settings:
            expected_types = valid_model_settings[key]
            if not isinstance(expected_types, tuple):
                expected_types = (expected_types,)
            if not isinstance(value, expected_types):
                type_names = '/'.join(t.__name__ for t in expected_types)
                print(f"Warning: 'model_settings.{key}' should be {type_names}, got {type(value).__name__}. Ignoring.")
                keys_to_remove.append(key)
        else:
            # Don't warn about unknown settings - they might be provider-specific
            pass
    
    # Remove invalid keys after iteration
    for key in keys_to_remove:
        del model_settings_config[key]
    
    # Validate specific enum values
    if 'truncation' in model_settings_config and model_settings_config['truncation'] not in ['auto', 'disabled']:
        print(f"Warning: 'model_settings.truncation' should be 'auto' or 'disabled', got '{model_settings_config['truncation']}'. Ignoring.")
        del model_settings_config['truncation']
    
    if 'verbosity' in model_settings_config and model_settings_config['verbosity'] not in ['low', 'medium', 'high']:
        print(f"Warning: 'model_settings.verbosity' should be 'low', 'medium', or 'high', got '{model_settings_config['verbosity']}'. Ignoring.")
        del model_settings_config['verbosity']

    # --- Agent Instructions ---
    base_dir = config_file.parent
    if remote_ws_url:
        # When using a remote agent we don't need local instructions
        get_instructions_func = lambda: ""
    else:
        get_instructions_func = _load_instructions(config, base_dir)

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

    if not remote_ws_url:
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

    # --- Guardrail Discovery and Selection ---
    # Check the 'guardrails' subdirectory for guardrail definitions
    input_guardrails_list = []
    output_guardrails_list = []
    
    if not remote_ws_url:  # Only discover guardrails for local agents
        guardrails_dir_path = (base_dir / "guardrails").resolve()
        if guardrails_dir_path.is_dir():
            # Discover guardrails (only verbose in debug mode)
            discovered_input, discovered_output = discover_guardrails_in_dir(str(guardrails_dir_path), debug_flag)
            if debug_flag:
                print(f"Discovered guardrails from 'guardrails' subdirectory: {guardrails_dir_path}")
                print(f"  Input guardrails: {list(discovered_input.keys())}")
                print(f"  Output guardrails: {list(discovered_output.keys())}")
        else:
            discovered_input = {}
            discovered_output = {}
            if debug_flag:
                print(f"No 'guardrails' subdirectory found at {guardrails_dir_path}")
        
        # Process input guardrails from config
        input_guardrail_names = config.get('input_guardrails', [])
        if not isinstance(input_guardrail_names, list):
            print(f"Warning: 'input_guardrails' key in {config_path} should be a list of guardrail names. Ignoring.")
            input_guardrail_names = []
        
        missing_input_guardrails = []
        for guardrail_name in input_guardrail_names:
            if guardrail_name in discovered_input:
                input_guardrails_list.append(discovered_input[guardrail_name])
            else:
                missing_input_guardrails.append(guardrail_name)
        
        if missing_input_guardrails:
            print(f"Warning: The following input guardrails listed in '{config_path}' were not found: {', '.join(missing_input_guardrails)}")
        
        # Process output guardrails from config
        output_guardrail_names = config.get('output_guardrails', [])
        if not isinstance(output_guardrail_names, list):
            print(f"Warning: 'output_guardrails' key in {config_path} should be a list of guardrail names. Ignoring.")
            output_guardrail_names = []
        
        missing_output_guardrails = []
        for guardrail_name in output_guardrail_names:
            if guardrail_name in discovered_output:
                output_guardrails_list.append(discovered_output[guardrail_name])
            else:
                missing_output_guardrails.append(guardrail_name)
        
        if missing_output_guardrails:
            print(f"Warning: The following output guardrails listed in '{config_path}' were not found: {', '.join(missing_output_guardrails)}")

    # --- Handoffs Configuration ---
    handoff_configs_list = []
    if not remote_ws_url:  # Only process handoffs for local agents
        handoffs_config = config.get('handoffs', [])
        if not isinstance(handoffs_config, list):
            print(f"Warning: 'handoffs' key in {config_path} should be a list. Ignoring.")
            handoffs_config = []
        
        for handoff_item in handoffs_config:
            if isinstance(handoff_item, dict):
                # Check if it's an external YAML reference
                if 'yaml' in handoff_item:
                    yaml_path = handoff_item.get('yaml')
                    
                    # Resolve path relative to current YAML file
                    handoff_yaml_path = (base_dir / yaml_path).resolve()
                    if not handoff_yaml_path.is_file():
                        try:
                            display_path = handoff_yaml_path.relative_to(Path.cwd())
                        except ValueError:
                            display_path = handoff_yaml_path
                        print(f"Warning: Handoff agent YAML file not found: {display_path}. Skipping.")
                        continue
                    
                    # Create HandoffConfig for external agent
                    # The actual YAML will be loaded during agent building
                    handoff_config = HandoffConfig(
                        yaml_path=str(handoff_yaml_path),
                        tool_name_override=handoff_item.get('tool_name_override'),
                        tool_description_override=handoff_item.get('tool_description_override')
                    )
                    handoff_configs_list.append(handoff_config)
                    
                # Check if it's an inline agent definition
                elif 'inline' in handoff_item:
                    inline_config = handoff_item.get('inline')
                    if not isinstance(inline_config, dict):
                        print(f"Warning: 'inline' value in handoff should be a dictionary. Skipping.")
                        continue
                    
                    try:
                        # Parse the inline agent configuration
                        inline_spec = _parse_inline_agent_spec(inline_config, base_dir, _loading_stack)
                        
                        # Create HandoffConfig for inline agent
                        handoff_config = HandoffConfig(
                            inline_spec=inline_spec,
                            tool_name_override=handoff_item.get('tool_name_override'),
                            tool_description_override=handoff_item.get('tool_description_override')
                        )
                        handoff_configs_list.append(handoff_config)
                    except Exception as e:
                        print(f"Warning: Failed to parse inline agent: {e}. Skipping.")
                        continue
                        
                else:
                    print(f"Warning: Handoff item must have either 'yaml' or 'inline' field in {config_path}. Skipping.")
            else:
                print(f"Warning: Invalid handoff item in {config_path}. Expected dict. Skipping.")

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
    
    # Extended MCP config
    mcp_config_from_yaml = config.get('mcp_config', {})
    if not isinstance(mcp_config_from_yaml, dict):
        print(f"Warning: 'mcp_config' should be a dictionary. Using empty dict.")
        mcp_config_from_yaml = {}

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
        # Icon strings should now be simple string names, not Flet constants
        for tool_name, icon_str in icon_map_config.items():
            if isinstance(icon_str, str):
                if icon_str.startswith("ft.Icons."):
                    print(f"Warning: Flet icon constants like '{icon_str}' are no longer supported in YAML. Please use icon names (e.g., 'map', 'search') instead.")
                else:
                    # Store the icon name as a string
                    processed_icon_map[tool_name] = icon_str
            else:
                print(f"Warning: Icon for tool '{tool_name}' should be a string. Skipping.")
        # ---------------------------------------------- #

    theme_color = _parse_theme_color(config.get('theme_color'), "#2196F3")  # Default Material Blue

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
        available_tools=selected_tool_functions if not remote_ws_url else [],
        model_name=model_name_from_config if not remote_ws_url else None,
        max_turns=max_turns_cfg,
        handoff_description=handoff_description,
        reset_tool_choice=reset_tool_choice,
        tool_use_behavior=tool_use_behavior,
        model_settings=model_settings_config if model_settings_config else None,
        mcp_config=mcp_config_from_yaml if mcp_config_from_yaml else None,
        output_type=output_type,
        prompt=prompt,
        
        # Handoffs
        handoff_configs=handoff_configs_list,
        
        # Guardrails
        input_guardrails=input_guardrails_list,
        output_guardrails=output_guardrails_list,

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
        layout=config.get('layout', 'classic'),

        # Remote agent metadata
        remote_ws_url=remote_ws_url,
        remote_auth_token_key=remote_token_env,

        # Optional Customizations (Not typically set via simple YAML)
        header_controls_builder=None,
        cleanup_handler=None,
    )

    return spec 