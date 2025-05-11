import os
import random
from datetime import datetime

import flet as ft
from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import function_tool, set_default_openai_client, set_default_openai_api, set_tracing_disabled, Agent
from agents.model_settings import ModelSettings
from omniagents.core.app_base import AgentApp

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    raise ValueError("Please set OPENAI_API_KEY in your environment or .env file")

# Initialize custom OpenAI client
client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
set_default_openai_client(client=client, use_for_tracing=False)
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# Define tools
@function_tool
def get_current_time() -> str:
    """Returns the current time."""
    return datetime.now().strftime("The current time is %H:%M:%S")

@function_tool
def flip_coin() -> str:
    """Flips a coin and returns heads or tails."""
    result = random.choice(["heads", "tails"])
    return f"The coin landed on {result}"

# Agent instructions loader
def get_instructions(run_context=None, agent=None) -> str:
    """Return the instructions for the agent (accepts optional parameters)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""Current date: {today}

You are a friendly assistant that can help with simple tasks.
You have access to two tools:
  - get_current_time: use this when someone asks for the current time
  - flip_coin: use this when someone asks to flip a coin

Only invoke these tools when explicitly asked to perform their tasks."""

# Build a direct Agent instance and run via AgentApp.from_agent
if __name__ == "__main__":
    import argparse

    # Create the Agent (using Agents SDK directly)
    agent = Agent(
        name="Hello World Assistant",
        instructions=get_instructions,
        tools=[get_current_time, flip_coin],
        model="gpt-4o",
    )
    # Wrap in an OmniAgents App
    app = AgentApp.from_agent(
        agent,
        welcome_text="Ask me the current time or to flip a coin!",
    )

    # Parse run mode
    parser = argparse.ArgumentParser(description="Hello World Agent Runner")
    parser.add_argument(
        "--mode", "-m",
        choices=["gui", "cli", "server"],
        default="gui",
        help="Run mode: 'gui' for graphical UI, 'cli' for command-line interface, 'server' to expose JSON-RPC/WebSocket endpoint"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind in server mode (default 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port for server mode (default 8000)")
    parser.add_argument("--auth-token", dest="auth_token", default=None, help="Bearer token to require from remote clients (server mode)")
    args = parser.parse_args()

    # Launch (pass extra server args if needed)
    if args.mode == "server":
        app.run(mode="server", host=args.host, port=args.port, auth_token=args.auth_token)
    else:
        app.run(mode=args.mode)
