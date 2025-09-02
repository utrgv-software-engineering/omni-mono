"""
Example 3: OmniAgents Python - Dynamic Function with Context
This shows how to use a dynamic function that returns strings with context placeholders.
Combines runtime-generated content (like current time) with context substitution.
"""

import os
from datetime import datetime
from dotenv import load_dotenv

from agents import Agent
from agents.run_context import RunContextWrapper
from omniagents import Runner
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()


async def dynamic_instructions_with_context(context_wrapper: RunContextWrapper[dict], agent: Agent) -> str:
    """Generate instructions template with dynamic content.
    
    This function can add dynamic values to the context by modifying context_wrapper.context,
    then return a clean Jinja2 template that uses all values uniformly.
    """
    # Add dynamic values to the context (we know it's a dict because of Agent[dict])
    context_wrapper.context['now'] = datetime.now().strftime('%A, %B %d, %Y at %I:%M:%S %p')
    
    # Return a clean Jinja2 template
    return """You are a helpful assistant for {{ user_name }}.
    
Current time: {{ now }}
User location: {{ location }}
User age: {{ age }}

Always personalize your responses and be aware of the current time."""


def main():
    """Use a dynamic function that returns strings with context placeholders."""
    # Initialize AgentIO tracing
    agentio.init(
        project="dynamic_instructions_function",
        name="time_aware_assistant",
        resume="never"  # Always create a new trace
    )
    
    # Use Agent[dict] to make it clear we're using dict context
    agent = Agent[dict](
        name="Time-Aware Assistant",
        instructions=dynamic_instructions_with_context,
        tools=[],
        model="gpt-4o-mini",
    )
    
    runner = Runner.from_agent(
        agent,
        context={
            "user_name": "Bob",
            "location": "New York",
            "age": 35
        },
        welcome_text="Hello! I'm your time-aware personal assistant.",
    )
    
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()