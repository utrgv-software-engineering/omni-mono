from omniagents import function_tool


@function_tool
def calculate(expression: str) -> str:
    """
    Safely evaluate a mathematical expression.

    Args:
        expression: A mathematical expression to evaluate

    Returns:
        The result of the calculation
    """
    try:
        # Create a safe environment for eval
        # In production, use a proper math parser like sympy or numexpr
        safe_dict = {
            "__builtins__": {},
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "pow": pow,
        }

        # Basic safety check - only allow certain characters
        allowed_chars = "0123456789+-*/()., "
        if not all(c in allowed_chars for c in expression):
            return f"Error: Expression contains invalid characters. Only basic arithmetic is supported."

        result = eval(expression, safe_dict, {})
        return f"{result}"
    except ZeroDivisionError:
        return "Error: Division by zero"
    except Exception as e:
        return f"Error: {str(e)}"
