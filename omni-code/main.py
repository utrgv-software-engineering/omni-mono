import argparse
import os
import sys
from omniagents import Runner
from omniagents.core.debug import Debug
from openai import AsyncAzureOpenAI, AsyncOpenAI
from agents import (
    set_default_openai_client,
    set_default_openai_api,
)
import agentio
from omni_code.tools_bootstrap import ensure_tools_importable

ensure_tools_importable()

from tools import *
from omni_code.config import load_config
from oagents.coding_agent import create_agent
from server_functions.agentio_functions import good, bad, note, notes
from server_functions.filesystem import (
    fs_get_cwd,
    fs_get_home,
    fs_list_dir,
    fs_get_workspace_root,
    session_ensure,
)
from server_functions.terminal import (
    TERMINAL_SERVER_FUNCTIONS,
    attach_terminal_manager,
    terminal_supported,
)

for key, value in load_config().items():
    if key not in os.environ:
        os.environ[key] = value

# Configure OpenAI provider (Azure vs. OpenAI) based on environment
provider = (os.getenv("OPENAI_PROVIDER") or "openai").strip().lower()
if provider == "azure":
    client = AsyncAzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        organization=os.getenv("OPENAI_ORG"),
        project=os.getenv("OPENAI_PROJECT"),
    )
    set_default_openai_client(client, use_for_tracing=False)
elif provider in {"openai", "custom"}:
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        organization=os.getenv("OPENAI_ORG"),
        project=os.getenv("OPENAI_PROJECT"),
    )
    set_default_openai_client(client, use_for_tracing=False)
else:
    raise ValueError(
        f"Unsupported OPENAI_PROVIDER: {provider!r}. Use 'azure', 'openai', or 'custom'."
    )


def cli_main(argv=None):
    agentio.init(
        project="omni_code",
        name="omni_code",
        resume="never",
    )

    agent = create_agent()

    terminal_available = terminal_supported()

    fs_functions = [
        fs_get_cwd,
        fs_get_home,
        fs_list_dir,
        fs_get_workspace_root,
        session_ensure,
    ]

    server_functions_list = [
        good,
        bad,
        note,
        notes,
        *fs_functions,
    ]

    if terminal_available:
        server_functions_list.extend(TERMINAL_SERVER_FUNCTIONS)

    runner = Runner.from_agent(
        agent,
        welcome_text="Welcome to Omni Code! I'm here to help you with your coding needs.",
        max_turns=1000,
        context_window=400000,
        server_functions=server_functions_list,
    )

    previous_registrar = getattr(runner.spec, "register_server_functions", None)

    def _register_with_terminal(service):
        if callable(previous_registrar):
            previous_registrar(service)
        if terminal_available:
            if not attach_terminal_manager(service):
                print("Warning: Terminal support unavailable on this platform.")

    runner.spec.register_server_functions = _register_with_terminal

    parser = argparse.ArgumentParser(
        prog="omni",
        description=f"Run {runner.spec.name}" if getattr(runner, "spec", None) else "Run agent",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=["gui", "cli", "server", "bubbletea", "tui"],
        default="tui",
        help="Execution mode (default: tui)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Show TUI session picker (Bubble Tea mode only).",
    )
    parser.add_argument(
        "--approvals",
        choices=["require", "auto", "skip"],
        help="Approval policy: require (manual), auto (auto-approve), skip (disable SafeAgent)",
    )
    parser.add_argument(
        "--on-reject",
        dest="on_reject",
        choices=["halt", "continue"],
        help="Behavior when a tool call is rejected: halt or continue",
    )
    parser.add_argument(
        "--session-id",
        help="Resume a previous CLI session",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--auth-token",
        dest="auth_token",
        help="Bearer token for authentication",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on code changes",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Optional message to send immediately after startup.",
    )

    args = parser.parse_args(argv)

    Debug.init(cli_flag=args.debug)

    kwargs = {"args": args}

    resume_flag = bool(getattr(args, "resume", False))
    if resume_flag and args.mode not in ("bubbletea", "tui"):
        print("Error: --resume is only supported in Bubble Tea TUI mode.")
        sys.exit(2)
    if resume_flag:
        kwargs["resume"] = True

    if args.mode == "cli":
        kwargs["session_id"] = args.session_id
    elif args.mode == "server":
        kwargs.update(
            {
                "host": args.host,
                "port": args.port,
                "auth_token": args.auth_token,
                "reload": args.reload,
            }
        )

    prompt_text = " ".join(args.prompt).strip() if args.prompt else ""
    if prompt_text:
        kwargs["initial_prompt"] = prompt_text

    try:
        runner.run(mode=args.mode, **kwargs)
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
