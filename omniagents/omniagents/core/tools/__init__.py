"""Tools module for OmniAgents."""

from .discovery import (
    function_tool,
    rich_function_tool,
    RichToolOutput,
    discover_functions_in_dir,
    approval_metadata,
    get_approval_metadata,
)

__all__ = [
    "function_tool",
    "rich_function_tool", 
    "RichToolOutput",
    "discover_functions_in_dir",
    "approval_metadata",
    "get_approval_metadata",
]