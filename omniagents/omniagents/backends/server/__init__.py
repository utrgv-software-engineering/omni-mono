"""Server backend for OmniAgents."""

from omniagents.core.interfaces.backend import Backend
from omniagents.core.agents.specs import AgentSpec


class ServerBackend:
    """Server backend implementation for running agents via JSON-RPC over WebSocket."""
    
    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the server backend with the given agent specification.
        
        Args:
            spec: The agent specification
            **kwargs: Additional backend-specific arguments:
                - config_path: Path to YAML config file
                - host: Host to bind to (default: 0.0.0.0)
                - port: Port to listen on (default: 8000)
                - auth_token: Optional bearer token for authentication
                - reload: Enable auto-reload on code changes
        """
        # Import server dependencies only when needed
        try:
            from .app import build_app
            import uvicorn
        except ImportError as e:
            print(f"Error: Server dependencies not installed. Install with: pip install omniagents[server]")
            print(f"Missing: {e}")
            import sys
            sys.exit(1)
        
        # Extract server-specific kwargs
        config_path = kwargs.get('config_path')
        host = kwargs.get('host', '0.0.0.0')
        port = kwargs.get('port', 8000)
        auth_token = kwargs.get('auth_token')
        reload = kwargs.get('reload', False)
        
        print(f"Starting OmniAgents RPC server on {host}:{port} (config: {config_path})")
        
        # Build and run the FastAPI app
        app = build_app(config_path, auth_token=auth_token)
        uvicorn.run(app, host=host, port=port, reload=reload)


__all__ = ['ServerBackend']