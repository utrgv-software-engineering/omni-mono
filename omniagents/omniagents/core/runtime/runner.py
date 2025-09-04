"""Core runner for executing agents in different modes."""

from typing import Optional, List, Any, Dict
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.config.loader import load_agent_spec_from_yaml, DEFAULT_SETTINGS_FIELDS
from omniagents.core.runtime.loader import load_backend
from omniagents.core.debug import Debug


class Runner:
    """Run agents in different modes."""
    
    def __init__(self, spec: Optional[AgentSpec] = None, config_path: Optional[str] = None):
        if spec:
            self.spec = spec
        elif config_path:
            self.spec = load_agent_spec_from_yaml(config_path)
        else:
            raise ValueError("Either spec or config_path must be provided")
        
        # Setup tracing if configured
        if self.spec.tracing_config:
            from omniagents.core.tracing import setup_tracing
            setup_tracing(self.spec.tracing_config)
    
    @classmethod
    def from_agent(
        cls,
        agent: Any,  # AgentLike
        *,
        welcome_text: Optional[str] = None,
        safe: bool = False,
        skip_approvals: bool = False,
        halt_on_rejection: bool = True,
        safe_tool_names: Optional[List[str]] = None,
        safe_tool_patterns: Optional[List[str]] = None,
        context: Optional[Any] = None,
        variables: Optional[Dict[str, Any]] = None,
        build_context: Optional[Any] = None,
        max_turns: int = 20
    ) -> "Runner":
        """
        Create a Runner from an existing agents.Agent instance.
        
        This is a convenience method that creates an AgentSpec from the agent
        and returns a Runner configured to use it.
        """
        from omniagents.core.utils.jinja_instructions import process_instructions
        
        name = getattr(agent, "name", "agent")
        
        # Process instructions through Jinja2 - converts string to callable
        # This ensures instructions are re-rendered on every run, not just at creation
        agent.instructions = process_instructions(agent.instructions)
        
        # Generate storage prefix from agent name
        storage_prefix = name.lower().replace(" ", "_") + "."
        
        # Build agent function - always store the original agent
        async def _builder(settings: Dict[str, Any], mcp, spec) -> Any:
            return agent
        
        # Handle context construction
        # If variables are provided, use build_context to construct context
        # Otherwise, use the provided context directly
        final_context = context
        final_variables = variables
        if variables and build_context:
            final_context = build_context(variables)
        elif variables and not build_context:
            # If variables provided but no factory, use variables as context (dict mode)
            final_context = variables
            final_variables = None  # Don't store variables separately in this case
        
        # Create spec from agent
        spec = AgentSpec(
            name=name,
            header_title=name,
            page_title=name,
            welcome_text=welcome_text,
            client_storage_prefix=storage_prefix,
            get_agent_instructions=lambda: agent.instructions,
            build_agent=_builder,  # Always use custom builder
            available_tools=getattr(agent, "tools", []),
            model_name=getattr(agent, "model", None),
            use_safe_agent=safe,
            safe_agent_options=SafeAgentOptions(
                skip_approvals=skip_approvals,
                halt_on_rejection=halt_on_rejection,
                safe_tool_names=safe_tool_names or [],
                safe_tool_patterns=safe_tool_patterns or [],
            ),
            settings_fields=list(DEFAULT_SETTINGS_FIELDS),
            context=final_context,  # Pass the constructed context
            variables=final_variables,  # Store variables separately if they exist
            build_context=build_context,  # Keep factory for server mode
            max_turns=max_turns  # Pass through max_turns parameter
        )
        
        return cls(spec=spec)
    
    @classmethod
    def from_remote(
        cls,
        url: str,
        *,
        name: str = "Remote Agent",
        welcome_text: Optional[str] = None,
        context: Optional[Any] = None,
        variables: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        token_env_var: Optional[str] = None
    ) -> "Runner":
        """
        Create a Runner that connects to a remote agent server.
        
        Args:
            url: WebSocket URL of the remote agent server (e.g., "ws://localhost:8000/ws")
            name: Display name for the agent
            welcome_text: Optional welcome message to display
            context: Optional pre-built context to use as-is on the server
            variables: Optional variables dict for server-side context construction
            token: Optional authentication token (direct value)
            token_env_var: Optional environment variable name containing the token
            
        Note:
            - Use 'variables' when you want the server to build context (e.g., {"user_id": "123"})
            - Use 'context' when you have a pre-built context to use as-is
            - You can specify both if the server needs to handle both patterns
            
        Returns:
            A Runner configured to connect to the remote agent
        """
        import os
        
        # Determine authentication token
        auth_token = token
        if not auth_token and token_env_var:
            auth_token = os.getenv(token_env_var)
        
        # Generate storage prefix from agent name
        storage_prefix = name.lower().replace(" ", "_") + "."
        
        # Create spec for remote agent with both context and variables
        spec = AgentSpec(
            name=name,
            header_title=name,
            page_title=name,
            welcome_text=welcome_text or f"Connected to {name}. Type your message:",
            client_storage_prefix=storage_prefix,
            get_agent_instructions=lambda: "",  # Not used for remote agents
            build_agent=None,  # Not needed for remote agents
            remote_ws_url=url,
            remote_auth_token_key=token_env_var,
            context=context,
            variables=variables,
            settings_fields=list(DEFAULT_SETTINGS_FIELDS)
        )
        
        return cls(spec=spec)
    
    def run(self, mode: str = "gui", **kwargs) -> None:
        """Run agent in specified mode.
        
        Args:
            mode: Execution mode - 'gui', 'cli', or 'server'
            **kwargs: Additional arguments passed to the backend
        """
        backend_class = load_backend(mode)
        if not backend_class:
            raise ValueError(f"Unknown backend: {mode}")
        
        backend = backend_class()
        backend.run(spec=self.spec, **kwargs)
    
    def main(self, prog_name: Optional[str] = None, default_mode: str = "cli") -> None:
        """Handle command-line arguments and run the agent.
        
        This provides a consistent CLI interface for all agents, eliminating the need
        for repetitive argparse boilerplate in every script.
        
        Args:
            prog_name: Optional program name for the argument parser
            default_mode: Default execution mode ('gui', 'cli', or 'server')
        
        Example:
            # In your agent script:
            runner = Runner.from_agent(build_agent())
            runner.main()
        """
        import argparse
        import sys
        
        parser = argparse.ArgumentParser(
            prog=prog_name,
            description=f"Run {self.spec.name}" if self.spec else "Run agent"
        )
        
        # Common arguments
        common_group = parser.add_argument_group('common arguments')
        common_group.add_argument(
            "--mode", "-m",
            choices=["gui", "cli", "server", "bubbletea", "tui"],
            default=default_mode,
            help=f"Execution mode (default: {default_mode})"
        )
        common_group.add_argument(
            "--debug", "-d",
            action="store_true",
            help="Enable debug mode"
        )
        
        # SafeAgent control arguments (CLI and GUI modes)
        safe_group = parser.add_argument_group('SafeAgent controls', 'Control tool approval behavior (CLI/GUI modes)')
        safe_group.add_argument(
            "--no-approval",
            action="store_true",
            help="Disable SafeAgent tool approval"
        )
        safe_group.add_argument(
            "--skip-approvals",
            action="store_true",
            help="Auto-approve all tool calls"
        )
        safe_group.add_argument(
            "--no-halt",
            action="store_true",
            help="Don't halt on rejection"
        )
        safe_group.add_argument(
            "--pre-approve",
            type=str,
            help="Comma-separated list of tool names to pre-approve"
        )
        safe_group.add_argument(
            "--safe-patterns",
            type=str,
            help="Comma-separated list of safe tool patterns"
        )
        
        # CLI-specific arguments
        cli_group = parser.add_argument_group('CLI mode options')
        cli_group.add_argument(
            "--session-id",
            help="Resume a previous CLI session"
        )
        
        # Server-specific arguments
        server_group = parser.add_argument_group('server mode options')
        server_group.add_argument(
            "--host",
            default="0.0.0.0",
            help="Host interface to bind (default: 0.0.0.0)"
        )
        server_group.add_argument(
            "--port", "-p",
            type=int,
            default=8000,
            help="Port to listen on (default: 8000)"
        )
        server_group.add_argument(
            "--auth-token",
            dest="auth_token",
            help="Bearer token for authentication"
        )
        server_group.add_argument(
            "--reload",
            action="store_true",
            help="Auto-reload on code changes"
        )
        
        args = parser.parse_args()
        
        # Initialize debug system
        Debug.init(cli_flag=args.debug)
        
        # Build kwargs for run()
        kwargs = {
            'args': args,  # Pass full args for all backends
        }
        
        # Add mode-specific kwargs
        if args.mode == 'cli':
            kwargs.update({
                'session_id': args.session_id,
            })
        elif args.mode == 'server':
            kwargs.update({
                'host': args.host,
                'port': args.port,
                'auth_token': args.auth_token,
                'reload': args.reload,
            })
        
        try:
            self.run(mode=args.mode, **kwargs)
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)