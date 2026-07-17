"""Tools module for OmniAgents."""

from .discovery import (
    function_tool,
    rich_function_tool,
    RichToolOutput,
    discover_functions_in_dir,
    approval_metadata,
    get_approval_metadata,
)
from .client_function import client_function_tool
from .run_context import (
    DEFAULT_TASK_SCOPE,
    ctx_value,
    session_id_from_ctx,
    task_scope_from_ctx,
)
from .slash_commands import (
    SlashCommandRegistry,
    default_slash_registry,
    register_slash_command,
)

__all__ = [
    "function_tool",
    "rich_function_tool",
    "RichToolOutput",
    "discover_functions_in_dir",
    "approval_metadata",
    "get_approval_metadata",
    "client_function_tool",
    "SlashCommandRegistry",
    "default_slash_registry",
    "register_slash_command",
    "DEFAULT_TASK_SCOPE",
    "ctx_value",
    "session_id_from_ctx",
    "task_scope_from_ctx",
]
