from __future__ import annotations

from agents import Agent
from omniagents import Runner, function_tool
from datetime import datetime
import random
import agentio  # Import AgentIO for tracing

INSTRUCTIONS = """You are a friendly assistant.\nUse `get_current_time` when a user asks for the current time.\nUse `flip_coin` when asked to flip a coin."""


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    return datetime.now().strftime("%H:%M:%S")


@function_tool
def flip_coin() -> str:
    """Return the result of a coin flip."""
    return random.choice(["heads", "tails"])


def build_agent() -> Agent:
    return Agent(
        name="Safe Tool Demo (Python)",
        instructions=INSTRUCTIONS,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )


def main() -> None:
    # Initialize AgentIO tracing
    agentio.init(
        project="safe_agent_demo",
        name="safe_tool_demo",
        resume="never",  # Always create a new trace
    )

    runner = Runner.from_agent(
        build_agent(),
        welcome_text="Ask me the time or to flip a coin!",
        safe=True,  # Enable SafeAgent wrapper
    )
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
