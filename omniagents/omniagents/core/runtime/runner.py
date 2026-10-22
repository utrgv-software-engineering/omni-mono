"""Core runner for executing agents in different modes."""

import time
import os
from typing import Optional, List, Any, Dict

from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions, MCPServerConfig
from omniagents.core.config.loader import (
    load_agent_spec_from_yaml,
    DEFAULT_SETTINGS_FIELDS,
)
from omniagents.core.runtime.loader import load_backend
from omniagents.core.debug import Debug


class Runner:
    """Run agents in different modes."""

    def __init__(
        self, spec: Optional[AgentSpec] = None, config_path: Optional[str] = None
    ):
        if spec:
            self.spec = spec
        elif config_path:
            self.spec = load_agent_spec_from_yaml(config_path)
        else:
            raise ValueError("Either spec or config_path must be provided")
        self._notebook_server: Optional[Any] = None

        # Setup tracing - disabled by default unless explicitly configured
        from omniagents.core.tracing import setup_tracing

        setup_tracing(self.spec.tracing_config)

    @classmethod
    def from_agent(
        cls,
        agent: Any,  # AgentLike
        *,
        voice_agent: Optional[Any] = None,
        welcome_text: Optional[str] = None,
        safe: bool = False,
        skip_approvals: bool = False,
        halt_on_rejection: bool = True,
        safe_tool_names: Optional[List[str]] = None,
        safe_tool_patterns: Optional[List[str]] = None,
        context: Optional[Any] = None,
        variables: Optional[Dict[str, Any]] = None,
        build_context: Optional[Any] = None,
        max_turns: int = 20,
        server_functions: Optional[List[Any]] = None,
        mcp_servers: Optional[List[Any]] = None,
    ) -> "Runner":
        """
        Create a Runner from an existing agents.Agent instance.

        This is a convenience method that creates an AgentSpec from the agent
        and returns a Runner configured to use it.
        """
        from omniagents.core.utils.jinja_instructions import process_instructions

        # Handle context construction once for all specs
        final_context = context
        final_variables = variables
        if build_context and variables is not None:
            final_context = build_context(variables)
            # When build_context is used, keep variables stored as-is
        elif variables is not None and not build_context:
            # If variables provided but no factory, use variables as context (dict mode)
            final_context = variables
            final_variables = None  # Don't store variables separately in this case

        registrar = None
        if server_functions:
            try:
                from omniagents.core.server_functions.discovery import (
                    build_server_functions_registrar,
                )

                registrar = build_server_functions_registrar(server_functions)
            except Exception:
                registrar = None

        # Convert MCP server instances to MCPServerConfig objects
        mcp_server_configs: List[MCPServerConfig] = []
        if mcp_servers:
            for server in mcp_servers:
                if isinstance(server, MCPServerConfig):
                    mcp_server_configs.append(server)
                elif hasattr(server, "params"):
                    # Convert MCPServerStdio/MCPServerSse to MCPServerConfig
                    server_type = "stdio"
                    if "MCPServerSse" in type(server).__name__:
                        server_type = "sse"
                    elif "MCPServerStreamableHttp" in type(server).__name__:
                        server_type = "streamable_http"
                    # Convert params to dict if it's a Pydantic model (e.g. StdioServerParameters)
                    # MCPServerConfig expects dict params, but MCPServerStdio stores params as
                    # a Pydantic model which doesn't support dict-style access
                    params = server.params
                    if hasattr(params, "model_dump"):
                        # Pydantic v2
                        params = params.model_dump(exclude_none=True)
                    elif hasattr(params, "dict"):
                        # Pydantic v1
                        params = params.dict(exclude_none=True)
                    elif not isinstance(params, dict):
                        # Fallback: try to convert to dict
                        params = (
                            dict(params)
                            if hasattr(params, "__iter__")
                            else vars(params)
                        )
                    mcp_server_configs.append(
                        MCPServerConfig(
                            name=getattr(
                                server, "name", f"mcp-{len(mcp_server_configs)}"
                            ),
                            type=server_type,
                            params=params,
                            options={
                                "name": getattr(server, "name", None),
                                "cache_tools_list": getattr(
                                    server, "cache_tools_list", False
                                ),
                            },
                        )
                    )

        def _build_spec(agent_obj: Any) -> AgentSpec:
            local_agent = agent_obj
            local_agent.instructions = process_instructions(local_agent.instructions)
            name = getattr(local_agent, "name", "agent")
            agent_slug_value = name.lower().replace(" ", "_").replace("-", "_")

            async def _builder(settings: Dict[str, Any], mcp, app) -> Any:
                # Update agent's mcp_servers with the connected servers from the runtime
                # The app object has mcp_servers set to all active connected servers
                # IMPORTANT: Always set mcp_servers to clear any stale references from
                # previous runs. The local_agent object persists across runs, so old
                # disconnected servers would cause "Server not initialized" errors.
                active_servers = getattr(app, "mcp_servers", None)
                if active_servers:
                    local_agent.mcp_servers = list(active_servers)
                elif mcp:
                    # Fallback to the single mcp server passed
                    if isinstance(mcp, (list, tuple)):
                        local_agent.mcp_servers = list(mcp)
                    else:
                        local_agent.mcp_servers = [mcp]
                else:
                    # Clear any stale server references from previous runs
                    local_agent.mcp_servers = []
                return local_agent

            spec_obj = AgentSpec(
                name=name,
                welcome_text=welcome_text,
                get_agent_instructions=lambda: local_agent.instructions,
                build_agent=_builder,
                available_tools=getattr(local_agent, "tools", []),
                model_name=getattr(local_agent, "model", None),
                use_safe_agent=safe,
                safe_agent_options=SafeAgentOptions(
                    skip_approvals=skip_approvals,
                    halt_on_rejection=halt_on_rejection,
                    safe_tool_names=list(safe_tool_names or []),
                    safe_tool_patterns=list(safe_tool_patterns or []),
                ),
                settings_fields=list(DEFAULT_SETTINGS_FIELDS),
                context=final_context,
                variables=final_variables,
                build_context=build_context,
                max_turns=max_turns,
                project_slug=None,
                agent_slug=agent_slug_value,
                mcp_servers=mcp_server_configs,
            )
            if registrar:
                spec_obj.register_server_functions = registrar
            return spec_obj

        spec = _build_spec(agent)
        if voice_agent:
            spec.voice_spec = _build_spec(voice_agent)

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
        token_env_var: Optional[str] = None,
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

        # Generate agent_slug from agent name
        agent_slug_value = name.lower().replace(" ", "_").replace("-", "_")

        # Create spec for remote agent with both context and variables
        spec = AgentSpec(
            name=name,
            welcome_text=welcome_text or f"Connected to {name}. Type your message:",
            get_agent_instructions=lambda: "",  # Not used for remote agents
            build_agent=None,  # Not needed for remote agents
            remote_ws_url=url,
            remote_auth_token_key=token_env_var,
            context=context,
            variables=variables,
            settings_fields=list(DEFAULT_SETTINGS_FIELDS),
            project_slug=None,
            agent_slug=agent_slug_value,
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

        # Redirect stdout AND stderr before launching TUI backends so
        # stray print() calls and logger tracebacks (logging defaults to
        # stderr) never corrupt the terminal the TUI renders on.
        if mode in ("ink", "web"):
            Debug.capture_stdout()
            Debug.capture_stderr()

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
            description=f"Run {self.spec.name}" if self.spec else "Run agent",
        )

        # Common arguments
        common_group = parser.add_argument_group("common arguments")
        common_group.add_argument(
            "--mode",
            "-m",
            choices=["server", "ink", "web"],
            default=default_mode,
            help=f"Execution mode (default: {default_mode})",
        )
        common_group.add_argument(
            "--debug", "-d", action="store_true", help="Enable debug mode"
        )
        common_group.add_argument(
            "--resume",
            action="store_true",
            help="Resume previous session.",
        )
        common_group.add_argument(
            "--ui-minimal",
            action="store_true",
            help="Hide the sidebar when launching the web UI (adds minimal=true query param)",
        )

        # SafeAgent control arguments (strict)
        safe_group = parser.add_argument_group(
            "SafeAgent controls", "Control tool approval behavior (CLI/GUI modes)"
        )
        safe_group.add_argument(
            "--approvals",
            choices=["require", "auto", "skip"],
            help="Approval policy: require (manual), auto (auto-approve), skip (disable SafeAgent)",
        )
        safe_group.add_argument(
            "--on-reject",
            dest="on_reject",
            choices=["halt", "continue"],
            help="Behavior when a tool call is rejected: halt or continue",
        )

        # CLI-specific arguments
        cli_group = parser.add_argument_group("CLI mode options")
        cli_group.add_argument("--session-id", help="Resume a previous CLI session")

        # Server-specific arguments
        server_group = parser.add_argument_group("server mode options")
        server_group.add_argument(
            "--host",
            default=None,
            help="Host interface to bind (default: 127.0.0.1)",
        )
        server_group.add_argument(
            "--port",
            "-p",
            type=int,
            default=None,
            help="Port to listen on (default: 8000)",
        )
        server_group.add_argument(
            "--auth-token", dest="auth_token", help="Bearer token for authentication"
        )
        server_group.add_argument(
            "--reload", action="store_true", help="Auto-reload on code changes"
        )

        args = parser.parse_args()

        # Initialize debug system
        Debug.init(cli_flag=args.debug)

        # Build kwargs for run()
        kwargs = {
            "args": args,  # Pass full args for all backends
        }

        resume_flag = bool(getattr(args, "resume", False))
        if resume_flag:
            kwargs["resume"] = True

        if getattr(args, "ui_minimal", False):
            kwargs["minimal_ui"] = True

        # Add mode-specific kwargs
        if args.mode == "server":
            codespaces_env = any(
                os.environ.get(key)
                for key in (
                    "CODESPACES",
                    "CODESPACE_NAME",
                    "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
                )
            )
            host_value = args.host or ("0.0.0.0" if codespaces_env else "127.0.0.1")
            port_value = args.port or 8000
            kwargs.update(
                {
                    "host": host_value,
                    "port": port_value,
                    "auth_token": args.auth_token,
                    "reload": args.reload,
                }
            )

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

    def run_notebook(
        self,
        height: int = 600,
        width: str = "100%",
        port: Optional[int] = None,
        auth_token: Optional[str] = None,
        minimal_ui: bool = True,
        input: Optional[str] = None,
        default_host_workspace_root: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Run the agent embedded in a Jupyter notebook.

        Starts the web server in a background thread and displays an IFrame
        with the agent UI inline in the notebook output.

        Args:
            height: Height of the embedded UI in pixels (default: 600)
            width: Width of the embedded UI (default: "100%")
            port: Specific port to use (default: a free port chosen by the OS)
            auth_token: Optional authentication token
            minimal_ui: If True, hide the sidebar (default: True)
            input: Optional message to send immediately on load
            default_host_workspace_root: Root for the notebook's host execution
                environment. Defaults to the current working directory at launch.
            **kwargs: Reserved for compatibility.

        Example:
            runner = Runner.from_agent(agent)
            runner.run_notebook()  # Displays embedded UI in notebook
            runner.run_notebook(input="Hello!")  # Starts with a message
            runner.run_notebook(default_host_workspace_root="/path/to/project")
        """
        try:
            from IPython.display import IFrame, HTML, display
        except ImportError:
            raise ImportError(
                "IPython is required for notebook mode. "
                "Install with: pip install ipython"
            )

        try:
            from omniagents.backends.server.app import build_app
            import uvicorn
            from fastapi.staticfiles import StaticFiles
            from omniagents.core.utils.serving import BackgroundServer, bind_socket
        except ImportError:
            raise ImportError(
                "Server dependencies not installed. "
                "Install with: pip install omniagents[server]"
            )

        try:
            from omniagents.core.agents.specs import RealtimeSettings

            realtime_spec = getattr(self.spec, "voice_spec", None) or self.spec
            voice_backend = getattr(realtime_spec, "voice_backend", "realtime")
            if voice_backend == "realtime":
                if not getattr(realtime_spec, "realtime_mode", False):
                    realtime_spec.realtime_mode = True
                if not getattr(realtime_spec, "realtime_settings", None):
                    realtime_spec.realtime_settings = RealtimeSettings()
                rs = realtime_spec.realtime_settings
                if not rs.modalities or ("audio" not in rs.modalities):
                    rs.modalities = ["audio"]
                if not rs.model_name:
                    rs.model_name = "gpt-realtime"
                if not rs.voice:
                    rs.voice = "alloy"
                if not rs.input_audio_format:
                    rs.input_audio_format = "pcm16"
                if not rs.output_audio_format:
                    rs.output_audio_format = "pcm16"
                if not rs.turn_detection:
                    rs.turn_detection = {
                        "type": "server_vad",
                        "threshold": 0.3,
                        "prefix_padding_ms": 100,
                        "silence_duration_ms": 200,
                    }
                if rs.temperature is None:
                    rs.temperature = 0.8
                if rs.max_output_tokens is None:
                    rs.max_output_tokens = 4096
                if not rs.input_audio_transcription:
                    rs.input_audio_transcription = {"model": "whisper-1"}
        except Exception:
            pass

        # Build the FastAPI app (registers /ws WebSocket route)
        # Like the local web launcher, notebooks need an explicit host
        # environment so filesystem and process tools have a workspace.
        app = build_app(
            spec=self.spec,
            auth_token=auth_token,
            default_host_workspace_root=(
                os.getcwd()
                if default_host_workspace_root is None
                else default_host_workspace_root
            ),
        )

        # Add CORS and cross-origin headers for embedding in Jupyter notebooks
        # Jupyter runs with COEP: require-corp, so we need these headers
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request

        class CrossOriginMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                response = await call_next(request)
                response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
                response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
                response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
                response.headers["Access-Control-Allow-Origin"] = "*"
                return response

        app.add_middleware(CrossOriginMiddleware)

        # Mount static files for the web UI
        # Important: FastAPI routes are matched in order. The /ws route is added first
        # by build_app(), so it takes precedence over the catch-all mount.
        dist_dir = self._resolve_web_ui_dist()
        if dist_dir:
            app.mount(
                "/", StaticFiles(directory=str(dist_dir), html=True), name="webui"
            )

        # Silence noisy third-party loggers
        import logging

        for name in (
            "agents",
            "openai",
            "httpx",
            "urllib3",
            "anthropic",
            "litellm",
        ):
            try:
                lg = logging.getLogger(name)
                lg.handlers.clear()
                lg.addHandler(logging.NullHandler())
                lg.setLevel(logging.CRITICAL)
                lg.propagate = False
            except Exception:
                pass

        # Re-running the cell replaces this runner's previous server instead
        # of leaving it running beside the new one.
        self.stop_notebook()

        # Bind the socket here (port 0 unless one was asked for) and hand it
        # to uvicorn, so the port the iframe points at is the one this server
        # listens on — never one another kernel grabbed in the meantime.
        server = BackgroundServer(
            uvicorn.Config(app=app, log_level="error", access_log=False),
            bind_socket("127.0.0.1", port or 0),
        )
        server.start()
        self._notebook_server = server
        port = server.port

        # Build URL with query params
        from urllib.parse import urlencode
        import uuid

        url = f"http://localhost:{port}/"
        params = {}
        # Generate a session ID so reloading the notebook doesn't create a new session
        params["session"] = str(uuid.uuid4())
        if auth_token:
            params["token"] = auth_token
        if minimal_ui:
            params["minimal"] = "true"
        if input:
            params["initial"] = input
        params["ts"] = str(int(time.time()))
        if params:
            url += "?" + urlencode(params)

        # Create and display IFrame (only use display(), don't return to avoid double display)
        iframe = IFrame(url, width=width, height=height)
        display(iframe)
        display(
            HTML(
                f'<div style="margin: 8px 0;"><a href="{url}" target="_blank" rel="noopener">Open in new tab</a></div>'
            )
        )

    def stop_notebook(self) -> None:
        """Stop the server started by :meth:`run_notebook`, if one is running."""
        server = self._notebook_server
        if server is not None:
            self._notebook_server = None
            server.stop()

    def _resolve_web_ui_dist(self) -> Optional[Any]:
        """Resolve the web UI dist directory."""
        from pathlib import Path

        # Check for override
        override = os.environ.get("OMNI_WEB_UI_PATH")
        if override:
            p = Path(override).expanduser()
            if p.is_dir() and (p / "index.html").exists():
                return p

        # Check packaged dist
        web_backend_path = Path(__file__).parent.parent.parent / "backends" / "web"
        packaged = web_backend_path / "ui" / "dist"
        if packaged.is_dir() and (packaged / "index.html").exists():
            return packaged

        # Check cache directory
        import platform

        home = Path.home()
        system = platform.system().lower()
        if system == "windows":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            cache_base = Path(base) / "OmniAgents" if base else None
        elif system == "darwin":
            cache_base = home / "Library" / "Caches" / "OmniAgents"
        else:
            xdg = os.environ.get("XDG_CACHE_HOME")
            cache_base = (
                Path(xdg) / "omniagents" if xdg else home / ".cache" / "omniagents"
            )

        if cache_base:
            candidate = cache_base / "web" / "ui" / "dist"
            if candidate.is_dir() and (candidate / "index.html").exists():
                return candidate

        return None
