"""Local agent implementation using in-memory RPC."""

import asyncio
import secrets
from typing import Optional, Any, List, Dict
from contextlib import suppress

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import AgentHostResources
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
            spec.get_agent_instructions()
            if spec.get_agent_instructions is not None
            else ""
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

        # Create custom build_agent function that captures settings.
        # Tool approval is no longer wired into the agent here — the
        # service-side configure_tool_approval pass handles it once the
        # session exists. Any LocalAgent ``approval_callback`` is now routed
        # through the JSON-RPC wire format (tool_approval_requested event +
        # tool_approval_response RPC).
        settings = self.settings
        mcp_servers = self.mcp_servers

        # Store the original builder before replacing it
        self._original_builder = self.spec.build_agent

        async def custom_build_agent(stub_settings, mcp_server, stub_app):
            runtime_spec = getattr(stub_app, "spec", self.spec)
            # If spec already has a custom builder, use it directly.
            if self._original_builder:
                return await self._original_builder(
                    settings,
                    mcp_servers[0] if mcp_servers else mcp_server,
                    stub_app,
                )

            # Otherwise use the default builder — WITH the session the
            # service put on the stub app. Dropping it here cost workers
            # every session-scoped setting: the model override and,
            # critically, the provider-aware gates (a codex-bound worker
            # kept `prompt_cache_retention` and 400ed on every run).
            from omniagents.core.agents.builder import _default_build_agent

            mcp_to_use = mcp_servers if mcp_servers else mcp_server
            return await _default_build_agent(
                settings,
                mcp_to_use,
                runtime_spec,
                None,
                session=getattr(stub_app, "session", None),
            )

        # Set the custom builder on the spec
        self.spec.build_agent = custom_build_agent

        # Connect server transport
        await self._server_transport.connect()

        # Create the channel first
        from omniagents.rpc.generated.gui_v1 import SERVER_NOTIFICATION_MODELS
        from omniagents.rpc.server import JsonRpcChannel

        channel = JsonRpcChannel(
            self._server_transport,
            notification_models=SERVER_NOTIFICATION_MODELS,
            enforce_initialization=True,
        )

        # Create agent service with custom spec and set channel immediately
        self._agent_service = AgentService(
            self.spec,
            AgentHostResources.create(f"local-agent-{secrets.token_hex(8)}"),
        )
        self._agent_service.channel = channel

        # The server app runs these registrars for the orchestrator's
        # service at boot; a nested LocalAgent service (workers) never
        # passed through that path, so workers ran with NO server
        # functions and NO runtime hooks — meaning no /compact command
        # and no reactive context-overflow recovery. Mirror the app's
        # best-effort semantics here.
        for registrar_name in ("register_server_functions", "register_runtime_hooks"):
            registrar = getattr(self.spec, registrar_name, None)
            if callable(registrar):
                try:
                    registrar(self._agent_service)
                except Exception as e:
                    print(f"Warning: {registrar_name} failed for local agent: {e}")

        # Create and start server endpoint
        from omniagents.rpc import JsonRpcEndpoint
        from omniagents.rpc.server import GUI_RPC_METHODS

        # The in-process client is RemoteAgent's JsonRpcClient, which runs
        # the GUI initialize/initialized handshake by default — the endpoint
        # must speak it too, mirroring the server app's wiring.
        endpoint = JsonRpcEndpoint(
            self._agent_service,
            allowed_methods=GUI_RPC_METHODS,
            gui_handshake=True,
            server_capabilities={
                "realtime": False,
                "mcp_apps": True,
                "client_functions": True,
                "approvals": True,
                "artifacts": True,
                "replay": True,
                "terminal": getattr(self._agent_service, "terminal_manager", None)
                is not None,
            },
        )

        # Start server task
        self._server_task = asyncio.create_task(self._run_server(endpoint, channel))

        # Don't start the RPC client yet - use lazy initialization like RemoteAgent
        self._initialized = True

    async def _ensure_client(self):
        """Ensure RPC client is connected (lazy initialization like RemoteAgent)."""
        if self._client is None:
            # Make sure we're initialized first
            await self.initialize()
            assert self._client_transport is not None  # set by initialize()

            # Connect client transport
            await self._client_transport.connect()

            # Set transport for RemoteAgent to use
            self._transport = self._client_transport

            # Call parent to create client
            await super()._ensure_client()

    async def _run_server(self, endpoint, channel):
        """Run the server side of the RPC connection."""
        try:
            server_transport = self._server_transport
            assert server_transport is not None  # set by initialize()
            while server_transport.is_connected():
                try:
                    data = await server_transport.receive_json()
                except TransportClosedError:
                    break
                except Exception:
                    continue

                await endpoint._process_message(channel, data)
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
