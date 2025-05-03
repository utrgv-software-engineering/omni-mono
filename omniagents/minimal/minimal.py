import os
import logging
import asyncio
import os

import sys
import types
import importlib.abc
import importlib.machinery

# Install a meta-path hook to stub out blacklisted modules that iOS's Python lacks
# also stub out importlib.metadata to make version("mcp") return "" instead of raising
BLACKLIST = {"uvicorn", "_multiprocessing", "importlib.metadata"}

class StubLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return types.ModuleType(spec.name)
    def exec_module(self, module):
        spec = getattr(module, "__spec__", None)
        name = spec.name if spec else None
        if name == "importlib.metadata":
            # make version() safe and define PackageNotFoundError
            module.PackageNotFoundError = Exception
            module.version = lambda distribution, *args, **kwargs: ""
        return

class StubFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        root = fullname.split('.', 1)[0]
        if fullname in BLACKLIST or root in BLACKLIST:
            return importlib.machinery.ModuleSpec(fullname, StubLoader())
        return None

sys.meta_path.insert(0, StubFinder())

# 1) suppress everything below WARNING by default
logging.basicConfig(level=logging.WARNING)

# 2) selectively enable DEBUG for HTTPX and httpcore
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

# (Optional) if you still want the MCP SSE noise:
logging.getLogger("mcp.client.sse").setLevel(logging.DEBUG)

# (Optional) if you want Flet's own INFO logs:
logging.getLogger("flet").setLevel(logging.INFO)
# leave flet_core at WARNING to keep it quiet:
logging.getLogger("flet_core").setLevel(logging.WARNING)

# send Fletd logs to file and set log level
os.environ["FLET_LOG_TO_FILE"] = "true"
os.environ["FLET_LOG_LEVEL"] = "debug"

import flet as ft
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession


async def initialize_mcp_task(url, headers, status_control: ft.Text, page_control: ft.Page):
    """Task to handle MCP initialization and UI updates."""
    success = False
    message = ""
    try:
        # Restore MCP code
        async with sse_client(url, headers=headers) as (read_stream, write_stream):
            print("Inside sse_client context manager...")  # Debug print
            # Use ClientSession to manage session lifecycle
            async with ClientSession(read_stream, write_stream) as session:
                # Perform initialization handshake
                init_result = await session.initialize()
                print(f"Initialization successful: protocol v{init_result.protocolVersion}")
                # List available tools
                tools_result = await session.list_tools()
                tool_names = [tool.name for tool in tools_result.tools]
                print(f"Tools received: {tool_names}")
                # Build a multi-line status message
                message = (
                    f"Initialized protocol v{init_result.protocolVersion}\n"
                    "Tools:\n" + "\n".join(tool_names)
                )
                success = True

    except Exception as ex:
        # Store error state and message
        import traceback
        message = f"Error: {ex}\n{traceback.format_exc()}" # Include full traceback
        success = False
        print(f"Exception during initialization:\n{message}") # Print traceback

    # Update UI AFTER the async with block has exited
    print("Attempting final page update from task (outside async with)...")
    status_control.value = message
    # Use page_control.update() to apply changes to the UI
    try:
        page_control.update()
        print("page_control.update() succeeded in task (outside async with).")
    except Exception as ui_ex:
        print(f"page_control.update() failed in task: {ui_ex}")


def main(page: ft.Page):
    page.title = "Minimal MCP Example"
    status = ft.Text("Disconnected")

    # Define the async click handler within main to capture status and page
    async def connect_mcp_click(e):
        status.value = "Connecting…"
        # Use page.update() even in async handlers
        page.update()
        url = "https://indiegym.app/mcp/sse"
        headers = {"Authorization": "Bearer DKCPRYika7d4tejz6dFuuZ6J"}
        # headers = {}
        # Run the initialization logic as a background task
        print("Running MCP initialization task...") # Add print statement
        page.run_task(initialize_mcp_task, url, headers, status, page)
        print("MCP initialization task started.") # Add print statement
        # Note: No await here, the task runs in the background.
        # The task itself will update the UI when done.

    btn = ft.ElevatedButton(
        "Connect MCP",
        on_click=connect_mcp_click,  # Use the async handler
    )
    page.add(status, btn)


# Restore imports
# (No longer need to remove _initialize_mcp function)


if __name__ == "__main__":
    ft.app(target=main) 