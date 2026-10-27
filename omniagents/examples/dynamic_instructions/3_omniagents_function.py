"""
Example 3: OmniAgents Python - Dynamic Function with Context
This shows how to use a dynamic function that returns strings with context placeholders.
Combines runtime-generated content with context substitution.
Demonstrates fetching user data based on user_id.
"""

import os
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

from agents import Agent
from agents.run_context import RunContextWrapper
from omniagents import Runner
from omniagents.core.tracing import local as studio_tracing

# Load environment variables from .env file
load_dotenv()


@dataclass
class UserContext:
    """Typed context for the time-aware assistant."""

    user_id: str
    user_name: str
    location: str
    age: int
    timezone: str = "UTC"

    def now(self) -> str:
        """Get current time in user's timezone."""
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(self.timezone)).strftime(
            "%A, %B %d, %Y at %I:%M:%S %p"
        )


async def dynamic_instructions_with_context(
    context_wrapper: RunContextWrapper[UserContext], agent: Agent
) -> str:
    """Generate instructions template with dynamic content.

    Since now() is a method on the context, we can call it in the template.
    This function returns a Jinja2 template that uses context fields and methods.
    """
    # Return a clean Jinja2 template that calls the now() method
    return """You are a helpful assistant for {{ user_name }} (User ID: {{ user_id }}).
    
Current time: {{ now() }}
User location: {{ location }}
User age: {{ age }}

Always personalize your responses and be aware of the current time."""


def fetch_user_profile(user_id: str) -> Optional[dict]:
    """Simulate fetching user profile from an API or database."""
    # In production, this would be an actual API call or database query
    profiles = {
        "user_001": {
            "name": "Alice Johnson",
            "location": "San Francisco",
            "age": 28,
            "timezone": "America/Los_Angeles",
        },
        "user_002": {
            "name": "Bob Smith",
            "location": "New York",
            "age": 35,
            "timezone": "America/New_York",
        },
    }
    return profiles.get(user_id)


def build_context_from_variables(variables: dict) -> UserContext:
    """Factory function to build typed context from variables.

    This pattern provides consistency between local and remote execution:
    - Local: Variables are passed to this factory to build context
    - Remote: Client sends variables dict, server uses this factory
    - Server mode: Remote clients send variables, this factory constructs context

    The factory handles:
    - Missing fields (with defaults)
    - Extra fields (ignored)
    - Type conversions
    """
    user_id = variables.get("user_id", "anonymous")

    # Fetch full user profile based on user_id
    profile = fetch_user_profile(user_id)

    if profile:
        return UserContext(
            user_id=user_id,
            user_name=profile["name"],
            location=profile["location"],
            age=profile["age"],
            timezone=profile["timezone"],
        )
    else:
        # Defaults for unknown users
        return UserContext(
            user_id=user_id,
            user_name="Guest",
            location="Unknown",
            age=0,
            timezone="UTC",
        )


def main():
    """Use a dynamic function that returns strings with context placeholders."""
    # Initialize Studio tracing
    studio_tracing.init(
        project="dynamic_instructions_function",
        name="time_aware_assistant",
        resume="never",  # Always create a new trace
    )

    # Use Agent[UserContext] with typed context
    agent = Agent[UserContext](
        name="Time-Aware Assistant",
        instructions=dynamic_instructions_with_context,
        tools=[],
        model="gpt-4o-mini",
    )

    # Use variables + factory pattern for consistency
    # This works the same for both local and remote execution
    runner = Runner.from_agent(
        agent,
        variables={
            "user_id": "user_002"  # Only pass user_id, factory fetches the rest
        },
        build_context=build_context_from_variables,
        welcome_text="Hello! I'm your time-aware personal assistant.",
    )

    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
