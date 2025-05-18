from __future__ import annotations

import argparse
from agents import Agent
from omniagents.core.app_base import AgentApp

from .tools import get_current_time, flip_coin

INSTRUCTIONS = open("instructions.md", "r").read()


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

    app = AgentApp.from_agent(build_agent())
    app.run(mode="server", host=args.host, port=args.port, auth_token=args.auth_token)


if __name__ == "__main__":
    main()
