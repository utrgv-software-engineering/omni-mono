from __future__ import annotations

from omniagents import function_tool
from datetime import datetime
import random


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    return datetime.now().strftime("%H:%M:%S")


@function_tool
def flip_coin() -> str:
    """Return the result of a coin flip."""
    return random.choice(["heads", "tails"])


@function_tool
def calculate(expression: str) -> str:
    """
    Safely evaluate a mathematical expression.

    Args:
        expression: A mathematical expression to evaluate (e.g., "2 + 2", "10 * 5")

    Returns:
        The result of the calculation as a string
    """
    try:
        # Only allow safe mathematical operations
        allowed_chars = "0123456789+-*/()., "
        if all(c in allowed_chars for c in expression):
            result = eval(expression)
            return str(result)
        else:
            return "Error: Invalid characters in expression"
    except Exception as e:
        return f"Error: {str(e)}"
