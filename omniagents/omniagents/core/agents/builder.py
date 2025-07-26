"""Core agent building functionality."""

import os
from typing import Dict, Any, Optional, TYPE_CHECKING

from omniagents.core.utils.logging import get_logger

if TYPE_CHECKING:
    from agents.mcp import MCPServerSse
    from omniagents.core.agents.specs import AgentSpec, AgentLike

logger = get_logger(__name__)


async def _default_build_agent(
    settings: Dict[str, Any],
    mcp_server: Optional["MCPServerSse"],
    spec: "AgentSpec",
    approval_callback: Optional[Any] = None,
) -> "AgentLike":
    """Internal default function to build the agent instance."""
    from agents import Agent
    from agents.model_settings import ModelSettings
    from openai import AsyncOpenAI

    logger.info("Building agent using internal default builder...")
    
    # Get core config from spec/settings
    agent_name = spec.name
    model_name = spec.model_name or settings.get("model_name", "gpt-4o")
    instructions = spec.get_agent_instructions()
    tools = spec.available_tools
    mcp_servers_list = [mcp_server] if mcp_server else []
    
    # Configure OpenAI client
    api_key_from_settings = settings.get("openai_api_key")
    api_key_from_env = os.getenv("OPENAI_API_KEY")
    
    api_key = api_key_from_settings or api_key_from_env
    if not api_key:
        raise ValueError(
            "API key must be provided either in settings or OPENAI_API_KEY environment variable"
        )
    
    api_base = settings.get("openai_api_base", None)
    
    # Build client config
    client_config = {"api_key": api_key}
    if api_base:
        client_config["base_url"] = api_base
    
    # Create AsyncOpenAI client
    client = AsyncOpenAI(**client_config)
    
    # ModelSettings: use tool_choice when tools are available
    if tools:
        model_settings = ModelSettings(tool_choice="auto")
    else:
        model_settings = ModelSettings()
    
    # Build the agent
    base_agent = Agent(
        name=agent_name,
        instructions=instructions,
        tools=tools,
        model=model_name,
        model_settings=model_settings,
        mcp_servers=mcp_servers_list,
    )
    
    # Optionally wrap with SafeAgent
    if spec.use_safe_agent and approval_callback:
        from omniagents.core.agents.safe import SafeAgent
        
        opts = spec.safe_agent_options
        safe_agent = SafeAgent(
            name=base_agent.name,
            tools=await base_agent.get_all_tools(),
            instructions=base_agent.instructions,
            model=base_agent.model,
            model_settings=base_agent.model_settings,
            mcp_servers=base_agent.mcp_servers,
            approval_callback=approval_callback,
            safe_tool_names=opts.safe_tool_names,
            safe_tool_patterns=opts.safe_tool_patterns,
            skip_approvals=opts.skip_approvals,
            halt_on_rejection=opts.halt_on_rejection,
            debug_mode=spec.debug_flag,
        )
        return safe_agent
    
    return base_agent