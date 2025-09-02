"""
Example 3: OmniAgents Python - Dynamic Function with Context
This shows how to use a dynamic function that returns strings with context placeholders.
Combines runtime-generated content (like current time) with context substitution.
Demonstrates using a dataclass for typed context with dynamic additions.
"""

import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from agents import Agent
from agents.run_context import RunContextWrapper
from omniagents import Runner
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()


@dataclass
class UserContext:
    """Typed context for the time-aware assistant."""
    user_name: str
    location: str
    age: int
    timezone: str = "UTC"
    
    def now(self) -> str:
        """Get current time in user's timezone."""
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(self.timezone)).strftime('%A, %B %d, %Y at %I:%M:%S %p')


async def dynamic_instructions_with_context(context_wrapper: RunContextWrapper[UserContext], agent: Agent) -> str:
    """Generate instructions template with dynamic content.
    
    Since now() is a method on the context, we can call it in the template.
    This function returns a Jinja2 template that uses context fields and methods.
    """
    # Return a clean Jinja2 template that calls the now() method
    return """You are a helpful assistant for {{ user_name }}.
    
Current time: {{ now() }}
User location: {{ location }}
User age: {{ age }}

Always personalize your responses and be aware of the current time."""


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
    return UserContext(
        user_name=variables.get('user_name', 'Guest'),
        location=variables.get('location', 'Unknown'),
        age=variables.get('age', 0),
        timezone=variables.get('timezone', 'UTC')
    )


def main():
    """Use a dynamic function that returns strings with context placeholders."""
    # Initialize AgentIO tracing
    agentio.init(
        project="dynamic_instructions_function",
        name="time_aware_assistant",
        resume="never"  # Always create a new trace
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
            "user_name": "Bob",
            "location": "New York",
            "age": 35,
            "timezone": "America/New_York"
        },
        build_context=build_context_from_variables,
        welcome_text="Hello! I'm your time-aware personal assistant.",
    )
    
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()