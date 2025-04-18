"""
SafeAgent implementation for OpenAI agents.

This module provides a SafeAgent class that wraps tools with approval checks,
allowing user approval before tools are executed.
"""

import json
import re
import sys
import inspect
import asyncio # Added asyncio import
from typing import Any, Dict, List, Optional, Set, Pattern, Callable, Tuple, Awaitable # Added Tuple import

from agents import Agent, ItemHelpers # Added ItemHelpers import
from agents.tool import Tool, FunctionTool
from agents.model_settings import ModelSettings # Added ModelSettings import

# Type hinting for the approval callback result
ApprovalResult = Tuple[bool, bool, Optional[str]]
ApprovalCallback = Callable[[str, str], Awaitable[ApprovalResult]]

class ToolsToFinalOutputResult:
    """Container class for tool execution results."""
    def __init__(self, is_final_output=False, final_output=None):
        self.is_final_output = is_final_output
        self.final_output = final_output

class SafeAgent(Agent):
    """A version of Agent that wraps tools with approval checks.

    This agent intercepts tools and wraps them with an approval mechanism
    that prompts the user (via a callback) before allowing the tool to be used.
    When a tool is rejected, the agent can halt further processing.

    If skip_approvals is set to True, the approval mechanism is bypassed and all
    tools are treated as if they were pre-approved.
    """

    @staticmethod
    async def auto_approval_callback(tool_name: str, formatted_args: str) -> ApprovalResult:
        """Automatic approval callback that always approves tools."""
        return True, False, None # approved=True, always_approve=False, error_msg=None

    def __init__(self,
                 name: str,
                 tools: List[Tool],
                 instructions: str,
                 model: str,
                 model_settings: Optional[ModelSettings] = None,
                 mcp_servers: Optional[List[Any]] = None, # Keep MCP servers if needed
                 # SafeAgent specific parameters
                 safe_tool_names: Optional[List[str]] = None,
                 safe_tool_patterns: Optional[List[str]] = None,
                 debug_mode: bool = False,
                 halt_on_rejection: bool = True,
                 approval_callback: Optional[ApprovalCallback] = None, # Made optional, but needed if not skipping
                 skip_approvals: bool = False,
                 **kwargs): # Added **kwargs to catch extra args from Agent init

        # Initialize the base Agent class
        super().__init__(
            name=name,
            tools=tools, # Will be wrapped later
            instructions=instructions,
            model=model,
            model_settings=model_settings,
            mcp_servers=mcp_servers,
            **kwargs
        )

        # Initialize SafeAgent specific properties
        self._safe_tool_names: Set[str] = set(safe_tool_names or [])
        self._safe_tool_patterns: List[Pattern] = [
            re.compile(pattern) for pattern in (safe_tool_patterns or [])
        ]
        self.debug_mode = debug_mode
        self.halt_on_rejection = halt_on_rejection
        self._skip_approvals = skip_approvals

        # Store the approval callback - raise error if needed but not provided
        if not skip_approvals and approval_callback is None:
            raise ValueError("approval_callback is required when skip_approvals is False")
        self._approval_callback = approval_callback or self.auto_approval_callback

        # Store attributes for halt state management
        self._halt_run = False
        self._halt_reason = None

        # Custom tool use behavior setup (optional, depends if base Agent has it)
        self._original_tool_use_behavior = None
        if hasattr(self, 'tool_use_behavior') and callable(self.tool_use_behavior):
            self._original_tool_use_behavior = self.tool_use_behavior
            self.tool_use_behavior = self._custom_tool_use_behavior

        if self.debug_mode:
            print(f"[SafeAgent] Initialized: skip={self._skip_approvals}, halt={self.halt_on_rejection}, "
                  f"safe_names={len(self._safe_tool_names)}, safe_patterns={len(self._safe_tool_patterns)}", file=sys.stderr)

    def is_tool_safe(self, tool: Tool) -> bool:
        """Check if a tool is considered safe and doesn't need approval."""
        if self._skip_approvals:
            return True
        if tool.name in self._safe_tool_names:
            return True
        for pattern in self._safe_tool_patterns:
            if pattern.match(tool.name):
                return True
        return False

    def add_safe_tool(self, tool_name: str) -> None:
        """Add a tool name to the safe list for the current session."""
        self._safe_tool_names.add(tool_name)
        if self.debug_mode:
            print(f"[SafeAgent] Added {tool_name} to safe tools list (session)", file=sys.stderr)

    def add_safe_tool_pattern(self, pattern: str) -> None:
        """Add a regex pattern for tool names that are considered safe."""
        try:
            pattern_obj = re.compile(pattern)
            self._safe_tool_patterns.append(pattern_obj)
            if self.debug_mode:
                print(f"[SafeAgent] Added pattern '{pattern}' to safe tools patterns", file=sys.stderr)
        except re.error as e:
            if self.debug_mode:
                print(f"[SafeAgent] Error adding pattern '{pattern}': {e}", file=sys.stderr)

    def remove_safe_tool(self, tool_name: str) -> bool:
        """Remove a tool name from the safe list."""
        if tool_name in self._safe_tool_names:
            self._safe_tool_names.remove(tool_name)
            if self.debug_mode:
                print(f"[SafeAgent] Removed {tool_name} from safe tools list", file=sys.stderr)
            return True
        return False

    def clear_safe_tools(self) -> None:
        """Clear all safe tool names and patterns."""
        self._safe_tool_names.clear()
        self._safe_tool_patterns.clear()
        if self.debug_mode:
            print("[SafeAgent] Cleared all safe tools and patterns", file=sys.stderr)

    async def _custom_tool_use_behavior(self, ctx, tool_results):
        """Custom tool use behavior that checks for the halt flag."""
        if self._halt_run:
            halt_message = self._halt_reason or "Run halted after tool rejection"
            if self.debug_mode:
                print(f"[SafeAgent] Halting run: {halt_message}", file=sys.stderr)
            # Return final output with empty message to stop processing
            return ToolsToFinalOutputResult(is_final_output=True, final_output="")

        # Delegate to original if it exists
        if self._original_tool_use_behavior and callable(self._original_tool_use_behavior):
            if inspect.iscoroutinefunction(self._original_tool_use_behavior):
                return await self._original_tool_use_behavior(ctx, tool_results)
            else:
                return self._original_tool_use_behavior(ctx, tool_results)

        # Default behavior: continue processing
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    def wrap_tool_with_approval(self, tool: Tool) -> Tool:
        """Wrap a FunctionTool with approval checks if it's not safe."""
        if not isinstance(tool, FunctionTool):
            if self.debug_mode:
                print(f"[SafeAgent] Tool {tool.name} is not a FunctionTool, skipping wrap", file=sys.stderr)
            return tool

        if self.is_tool_safe(tool):
            if self.debug_mode:
                print(f"[SafeAgent] Tool {tool.name} is safe, not wrapping", file=sys.stderr)
            return tool

        if hasattr(tool, "_approval_wrapped") and tool._approval_wrapped:
            return tool # Already wrapped

        original_on_invoke = tool.on_invoke_tool

        async def approved_on_invoke(context, params_json: str):
            # Format arguments for display
            try:
                args_dict = json.loads(params_json) if params_json else {}
                # Limit display length of args for clarity
                formatted_args = ", ".join([f"{k}: {repr(v)[:100]}{'...' if len(repr(v)) > 100 else ''}" for k, v in args_dict.items()])
            except Exception:
                formatted_args = params_json[:200] + ('...' if len(params_json) > 200 else '')

            # Get approval using the callback
            approved, always_approve, error_msg = await self._approval_callback(tool.name, formatted_args)

            if approved:
                if always_approve:
                    self.add_safe_tool(tool.name) # Add to safe list for this session
                # Run the original invocation
                return await original_on_invoke(context, params_json)
            else:
                # Tool was rejected
                rejection_reason = error_msg or "User rejected tool call"
                if self.halt_on_rejection:
                    self._halt_run = True # Set halt flag on the agent instance
                    self._halt_reason = f"{rejection_reason} ({tool.name})"
                    if self.debug_mode:
                        print(f"[SafeAgent] Setting halt flag due to rejection of {tool.name}", file=sys.stderr)

                # Return structured rejection response
                return json.dumps({
                    "error": "TOOL_REJECTED",
                    "message": rejection_reason,
                    "tool": tool.name
                })

        # Create a new FunctionTool instance with the wrapped invoke method
        # Ensure we preserve all relevant properties from the original tool
        wrapped_tool = FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=dict(tool.params_json_schema), # Use a copy
            on_invoke_tool=approved_on_invoke,
            strict_json_schema=tool.strict_json_schema
        )
        wrapped_tool._approval_wrapped = True # Mark as wrapped

        if self.debug_mode:
            print(f"[SafeAgent] Wrapped tool {tool.name} with approval checks", file=sys.stderr)

        return wrapped_tool

    # Override get_all_tools to ensure wrapping happens dynamically
    async def get_all_tools(self) -> List[Tool]:
        """Get all tools from the base class and wrap them with approval checks."""
        # Reset halt flag at the beginning of each run potentially involving tool fetching
        self._halt_run = False
        self._halt_reason = None

        # Get tools from base Agent (could come from init or MCP servers)
        tools = await super().get_all_tools()

        # Wrap necessary tools
        wrapped_tools = [self.wrap_tool_with_approval(tool) for tool in tools]
        return wrapped_tools

    # Reset halt state when starting a new run, e.g., called by Runner
    def reset_run_state(self):
        """Resets any state related to a single run, like the halt flag."""
        self._halt_run = False
        self._halt_reason = None
        if self.debug_mode:
            print("[SafeAgent] Resetting run state (halt flags)", file=sys.stderr)

# Optional: Add helper function or adapt Runner if reset_run_state needs explicit call
# For example, Runner.run_streamed could check if agent has reset_run_state and call it.
# Or the AgentApp could call it before Runner.run_streamed.
# Let's assume AgentApp will handle calling reset if needed for now. 