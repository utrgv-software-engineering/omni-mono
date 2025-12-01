"""
Context factory discovery and registration for omniagents.

Context factories are functions that build dynamic context for agent instruction
templates. They are discovered by scanning the agent directory for Python files
containing functions decorated with @context_factory.

Usage:
    # In any .py file in your agent directory:
    from omniagents import context_factory

    @context_factory
    def build_hotel_context(variables):
        return {
            "current_date": "2024-01-01",
            "hotel_name": "Grand Hotel",
            ...
        }

    # In agent.yml:
    context: build_hotel_context

    variables:
      hotel_id: "${HOTEL_ID}"
"""

from .decorator import context_factory
from .discovery import discover_context_factories

__all__ = ["context_factory", "discover_context_factories"]
