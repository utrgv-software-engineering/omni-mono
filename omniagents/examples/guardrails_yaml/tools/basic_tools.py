"""Basic tools for the guardrails demo agent."""

from datetime import datetime
from omniagents.core.tools.discovery import function_tool


@function_tool
def get_current_time() -> str:
    """Get the current time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@function_tool
def calculate(expression: str) -> str:
    """
    Safely evaluate a mathematical expression.
    
    Args:
        expression: A mathematical expression to evaluate (e.g., "2 + 2", "10 * 5")
    
    Returns:
        The result of the calculation as a string.
    """
    try:
        # Only allow safe mathematical operations
        allowed_chars = "0123456789+-*/() ."
        if all(c in allowed_chars for c in expression):
            result = eval(expression)
            return str(result)
        else:
            return "Error: Invalid characters in expression. Only numbers and basic operators (+, -, *, /, parentheses) are allowed."
    except Exception as e:
        return f"Error evaluating expression: {str(e)}"