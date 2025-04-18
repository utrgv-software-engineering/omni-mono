# This file makes Python treat the directory omniagents_core as a package. 

# Expose key classes for easier import
from .app_base import AgentApp
from .specs import AgentSpec, SafeAgentOptions
from .settings import SettingField
from .config_loader import load_agent_spec_from_yaml
# --- Expose the wrapped function_tool decorator --- #
from .tooling import function_tool
# Optionally expose other useful components like SafeAgentOptions etc.
# from .specs import SafeAgentOptions # Example if exposing SafeAgentOptions 