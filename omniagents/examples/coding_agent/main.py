import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import set_default_openai_client, set_tracing_disabled
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

# Entry point: configure OpenAI client and launch the manager agent
if __name__ == "__main__":
    import argparse

    # ----------------------
    # Parse CLI arguments
    # ----------------------
    parser = argparse.ArgumentParser(description="Multi-Tool Assistant Runner")
    parser.add_argument(
        "--mode", "-m",
        choices=["gui", "cli", "server"],
        default="cli",
        help="Run mode: 'gui' for graphical UI, 'cli' for command-line interface, 'server' to expose JSON-RPC/WebSocket endpoint"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind in server mode (default 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port for server mode (default 8000)")
    parser.add_argument("--auth-token", dest="auth_token", default=None, help="Bearer token to require from remote clients (server mode)")
    # NEW: workspace directory argument
    parser.add_argument(
        "--workspace", "-w",
        default=None,
        help="Path to the working directory to operate in. If provided, the program will 'cd' into this directory before launching the agent."
    )
    args = parser.parse_args()

    # ----------------------
    # Change working directory if requested
    # ----------------------
    if args.workspace:
        workspace_path = os.path.abspath(os.path.expanduser(args.workspace))
        if not os.path.isdir(workspace_path):
            raise ValueError(f"Provided workspace directory does not exist: {workspace_path}")
        os.chdir(workspace_path)

    # ----------------------
    # Create the Agent (using Agents SDK directly)
    # ----------------------
    from multi_agents import create_manager_agent
    agent = create_manager_agent()

    # Wrap in an OmniAgents App
    app = AgentApp.from_agent(
        agent,
        welcome_text="Welcome to Otto Code! I'm here to help you with your coding needs.",
        safe=True,
        safe_tool_names=[
            "list_directory",
            "glob_files",
            "grep_files",
            "read_file"
        ],
        max_turns=100,
        layout="tabs"
    )

        # Launch (pass extra server args if needed)
    if args.mode == "server":
        app.run(mode="server", host=args.host, port=args.port, auth_token=args.auth_token)
    else:
        app.run(mode=args.mode)
