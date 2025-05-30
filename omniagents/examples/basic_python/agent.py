from __future__ import annotations

import argparse
from datetime import datetime
import random
from time import sleep

from agents import Agent
from omniagents.core.app_base import AgentApp
from omniagents.core import function_tool


INSTRUCTIONS = """You are a friendly assistant.\nUse `get_current_time` when a user asks for the current time.\nUse `flip_coin` when asked to flip a coin."""


@function_tool
def get_current_time() -> str:
    """Return the current time as a string."""
    return datetime.now().strftime("%H:%M:%S")

@function_tool
def flip_coin() -> str:
    sleep(15)
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
    parser = argparse.ArgumentParser(description="Run the Python tools example")
    parser.add_argument("--mode", choices=["gui", "cli", "server"], default="cli")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = AgentApp.from_agent(build_agent(), welcome_text="Ask me the time or to flip a coin!", safe=False)
    if args.mode == "server":
        app.run(mode="server", host=args.host, port=args.port)
    else:
        app.run(mode=args.mode)


if __name__ == "__main__":
    main()
