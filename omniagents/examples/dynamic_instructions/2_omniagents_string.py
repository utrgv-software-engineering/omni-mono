"""
Example 2: OmniAgents Python - String Instructions with Jinja2 Templates
This shows how OmniAgents automatically converts string instructions to Jinja2 templates.
The template is re-rendered on every message with the current context.
"""

import os
from dotenv import load_dotenv

from agents import Agent
from omniagents import Runner
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()


def main():
    """Use string instructions with Jinja2 template syntax."""
    # Initialize AgentIO tracing
    agentio.init(
        project="dynamic_instructions_string",
        name="personal_assistant",
        resume="never"  # Always create a new trace
    )
    
    # Create agent with dict context type hint
    # The string instructions will be automatically converted to a Jinja2 template
    agent = Agent[dict](
        name="Personal Assistant",
        instructions="You are a helpful assistant for {{ user_name }} who lives in {{ location }} and is {{ age }} years old.",
        tools=[],
        model="gpt-4o-mini",
    )
    
    runner = Runner.from_agent(
        agent,
        context={
            "user_name": "Alice",
            "location": "San Francisco",
            "age": 28
        },
        welcome_text="Hello! I'm your personal assistant.",
    )
    
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()