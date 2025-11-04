import os
import sys
import json
import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from datetime import datetime
from typing import List, Optional, Tuple, Awaitable

from dotenv import load_dotenv
from agents import RunConfig, ItemHelpers

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.runtime.overrides import apply_cli_overrides
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.agents.safe import SafeAgent
from omniagents.core.agents.mcp import build_mcp_servers
from omniagents.core.session import Session
from omniagents.core.debug import Debug

ApprovalResult = Tuple[bool, bool, Optional[str]]


async def cli_approval_callback(
    tool_name: str, formatted_args: str, metadata: dict | None = None
) -> ApprovalResult:
    """
    CLI implementation for tool approval.
    Returns a tuple: (approved: bool, always_approve: bool, error_message: Optional[str])
    """
    # Display the tool call and approval request
    print(f"\n\033[33m⏺\033[0m \033[1m{tool_name}\033[0m({formatted_args})")
    # Optionally show extra metadata if provided (short preview)
    if metadata:
        try:
            preview = json.dumps(metadata)
            if len(preview) > 200:
                preview = preview[:200] + "..."
            print(f"  ⎿  Context: {preview}")
        except Exception:
            pass
    print("  ⎿  Approval Required")
    print(f"\n⚠ Do you want to allow tool '{tool_name}' with args: {formatted_args}?")
    print("  1. Yes")
    print("  2. Yes, and don't ask again for this session")
    print("  3. No, cancel this tool call")
    # Get user choice
    while True:
        try:
            choice = input("\nYour choice [1-3]: ").strip()
            if choice == "1":
                # approved: continue without additional echo (CLI runner will print)
                return True, False, None
            elif choice == "2":
                # approved and always-approve: continue without additional echo
                return True, True, None
            elif choice == "3":
                print("\n❌ Tool call cancelled by user")
                return False, False, "User rejected tool call"
            else:
                print("Invalid choice. Please enter 1, 2, or 3.")
        except KeyboardInterrupt:
            print("\n❌ Operation cancelled by user")
            return False, False, "Operation cancelled by user"


async def run_cli(config_path: str | None = None, args=None, spec=None) -> None:
    """
    Run an OmniAgents agent in CLI mode using the given YAML config and command-line args.
    """
    # Load environment variables
    # First try the standard behavior (search from cwd upward)
    try:
        load_dotenv()
    except Exception:
        pass

    # If we have a config path, also try loading .env from the config's directory tree
    # This handles the case where the user runs the command from a different directory
    if config_path:
        from pathlib import Path

        config_dir = Path(config_path).resolve().parent

        # Walk up from config directory to find .env file
        current = config_dir
        while current != current.parent:
            env_file = current / ".env"
            if env_file.exists():
                try:
                    load_dotenv(env_file, override=True)
                    break
                except Exception:
                    pass
            current = current.parent

    # Determine spec: use provided in-memory spec or load from YAML
    if spec is None:
        if not config_path:
            print("Error: config_path is required when spec is not provided.")
            sys.exit(1)
        spec = load_agent_spec_from_yaml(config_path)

    # Build settings dict from environment or defaults
    settings = {}
    for field in spec.settings_fields:
        env_val = os.getenv(field.key.upper())
        settings[field.key] = env_val if env_val not in (None, "") else field.default

    # Initialize debug system if not already initialized
    if args is not None:
        Debug.init(cli_flag=getattr(args, "debug", False))

    apply_cli_overrides(spec, args)

    # Note: OpenAI client configuration is now handled by the agent builder
    # which respects set_default_openai_client() if the user has set a custom client

    # Ensure model_name is set on spec
    if not spec.model_name:
        spec.model_name = settings.get("model_name")

    # Prepare MCP server context if used
    mcp_server_configs = []
    if spec.mcp_servers:
        try:
            mcp_server_configs = build_mcp_servers(spec, settings)
        except Exception as exc:
            print(f"Warning: Failed to build MCP servers: {exc}")
            mcp_server_configs = []

    # Choose approval callback (always determine one so we can pass it through)
    if spec.safe_agent_options.skip_approvals:
        approval_callback = SafeAgent.auto_approval_callback
    else:
        approval_callback = cli_approval_callback

    async def _chat_loop_stdio():
        """
        Chat loop for stdio MCP servers.

        For stdio servers, we don't pass MCP servers to create_unified_agent.
        Instead, AgentService (inside LocalAgent) will manage the MCP server
        lifecycle and rebuild stdio connections for each run automatically.
        This is handled by AgentService._build_agent_for_session which detects
        stdio servers and creates fresh connections per run.
        """
        from omniagents.core.tools.artifacts import set_artifact_callback

        def _cli_artifact(
            title: str,
            content: str,
            mode: str = "markdown",
            artifact_id: str | None = None,
        ) -> None:
            print(f"\n== {title} ==")
            print(content)
            print()

        set_artifact_callback(_cli_artifact)

        # Create agent once - AgentService will manage MCP servers at session level
        agent = await create_unified_agent(
            spec=spec,
            settings=settings,
            approval_callback=approval_callback,
            mcp_servers=None,  # Let AgentService handle MCP server lifecycle
        )

        # Intro
        print(f"\nStarting CLI chat for agent: {spec.name}")
        print(f"{spec.welcome_text}\n(Type 'exit' to quit)\n")

        session_id = getattr(args, "session_id", None) if args is not None else None
        local_session = Session(spec.project_slug, spec.agent_slug, session_id)
        conversation = list(local_session.history)
        if session_id is None:
            print(f"Session ID: {local_session.id}")

        while True:
            try:
                user_input = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting CLI chat.")
                break
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue
            if Debug.enabled():
                print(f"[DEBUG] User input: {user_input}")

            user_msg = {"role": "user", "content": user_input}
            conversation.append(user_msg)
            local_session.append_message(user_msg)
            new_input = conversation
            run_config = RunConfig()

            # Run the agent with this message
            result = agent.run_streamed(new_input, run_config=run_config)

            message_output = ""
            final_items = []
            async for event in result.stream_events():
                # Handle client_request events (e.g., tool approval requests)
                if event.type == "client_request":
                    if Debug.enabled():
                        print(f"[DEBUG] Received client_request event: {event.item}")

                    # Extract request details
                    request_data = event.item
                    request_id = request_data.get("request_id")
                    function = request_data.get("function")
                    request_args = request_data.get("args", {})

                    # Handle tool approval requests
                    if function == "ui.request_tool_approval":
                        tool = request_args.get("tool")
                        arguments = request_args.get("arguments")
                        metadata = request_args.get("metadata")

                        try:
                            # Call CLI approval callback
                            approved, always_approve, error_msg = (
                                await cli_approval_callback(tool, arguments, metadata)
                            )

                            # Send response back via RPC
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=True,
                                    result={
                                        "approved": approved,
                                        "always_approve": always_approve,
                                        "error_message": error_msg,
                                    },
                                )
                            elif Debug.enabled():
                                print(
                                    "[DEBUG] Warning: Could not send approval response (no RPC client)"
                                )
                        except Exception as e:
                            # Send error response
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=False,
                                    error={
                                        "message": str(e),
                                        "type": type(e).__name__,
                                    },
                                )
                            if Debug.enabled():
                                print(f"[DEBUG] Error handling approval request: {e}")
                    continue

                if event.type == "run_item_stream_event":
                    item = event.item

                    # Check if this is a client_request dict (not a typed item)
                    if (
                        isinstance(item, dict)
                        and "function" in item
                        and item.get("function") == "ui.request_tool_approval"
                    ):
                        if Debug.enabled():
                            print(
                                f"[DEBUG] Received client_request via run_item_stream_event: {item}"
                            )

                        request_id = item.get("request_id")
                        request_args = item.get("args", {})
                        tool = request_args.get("tool")
                        arguments = request_args.get("arguments")
                        metadata = request_args.get("metadata")

                        try:
                            # Call CLI approval callback
                            approved, always_approve, error_msg = (
                                await cli_approval_callback(tool, arguments, metadata)
                            )

                            # Send response back via RPC
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=True,
                                    result={
                                        "approved": approved,
                                        "always_approve": always_approve,
                                        "error_message": error_msg,
                                    },
                                )
                            elif Debug.enabled():
                                print(
                                    "[DEBUG] Warning: Could not send approval response (no RPC client)"
                                )
                        except Exception as e:
                            # Send error response
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=False,
                                    error={
                                        "message": str(e),
                                        "type": type(e).__name__,
                                    },
                                )
                            if Debug.enabled():
                                print(f"[DEBUG] Error handling approval request: {e}")
                        continue

                    final_items.append(item)
                    if not hasattr(item, "type"):
                        if Debug.enabled():
                            try:
                                it_repr = json.dumps(item)[:200]
                            except Exception:
                                it_repr = str(item)[:200]
                            print(
                                f"[DEBUG] Skipping non-item payload event: {event.type} item={it_repr}"
                            )
                        continue
                    if item.type == "tool_call_item":
                        inp = item.to_input_item() or {}
                        name = inp.get("name", "<tool>")
                        args_str = inp.get("arguments", "")
                        try:
                            args_dict = json.loads(args_str)
                            formatted_args = ", ".join(
                                f"{k}: {repr(v)}" for k, v in args_dict.items()
                            )
                        except Exception:
                            formatted_args = args_str
                        print(
                            f"\n\033[36m⏺\033[0m \033[1m{name}\033[0m({formatted_args})"
                        )
                    elif item.type == "tool_call_output_item":
                        if Debug.enabled():
                            print(f"[DEBUG] Tool output: {item.output}")
                    elif item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(item)
                        if Debug.enabled():
                            print(f"[DEBUG] Message building: {message_output}")
                    elif Debug.enabled():
                        print(
                            f"[DEBUG] Event: {event.type}, Item: {getattr(item, 'type', None)}"
                        )

            # Append assistant and tool items to conversation
            for item in final_items:
                if hasattr(item, "to_input_item"):
                    inp = item.to_input_item()
                    if inp:
                        conversation.append(inp)
                        local_session.append_message(inp)

            # Display response
            print(f"\n⏺ {message_output}\n")

    async def _chat_loop(mcp_servers):
        # Build unified agent using factory
        mcp_list = mcp_servers if mcp_servers else None

        # Pass approval callback unconditionally so prebuilt SafeAgent works even if safe=False
        approval_cb = approval_callback

        # Create agent through unified factory
        agent = await create_unified_agent(
            spec=spec,
            settings=settings,
            approval_callback=approval_cb,
            mcp_servers=mcp_list,
        )

        from omniagents.core.tools.artifacts import set_artifact_callback

        def _cli_artifact(
            title: str,
            content: str,
            mode: str = "markdown",
            artifact_id: str | None = None,
        ) -> None:
            print(f"\n== {title} ==")
            print(content)
            print()

        set_artifact_callback(_cli_artifact)

        # Intro
        print(f"\nStarting CLI chat for agent: {spec.name}")
        print(f"{spec.welcome_text}\n(Type 'exit' to quit)\n")

        session_id = getattr(args, "session_id", None) if args is not None else None
        local_session = Session(spec.project_slug, spec.agent_slug, session_id)
        conversation = list(local_session.history)
        if session_id is None:
            print(f"Session ID: {local_session.id}")
        while True:
            try:
                user_input = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting CLI chat.")
                break
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue
            if Debug.enabled():
                print(f"[DEBUG] User input: {user_input}")

            user_msg = {"role": "user", "content": user_input}
            conversation.append(user_msg)
            local_session.append_message(user_msg)
            new_input = conversation
            run_config = RunConfig()
            # All unified agents support run_streamed
            result = agent.run_streamed(new_input, run_config=run_config)

            message_output = ""
            final_items = []
            async for event in result.stream_events():
                # Handle client_request events (e.g., tool approval requests)
                if event.type == "client_request":
                    if Debug.enabled():
                        print(f"[DEBUG] Received client_request event: {event.item}")

                    # Extract request details
                    request_data = event.item
                    request_id = request_data.get("request_id")
                    function = request_data.get("function")
                    request_args = request_data.get("args", {})

                    # Handle tool approval requests
                    if function == "ui.request_tool_approval":
                        tool = request_args.get("tool")
                        arguments = request_args.get("arguments")
                        metadata = request_args.get("metadata")

                        try:
                            # Call CLI approval callback
                            approved, always_approve, error_msg = (
                                await cli_approval_callback(tool, arguments, metadata)
                            )

                            # Send response back via RPC
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=True,
                                    result={
                                        "approved": approved,
                                        "always_approve": always_approve,
                                        "error_message": error_msg,
                                    },
                                )
                            elif Debug.enabled():
                                print(
                                    "[DEBUG] Warning: Could not send approval response (no RPC client)"
                                )
                        except Exception as e:
                            # Send error response
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=False,
                                    error={"message": str(e), "type": type(e).__name__},
                                )
                            if Debug.enabled():
                                print(f"[DEBUG] Error handling approval request: {e}")
                    continue

                if event.type == "run_item_stream_event":
                    item = event.item

                    # Check if this is a client_request dict (not a typed item)
                    if (
                        isinstance(item, dict)
                        and "function" in item
                        and item.get("function") == "ui.request_tool_approval"
                    ):
                        if Debug.enabled():
                            print(
                                f"[DEBUG] Received client_request via run_item_stream_event: {item}"
                            )

                        request_id = item.get("request_id")
                        request_args = item.get("args", {})
                        tool = request_args.get("tool")
                        arguments = request_args.get("arguments")
                        metadata = request_args.get("metadata")

                        try:
                            # Call CLI approval callback
                            approved, always_approve, error_msg = (
                                await cli_approval_callback(tool, arguments, metadata)
                            )

                            # Send response back via RPC
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=True,
                                    result={
                                        "approved": approved,
                                        "always_approve": always_approve,
                                        "error_message": error_msg,
                                    },
                                )
                            elif Debug.enabled():
                                print(
                                    "[DEBUG] Warning: Could not send approval response (no RPC client)"
                                )
                        except Exception as e:
                            # Send error response
                            if hasattr(agent, "_rpc_client") and agent._rpc_client:
                                await agent._rpc_client.other.client_response(
                                    request_id=request_id,
                                    ok=False,
                                    error={"message": str(e), "type": type(e).__name__},
                                )
                            if Debug.enabled():
                                print(f"[DEBUG] Error handling approval request: {e}")
                        continue

                    final_items.append(item)
                    # Some backends (e.g., token usage/approval events) may deliver
                    # plain dict payloads via RunItemStreamEvent. Guard against those
                    # so we only process well-formed run items that expose a 'type'.
                    if not hasattr(item, "type"):
                        if Debug.enabled():
                            try:
                                it_repr = json.dumps(item)[:200]
                            except Exception:
                                it_repr = str(item)[:200]
                            print(
                                f"[DEBUG] Skipping non-item payload event: {event.type} item={it_repr}"
                            )
                        continue
                    # Tool call: always display in CLI
                    if item.type == "tool_call_item":
                        inp = item.to_input_item() or {}
                        name = inp.get("name", "<tool>")
                        args_str = inp.get("arguments", "")
                        # Format arguments dict if JSON
                        try:
                            args_dict = json.loads(args_str)
                            formatted_args = ", ".join(
                                f"{k}: {repr(v)}" for k, v in args_dict.items()
                            )
                        except Exception:
                            formatted_args = args_str
                        # Emit colored, bold tool call, with leading newline for spacing
                        print(
                            f"\n\033[36m⏺\033[0m \033[1m{name}\033[0m({formatted_args})"
                        )
                    # Tool output: optionally debug-print
                    elif item.type == "tool_call_output_item":
                        if Debug.enabled():
                            print(f"[DEBUG] Tool output: {item.output}")
                    # Chat message content
                    elif item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(item)
                        if Debug.enabled():
                            print(f"[DEBUG] Message building: {message_output}")
                    # Other events: debug only
                    elif Debug.enabled():
                        print(
                            f"[DEBUG] Event: {event.type}, Item: {getattr(item, 'type', None)}"
                        )

            # Append assistant and tool items to conversation
            for item in final_items:
                if hasattr(item, "to_input_item"):
                    inp = item.to_input_item()
                    if inp:
                        conversation.append(inp)
                        local_session.append_message(inp)

            # Display response
            print(f"\n⏺ {message_output}\n")

    # Check if any MCP servers use stdio transport
    # Stdio servers use AnyIO task groups that cannot be safely reused across asyncio tasks
    has_stdio = False
    if mcp_server_configs and spec.mcp_servers:
        has_stdio = any(
            hasattr(cfg, "type") and str(getattr(cfg, "type", "")).lower() == "stdio"
            for cfg in spec.mcp_servers
        )

    # Run chat loop within MCP context if configured
    if mcp_server_configs:
        if has_stdio:
            # For stdio servers, we cannot reuse the AsyncExitStack across messages
            # because each message may create new asyncio tasks, causing AnyIO violations.
            # We rebuild server instances per message to avoid AnyIO task group reuse.
            await _chat_loop_stdio()
        else:
            # For HTTP/SSE servers, we can safely reuse connections
            async with AsyncExitStack() as stack:
                active_servers = []
                for server in mcp_server_configs:
                    if hasattr(server, "__aenter__") and hasattr(server, "__aexit__"):
                        active_servers.append(await stack.enter_async_context(server))
                    else:
                        await server.connect()
                        stack.push_async_callback(server.cleanup)
                        active_servers.append(server)
                await _chat_loop(active_servers)
    else:
        # No MCP; run chat loop directly
        await _chat_loop([])
