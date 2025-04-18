"""
SafeAgent implementation for OpenAI agents.

This module provides a SafeAgent class that wraps tools with approval checks,
allowing user approval before tools are executed.
"""

import json
import re
import sys
import inspect
from typing import Any, Dict, List, Optional, Set, Pattern, Callable

from agents import Agent
from agents.tool import Tool, FunctionTool

class ToolsToFinalOutputResult:
    """Container class for tool execution results."""
    def __init__(self, is_final_output=False, final_output=None):
        self.is_final_output = is_final_output
        self.final_output = final_output

class SafeAgent(Agent):
    """A version of Agent that wraps all tools with approval checks.
    
    This agent intercepts all tools returned by get_all_tools() and wraps them
    with an approval mechanism that prompts the user before allowing the tool to be used.
    When a tool is rejected, the agent can halt further processing.
    
    If skip_approvals is set to True, the approval mechanism is bypassed and all
    tools are treated as if they were pre-approved.
    """
    
    @staticmethod
    async def auto_approval_callback(tool_name: str, formatted_args: str):
        """Automatic approval callback that always approves tools.
        
        Returns:
            tuple: (approved, always_approve, error_message)
        """
        # Always return approved=True, always_approve=False, error_msg=None
        return True, False, None
    
    def __init__(self, 
                 *args, 
                 safe_tool_names: Optional[List[str]] = None,
                 safe_tool_patterns: Optional[List[str]] = None,
                 debug_mode: bool = False,
                 halt_on_rejection: bool = True,
                 approval_callback: Callable,
                 skip_approvals: bool = False,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self._safe_tool_names: Set[str] = set(safe_tool_names or [])
        self._safe_tool_patterns: List[Pattern] = [
            re.compile(pattern) for pattern in (safe_tool_patterns or [])
        ]
        self.debug_mode = debug_mode
        self.halt_on_rejection = halt_on_rejection
        self._skip_approvals = skip_approvals
        
        # Store the approval callback (required parameter)
        self._approval_callback = approval_callback
        
        # Store attributes for halt state management
        self._halt_run = False
        self._halt_reason = None
        
        # Store the original behavior if it's a callable
        self._original_tool_use_behavior = None
        if hasattr(self, 'tool_use_behavior') and callable(self.tool_use_behavior):
            self._original_tool_use_behavior = self.tool_use_behavior
            # Override with our custom behavior
            self.tool_use_behavior = self._custom_tool_use_behavior
        
        if self.debug_mode:
            print(f"SafeAgent initialized with {len(self._safe_tool_names)} safe tools and {len(self._safe_tool_patterns)} patterns", 
                  file=sys.stderr)
            print(f"Halt on rejection: {self.halt_on_rejection}", file=sys.stderr)
            print(f"Skip approvals: {self._skip_approvals}", file=sys.stderr)
    
    def is_tool_safe(self, tool: Tool) -> bool:
        """Check if a tool is considered safe and doesn't need approval."""
        # If skip_approvals is True, all tools are considered safe
        if self._skip_approvals:
            return True
            
        # Check direct name match
        if tool.name in self._safe_tool_names:
            return True
            
        # Check pattern match
        for pattern in self._safe_tool_patterns:
            if pattern.match(tool.name):
                return True
                
        return False
    
    def add_safe_tool(self, tool_name: str) -> None:
        """Add a tool name to the safe list."""
        self._safe_tool_names.add(tool_name)
        if self.debug_mode:
            print(f"Added {tool_name} to safe tools list", file=sys.stderr)
    
    def add_safe_tool_pattern(self, pattern: str) -> None:
        """Add a regex pattern for tool names that are considered safe."""
        try:
            pattern_obj = re.compile(pattern)
            self._safe_tool_patterns.append(pattern_obj)
            if self.debug_mode:
                print(f"Added pattern '{pattern}' to safe tools patterns", file=sys.stderr)
        except re.error as e:
            if self.debug_mode:
                print(f"Error adding pattern '{pattern}': {e}", file=sys.stderr)
    
    def remove_safe_tool(self, tool_name: str) -> bool:
        """Remove a tool name from the safe list."""
        if tool_name in self._safe_tool_names:
            self._safe_tool_names.remove(tool_name)
            if self.debug_mode:
                print(f"Removed {tool_name} from safe tools list", file=sys.stderr)
            return True
        return False
        
    def clear_safe_tools(self) -> None:
        """Clear all safe tool names and patterns."""
        self._safe_tool_names.clear()
        self._safe_tool_patterns.clear()
        if self.debug_mode:
            print("Cleared all safe tools and patterns", file=sys.stderr)
    
    # Custom tool_use_behavior implementation
    async def _custom_tool_use_behavior(self, ctx, tool_results):
        """Custom tool use behavior that checks for the halt flag."""
        # If the halt flag is set, return a final output to stop processing
        if self._halt_run:
            halt_message = self._halt_reason or "Run halted after tool rejection"
            if self.debug_mode:
                print(f"Halting run: {halt_message}", file=sys.stderr)
            # Return a final output with empty message to immediately end the run
            # without letting the LLM generate a response
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=""
            )

        # Otherwise, delegate to the original behavior
        if self._original_tool_use_behavior and callable(self._original_tool_use_behavior):
            if inspect.iscoroutinefunction(self._original_tool_use_behavior):
                return await self._original_tool_use_behavior(ctx, tool_results)
            else:
                return self._original_tool_use_behavior(ctx, tool_results)

        # Default behavior if no original was set
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)
    
    def wrap_tool_with_approval(self, tool: Tool) -> Tool:
        """Wrap a tool with approval checks."""
        if self.is_tool_safe(tool):
            if self.debug_mode:
                print(f"Tool {tool.name} is safe, not wrapping", file=sys.stderr)
            return tool
            
        # We don't want to wrap a tool twice
        if hasattr(tool, "_approval_wrapped") and tool._approval_wrapped:
            return tool
            
        if not isinstance(tool, FunctionTool):
            # Not a function tool, can't wrap easily
            if self.debug_mode:
                print(f"Tool {tool.name} is not a FunctionTool, can't wrap", file=sys.stderr)
            return tool
        
        # For FunctionTool, we need to wrap the on_invoke_tool method
        original_on_invoke = tool.on_invoke_tool
        
        async def approved_on_invoke(context, params_json):
            # Format arguments for display
            try:
                args_dict = json.loads(params_json) if params_json else {}
                formatted_args = ", ".join([f"{k}: {repr(v)}" for k, v in args_dict.items()])
            except:
                formatted_args = params_json
                
            # Get approval using the callback
            approved, always_approve, error_msg = await self._approval_callback(tool.name, formatted_args)
            
            if approved:
                # If set to always approve, add to safe tools
                if always_approve:
                    self.add_safe_tool(tool.name)
                
                # Run the original invocation
                return await original_on_invoke(context, params_json)
            else:
                # Tool was rejected
                
                # Set the halt flag if configured to halt on rejection
                if self.halt_on_rejection:
                    # Store halt state in the agent instance itself
                    self._halt_run = True
                    self._halt_reason = f"{error_msg or 'User rejected'} the {tool.name} tool"
                    if self.debug_mode:
                        print(f"Setting halt flag due to rejection of {tool.name}", file=sys.stderr)
                
                # Return a structured rejection response the agent can understand
                return json.dumps({
                    "error": "TOOL_REJECTED",
                    "message": error_msg or "User rejected tool call",
                    "tool": tool.name
                })
        
        # Create a copy of the tool with our wrapper
        # Create a deep copy of the original params JSON schema
        params_schema = dict(tool.params_json_schema)  # Make a copy
        
        # Create the wrapped tool while preserving the original schema properties
        wrapped_tool = FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=params_schema,
            on_invoke_tool=approved_on_invoke,
            strict_json_schema=tool.strict_json_schema  # Preserve the original strict_json_schema setting
        )
        
        # Mark it as wrapped
        wrapped_tool._approval_wrapped = True
        
        if self.debug_mode:
            print(f"Wrapped tool {tool.name} with approval checks", file=sys.stderr)
            
        return wrapped_tool
    
    async def get_all_tools(self) -> List[Tool]:
        """Get all tools, wrapping them with approval checks as needed."""
        tools = await super().get_all_tools()
        return [self.wrap_tool_with_approval(tool) for tool in tools]


