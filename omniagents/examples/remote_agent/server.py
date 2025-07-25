from __future__ import annotations

import argparse
from agents import Agent
from omniagents.core.runner import Runner
from omniagents.core import function_tool
from datetime import datetime
import random

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
    parser = argparse.ArgumentParser(description="Run the remote agent server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--auth-token", dest="auth_token", default=None)
    args = parser.parse_args()

    runner = Runner.from_agent(build_agent())
    runner.run(mode="server", host=args.host, port=args.port, auth_token=args.auth_token)


if __name__ == "__main__":
    main()
