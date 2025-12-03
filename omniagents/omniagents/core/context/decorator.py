"""
Context factory decorator for marking functions as context builders.
"""

from typing import Callable, Any, Dict


def context_factory(func: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Callable:
    """
    Decorator to mark a function as a context factory.

    Context factories are functions that take a variables dict and return
    a context dict for use in Jinja2 instruction templates.

    Usage:
        @context_factory
        def build_my_context(variables):
            return {
                "current_date": datetime.now().strftime("%Y-%m-%d"),
                "user_name": variables.get("user_name", "Guest"),
            }

    Args:
        func: A function that takes a dict of variables and returns a dict of context.

    Returns:
        The decorated function with a marker attribute for discovery.
    """
    # Add marker attribute for discovery
    setattr(func, "_is_context_factory", True)
    return func
