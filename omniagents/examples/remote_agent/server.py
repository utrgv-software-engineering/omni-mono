from __future__ import annotations

from agents import Agent
from omniagents import Runner, function_tool
from datetime import datetime
import random
import agentio  # Import AgentIO for tracing

INSTRUCTIONS = open("instructions.md", "r").read()


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
        name="Remote Tool Agent",
        instructions=INSTRUCTIONS,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )


def main() -> None:
    # Initialize AgentIO tracing
    agentio.init(
        project="remote_agent_demo",
        name="remote_server",
        resume="never",  # Always create a new trace
    )

    runner = Runner.from_agent(build_agent())
    runner.main(default_mode="server")


if __name__ == "__main__":
    main()
