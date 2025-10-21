from omniagents import function_tool


@function_tool
def calculate(expression: str) -> str:
    """
    Evaluate a mathematical expression and return the result.

    Args:
        expression: A mathematical expression to evaluate (e.g., "2 + 2", "10 * 5")

    Returns:
        The result of the calculation as a string
    """
    try:
        # Use eval safely for basic math operations
        # In production, use a proper math parser like sympy
        result = eval(expression, {"__builtins__": {}}, {})
        return f"Result: {result}"
    except Exception as e:
        return f"Error: Invalid expression - {str(e)}"
