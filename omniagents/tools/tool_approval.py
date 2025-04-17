from typing import Dict, Any, Callable, Optional, Set
import functools
import os

# Store approved tools for the session
_approved_tools: Set[str] = set()

def requires_approval(tool_function: Callable):
    """
    Decorator that requires user approval before executing a tool.
    
    Args:
        tool_function: The function to decorate
        
    Returns:
        Wrapped function that requests approval before execution
    """
    @functools.wraps(tool_function)
    def wrapper(*args, **kwargs):
        tool_name = tool_function.__name__
        
        # Check if this tool has already been approved for the session
        if tool_name in _approved_tools:
            return tool_function(*args, **kwargs)
            
        # Get specific arguments based on tool function
        if "bash" in tool_name.lower():
            command = args[0] if args else kwargs.get("command", "unknown")
            action_text = f"execute command: '{command}'"
        elif "edit" in tool_name.lower():
            file_path = args[0] if args else kwargs.get("file_path", "unknown")
            action_text = f"edit file: '{file_path}'"
        elif "write" in tool_name.lower():
            file_path = args[0] if args else kwargs.get("file_path", "unknown")
            action_text = f"write to file: '{file_path}'"
        else:
            # For any other tools, format arguments
            arg_str = ", ".join([f"{repr(arg)}" for arg in args])
            kwarg_str = ", ".join([f"{k}={repr(v)}" for k, v in kwargs.items()])
            all_args = ", ".join(filter(None, [arg_str, kwarg_str]))
            action_text = f"use {tool_name} with args: {all_args}"
            
        # Determine tool display format based on tool type
        if "bash" in tool_name.lower():
            tool_display = f"execute_bash(command: '{command}', timeout: {kwargs.get('timeout', 'None')})"
        elif "edit" in tool_name.lower():
            file_path = kwargs.get("file_path", "unknown")
            old_text = kwargs.get("old_text", "...")
            if len(old_text) > 20:  # Truncate for display
                old_text = old_text[:17] + "..."
            tool_display = f"edit_file(file_path: '{file_path}', old_text: '{old_text}', ...)"
        elif "write" in tool_name.lower():
            file_path = kwargs.get("file_path", "unknown")
            content = kwargs.get("content", "...")
            if len(content) > 20:  # Truncate for display
                content = content[:17] + "..."
            append = "True" if kwargs.get("append", False) else "False"
            tool_display = f"write_file(file_path: '{file_path}', content: '{content}...', append: {append})"
        else:
            tool_display = f"{tool_name}(...)"
        
        # Display the tool call and approval request with yellow for requiring approval
        # Yellow color for the ⏺ symbol (ANSI color code 33 is yellow)
        print(f"\n\033[33m⏺\033[0m {tool_display}")
        print("  ⎿  Approval Required")
        print(f"\n⚠ Do you want to {action_text}?")
        print("  1. Yes")
        print("  2. Yes, and don't ask again for this session")
        print("  3. No, and tell me what to do differently")
        
        # Get user choice
        while True:
            try:
                choice = input("\nYour choice [1-3]: ")
                if choice == "1":
                    # Approve once - change to blue (36) after approval
                    print(f"\n\033[36m⏺\033[0m {tool_display}")  # Show the tool call again after approval, in blue
                    return tool_function(*args, **kwargs)
                elif choice == "2":
                    # Approve for the session - change to blue (36) after approval
                    _approved_tools.add(tool_name)
                    print(f"\n\033[36m⏺\033[0m {tool_display}")  # Show the tool call again after approval, in blue
                    return tool_function(*args, **kwargs)
                elif choice == "3":
                    # Reject with feedback using the same UI prompt style
                    message = input("> ")
                    return {
                        "error": "Permission denied by user",
                        "feedback": message
                    }
                else:
                    print("Invalid choice. Please enter 1, 2, or 3.")
            except KeyboardInterrupt:
                # Handle Ctrl+C gracefully
                return {"error": "Operation cancelled by user"}
            
    return wrapper

def reset_approvals():
    """Reset all tool approvals for the session"""
    _approved_tools.clear()