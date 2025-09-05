"""Tools module for OmniAgents."""

from .discovery import (
    function_tool,
    rich_function_tool,
    RichToolOutput,
    discover_functions_in_dir,
)

__all__ = [
    "function_tool",
    "rich_function_tool", 
    "RichToolOutput",
    "discover_functions_in_dir",
]