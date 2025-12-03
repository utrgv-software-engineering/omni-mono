"""
Agent factory for creating unified agents.

This module provides a factory function that creates the appropriate
agent implementation (local or remote) based on the agent specification.
"""

import os
from typing import Optional, Dict, Any, List

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.interfaces.agent import UnifiedAgent
from omniagents.rpc.agents import LocalAgent, RemoteAgent


async def create_unified_agent(
    spec: AgentSpec,
    settings: Optional[Dict[str, Any]] = None,
    approval_callback: Optional[Any] = None,
    mcp_servers: Optional[List[Any]] = None,
) -> UnifiedAgent:
    """
    Create a unified agent based on spec configuration.

    This factory function creates either a remote agent (via WebSocket)
    or a local agent (via in-memory RPC) based on the presence of
    spec.remote_ws_url.

    Args:
        spec: Agent specification
        settings: Optional settings dict (for API keys, etc.)
        approval_callback: Optional callback for tool approval (local agents only)
        mcp_servers: Optional list of MCP server instances (local agents only)

    Returns:
        A UnifiedAgent instance (either RemoteAgent or LocalAgent)
    """
    if spec.remote_ws_url:
        # Remote agent via WebSocket
        token = None
        if spec.remote_auth_token_key:
            token = os.getenv(spec.remote_auth_token_key)

        # Pass both variables and context from spec to RemoteAgent
        agent = RemoteAgent(
            spec.remote_ws_url, token, variables=spec.variables, context=spec.context
        )

        # Override properties from spec if provided
        if spec.name:
            agent.name = spec.name
        if spec.model_name:
            agent.model = spec.model_name

        return agent
    else:
        # Local agent via in-memory RPC
        # First ensure we have necessary settings
        if settings is None:
            settings = {}

        # Build settings from environment if not provided
        for field in spec.settings_fields:
            if field.key not in settings:
                env_val = os.getenv(field.key.upper())
                settings[field.key] = (
                    env_val if env_val not in (None, "") else field.default
                )

        # Ensure model_name is set
        if not spec.model_name:
            spec.model_name = settings.get("model_name", "gpt-4")

        # Create local agent adapter with settings, approval callback, and MCP servers
        adapter = LocalAgent(spec, settings, approval_callback, mcp_servers)

        return adapter


def is_remote_agent(agent: UnifiedAgent) -> bool:
    """
    Check if an agent is a remote agent (not a local agent).

    Args:
        agent: The agent to check

    Returns:
        True if the agent is a RemoteAgent but not a LocalAgent, False otherwise
    """
    # LocalAgent is a subclass of RemoteAgent, so we need to exclude it
    return isinstance(agent, RemoteAgent) and not isinstance(agent, LocalAgent)


def is_local_agent(agent: UnifiedAgent) -> bool:
    """
    Check if an agent is a local agent.

    Args:
        agent: The agent to check

    Returns:
        True if the agent is a LocalAgent, False otherwise
    """
    return isinstance(agent, LocalAgent)
