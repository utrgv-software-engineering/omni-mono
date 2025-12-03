"""Local agent implementation using in-memory RPC."""

import asyncio
from typing import Optional, Any, List, Dict
from contextlib import suppress

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.transport.inmemory import InMemoryTransport
from omniagents.rpc.transport import TransportClosedError
from .remote import RemoteAgent


class LocalAgent(RemoteAgent):
    """
    Local agent implementation using in-memory RPC.

    This adapter creates an in-memory RPC connection to an AgentService,
    providing the same interface as RemoteAgent but for local agents.
    """

    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        approval_callback: Optional[Any] = None,
        mcp_servers: Optional[List[Any]] = None,
    ):
        """
        Initialize local agent.

        Args:
            spec: Agent specification
            settings: Settings dictionary
            approval_callback: Optional callback for tool approvals
            mcp_servers: Optional MCP servers
        """
        # Initialize RemoteAgent with no URL (we'll use custom transport)
        # Pass context and variables from spec to parent
        super().__init__(
            url=None,
            variables=getattr(spec, "variables", None),
            context=getattr(spec, "context", None),
        )

        self.spec = spec
        self.settings = settings or {}
        self.approval_callback = approval_callback
        self.mcp_servers = mcp_servers or []

        # RPC infrastructure (server side)
        self._agent_service: Optional[AgentService] = None
        self._client_transport: Optional[InMemoryTransport] = None
        self._server_transport: Optional[InMemoryTransport] = None
        self._initialized = False

        # Set agent properties from spec
        self.name = spec.name
        self.instructions = (
            spec.get_agent_instructions() if spec.get_agent_instructions else ""
        )
        self.model = spec.model_name or "gpt-4"
        self.tools: List[Any] = []
        self.model_settings: Dict[str, Any] = {}
        self.handoffs: List[Any] = []
        self.input_type = str
        self.output_type = str

    async def initialize(self):
        """Initialize the RPC connection."""
        if self._initialized:
            return

        # Create in-memory transport pair
        self._client_transport, self._server_transport = InMemoryTransport.create_pair()

        # Create custom build_agent function that captures settings and callbacks
        settings = self.settings
        approval_callback = self.approval_callback
        mcp_servers = self.mcp_servers

        # Store the original builder before replacing it
        self._original_builder = self.spec.build_agent

        async def custom_build_agent(stub_settings, mcp_server, stub_app):
            # Set global approval callback and safe tools if we have them
            from omniagents.core.agents.safe import SafeAgent

            if self.spec.safe_agent_options:
                SafeAgent.set_global_safe_tools(
                    tool_names=self.spec.safe_agent_options.safe_tool_names,
                    tool_patterns=self.spec.safe_agent_options.safe_tool_patterns,
                )

            # If spec already has a custom builder, use it
            if self._original_builder:
                agent = await self._original_builder(
                    settings, mcp_servers[0] if mcp_servers else mcp_server, self.spec
                )

                # Check if agent is already a SafeAgent that needs callback
                if isinstance(agent, SafeAgent) and approval_callback:
                    # Set the instance callback as well for this specific agent
                    agent.set_approval_callback(approval_callback)

                # If safe mode is enabled and agent is not already a SafeAgent, wrap it
                elif (
                    self.spec.use_safe_agent
                    and approval_callback
                    and not isinstance(agent, SafeAgent)
                ):
                    opts = self.spec.safe_agent_options

                    # Create SafeAgent, passing through all attributes via **kwargs
                    # SafeAgent's __init__ accepts **kwargs and passes them to Agent.__init__
                    safe_agent = SafeAgent(
                        name=agent.name,
                        tools=agent.tools,  # Use static tools at build time
                        instructions=agent.instructions,
                        model=agent.model,
                        model_settings=getattr(agent, "model_settings", None),
                        mcp_servers=getattr(agent, "mcp_servers", [])
                        or [],  # Ensure it's a list, not None
                        # Pass through other Agent attributes
                        handoffs=getattr(agent, "handoffs", []),
                        handoff_description=getattr(agent, "handoff_description", None),
                        prompt=getattr(agent, "prompt", None),
                        # SafeAgent specific parameters
                        approval_callback=approval_callback,
                        safe_tool_names=opts.safe_tool_names,
                        safe_tool_patterns=opts.safe_tool_patterns,
                        skip_approvals=opts.skip_approvals,
                        halt_on_rejection=opts.halt_on_rejection,
                    )
                    return safe_agent

                return agent

            # Otherwise use the default builder
            from omniagents.core.agents.builder import _default_build_agent

            # Use our captured settings instead of stub_settings
            # Use our captured MCP servers, preferring the first one if available
            if mcp_servers:
                mcp_to_use = mcp_servers
            else:
                mcp_to_use = mcp_server

            return await _default_build_agent(
                settings, mcp_to_use, self.spec, approval_callback
            )

        # Set the custom builder on the spec
        self.spec.build_agent = custom_build_agent

        # Connect server transport
        await self._server_transport.connect()

        # Create the channel first
        from omniagents.rpc.server import JsonRpcChannel

        channel = JsonRpcChannel(self._server_transport)

        # Create agent service with custom spec and set channel immediately
        self._agent_service = AgentService(self.spec)
        self._agent_service.channel = channel

        # Create and start server endpoint
        from omniagents.rpc import JsonRpcEndpoint

        endpoint = JsonRpcEndpoint(self._agent_service)

        # Start server task
        self._server_task = asyncio.create_task(self._run_server(endpoint, channel))

        # Don't start the RPC client yet - use lazy initialization like RemoteAgent
        self._initialized = True

    async def _ensure_client(self):
        """Ensure RPC client is connected (lazy initialization like RemoteAgent)."""
        if self._client is None:
            # Make sure we're initialized first
            await self.initialize()

            # Connect client transport
            await self._client_transport.connect()

            # Set transport for RemoteAgent to use
            self._transport = self._client_transport

            # Call parent to create client
            await super()._ensure_client()

    async def _run_server(self, endpoint, channel):
        """Run the server side of the RPC connection."""
        try:
            while self._server_transport.is_connected():
                try:
                    data = await self._server_transport.receive_json()
                except TransportClosedError:
                    break
                except Exception:
                    continue

                # Process RPC call
                call_id = data.get("id")
                method = data.get("method")
                params = data.get("params") or data.get("args") or {}

                if method:
                    try:
                        func = getattr(endpoint.methods, method)
                        result = await func(**params)
                        if call_id is not None:
                            await channel.send_result(call_id, result)
                    except Exception as e:
                        if call_id is not None:
                            error_response = {
                                "jsonrpc": "2.0",
                                "id": call_id,
                                "error": {
                                    "code": -32603,
                                    "message": str(e),
                                    "data": {
                                        "type": e.__class__.__name__,
                                        "method": method,
                                    },
                                },
                            }
                            await self._server_transport.send_json(error_response)
        except Exception:
            # Server loop ended
            pass

    async def cleanup(self):
        """Clean up resources."""
        self._initialized = False

        # Restore original builder
        if hasattr(self, "_original_builder") and self.spec:
            self.spec.build_agent = self._original_builder

        # Cancel server task
        if hasattr(self, "_server_task"):
            self._server_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._server_task

        # Clean up RPC client via parent
        if self._client:
            await self._client.__aexit__(None, None, None)

        # Disconnect transports
        if self._client_transport:
            await self._client_transport.disconnect()
        if self._server_transport:
            await self._server_transport.disconnect()

    # Context manager support for resource cleanup
    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.cleanup()
