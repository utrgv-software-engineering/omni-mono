"""
Example 1: OpenAI Agents SDK Native Approach
This shows how to use dynamic variables with the raw OpenAI Agents SDK.
"""

import os
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
from agents import Agent
from agents.run_context import RunContextWrapper
from omniagents import Runner
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()

@dataclass
class UserContext:
    """Context object containing user information and variables."""
    user_name: str
    location: str
    age: int


async def dynamic_instructions(context: RunContextWrapper[UserContext], agent: Agent) -> str:
    """Generate instructions dynamically using context variables."""
    now = datetime.now()
    user = context.context  # Access the UserContext object
    
    return f"""You are a helpful assistant for {user.user_name}.
    
User Details:
- Name: {user.user_name}
- Age: {user.age}
- Location: {user.location}
- Current time: {now.strftime('%A, %B %d, %Y at %I:%M:%S %p')}

Always personalize your responses based on these details."""


def main():
    # Initialize AgentIO tracing
    agentio.init(
        project="dynamic_instructions_sdk",
        name="personal_assistant",
        resume="never"  # Always create a new trace
    )
    
    # Create context with user information
    user_context = UserContext(
        user_name="Alice",
        location="San Francisco",
        age=28
    )
    
    # Create agent with dynamic instructions
    agent = Agent[UserContext](
        name="Personal Assistant",
        instructions=dynamic_instructions,
        tools=[],
        model="gpt-4o-mini",
    )
    
    # Create and run the OmniAgents runner
    runner = Runner.from_agent(
        agent,
        context=user_context,
        welcome_text="Hello! I'm your personal assistant.",
    )
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()