"""
SafeAgent implementation for OpenAI agents.

This module provides a SafeAgent class that wraps tools with approval checks,
allowing user approval before tools are executed.
"""

import json
import re
import sys
import os
import inspect
import asyncio  # Added asyncio import
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Pattern,
    Callable,
    Tuple,
    Awaitable,
)  # Added Tuple import
import contextvars

from agents import Agent, ItemHelpers  # Added ItemHelpers import
from agents.tool import Tool, FunctionTool
from agents.model_settings import ModelSettings  # Added ModelSettings import
from omniagents.core.debug import Debug

# Type hinting for the approval callback result
ApprovalResult = Tuple[bool, bool, Optional[str]]
ApprovalCallback = Callable[
    [str, str, Optional[Dict[str, Any]]], Awaitable[ApprovalResult]
]

# Context variables for global safe tool settings
_global_safe_tool_names: contextvars.ContextVar[Set[str]] = contextvars.ContextVar(
    "global_safe_tool_names", default=set()
)

_global_safe_tool_patterns: contextvars.ContextVar[List[Pattern]] = (
    contextvars.ContextVar("global_safe_tool_patterns", default=[])
)


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
    async def auto_approval_callback(
        tool_name: str, formatted_args: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ApprovalResult:
        """Automatic approval callback that always approves tools."""
        return True, False, None  # approved=True, always_approve=False, error_msg=None

    @classmethod
    def set_global_safe_tools(
        cls,
        tool_names: Optional[List[str]] = None,
        tool_patterns: Optional[List[str]] = None,
    ) -> None:
        """Set global safe tool settings that apply to all SafeAgent instances.

        Args:
            tool_names: List of exact tool names that are considered safe
            tool_patterns: List of regex patterns for tools that are considered safe
        """
        if tool_names:
            _global_safe_tool_names.set(set(tool_names))
        if tool_patterns:
            _global_safe_tool_patterns.set([re.compile(p) for p in tool_patterns])

    @classmethod
    def add_global_safe_tool(cls, tool_name: str) -> None:
        """Add a tool to the global safe tools list."""
        current = _global_safe_tool_names.get()
        updated = current.copy()
        updated.add(tool_name)
        _global_safe_tool_names.set(updated)

    def __init__(
        self,
        name: str,
        tools: List[Tool],
        instructions: str,
        model: str,
        model_settings: Optional[ModelSettings] = None,
        mcp_servers: Optional[List[Any]] = None,  # Keep MCP servers if needed
        # SafeAgent specific parameters
        safe_tool_names: Optional[List[str]] = None,
        safe_tool_patterns: Optional[List[str]] = None,
        halt_on_rejection: bool = True,
        approval_callback: Optional[
            ApprovalCallback
        ] = None,  # Made optional, but needed if not skipping
        skip_approvals: bool = False,
        **kwargs,
    ):  # Added **kwargs to catch extra args from Agent init

        # Import ModelSettings here to avoid circular imports
        from agents.model_settings import ModelSettings as AgentModelSettings

        # Ensure model_settings is not None for base Agent
        if model_settings is None:
            model_settings = AgentModelSettings()

        # Extract handoffs before passing to parent, we'll wrap them
        handoffs = kwargs.pop("handoffs", [])

        # Initialize the base Agent class
        super().__init__(
            name=name,
            tools=tools,  # Will be wrapped later
            instructions=instructions,
            model=model,
            model_settings=model_settings,
            mcp_servers=mcp_servers if mcp_servers is not None else [],
            handoffs=[],  # Will be set after wrapping
            **kwargs,
        )

        # Initialize SafeAgent specific properties
        self._safe_tool_names: Set[str] = set(safe_tool_names or [])
        self._safe_tool_patterns: List[Pattern] = [
            re.compile(pattern) for pattern in (safe_tool_patterns or [])
        ]
        self.halt_on_rejection = halt_on_rejection
        self._skip_approvals = skip_approvals

        # Store the approval callback - allow None for deferred binding
        # Callback will be set later by Runner/backend if needed
        self._approval_callback = approval_callback
        self._needs_callback = not skip_approvals and approval_callback is None

        # Store attributes for halt state management
        self._halt_run = False
        self._halt_reason = None

        # Optional audit callback: async (event_type, tool_name, details, reviewer_id=None) -> None
        # Set by the builder/service layer when audit is enabled.
        self._audit_emit = None
        # Identity of the human reviewing tool approvals (from Session.user_id)
        self._reviewer_id: str | None = None

        # Wrap handoffs to automatically wrap returned agents in SafeAgent
        self.handoffs = self._wrap_handoffs(handoffs)

        # Custom tool use behavior setup
        # Store the original tool_use_behavior (could be string, dict, or function)
        self._original_tool_use_behavior = getattr(
            self, "tool_use_behavior", "run_llm_again"
        )
        # Always set our custom behavior to intercept and check halt state
        self.tool_use_behavior = self._custom_tool_use_behavior

        Debug.log(f"[SafeAgent] Initialized: skip={self._skip_approvals}, halt={self.halt_on_rejection}, "
                f"safe_names={len(self._safe_tool_names)}, safe_patterns={len(self._safe_tool_patterns)}")

    def _wrap_handoffs(self, handoffs: List[Any]) -> List[Any]:
        """Wrap handoff agents to automatically convert them to SafeAgent when invoked.

        This ensures that when safe mode is enabled, ALL agents in the chain
        (including handoffs) respect the approval mechanism.
        """
        from agents.handoffs import Handoff

        wrapped_handoffs = []
        for handoff in handoffs:
            if isinstance(handoff, Handoff):
                original_invoke = handoff.on_invoke_handoff

                async def wrapped_invoke(ctx, input_json, _original=original_invoke):
                    agent = await _original(ctx, input_json)

                    if (
                        isinstance(agent, SafeAgent)
                        and self._approval_callback
                        and hasattr(agent, "set_approval_callback")
                    ):
                        try:
                            agent.set_approval_callback(self._approval_callback)
                        except Exception:
                            pass

                    return agent

                import dataclasses

                wrapped_handoff = dataclasses.replace(
                    handoff, on_invoke_handoff=wrapped_invoke
                )
                wrapped_handoffs.append(wrapped_handoff)
            else:
                wrapped_handoffs.append(handoff)

        return wrapped_handoffs

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        """Set the approval callback after initialization.

        This is called by the Runner/backend to inject the appropriate
        callback for the execution environment (CLI, GUI, server).

        Also propagates the callback to any SafeAgent instances in handoffs.
        """
        self._approval_callback = callback
        self._needs_callback = False

        # Propagate to handoff agents if they are SafeAgents
        if hasattr(self, "handoffs"):
            for handoff in self.handoffs:
                # Handle both Handoff objects and direct Agent references
                agent = None
                if hasattr(handoff, "agent"):
                    agent = handoff.agent
                elif hasattr(handoff, "on_invoke_handoff"):
                    # For handoff() created objects, we may need to inspect differently
                    # This is tricky as the agent is returned by the function
                    pass

                if agent and isinstance(agent, SafeAgent):
                    agent.set_approval_callback(callback)

        Debug.log(f"[SafeAgent] Approval callback set for {self.name}")

    def is_tool_safe(self, tool: Tool) -> bool:
        """Check if a tool is considered safe and doesn't need approval.

        Checks both instance-level and global safe tool settings.
        """
        if self._skip_approvals:
            return True

        # Check instance-level safe tools
        if tool.name in self._safe_tool_names:
            return True
        for pattern in self._safe_tool_patterns:
            if pattern.match(tool.name):
                return True

        # Check global safe tools
        global_names = _global_safe_tool_names.get()
        if tool.name in global_names:
            return True

        global_patterns = _global_safe_tool_patterns.get()
        for pattern in global_patterns:
            if pattern.match(tool.name):
                return True

        return False

    def add_safe_tool(self, tool_name: str) -> None:
        """Add a tool name to the safe list for the current session."""
        self._safe_tool_names.add(tool_name)
        Debug.log(f"[SafeAgent] Added {tool_name} to safe tools list (session)")

    def add_safe_tool_pattern(self, pattern: str) -> None:
        """Add a regex pattern for tool names that are considered safe."""
        try:
            pattern_obj = re.compile(pattern)
            self._safe_tool_patterns.append(pattern_obj)
            Debug.log(
                    f"[SafeAgent] Added pattern '{pattern}' to safe tools patterns")
        except re.error as e:
            Debug.log(
                    f"[SafeAgent] Error adding pattern '{pattern}': {e}")

    def remove_safe_tool(self, tool_name: str) -> bool:
        """Remove a tool name from the safe list."""
        if tool_name in self._safe_tool_names:
            self._safe_tool_names.remove(tool_name)
            Debug.log(
                    f"[SafeAgent] Removed {tool_name} from safe tools list")
            return True
        return False

    def clear_safe_tools(self) -> None:
        """Clear all safe tool names and patterns."""
        self._safe_tool_names.clear()
        self._safe_tool_patterns.clear()
        Debug.log("[SafeAgent] Cleared all safe tools and patterns")

    def _compute_approval_metadata(
        self, tool_name: str, args: Dict
    ) -> Optional[Dict[str, Any]]:
        """Compute rich metadata for tool approvals.

        Returns metadata from registered generators via the @approval_metadata decorator.
        """
        from omniagents.core.tools import get_approval_metadata

        # Get metadata from registered generators
        return get_approval_metadata(tool_name, args)

    async def _custom_tool_use_behavior(self, ctx, tool_results):
        """Custom tool use behavior that checks for the halt flag."""
        if self._halt_run:
            halt_message = self._halt_reason or "Run halted after tool rejection"
            Debug.log(f"[SafeAgent] Halting run: {halt_message}")
            # Return final output with empty message to stop processing
            return ToolsToFinalOutputResult(is_final_output=True, final_output="")

        # Handle different types of original tool_use_behavior
        original = self._original_tool_use_behavior

        # If it's callable (a custom function), call it
        if callable(original):
            if inspect.iscoroutinefunction(original):
                return await original(ctx, tool_results)
            else:
                return original(ctx, tool_results)

        # Handle string literals (default agent behaviors)
        elif isinstance(original, str):
            if original == "run_llm_again":
                # Default behavior: continue processing
                return ToolsToFinalOutputResult(
                    is_final_output=False, final_output=None
                )
            elif original == "stop_on_first_tool":
                # Stop on first tool call
                if tool_results:
                    first_result = tool_results[0]
                    return ToolsToFinalOutputResult(
                        is_final_output=True, final_output=first_result.output
                    )
                return ToolsToFinalOutputResult(
                    is_final_output=False, final_output=None
                )

        # Handle StopAtTools dict
        elif isinstance(original, dict) and "stop_at_tool_names" in original:
            stop_at_names = original["stop_at_tool_names"]
            for result in tool_results:
                if hasattr(result, "name") and result.name in stop_at_names:
                    return ToolsToFinalOutputResult(
                        is_final_output=True, final_output=result.output
                    )
            return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

        # Default fallback: continue processing
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    def wrap_tool_with_approval(self, tool: Tool) -> Tool:
        """Wrap a FunctionTool with approval checks if it's not safe."""
        if not isinstance(tool, FunctionTool):
            Debug.log(
                    f"[SafeAgent] Tool {tool.name} is not a FunctionTool, skipping wrap")
            return tool

        if self.is_tool_safe(tool):
            Debug.log(
                    f"[SafeAgent] Tool {tool.name} is safe, not wrapping")
            return tool

        if hasattr(tool, "_approval_wrapped") and tool._approval_wrapped:
            return tool  # Already wrapped

        original_on_invoke = tool.on_invoke_tool

        async def approved_on_invoke(context, params_json: str):
            # If the tool has become safe (e.g., user clicked "Always" before)
            # we can execute it directly without another approval prompt.
            if self.is_tool_safe(tool):
                return await original_on_invoke(context, params_json)

            # Get approval callback - prefer instance callback, fall back to global
            approval_callback = self._approval_callback

            # Check if we have a callback available
            if approval_callback is None:
                if self._needs_callback:
                    # This shouldn't happen in normal flow as Runner should set callback
                    # But provide a helpful error message
                    raise RuntimeError(
                        f"SafeAgent '{self.name}' needs an approval callback but none was set. "
                        "This usually means the agent wasn't properly initialized by the Runner."
                    )
                # If we don't need a callback (skip_approvals=True), just execute
                return await original_on_invoke(context, params_json)

            # Otherwise, prompt for approval
            try:
                args_dict = json.loads(params_json) if params_json else {}
                formatted_args = ", ".join(
                    [
                        f"{k}: {repr(v)[:100]}{'...' if len(repr(v)) > 100 else ''}"
                        for k, v in args_dict.items()
                    ]
                )
            except Exception:
                formatted_args = params_json[:200] + (
                    "..." if len(params_json) > 200 else ""
                )

            # Compute metadata for rich approval UI
            metadata = self._compute_approval_metadata(
                tool.name, args_dict if "args_dict" in locals() else {}
            )

            approved, always_approve, error_msg = await approval_callback(
                tool.name, formatted_args, metadata
            )

            if approved:
                if always_approve:
                    self.add_safe_tool(tool.name)  # Instance level only
                # Audit: tool approved (include reviewer identity)
                if self._audit_emit is not None:
                    try:
                        await self._audit_emit(
                            "tool_approved", tool.name, {},
                            self._reviewer_id,
                        )
                    except Exception:
                        pass
                return await original_on_invoke(context, params_json)

            # --- Rejected path ---------------------------------------------------
            rejection_reason = error_msg or "User rejected tool call"
            if self.halt_on_rejection:
                self._halt_run = True
                self._halt_reason = f"{rejection_reason} ({tool.name})"
                Debug.log(
                        f"[SafeAgent] Setting halt flag due to rejection of {tool.name}")

            # Audit: tool rejected (include reviewer identity)
            if self._audit_emit is not None:
                try:
                    await self._audit_emit(
                        "tool_rejected", tool.name,
                        {"reason": rejection_reason},
                        self._reviewer_id,
                    )
                except Exception:
                    pass

            return json.dumps(
                {
                    "error": "TOOL_REJECTED",
                    "message": rejection_reason,
                    "tool": tool.name,
                }
            )

        # Create a new FunctionTool instance with the wrapped invoke method
        # Ensure we preserve all relevant properties from the original tool
        wrapped_tool = FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=dict(tool.params_json_schema),  # Use a copy
            on_invoke_tool=approved_on_invoke,
            strict_json_schema=tool.strict_json_schema,
        )
        wrapped_tool._approval_wrapped = True  # Mark as wrapped

        Debug.log(
                f"[SafeAgent] Wrapped tool {tool.name} with approval checks")

        return wrapped_tool

    # Override get_all_tools to ensure wrapping happens dynamically
    async def get_all_tools(self, run_context: Any) -> List[Tool]:
        """Get all tools from the base class and wrap them with approval checks.

        Args:
            run_context: The run context wrapper, required by parent class
        """
        # Reset halt flag at the beginning of each run potentially involving tool fetching
        self._halt_run = False
        self._halt_reason = None

        # Get tools from base Agent (could come from init or MCP servers)
        tools = await super().get_all_tools(run_context)

        # Wrap necessary tools
        wrapped_tools = [self.wrap_tool_with_approval(tool) for tool in tools]
        return wrapped_tools

    # Reset halt state when starting a new run, e.g., called by Runner
    def reset_run_state(self):
        """Resets any state related to a single run, like the halt flag."""
        self._halt_run = False
        self._halt_reason = None
        Debug.log("[SafeAgent] Resetting run state (halt flags)")


# Optional: Add helper function or adapt Runner if reset_run_state needs explicit call
# For example, Runner.run_streamed could check if agent has reset_run_state and call it.
# Or the AgentApp could call it before Runner.run_streamed.
# Let's assume AgentApp will handle calling reset if needed for now.
