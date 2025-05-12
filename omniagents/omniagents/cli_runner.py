import os
import sys
import json
import asyncio
from types import SimpleNamespace
from datetime import datetime
from typing import List, Optional, Tuple, Awaitable

from dotenv import load_dotenv
from agents import Runner, RunConfig, ItemHelpers
from agents.mcp import MCPServerSse

from omniagents.core.config_loader import load_agent_spec_from_yaml
from omniagents.core.app_base import _default_build_agent
from omniagents.core.safe_agent import SafeAgent

ApprovalResult = Tuple[bool, bool, Optional[str]]


async def cli_approval_callback(tool_name: str, formatted_args: str) -> ApprovalResult:
    """
    CLI implementation for tool approval.
    Returns a tuple: (approved: bool, always_approve: bool, error_message: Optional[str])
    """
    # Display the tool call and approval request
    print(f"\n\033[33m⏺\033[0m \033[1m{tool_name}\033[0m({formatted_args})")
    print("  ⎿  Approval Required")
    print(f"\n⚠ Do you want to allow tool '{tool_name}' with args: {formatted_args}?" )
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


async def run_cli(
    config_path: str | None = None,
    args=None,
    spec=None
) -> None:
    """
    Run an OmniAgents agent in CLI mode using the given YAML config and command-line args.
    """
    # Load environment variables
    try:
        load_dotenv()
    except Exception:
        pass

    # Load environment variables
    try:
        load_dotenv()
    except Exception:
        pass

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

    # Apply CLI overrides if args provided
    spec.debug_flag = getattr(args, "debug", False) if args is not None else spec.debug_flag
    if args is not None:
        if getattr(args, "no_approval", False):
            spec.use_safe_agent = False
        if getattr(args, "skip_approvals", False):
            spec.safe_agent_options.skip_approvals = True
        if getattr(args, "no_halt", False):
            spec.safe_agent_options.halt_on_rejection = False
        if getattr(args, "pre_approve", None):
            pre = args.pre_approve.split(",")
            spec.safe_agent_options.safe_tool_names.extend([t.strip() for t in pre if t.strip()])
        if getattr(args, "safe_patterns", None):
            pats = args.safe_patterns.split(",")
            spec.safe_agent_options.safe_tool_patterns.extend([p.strip() for p in pats if p.strip()])

    # Configure OpenAI client
    api_base = settings.get("openai_api_base")
    api_key = settings.get("openai_api_key")
    if not api_key:
        print("Error: OPENAI_API_KEY is required for CLI mode.")
        sys.exit(1)

    # Ensure model_name is set on spec
    if not spec.model_name:
        spec.model_name = settings.get("model_name")

    # Prepare MCP server context if used
    mcp_ctx = None
    if spec.uses_mcp:
        mcp_url = settings.get(spec.mcp_url_setting_key)
        if not mcp_url:
            print(f"Warning: MCP URL not set (environment '{spec.mcp_url_setting_key.upper()}').")
            mcp_url = ""
        mcp_params = {
            "url": mcp_url,
            "timeout": 30,
            "sse_read_timeout": 600,
        }
        token = settings.get(spec.mcp_token_setting_key)
        if token:
            mcp_params["headers"] = {"Authorization": f"Bearer {token}"}
        if spec.debug_flag:
            print(f"MCP parameters: {mcp_params}")
        mcp_ctx = MCPServerSse(name=f"{spec.name} MCP Server", params=mcp_params)

    # Choose approval callback
    if spec.safe_agent_options.skip_approvals:
        approval_callback = SafeAgent.auto_approval_callback
    else:
        approval_callback = cli_approval_callback

    async def _chat_loop(mcp_server):
        # Build base Agent
        from agents import Agent
        from agents.model_settings import ModelSettings

        # --- Remote Agent Handling ---
        if spec.remote_ws_url:
            from omniagents.remote_proxy import RemoteAgentProxy
            token = os.getenv(spec.remote_auth_token_key) if spec.remote_auth_token_key else None
            base_agent = RemoteAgentProxy(spec.remote_ws_url, token)
            tools = []
            instructions = ""
        else:
            instructions = spec.get_agent_instructions()
            tools = spec.available_tools
        mcp_list = [mcp_server] if mcp_server else []
        # ModelSettings: use tool_choice when tools are available
        if tools:
            model_settings = ModelSettings(tool_choice="auto")
        else:
            model_settings = ModelSettings()
        if not spec.remote_ws_url:
            base_agent = Agent(
                name=spec.name,
                tools=tools,
                instructions=instructions,
                model=spec.model_name,
                model_settings=model_settings,
                mcp_servers=mcp_list,
            )
        # Wrap with SafeAgent if configured
        if spec.remote_ws_url:
            agent = base_agent  # Remote proxy, no safe wrapper
        elif spec.use_safe_agent:
            wrapped_tools = await base_agent.get_all_tools()
            agent = SafeAgent(
                name=base_agent.name,
                tools=wrapped_tools,
                instructions=base_agent.instructions,
                model=base_agent.model,
                model_settings=base_agent.model_settings,
                mcp_servers=base_agent.mcp_servers,
                safe_tool_names=spec.safe_agent_options.safe_tool_names,
                safe_tool_patterns=spec.safe_agent_options.safe_tool_patterns,
                skip_approvals=spec.safe_agent_options.skip_approvals,
                halt_on_rejection=spec.safe_agent_options.halt_on_rejection,
                approval_callback=approval_callback,
                debug_mode=spec.debug_flag,
            )
        else:
            agent = base_agent

        # Intro
        print(f"\nStarting CLI chat for agent: {spec.name}")
        print(f"{spec.welcome_text}\n(Type 'exit' to quit)\n")

        conversation = []
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
            if spec.debug_flag:
                print(f"[DEBUG] User input: {user_input}")

            new_input = conversation + [{"role": "user", "content": user_input}]
            run_config = RunConfig()
            result = Runner.run_streamed(agent, new_input, max_turns=spec.max_turns, run_config=run_config)

            message_output = ""
            final_items = []
            async for event in result.stream_events():
                if event.type == "run_item_stream_event":
                    item = event.item
                    final_items.append(item)
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
                        print(f"\n\033[36m⏺\033[0m \033[1m{name}\033[0m({formatted_args})")
                    # Tool output: optionally debug-print
                    elif item.type == "tool_call_output_item":
                        if spec.debug_flag:
                            print(f"[DEBUG] Tool output: {item.output}")
                    # Chat message content
                    elif item.type == "message_output_item":
                        message_output = ItemHelpers.text_message_output(item)
                        if spec.debug_flag:
                            print(f"[DEBUG] Message building: {message_output}")
                    # Other events: debug only
                    elif spec.debug_flag:
                        print(f"[DEBUG] Event: {event.type}, Item: {getattr(item, 'type', None)}")

            # Append assistant and tool items to conversation
            for item in final_items:
                if hasattr(item, "to_input_item"):
                    inp = item.to_input_item()
                    if inp:
                        conversation.append(inp)

            # Display response
            print(f"\n⏺ {message_output}\n")

    # Run chat loop within MCP context if configured
    if mcp_ctx:
        # Use the MCPServerSse as an async context manager
        async with mcp_ctx as server:
            await _chat_loop(server)
    else:
        # No MCP; run chat loop directly
        await _chat_loop(None)