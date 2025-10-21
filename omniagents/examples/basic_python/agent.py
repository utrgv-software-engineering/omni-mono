from __future__ import annotations

from datetime import datetime
import random
from time import sleep

from dotenv import load_dotenv
from agents import Agent
from omniagents import Runner, function_tool
import agentio  # Import AgentIO for tracing

# Load environment variables from .env file
load_dotenv()


INSTRUCTIONS = """You are a friendly assistant.\nUse `get_current_time` when a user asks for the current time.\nUse `flip_coin` when asked to flip a coin."""


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    return datetime.now().strftime("%H:%M:%S")


@function_tool
def flip_coin() -> str:
    sleep(5)
    """Return the result of a coin flip."""
    return random.choice(["heads", "tails"])


def build_agent() -> Agent:
    return Agent(
        name="Tool Demo (Python)",
        instructions=INSTRUCTIONS,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )


def main() -> None:
    # Initialize AgentIO tracing
    agentio.init(
        project="basic_python_demo",
        name="tool_demo",
        resume="never",  # Always create a new trace
    )

    runner = Runner.from_agent(
        build_agent(), welcome_text="Ask me the time or to flip a coin!", safe=False
    )
    runner.main(default_mode="cli")


if __name__ == "__main__":
    main()
