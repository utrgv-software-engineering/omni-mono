"""
Example 2: OmniAgents Python - String Instructions with Jinja2 Templates
This shows how OmniAgents automatically converts string instructions to Jinja2 templates.
The template is re-rendered on every message with the current context.
Demonstrates fetching user data based on user_id.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from agents import Agent
from omniagents import Runner
from omniagents.core.tracing import local as studio_tracing

# Load environment variables from .env file
load_dotenv()


@dataclass
class UserContext:
    """Typed context for the personal assistant."""

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


def fetch_user_data(user_id: str) -> Optional[dict]:
    """Simulate fetching user data from a database or API."""
    # In production, this would be an actual database query or API call
    user_database = {
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
    return user_database.get(user_id)


def build_context_from_variables(variables: dict) -> UserContext:
    """Factory function to build typed context from variables."""
    user_id = variables.get("user_id", "anonymous")

    # Fetch user data from database/API
    user_data = fetch_user_data(user_id)

    if user_data:
        return UserContext(
            user_id=user_id,
            user_name=user_data["name"],
            location=user_data["location"],
            age=user_data["age"],
            timezone=user_data["timezone"],
        )
    else:
        # Default for unknown users
        return UserContext(
            user_id=user_id,
            user_name="Guest",
            location="Unknown",
            age=0,
            timezone="UTC",
        )


def main():
    """Use string instructions with Jinja2 template syntax and typed context."""
    # Initialize Studio tracing
    studio_tracing.init(
        project="dynamic_instructions_string",
        name="personal_assistant",
        resume="never",  # Always create a new trace
    )

    # Create agent with dataclass context type hint
    # The string instructions will be automatically converted to a Jinja2 template
    # Note: Methods like now() can be called in templates
    agent = Agent[UserContext](
        name="Personal Assistant",
        instructions="You are a helpful assistant for {{ user_name }} (ID: {{ user_id }}) who lives in {{ location }} and is {{ age }} years old. Current time: {{ now() }}",
        tools=[],
        model="gpt-4o-mini",
    )

    # Use variables + factory pattern for consistency across all examples
    # Client only needs to send user_id, server fetches the rest
    runner = Runner.from_agent(
        agent,
        variables={
            "user_id": "user_001"  # In production, this would come from auth/session
        },
        build_context=build_context_from_variables,
        welcome_text="Hello! I'm your personal assistant.",
    )

    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
