from __future__ import annotations

from datetime import datetime
import random
from time import sleep


from dotenv import load_dotenv
from agents import Agent
from omniagents import Runner, function_tool
from server_functions.note import set_note, get_note

# Load environment variables from .env file
load_dotenv()


INSTRUCTIONS = (
    """
You are a friendly assistant.
Use `get_current_time` when a user asks for the current time.
Use `flip_coin` when asked to flip a coin.
"""
).strip()


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    return datetime.now().strftime("%H:%M:%S")


# Demonstrates Client Functions: override the TUI status line while this tool runs
@function_tool(client_status="Flipping coin...")
def flip_coin() -> str:
    """Return the result of a coin flip (after a short delay)."""
    # Simulate work so you can see the status override in the Bubble Tea UI
    sleep(2)
    return random.choice(["heads", "tails"])


def build_agent() -> Agent:
    return Agent(
        name="Client/Server Functions Demo",
        instructions=INSTRUCTIONS,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )


def main() -> None:
    runner = Runner.from_agent(
        build_agent(),
        welcome_text=(
            "Ask me the time or to flip a coin!\n"
            "Try commands: /set_note Hello world, /get_note, /help, /exit"
        ),
        safe=False,
        server_functions=[set_note, get_note],
    )
    runner.main(default_mode="bubbletea")


if __name__ == "__main__":
    main()
