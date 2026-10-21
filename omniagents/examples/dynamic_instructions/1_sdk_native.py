"""
Example 1: OpenAI Agents SDK Native Approach
This shows how to use dynamic variables with the raw OpenAI Agents SDK.
Demonstrates fetching user data within the context object itself.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from agents import Agent
from agents.run_context import RunContextWrapper
from omniagents import Runner
import omniagents.studio.tracing as studio_tracing  # Import Studio for tracing

# Load environment variables from .env file
load_dotenv()


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


@dataclass
class UserContext:
    """Context object containing user information.

    With SDK native approach, the context must be fully constructed
    before passing to the agent. Data fetching happens in __post_init__.
    """

    user_id: str
    user_name: str = None
    location: str = None
    age: int = None
    timezone: str = "UTC"

    def __post_init__(self):
        """Fetch user data after initialization if only user_id provided."""
        if self.user_name is None:  # Assume we need to fetch data
            user_data = fetch_user_data(self.user_id)
            if user_data:
                self.user_name = user_data["name"]
                self.location = user_data["location"]
                self.age = user_data["age"]
                self.timezone = user_data["timezone"]
            else:
                # Defaults for unknown users
                self.user_name = "Guest"
                self.location = "Unknown"
                self.age = 0

    def now(self) -> str:
        """Get current time in user's timezone."""
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(self.timezone)).strftime(
            "%A, %B %d, %Y at %I:%M:%S %p"
        )


async def dynamic_instructions(
    context: RunContextWrapper[UserContext], agent: Agent
) -> str:
    """Generate instructions dynamically using context variables."""
    user = context.context  # Access the UserContext object

    return f"""You are a helpful assistant for {user.user_name} (ID: {user.user_id}).
    
User Details:
- Name: {user.user_name}
- Age: {user.age}
- Location: {user.location}
- Current time: {user.now()}

Always personalize your responses based on these details."""


def main():
    # Initialize Studio tracing
    studio_tracing.init(
        project="dynamic_instructions_sdk",
        name="personal_assistant",
        resume="never",  # Always create a new trace
    )

    # Create context with just user_id - __post_init__ fetches the rest
    user_context = UserContext(user_id="user_001")

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
