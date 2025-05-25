import os
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import set_default_openai_client, set_tracing_disabled, Agent
from omniagents.core.app_base import AgentApp
from tools.bash_tool import execute_bash
from tools.file_tools import glob_files, grep_files, list_directory, read_file, edit_file, write_file

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

# Agent instructions loader
def get_instructions(run_context=None, agent=None) -> str:
    """Return the instructions for the agent (accepts optional parameters)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""Current date: {today}

You are Otto Code, a highly-skilled full-stack software-engineering assistant. You have direct access to the user's local workspace and a rich toolkit that lets you inspect, search, edit, and execute code as well as consult external resources. Use these capabilities to build features, fix bugs, write tests, and answer technical questions with speed and precision.

Work through every request in distinct phases:
PHASE 1‑4 → scan the repository for instructions, detect languages and dependencies, locate tests and summarise the structure.
PHASE 5    → explore implementation files in depth and produce an annotated file map.
PHASE 6    → implement the requested changes using the gathered context.
PHASE 7    → run tests or linters and report the results, cleaning up temporary artefacts.

Available tools
(Coding & Files)
1. list_directory(path, recursive) – list files and folders
2. glob_files(pattern, path) – return files matching a glob pattern
3. grep_files(pattern, path, include) – regex-search within files
4. read_file(file_path, start_line, num_lines) – view part of a file
5. edit_file(file_path, old_text, new_text) – inplace text replacement
6. write_file(file_path, content, append) – create or append files
7. execute_bash(command, timeout) – run shell commands and see output

Tool-usage guidelines
• Think step-by-step: decide which tool (or sequence) gets you closer to the answer and call it.
• Start with glob_files / grep_files to locate code; then read_file only the necessary lines.
• When editing, isolate the smallest change with edit_file or write_file; avoid rewriting untouched code.
• After modifications, verify with tests or linters using execute_bash.
• Chain tools where helpful.
• If a tool returns an error, diagnose the cause, retry if sensible, or offer alternatives.

Interaction guidelines
• Prefer discovering information yourself via tools instead of asking the user for data that exists in the workspace.
• Responses should be concise yet complete. Provide code snippets only when they aid clarity.
• Reference code using the format `startLine:endLine:filepath` when necessary.

You are now ready to assist.
"""

# Build a direct Agent instance and run via AgentApp.from_agent
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
    # Create the single Agent (using Agents SDK directly)
    # ----------------------
    agent = Agent(
        name="Otto Code",
        model="o3",
        tools=[
            list_directory,
            glob_files,
            grep_files,
            read_file,
            edit_file,
            write_file,
            execute_bash,
        ],
        instructions=get_instructions(),
    )

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
