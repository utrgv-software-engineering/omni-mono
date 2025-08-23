"""Local agent implementation using in-memory RPC."""

import asyncio
from typing import Optional, Any, List, Dict
from contextlib import suppress

from omniagents.core.interfaces.agent import UnifiedAgentBase
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc import RpcMethodsBase, JsonRpcClient
from omniagents.rpc.transport.inmemory import InMemoryTransport
from omniagents.rpc.transport import TransportClosedError
from ._events import map_notification_to_event


class _ClientEventMethods(RpcMethodsBase):
    """RPC methods for receiving events from the local agent service."""
    
    def __init__(self, event_queue: asyncio.Queue):
        super().__init__()
        self._event_queue = event_queue
    
    async def _push_event(self, method: str, **params) -> bool:
        """Push event to the queue."""
        await self._event_queue.put({"method": method, "params": params})
        return True
    
    # Event notification methods
    async def tool_called(self, **params):
        return await self._push_event("tool_called", **params)
    
    async def tool_result(self, **params):
        return await self._push_event("tool_result", **params)
    
    async def message_output(self, **params):
        return await self._push_event("message_output", **params)
    
    async def token(self, **params):
        return await self._push_event("token", **params)
    
    async def run_end(self, **params):
        return await self._push_event("run_end", **params)
    
    async def tool_approval_request(self, **params):
        return await self._push_event("tool_approval_request", **params)


class LocalAgent(UnifiedAgentBase):
    """
    Local agent implementation using in-memory RPC.
    
    This adapter creates an in-memory RPC connection to an AgentService,
    providing the same interface as RemoteAgent but for local agents.
    """
    
    def __init__(self, spec: AgentSpec, settings: Optional[Dict[str, Any]] = None, 
                 approval_callback: Optional[Any] = None, mcp_servers: Optional[List[Any]] = None):
        """
        Initialize local agent.
        
        Args:
            spec: Agent specification
            settings: Settings dictionary
            approval_callback: Optional callback for tool approvals
            mcp_servers: Optional MCP servers
        """
        super().__init__()
        self.spec = spec
        self.settings = settings or {}
        self.approval_callback = approval_callback
        self.mcp_servers = mcp_servers or []
        
        # RPC infrastructure
        self._agent_service: Optional[AgentService] = None
        self._rpc_client: Optional[JsonRpcClient] = None
        self._client_transport: Optional[InMemoryTransport] = None
        self._server_transport: Optional[InMemoryTransport] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._session_id: Optional[str] = None
        self._initialized = False
        
        # Set agent properties from spec
        self.name = spec.name
        self.instructions = spec.get_agent_instructions() if spec.get_agent_instructions else ""
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
            # This will be available to all SafeAgent instances including handoffs
            from omniagents.core.agents.safe import SafeAgent
            if approval_callback:
                SafeAgent.set_global_approval_callback(approval_callback)
            
            # Set global safe tool settings
            if self.spec.safe_agent_options:
                SafeAgent.set_global_safe_tools(
                    tool_names=self.spec.safe_agent_options.safe_tool_names,
                    tool_patterns=self.spec.safe_agent_options.safe_tool_patterns
                )
            
            # If spec already has a custom builder, use it
            if self._original_builder:
                agent = await self._original_builder(settings, mcp_servers[0] if mcp_servers else mcp_server, self.spec)
                
                # Check if agent is already a SafeAgent that needs callback
                if isinstance(agent, SafeAgent) and approval_callback:
                    # Set the instance callback as well for this specific agent
                    agent.set_approval_callback(approval_callback)
                
                # If safe mode is enabled and agent is not already a SafeAgent, wrap it
                elif self.spec.use_safe_agent and approval_callback and not isinstance(agent, SafeAgent):
                    opts = self.spec.safe_agent_options
                    
                    # Create SafeAgent, passing through all attributes via **kwargs
                    # SafeAgent's __init__ accepts **kwargs and passes them to Agent.__init__
                    safe_agent = SafeAgent(
                        name=agent.name,
                        tools=agent.tools,  # Use static tools at build time
                        instructions=agent.instructions,
                        model=agent.model,
                        model_settings=getattr(agent, 'model_settings', None),
                        mcp_servers=getattr(agent, 'mcp_servers', []) or [],  # Ensure it's a list, not None
                        # Pass through other Agent attributes
                        handoffs=getattr(agent, 'handoffs', []),
                        handoff_description=getattr(agent, 'handoff_description', None),
                        prompt=getattr(agent, 'prompt', None),
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
            # Use our captured approval_callback if safe agent is enabled
            approval_cb = approval_callback if self.spec.use_safe_agent else None
            
            # Use our captured MCP servers, preferring the first one if available
            mcp_to_use = mcp_servers[0] if mcp_servers else mcp_server
            
            return await _default_build_agent(
                settings,
                mcp_to_use,
                self.spec,
                approval_cb
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
        if self._rpc_client is None:
            # Make sure we're initialized first
            await self.initialize()
            
            # Connect client transport
            await self._client_transport.connect()
            
            # Create and start RPC client
            event_methods = _ClientEventMethods(self._event_queue)
            self._rpc_client = JsonRpcClient(self._client_transport, event_methods)
            await self._rpc_client.__aenter__()

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
                                    "message": str(e)
                                }
                            }
                            await self._server_transport.send_json(error_response)
        except Exception:
            # Server loop ended
            pass

    async def cleanup(self):
        """Clean up resources."""
        self._initialized = False
        
        # Restore original builder
        if hasattr(self, '_original_builder') and self.spec:
            self.spec.build_agent = self._original_builder
        
        # Cancel server task
        if hasattr(self, '_server_task'):
            self._server_task.cancel()
            with suppress(Exception):
                await self._server_task
        
        # Clean up RPC client
        if self._rpc_client:
            await self._rpc_client.__aexit__(None, None, None)
        
        # Disconnect transports
        if self._client_transport:
            await self._client_transport.disconnect()
        if self._server_transport:
            await self._server_transport.disconnect()

    def run_streamed(self, messages: List[Dict[str, str]], *, run_config=None):
        """Run agent with streaming output."""
        async def _events_gen():
            await self._ensure_client()
            
            # Extract prompt from messages
            prompt = messages[-1]["content"] if messages else ""
            
            # Start run via RPC
            response = await self._rpc_client.other.start_run(
                prompt=prompt,
                session_id=self._session_id
            )
            
            # Extract session_id and run_id
            if hasattr(response, 'result'):
                result = response.result
            else:
                result = response
                
            if isinstance(result, dict):
                if not self._session_id and "session_id" in result:
                    self._session_id = result["session_id"]
                run_id = result.get("run_id")
            else:
                run_id = None
            
            # Stream events until run_end
            while True:
                event_data = await self._event_queue.get()
                
                if event_data["method"] == "run_end":
                    params = event_data.get("params", {})
                    if not run_id or params.get("run_id") == run_id:
                        break
                
                # Convert to StreamEvent
                event = map_notification_to_event(
                    event_data["method"], 
                    event_data.get("params", {})
                )
                if event:
                    yield event
        
        class _StreamResult:
            def __init__(self, events_gen):
                self._events_gen = events_gen
            
            def stream_events(self):
                return self._events_gen
        
        return _StreamResult(_events_gen())

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List available sessions."""
        await self._ensure_client()
        response = await self._rpc_client.other.list_sessions()
        return getattr(response, "result", response)
    
    async def archive_session(self, session_id: str) -> None:
        """Archive a session."""
        await self._ensure_client()
        await self._rpc_client.other.archive_session(session_id=session_id)
    
    # Context manager support for resource cleanup
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        await self.cleanup()
    
    # Compatibility methods
    def get_all_tools(self) -> List[Any]:
        """Get all available tools (empty for RPC agents)."""
        return []
    
    def reset_run_state(self) -> None:
        """Reset agent state (no-op for RPC agents)."""
        pass