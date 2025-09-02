"""
Example 2: OmniAgents Python - String Instructions with Jinja2 Templates
This shows how OmniAgents automatically converts string instructions to Jinja2 templates.
The template is re-rendered on every message with the current context.
Demonstrates using a dataclass with methods for typed context.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv

from agents import Agent
from omniagents import Runner
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()


@dataclass
class UserContext:
    """Typed context for the personal assistant."""
    user_name: str
    location: str
    age: int
    timezone: str = "UTC"
    
    def now(self) -> str:
        """Get current time in user's timezone."""
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(self.timezone)).strftime('%A, %B %d, %Y at %I:%M:%S %p')


def build_context_from_variables(variables: dict) -> UserContext:
    """Factory function to build typed context from variables."""
    return UserContext(
        user_name=variables.get('user_name', 'Guest'),
        location=variables.get('location', 'Unknown'),
        age=variables.get('age', 0),
        timezone=variables.get('timezone', 'UTC')
    )


def main():
    """Use string instructions with Jinja2 template syntax and typed context."""
    # Initialize AgentIO tracing
    agentio.init(
        project="dynamic_instructions_string",
        name="personal_assistant",
        resume="never"  # Always create a new trace
    )
    
    # Create agent with dataclass context type hint
    # The string instructions will be automatically converted to a Jinja2 template
    # Note: Methods like now() can be called in templates
    agent = Agent[UserContext](
        name="Personal Assistant",
        instructions="You are a helpful assistant for {{ user_name }} who lives in {{ location }} and is {{ age }} years old. Current time: {{ now() }}",
        tools=[],
        model="gpt-4o-mini",
    )
    
    # Use variables + factory pattern for consistency across all examples
    # This same pattern works for local, server, and remote execution
    runner = Runner.from_agent(
        agent,
        variables={
            "user_name": "Alice",
            "location": "San Francisco",
            "age": 28,
            "timezone": "America/Los_Angeles"
        },
        build_context=build_context_from_variables,
        welcome_text="Hello! I'm your personal assistant.",
    )
    
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()