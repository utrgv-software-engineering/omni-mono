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
    from agents import Agent, set_default_openai_key
    from agents.model_settings import ModelSettings
    from agents.models._openai_shared import get_default_openai_client

    logger.info("Building agent using internal default builder...")
    
    # Set global approval callback and safe tools if we have them
    # This will be available to all SafeAgent instances including handoffs
    if approval_callback:
        from omniagents.core.agents.safe import SafeAgent
        SafeAgent.set_global_approval_callback(approval_callback)
    
    # Set global safe tool settings
    if spec.safe_agent_options:
        from omniagents.core.agents.safe import SafeAgent
        SafeAgent.set_global_safe_tools(
            tool_names=spec.safe_agent_options.safe_tool_names,
            tool_patterns=spec.safe_agent_options.safe_tool_patterns
        )
    
    # Get core config from spec/settings
    agent_name = spec.name
    model_name = spec.model_name or settings.get("model_name", "gpt-4o")
    instructions = spec.get_agent_instructions()
    tools = spec.available_tools
    mcp_servers_list = [mcp_server] if mcp_server else []
    handoff_description = spec.handoff_description
    reset_tool_choice = spec.reset_tool_choice
    tool_use_behavior = spec.tool_use_behavior
    
    # MCP config - merge spec config with any defaults
    mcp_config = spec.mcp_config if spec.mcp_config else {}
    
    # Only set the API key from settings if no custom client has been set
    # This preserves any client set via set_default_openai_client() (e.g., AsyncAzureOpenAI)
    if get_default_openai_client() is None:
        api_key_from_settings = settings.get("openai_api_key")
        if api_key_from_settings:
            logger.debug("No custom client set, using API key from settings")
            set_default_openai_key(api_key_from_settings, use_for_tracing=False)
    else:
        logger.debug("Custom client already set, skipping API key configuration")
    
    # Note: We don't create a client here anymore. The SDK will handle client resolution
    # through its provider chain, which respects set_default_openai_client() if called
    
    # Build ModelSettings from spec config or use defaults
    if spec.model_settings:
        # Create ModelSettings from the dictionary
        model_settings_kwargs = spec.model_settings.copy()
        # If tools are available and tool_choice not explicitly set, default to "auto"
        if tools and 'tool_choice' not in model_settings_kwargs:
            model_settings_kwargs['tool_choice'] = 'auto'
        model_settings = ModelSettings(**model_settings_kwargs)
    elif tools:
        # Default settings with tool_choice when tools are available
        model_settings = ModelSettings(tool_choice="auto")
    else:
        # Default settings without tools
        model_settings = ModelSettings()
    
    # Build the agent
    base_agent = Agent(
        name=agent_name,
        instructions=instructions,
        tools=tools,
        model=model_name,
        model_settings=model_settings,
        mcp_servers=mcp_servers_list,
        mcp_config=mcp_config,
        handoff_description=handoff_description,
        reset_tool_choice=reset_tool_choice,
        tool_use_behavior=tool_use_behavior,
    )
    
    # Optionally wrap with SafeAgent
    if spec.use_safe_agent and approval_callback:
        from omniagents.core.agents.safe import SafeAgent
        
        opts = spec.safe_agent_options
        safe_agent = SafeAgent(
            name=base_agent.name,
            tools=base_agent.tools,  # Use static tools at build time
            instructions=base_agent.instructions,
            model=base_agent.model,
            model_settings=base_agent.model_settings,
            mcp_servers=base_agent.mcp_servers,
            mcp_config=base_agent.mcp_config,
            handoff_description=base_agent.handoff_description,
            reset_tool_choice=base_agent.reset_tool_choice,
            tool_use_behavior=base_agent.tool_use_behavior,
            approval_callback=approval_callback,
            safe_tool_names=opts.safe_tool_names,
            safe_tool_patterns=opts.safe_tool_patterns,
            skip_approvals=opts.skip_approvals,
            halt_on_rejection=opts.halt_on_rejection,
            debug_mode=spec.debug_flag,
        )
        return safe_agent
    
    return base_agent