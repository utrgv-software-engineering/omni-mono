# This file makes Python treat the directory omniagents_core as a package. 

# Expose key classes for easier import
from .agents.specs import AgentSpec, SafeAgentOptions, SettingField
from .config.loader import load_agent_spec_from_yaml
# --- Expose the wrapped function_tool decorator --- #
from .tools.discovery import function_tool
from .server_functions.discovery import (
    server_function,
    discover_server_functions_in_dir,
    build_server_functions_registrar,
    make_server_functions_registrar_from_dir,
    register_server_functions_from_dir,
)
from .tools.artifacts import display_artifact, set_artifact_callback, emit_artifact
# Optionally expose other useful components like SafeAgentOptions etc.
# from .agents.specs import SafeAgentOptions # Example if exposing SafeAgentOptions 
