from __future__ import annotations

import argparse
from agents import Agent
from omniagents.core.app_base import AgentApp

from .tools import get_current_time, flip_coin

INSTRUCTIONS = """You are a friendly assistant.\nUse `get_current_time` when a user asks for the current time.\nUse `flip_coin` when asked to flip a coin."""


def build_agent() -> Agent:
    return Agent(
        name="Safe Tool Demo (Python)",
        instructions=INSTRUCTIONS,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SafeAgent tools example")
    parser.add_argument("--mode", choices=["gui", "cli", "server"], default="cli")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = AgentApp.from_agent(build_agent(), welcome_text="Ask me the time or to flip a coin!", safe=True)
    if args.mode == "server":
        app.run(mode="server", host=args.host, port=args.port)
    else:
        app.run(mode=args.mode)


if __name__ == "__main__":
    main()
