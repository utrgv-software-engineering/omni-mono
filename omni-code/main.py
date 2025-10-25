import os
from omniagents import Runner
from omniagents.core.agents.specs import MCPServerConfig
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


def cli_main():
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

    runner.main(default_mode="tui")


if __name__ == "__main__":
    cli_main()
