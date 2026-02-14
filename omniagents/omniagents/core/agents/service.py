"""
Centralized AgentService for handling all agent-related JSON-RPC operations.

This service provides a unified interface for agent operations across different
execution modes (CLI, Flet, Server), exposing methods via JSON-RPC protocol.
"""

import os
import asyncio
import uuid
import copy
import logging
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Optional, Dict, Any
from pathlib import Path
import inspect

from agents import (
    AgentsException,
    MaxTurnsExceeded,
    ModelBehaviorError,
    UserError,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)

try:
    from openai import (
        APIError,
        APIStatusError,
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
    )
except ImportError:
    APIError = None
    APIStatusError = None
    RateLimitError = None
    APIConnectionError = None
    APITimeoutError = None

from omniagents.rpc import RpcMethodsBase
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.safe import SafeAgent
from omniagents.core.session import Session, SessionManager
from omniagents.core.session.history_db import get_user_messages
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.agents.mcp import build_mcp_servers
from omniagents.core.debug import Debug


# Setup logging
logger = logging.getLogger("omniagents.service")

# Configure file logging if requested
DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in ("1", "true")
LOG_FILE = os.getenv("OMNIAGENTS_SERVER_LOG_FILE")

if LOG_FILE or DEBUG_SERVER:
    logger.setLevel(logging.DEBUG)

    # Console handler (always for debug mode)
    if DEBUG_SERVER:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter("[srv] %(message)s"))
        logger.addHandler(console_handler)

    # File handler (if log file specified)
    if LOG_FILE:
        log_path = Path(os.path.expanduser(LOG_FILE))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, mode="a")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [srv] %(levelname)s: %(message)s")
        )
        logger.addHandler(file_handler)


def _debug_rpc(method: str, payload: dict | None = None) -> None:
    """Log RPC method calls when debug is enabled.

    Enabled when either OMNIAGENTS_SERVER_DEBUG=1 or Debug.enabled().
    """
    if DEBUG_SERVER or Debug.enabled():
        if payload is not None:
            logger.debug(f"★ {method} payload={payload!r}")
        else:
            logger.debug(f"★ {method}")


def _safe_slug(value: str | None, fallback: str) -> str:
    token = value or ""
    token = token.strip().lower()
    if not token:
        token = fallback
    out: list[str] = []
    for ch in token:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or fallback


def _build_agent_error_info(exc: AgentsException) -> dict:
    """Build error information from an AgentsException with run_data preservation."""
    error_info = {
        "message": str(exc),
        "type": exc.__class__.__name__,
    }

    # Preserve run_data if available
    if hasattr(exc, "run_data") and exc.run_data is not None:
        run_data = exc.run_data
        error_info["run_data"] = {
            "turn_count": (
                len(run_data.new_items) if hasattr(run_data, "new_items") else 0
            ),
            "agent_name": (
                run_data.last_agent.name
                if hasattr(run_data, "last_agent") and run_data.last_agent
                else None
            ),
        }

    return error_info


def _build_guardrail_error_info(
    exc: (
        InputGuardrailTripwireTriggered
        | OutputGuardrailTripwireTriggered
        | ToolInputGuardrailTripwireTriggered
        | ToolOutputGuardrailTripwireTriggered
    ),
) -> dict:
    """Build error information from a guardrail exception with guardrail details."""
    error_info = {
        "message": str(exc),
        "type": exc.__class__.__name__,
    }

    # Add guardrail-specific information
    if isinstance(
        exc, (InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered)
    ):
        if hasattr(exc, "guardrail_result"):
            result = exc.guardrail_result
            error_info["guardrail"] = {
                "name": (
                    result.guardrail.get_name()
                    if hasattr(result.guardrail, "get_name")
                    else result.guardrail.__class__.__name__
                ),
                "result": (
                    str(result.output.output_info)
                    if hasattr(result, "output")
                    and hasattr(result.output, "output_info")
                    else None
                ),
            }
    elif isinstance(
        exc, (ToolInputGuardrailTripwireTriggered, ToolOutputGuardrailTripwireTriggered)
    ):
        if hasattr(exc, "guardrail"):
            error_info["guardrail"] = {
                "name": (
                    exc.guardrail.get_name()
                    if hasattr(exc.guardrail, "get_name")
                    else exc.guardrail.__class__.__name__
                ),
            }
        if hasattr(exc, "output"):
            output = exc.output
            if hasattr(output, "behavior"):
                error_info["guardrail"]["behavior"] = output.behavior
            if hasattr(output, "output_info"):
                error_info["guardrail"]["output_info"] = output.output_info

    # Also include run_data if this is an AgentsException
    if (
        isinstance(exc, AgentsException)
        and hasattr(exc, "run_data")
        and exc.run_data is not None
    ):
        run_data = exc.run_data
        error_info["run_data"] = {
            "turn_count": (
                len(run_data.new_items) if hasattr(run_data, "new_items") else 0
            ),
            "agent_name": (
                run_data.last_agent.name
                if hasattr(run_data, "last_agent") and run_data.last_agent
                else None
            ),
        }

    return error_info


class AgentService(RpcMethodsBase):
    """JSON-RPC methods for agent operations.

    This service handles:
    - Starting and stopping agent runs
    - Managing conversation history
    - Session management (list, archive)
    - Tool approval workflows
    - Event broadcasting to multiple channels
    """

    def __init__(self, spec: AgentSpec) -> None:
        """Initialize AgentService with an agent specification.

        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings.
        """
        super().__init__()
        self.spec = spec
        project_slug = _safe_slug(getattr(self.spec, "project_slug", None), "default")
        agent_slug = _safe_slug(
            getattr(self.spec, "agent_slug", None),
            _safe_slug(getattr(self.spec, "name", None), "agent"),
        )
        if not getattr(self.spec, "project_slug", None):
            self.spec.project_slug = project_slug
        if not getattr(self.spec, "agent_slug", None):
            self.spec.agent_slug = agent_slug
        self._session_manager = SessionManager(project_slug, agent_slug)
        try:
            # Always setup tracing to ensure defaults (disabled) are applied
            from omniagents.core.tracing import setup_tracing

            setup_tracing(getattr(self.spec, "tracing_config", None))
        except Exception:
            pass
        # Client-function request tracking (server→client RPC-style)
        self._pending_client_requests: dict[str, asyncio.Future] = {}
        # Optional capability registry per connected channel
        self._client_capabilities: dict[Any, dict] = {}
        self._client_request_sessions: dict[str, Session] = {}
        # Server functions registry (client→server dynamic functions)
        # Map name -> {"func": callable, "description": str | None, "params_schema": dict | None, "result_schema": dict | None}
        self._server_functions: dict[str, dict] = {}
        self._runtime_hooks: dict[str, list[Any]] = {}
        # Note: we intentionally do NOT register built-in example functions
        # like "session.get_history" so they won't surface in UIs that
        # dynamically list server functions (e.g., Bubble Tea /help).
        self._default_settings: Dict[str, Any] = {}
        for field in getattr(self.spec, "settings_fields", []) or []:
            key = getattr(field, "key", None)
            if not key:
                continue
            value = os.getenv(key.upper())
            if value in (None, ""):
                value = getattr(field, "default", None)
            self._default_settings[key] = value

        self.register_server_function(
            "session.ensure",
            self._sf_session_ensure,
            description="Ensure a session exists and persist workspace metadata.",
            params_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "workspace_root": {"type": "string"},
                    "context": {"type": "object"},
                    "variables": {"type": "object"},
                },
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "workspace_root": {"type": "string"},
                    "context": {"type": "object"},
                    "variables": {"type": "object"},
                },
                "required": ["session_id"],
            },
            strict=False,
        )

    async def _ensure_session_mcp_servers(self, session: Session) -> list[Any]:
        """Ensure MCP servers are initialized and running for this session.

        Creates a dedicated asyncio task to manage the MCP lifecycle, ensuring
        that AsyncExitStack.__aenter__ and __aexit__ are called from the same
        task. This prevents AnyIO "cancel scope in different task" violations.

        Servers are created once per session and reused across all runs until
        the session is archived.

        Args:
            session: The session to initialize MCP servers for

        Returns:
            List of active MCP server instances
        """
        # Already initialized?
        if hasattr(session, "_mcp_servers_active") and session._mcp_servers_active:
            return session._mcp_servers_active

        if not self.spec.mcp_servers:
            return []

        # Create Event to signal when servers are ready
        servers_ready = asyncio.Event()
        active_servers_holder = {"servers": []}

        async def mcp_lifecycle_manager():
            """Long-lived task that owns the MCP AsyncExitStack lifecycle.

            This task:
            1. Creates AsyncExitStack
            2. Builds and enters all MCP servers
            3. Signals that servers are ready
            4. Stays alive until session shutdown
            5. Cleans up all servers on exit

            By keeping __aenter__ and __aexit__ in the same task, we avoid
            AnyIO cross-task violations.
            """
            settings = dict(self._default_settings)

            async with AsyncExitStack() as stack:
                active_servers = []

                try:
                    # Build all MCP servers
                    servers = build_mcp_servers(self.spec, settings)

                    # Enter each server into the exit stack
                    # Handle individual server failures gracefully
                    for server in servers:
                        server_name = getattr(server, "name", "unknown")
                        try:
                            if hasattr(server, "__aenter__") and hasattr(
                                server, "__aexit__"
                            ):
                                # Context manager style (stdio, sse, etc.)
                                active = await stack.enter_async_context(server)
                            else:
                                # Manual connect/cleanup style
                                await server.connect()
                                stack.push_async_callback(server.cleanup)
                                active = server
                            active_servers.append(active)
                        except Exception as server_err:
                            # Log at debug level to avoid TUI interference
                            # Users can enable debug logging to see MCP connection failures
                            from omniagents.core.utils.logging import get_logger

                            _mcp_logger = get_logger(__name__)
                            _mcp_logger.debug(
                                f"MCP server '{server_name}' failed to connect: {server_err}. "
                                "Continuing without this server."
                            )

                    # Store active servers on session (may be partial list)
                    session._mcp_servers_active = active_servers
                    active_servers_holder["servers"] = active_servers

                    # Signal that servers are ready to use
                    servers_ready.set()

                    # Keep this task alive until shutdown signal
                    shutdown_event = getattr(session, "_mcp_shutdown", None)
                    if shutdown_event:
                        await shutdown_event.wait()

                except Exception as e:
                    # If initialization fails catastrophically, signal error state
                    session._mcp_servers_active = []
                    active_servers_holder["servers"] = []
                    servers_ready.set()
                    raise

                finally:
                    # AsyncExitStack cleanup happens here when context exits
                    session._mcp_servers_active = []

        # Create shutdown event for this session
        session._mcp_shutdown = asyncio.Event()

        # Start the lifecycle manager task
        session._mcp_lifecycle_task = asyncio.create_task(
            mcp_lifecycle_manager(), name=f"mcp_lifecycle_{session.id[:8]}"
        )

        # Wait for servers to be initialized
        await servers_ready.wait()

        return active_servers_holder["servers"]

    async def _build_agent_for_session(
        self, session: Session, approval_callback
    ) -> Any:
        stub_app = SimpleNamespace(
            spec=self.spec,
            _request_tool_approval=approval_callback,
        )

        settings = dict(self._default_settings)

        # Use session-scoped MCP servers (managed by dedicated task)
        active_servers = await self._ensure_session_mcp_servers(session)

        setattr(stub_app, "mcp_servers", active_servers)

        try:
            if self.spec.build_agent:
                primary_server = active_servers[0] if active_servers else None
                agent = await self.spec.build_agent(settings, primary_server, stub_app)
                if (
                    self.spec.use_safe_agent
                    and approval_callback
                    and not isinstance(agent, SafeAgent)
                ):
                    opts = self.spec.safe_agent_options
                    agent = SafeAgent(
                        name=agent.name,
                        tools=agent.tools,
                        instructions=agent.instructions,
                        model=agent.model,
                        model_settings=getattr(agent, "model_settings", None),
                        mcp_servers=getattr(agent, "mcp_servers", []) or [],
                        handoffs=getattr(agent, "handoffs", []),
                        handoff_description=getattr(agent, "handoff_description", None),
                        prompt=getattr(agent, "prompt", None),
                        approval_callback=approval_callback,
                        safe_tool_names=opts.safe_tool_names,
                        safe_tool_patterns=opts.safe_tool_patterns,
                        skip_approvals=opts.skip_approvals,
                        halt_on_rejection=opts.halt_on_rejection,
                    )
                elif isinstance(agent, SafeAgent) and approval_callback:
                    try:
                        agent.set_approval_callback(approval_callback)
                    except Exception:
                        pass
            else:
                agent = await _default_build_agent(
                    settings,
                    active_servers,
                    self.spec,
                    approval_callback,
                    session=session,
                )

            agent = self._clone_agent(agent)

            if (
                approval_callback
                and hasattr(agent, "set_approval_callback")
                and callable(getattr(agent, "set_approval_callback"))
            ):
                try:
                    agent.set_approval_callback(approval_callback)
                except Exception:
                    pass

            self._apply_session_tool_policy(session, agent)
            return agent
        except Exception:
            # If agent building fails, cleanup MCP lifecycle if it was just created
            lifecycle_task = getattr(session, "_mcp_lifecycle_task", None)
            if lifecycle_task and not lifecycle_task.done():
                # Cancel the lifecycle task
                shutdown_event = getattr(session, "_mcp_shutdown", None)
                if shutdown_event:
                    shutdown_event.set()
                lifecycle_task.cancel()
                try:
                    await lifecycle_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            # Clean up session attributes
            setattr(session, "_mcp_shutdown", None)
            setattr(session, "_mcp_lifecycle_task", None)
            setattr(session, "_mcp_servers_active", [])
            raise

    def _clone_agent(self, agent: Any) -> Any:
        static_mcp_servers = None
        restored_original = False
        if hasattr(agent, "mcp_servers"):
            try:
                servers = getattr(agent, "mcp_servers")
                static_mcp_servers = servers
                setattr(agent, "mcp_servers", [])
                restored_original = True
            except Exception:
                static_mcp_servers = None
        try:
            cloned = copy.deepcopy(agent)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Agent builder must return deepcopy-able agent instances for per-run isolation"
            ) from exc
        finally:
            if restored_original:
                try:
                    setattr(agent, "mcp_servers", static_mcp_servers)
                except Exception:
                    pass
        if static_mcp_servers is not None:
            try:
                setattr(cloned, "mcp_servers", static_mcp_servers)
            except Exception:
                pass
        return cloned

    def _apply_session_tool_policy(self, session: Session, agent: Any) -> None:
        names = getattr(session, "always_allow_tools", None)
        if not names:
            return

        if isinstance(agent, SafeAgent):
            for tool_name in names:
                try:
                    agent.add_safe_tool(tool_name)
                except Exception:
                    pass
            return

        try:
            existing = set(getattr(agent, "always_allow_tools", set()))
            merged = existing | set(names)
            setattr(agent, "always_allow_tools", merged)
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  Server Functions registry APIs (programmatic registration)
    # ------------------------------------------------------------------
    def register_server_function(
        self,
        name: str,
        func: Any,
        *,
        description: str | None = None,
        params_schema: dict | None = None,
        result_schema: dict | None = None,
        on_invoke: Any | None = None,
        strict: bool | None = None,
    ) -> None:
        """Register a server function callable under *name*.

        Convention: function signature is one of
            (service, session, args), (session, args), (args), or ()
        where args is a dict.
        """
        if on_invoke is None:
            on_invoke = getattr(func, "_server_function_on_invoke", None)
        if strict is None:
            strict = getattr(func, "_server_function_strict", None)

        self._server_functions[name] = {
            "func": func,
            "description": description,
            "params_schema": params_schema,
            "result_schema": result_schema,
            "on_invoke": on_invoke,
            "strict": strict,
        }

    def register_runtime_hook(self, event: str, func: Any) -> None:
        self._runtime_hooks.setdefault(str(event), []).append(func)

    async def emit_runtime_hook(self, event: str, **kwargs: Any) -> None:
        funcs = list(self._runtime_hooks.get(str(event), []))
        if not funcs:
            return

        for fn in funcs:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(service=self, event=str(event), **kwargs)
                else:
                    fn(service=self, event=str(event), **kwargs)
            except Exception:
                logger.debug(
                    "Runtime hook failed",
                    exc_info=True,
                    extra={"runtime_hook_event": str(event)},
                )

    async def invoke_runtime_hooks(self, event: str, **kwargs: Any) -> Any | None:
        funcs = list(self._runtime_hooks.get(str(event), []))
        if not funcs:
            return None

        result: Any | None = None
        for fn in funcs:
            try:
                if asyncio.iscoroutinefunction(fn):
                    value = await fn(service=self, event=str(event), **kwargs)
                else:
                    value = fn(service=self, event=str(event), **kwargs)
                if value is not None:
                    result = value
            except Exception:
                logger.debug(
                    "Runtime hook failed",
                    exc_info=True,
                    extra={"runtime_hook_event": str(event)},
                )
        return result

    async def list_server_functions(self) -> list[dict]:  # noqa: D401
        """Return metadata for all registered server functions."""
        result = []
        for name, meta in self._server_functions.items():
            result.append(
                {
                    "name": name,
                    "description": meta.get("description"),
                    "params_schema": meta.get("params_schema"),
                    "result_schema": meta.get("result_schema"),
                    "strict": meta.get("strict"),
                }
            )
        _debug_rpc("list_server_functions", {"count": len(result)})
        return result

    async def server_call(
        self, function: str, args: dict | None = None, session_id: str | None = None
    ) -> Any:  # noqa: D401
        """Invoke a registered server function.

        Args:
            function: Registered function name
            args: Arguments dict passed to the function
            session_id: Optional session identifier, provided to the function if it accepts a session
        """
        _debug_rpc(
            "server_call",
            {"function": function, "has_args": bool(args), "session_id": session_id},
        )
        entry = self._server_functions.get(function)
        if not entry:
            raise ValueError(f"Unknown server function: {function}")
        fn = entry["func"]
        on_invoke = entry.get("on_invoke")

        sess: Session | None = None
        if session_id:
            try:
                sess = self._session_manager.get_or_create(session_id)
                # Ensure the invoking channel is tracked for client notifications
                if self.channel is not None and self.channel not in sess.channels:
                    sess.channels.append(self.channel)
            except Exception:
                sess = None

        if on_invoke is not None:
            if asyncio.iscoroutinefunction(on_invoke):
                return await on_invoke(self, sess, args or {})
            return on_invoke(self, sess, args or {})

        # Try common calling conventions in order
        to_call = None
        is_bound = getattr(fn, "__self__", None) is not None
        candidates_async = []
        candidates_sync = []
        if is_bound:
            candidates_sync = [
                (sess, args or {}),
                (args or {},),
                tuple(),
            ]
        else:
            candidates_sync = [
                (self, sess, args or {}),
                (sess, args or {}),
                (args or {},),
                tuple(),
            ]
        candidates_async = candidates_sync

        if asyncio.iscoroutinefunction(fn):
            for cand in candidates_async:
                try:
                    return await fn(*cand)
                except TypeError:
                    continue
            # Last resort: call with no args
            return await fn()
        else:
            for cand in candidates_sync:
                try:
                    return fn(*cand)
                except TypeError:
                    continue
            return fn()

    # --- Built-in server functions -------------------------------------
    def _sf_session_get_history(
        self, session: Session | None, args: dict | None = None
    ) -> list[dict]:
        """Return the current history for the given session.
        If no session was supplied, return an empty list.
        """
        try:
            return list(session.history) if session else []
        except Exception:
            return []

    def _sf_session_ensure(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        requested_id = payload.get("session_id")
        workspace_root = payload.get("workspace_root")
        provided_context = payload.get("context") if "context" in payload else None
        provided_context_set = "context" in payload
        provided_variables = (
            payload.get("variables") if "variables" in payload else None
        )
        provided_variables_set = "variables" in payload

        target_session: Session
        if session is not None and (not requested_id or session.id == requested_id):
            target_session = session
        else:
            target_session = self._session_manager.get_or_create(requested_id)

        if self.channel is not None and self.channel not in target_session.channels:
            target_session.channels.append(self.channel)

        context_value = (
            provided_context
            if provided_context_set
            else getattr(target_session, "context", None)
        )
        variables_value = (
            provided_variables
            if provided_variables_set
            else getattr(target_session, "variables", None)
        )

        def _ensure_mapping(value):
            if isinstance(value, dict):
                return dict(value)
            if value is None:
                return {}
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            return {}

        updated = False
        if workspace_root:
            context_value = _ensure_mapping(context_value)
            context_value["workspace_root"] = workspace_root
            variables_value = _ensure_mapping(variables_value)
            variables_value["workspace_root"] = workspace_root
            updated = True

        if provided_context_set:
            updated = True
        if provided_variables_set:
            updated = True

        target_session.context = context_value
        target_session.variables = variables_value

        if updated:
            self._session_manager.persist_metadata(
                target_session,
                context=context_value,
                variables=variables_value,
            )

        effective_workspace = None
        if isinstance(context_value, dict):
            effective_workspace = context_value.get("workspace_root")
        if effective_workspace is None and isinstance(variables_value, dict):
            effective_workspace = variables_value.get("workspace_root")

        return {
            "session_id": target_session.id,
            "workspace_root": effective_workspace,
            "context": context_value,
            "variables": variables_value,
        }

    def _record_live_event(
        self, session: Session | None, method: str, payload: dict
    ) -> None:
        if session is None:
            return
        data = dict(payload)
        if method == "run_started":
            session.active_run_id = data.get("run_id")
            session.active_run_started_payload = data
        elif method == "run_status":
            session.active_run_status_payload = data
        elif method == "client_request" and data.get("function") == "ui.set_status":
            session.active_run_set_status_payload = data
        elif (
            method == "client_request"
            and data.get("function") == "ui.set_session_state"
        ):
            session.active_session_state_payload = data

    def _clear_live_state(self, session: Session | None) -> None:
        if session is None:
            return
        session.active_run_id = None
        session.active_run_started_payload = None
        session.active_run_status_payload = None
        session.active_run_set_status_payload = None
        session.active_session_state_payload = None
        for req_id in list(session.pending_client_requests.keys()):
            self._client_request_sessions.pop(req_id, None)
        session.pending_client_requests.clear()

    async def _replay_live_state(self, session: Session, channel) -> None:
        if session is None or channel is None:
            return
        events: list[tuple[str, dict]] = []
        if session.active_run_started_payload:
            events.append(("run_started", session.active_run_started_payload))
        if session.active_run_status_payload:
            events.append(("run_status", session.active_run_status_payload))
        if session.active_run_set_status_payload:
            events.append(("client_request", session.active_run_set_status_payload))
        if session.active_session_state_payload:
            events.append(("client_request", session.active_session_state_payload))
        for payload in session.pending_client_requests.values():
            events.append(("client_request", payload))
        for method, payl in events:
            await self._safe_notify(channel, method, payl)

    def _forget_pending_request(self, request_id: str) -> None:
        sess = self._client_request_sessions.pop(request_id, None)
        if sess is not None:
            sess.pending_client_requests.pop(request_id, None)

    # ------------------------------------------------------------------
    #  Helper: best-effort async_call that tolerates disconnected sockets.
    #  Returns True if the notification was delivered, False if the WebSocket
    #  had already been closed.
    # ------------------------------------------------------------------

    async def _safe_notify(
        self, channel, method: str, payload: dict
    ) -> bool:  # noqa: D401
        try:
            await channel.async_call(method, args=payload)
            return True
        except Exception:
            return False

    async def _broadcast_session(
        self,
        session: Session,
        method: str,
        payload: dict,
        *,
        queue_if_unseen: bool = False,
        track_live: bool = False,
    ) -> bool:
        if track_live:
            self._record_live_event(session, method, payload)
        if not session.channels:
            if queue_if_unseen and hasattr(session, "pending_events"):
                session.pending_events.append((method, payload))
            return False

        delivered_any = False
        dead_channels = []
        for ch in list(session.channels):
            ok = await self._safe_notify(ch, method, payload)
            if ok:
                delivered_any = True
            else:
                dead_channels.append(ch)

        for ch in dead_channels:
            try:
                session.channels.remove(ch)
            except ValueError:
                pass

        if queue_if_unseen and not delivered_any and hasattr(session, "pending_events"):
            session.pending_events.append((method, payload))

        return delivered_any

    # ------------------------------------------------------------------
    #  RPC method invoked by the *client* when the user answers the
    #  approval prompt.  Resolves the Future we saved when we asked.
    # ------------------------------------------------------------------

    async def tool_approval_response(
        self,
        approval_id: str,
        approved: bool,
        always_approve: bool = False,
        error_message: str | None = None,
    ) -> bool:  # noqa: D401
        _debug_rpc(
            "tool_approval_response",
            {
                "approval_id": approval_id,
                "approved": approved,
                "always_approve": always_approve,
                "error_message": error_message,
            },
        )
        raise RuntimeError(
            "tool_approval_response is deprecated; respond via client_request/"
            "client_response instead"
        )

    # ------------------------------------------------------------------
    #  Client function registration (capability handshake)
    # ------------------------------------------------------------------
    async def client_functions(
        self, version: int | None = None, functions: list[dict] | None = None
    ) -> bool:  # noqa: D401
        """Register client-supported functions for the current channel.

        The information is stored best-effort and currently advisory only.
        """
        payload = {"version": version, "functions": functions or []}
        _debug_rpc("client_functions", payload)
        # Remember per-channel capabilities
        try:
            self._client_capabilities[self.channel] = payload
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    #  Client function response: resolves the future awaited by server
    # ------------------------------------------------------------------
    async def client_response(
        self,
        request_id: str,
        ok: bool,
        result: Any | None = None,
        error: dict | None = None,
    ) -> bool:  # noqa: D401
        _debug_rpc("client_response", {"request_id": request_id, "ok": ok})
        fut = self._pending_client_requests.pop(request_id, None)
        if fut and not fut.done():
            # Store a simple envelope
            fut.set_result({"ok": ok, "result": result, "error": error})
        self._forget_pending_request(request_id)
        return True

    # ------------------------------------------------------------------
    #  Helper to send a client_request to one or more channels.
    #  If expect_result=True, returns the client_response or None on timeout.
    # ------------------------------------------------------------------
    async def request_client_function(
        self,
        function: str,
        args: dict | None = None,
        *,
        session: Session | None = None,
        run_id: str | None = None,
        timeout_ms: int | None = None,
        idempotency_key: str | None = None,
        expect_result: bool = False,
    ) -> Any | None:
        import uuid

        req_id = uuid.uuid4().hex
        payload = {
            "request_id": req_id,
            "function": function,
            "args": args or {},
        }
        if session is not None:
            payload["session_id"] = session.id
        if run_id is not None:
            payload["run_id"] = run_id
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key

        _debug_rpc("client_request", payload)

        if session is not None and function == "ui.set_status":
            session.active_run_set_status_payload = dict(payload)
        if session is not None and function == "ui.set_session_state":
            session.active_session_state_payload = dict(payload)

        # Broadcast to all known channels for the session, or current channel if none provided
        if session is not None:
            channels = list(session.channels)
            if not channels and self.channel is not None:
                channels = [self.channel]
        else:
            channels = [self.channel]

        delivered_any = False
        for ch in channels:
            try:
                ok = await self._safe_notify(ch, "client_request", payload)
                delivered_any = delivered_any or ok
            except Exception:
                pass

        if not expect_result:
            return delivered_any

        # Await response with optional timeout
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_client_requests[req_id] = fut
        try:
            if timeout_ms is not None and timeout_ms > 0:
                res = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            else:
                res = await fut
            return res
        except asyncio.TimeoutError:
            # Clean up and return None on timeout
            self._pending_client_requests.pop(req_id, None)
            return None

    # Convenience: set status message on client (best-effort UI hint)
    async def set_client_status(
        self,
        text: str,
        *,
        session: Session | None = None,
        run_id: str | None = None,
        show_spinner: bool = True,
    ) -> bool:
        payload = {"text": text, "show_spinner": bool(show_spinner)}
        ok = await self.request_client_function(
            "ui.set_status",
            payload,
            session=session,
            run_id=run_id,
            expect_result=False,
        )
        # request_client_function returns True if delivered to at least one channel
        return bool(ok)

    # ------------------------------------------------------------------
    #  Callback passed to SafeAgent: emits a *request* to the client and
    #  waits for the matching response.
    # ------------------------------------------------------------------

    async def _request_tool_approval(
        self,
        tool_name: str,
        formatted_args: str,
        _sess: Session,
        metadata: Optional[Dict[str, Any]] = None,
        _run_id: str | None = None,
    ):  # noqa: D401
        """Ask the client for approval; buffer request if not delivered."""

        if _sess is None:
            raise RuntimeError(
                "Tool approval requests require an explicit session context"
            )

        approval_id = uuid.uuid4().hex
        run_id = _run_id
        if run_id is None:
            current_task = asyncio.current_task()
            if current_task is not None:
                try:
                    run_id = current_task.get_name()
                except Exception:
                    run_id = None
        payload = {
            "approval_id": approval_id,
            "tool": tool_name,
            "arguments": formatted_args,
        }
        if run_id:
            payload["run_id"] = run_id

        payload["session_id"] = _sess.id

        if metadata:
            payload["metadata"] = metadata

        if Debug.enabled():
            logger.debug(
                f"  → tool_approval_request approval_id={approval_id}"
                f" session={_sess.id}"
                f" run_id={run_id}"
            )
            logger.debug(
                f"  → tool_approval_request approval_id={approval_id} details={payload}"
            )

        request_args = dict(payload)
        req_id = uuid.uuid4().hex
        request_args.pop("approval_id", None)

        event_payload = {
            "request_id": req_id,
            "function": "ui.request_tool_approval",
            "args": request_args,
            "session_id": payload.get("session_id"),
            "run_id": payload.get("run_id"),
            "approval_id": approval_id,
        }

        _sess.pending_client_requests[req_id] = dict(event_payload)
        self._client_request_sessions[req_id] = _sess

        targets: list[Any] = []
        if run_id:
            preferred = _sess.run_channels.get(run_id)
            if preferred is not None:
                targets.append(preferred)
        for ch in _sess.channels:
            if ch is None:
                continue
            if ch not in targets:
                targets.append(ch)

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_client_requests[req_id] = fut

        delivered_any = False
        dead_channels = []
        for ch in list(targets):
            if ch is None:
                continue
            ok = await self._safe_notify(
                ch,
                "client_request",
                event_payload,
            )
            if ok:
                delivered_any = True
            else:
                dead_channels.append(ch)

        for dead in dead_channels:
            try:
                _sess.channels.remove(dead)
            except ValueError:
                pass
            if run_id and _sess.run_channels.get(run_id) is dead:
                _sess.run_channels.pop(run_id, None)

        if not delivered_any and hasattr(_sess, "pending_events"):
            _sess.pending_events.append(
                (
                    "client_request",
                    event_payload,
                )
            )

        try:
            envelope = await fut
        finally:
            self._pending_client_requests.pop(req_id, None)
            self._forget_pending_request(req_id)

        if not isinstance(envelope, dict):
            raise RuntimeError("Invalid client response for tool approval")

        ok = envelope.get("ok")
        result = envelope.get("result") or {}
        error = envelope.get("error") or {}

        if not ok:
            return (
                False,
                False,
                (error.get("message") if isinstance(error, dict) else None),
            )

        approved = bool(result.get("approved"))
        always_approve = bool(result.get("always_approve"))
        error_message = result.get("error_message")

        if approved and always_approve:
            allow_set = getattr(_sess, "always_allow_tools", None)
            if allow_set is None:
                allow_set = set()
                setattr(_sess, "always_allow_tools", allow_set)
            allow_set.add(tool_name)

        return approved, always_approve, error_message

    async def start_run(
        self,
        prompt: str,
        session_id: str | None = None,
        variables: dict | None = None,
        context: Any | None = None,
        content: Any | None = None,
        workflow_name: str | None = None,
        group_id: str | None = None,
    ) -> dict:  # noqa: D401
        sess: Session = self._session_manager.get_or_create(session_id)
        channel = getattr(self, "channel", None)

        sess.cancel_requested = False
        sess.cancel_end_reason = None
        sess.current_stream_result = None

        # Store variables and/or context in session
        if variables is not None:
            sess.variables = variables
        if context is not None:
            sess.context = context

        # Store workflow_name and group_id for tracing
        if workflow_name is not None:
            sess.workflow_name = workflow_name
        if group_id is not None:
            sess.group_id = group_id

        # Initialize model_config from spec resolver if not already set
        if sess.model_config is None and self.spec.resolve_default_model_config:
            try:
                default_config = self.spec.resolve_default_model_config()
                if default_config:
                    sess.model_config = default_config
                    if default_config.get("name"):
                        sess.active_model = default_config.get("name")
                    # Notify UI of initial session state
                    try:
                        await self.request_client_function(
                            "ui.set_session_state",
                            {
                                "model": sess.active_model,
                                "label": default_config.get("label"),
                                "provider": default_config.get("provider"),
                                "max_input_tokens": default_config.get(
                                    "max_input_tokens"
                                ),
                                "max_output_tokens": default_config.get(
                                    "max_output_tokens"
                                ),
                            },
                            session=sess,
                            expect_result=False,
                        )
                    except Exception:
                        pass  # UI notification is best-effort
            except Exception as e:
                logger.debug(f"Failed to resolve default model config: {e}")

        # Track current channel for real-time notifications.
        if channel is not None and channel not in sess.channels:
            sess.channels.append(channel)

        if sess.current_task and not sess.current_task.done():
            raise RuntimeError("Run already active in this session")

        # --------------------------------------------------------------
        # Flush any events that were generated while the client was
        # disconnected (e.g. pending approval requests).
        # --------------------------------------------------------------

        if sess.pending_events:
            if DEBUG_SERVER:
                logger.debug(
                    f" ↻ replay {len(sess.pending_events)} queued events for {sess.id[:8]}"
                )
            still_pending: list[tuple[str, dict]] = []
            for meth, payl in list(sess.pending_events):
                delivered_any = False
                # Try every channel currently known for the session
                for ch in list(sess.channels):
                    if await self._safe_notify(ch, meth, payl):
                        delivered_any = True
                if not delivered_any:
                    still_pending.append((meth, payl))
            sess.pending_events = still_pending

        approval_callback_base = getattr(sess, "approval_callback_base", None)
        if approval_callback_base is None:
            approval_callback_base = (
                lambda name, args, metadata=None: self._request_tool_approval(
                    name, args, sess, metadata
                )
            )
            sess.approval_callback_base = approval_callback_base
        sess.approval_callback = approval_callback_base

        agent = await self._build_agent_for_session(sess, approval_callback_base)

        async def _worker():
            end_reason = "error"  # Default in case of unexpected exception
            final_details: dict | None = None
            error_info: dict | None = None

            current_task = asyncio.current_task()
            run_name = current_task.get_name() if current_task else None

            async def run_scoped_approval(
                tool_name: str, args: str, metadata: Optional[Dict[str, Any]] = None
            ):  # noqa: ANN001
                return await self._request_tool_approval(
                    tool_name, args, sess, metadata, _run_id=run_name
                )

            try:
                if hasattr(agent, "set_approval_callback") and callable(
                    getattr(agent, "set_approval_callback")
                ):
                    agent.set_approval_callback(run_scoped_approval)
            except Exception:
                pass
            sess.approval_callback = run_scoped_approval

            # Resolve runtime context once
            if hasattr(sess, "context") and sess.context is not None:
                runtime_context = sess.context
            elif hasattr(sess, "variables") and sess.variables is not None:
                if self.spec.build_context:
                    runtime_context = self.spec.build_context(sess.variables)
                else:
                    runtime_context = sess.variables
            else:
                # Fall back to spec-level context, building from factory if available
                if self.spec.build_context and self.spec.variables is not None:
                    runtime_context = self.spec.build_context(self.spec.variables)
                else:
                    runtime_context = self.spec.context

            # Retry policy (env configurable)
            import math

            max_retries = int(os.getenv("OMNIAGENTS_RETRY_MAX", "3"))
            base_delay = float(os.getenv("OMNIAGENTS_RETRY_BASE", "1.5"))

            def _is_retryable(ex: Exception) -> bool:
                """Check if an exception is transient and worth retrying.

                Uses exception types first (reliable), falls back to string matching
                for unknown errors.
                """
                # Never retry AgentsException types - these are permanent failures
                if isinstance(ex, AgentsException):
                    return False

                # Check OpenAI SDK exception types (most reliable)
                if RateLimitError is not None and isinstance(ex, RateLimitError):
                    return True
                if APIConnectionError is not None and isinstance(
                    ex, APIConnectionError
                ):
                    return True
                if APITimeoutError is not None and isinstance(ex, APITimeoutError):
                    return True
                if APIStatusError is not None and isinstance(ex, APIStatusError):
                    if hasattr(ex, "status_code") and ex.status_code in (
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):
                        return True
                if APIError is not None and isinstance(ex, APIError):
                    # Check HTTP status code if available
                    if hasattr(ex, "status_code") and ex.status_code in (
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):
                        return True

                # Check standard Python timeout/connection errors
                if isinstance(
                    ex, (asyncio.TimeoutError, TimeoutError, ConnectionError)
                ):
                    return True

                # Fall back to string matching for unknown transient errors
                # (kept minimal to avoid false positives)
                msg = str(ex).lower()
                retry_keywords = (
                    "rate limit",
                    "429",
                    "500",
                    "502",
                    "503",
                    "504",
                    "service unavailable",
                    "internal server error",
                    "bad gateway",
                    "gateway timeout",
                    "request timeout",
                    "overloaded",
                    "connection reset",
                    "connection refused",
                    "timed out",
                    "timeout while",
                )
                return any(k in msg for k in retry_keywords)

            attempt = 0
            include_user_message = True
            try:
                # Determine workflow_name: session override > spec config > default
                workflow_name_override = getattr(sess, "workflow_name", None)
                if workflow_name_override is None and isinstance(
                    self.spec.tracing_config, dict
                ):
                    workflow_name_override = self.spec.tracing_config.get(
                        "workflow_name"
                    )
                # Also get group_id from session if provided
                group_id_override = getattr(sess, "group_id", None)

                # Determine model provider based on session model config.
                # We must create an explicit provider with the correct
                # api_key/base_url so that switching models actually
                # routes requests to the right endpoint.
                model_provider = None
                session_model_config = getattr(sess, "model_config", None)
                if session_model_config:
                    provider_type = session_model_config.get("provider")
                    if provider_type == "litellm":
                        try:
                            from agents.extensions.models.litellm_provider import (
                                LitellmProvider,
                            )

                            model_provider = LitellmProvider()
                        except ImportError:
                            logger.warning(
                                "LiteLLM not installed. Install with: pip install litellm"
                            )
                    elif provider_type in ("openai", "openai-compatible", "azure"):
                        # Build an explicit OpenAIProvider with the
                        # model's own base_url / api_key so we don't
                        # rely on (possibly stale) env vars.
                        #
                        # IMPORTANT: We must always create an AsyncOpenAI
                        # client with an explicit base_url. If we pass
                        # base_url=None, the OpenAI SDK falls back to
                        # os.environ["OPENAI_BASE_URL"] which may point
                        # to a previously-configured provider (e.g. after
                        # startup set it for an openai-compatible default).
                        try:
                            from openai import AsyncOpenAI
                            from agents.models.openai_provider import OpenAIProvider

                            _base_url = session_model_config.get("base_url")
                            _api_key = session_model_config.get("api_key")

                            # Expand env var references like ${VAR}
                            def _expand(val):
                                if (
                                    val
                                    and isinstance(val, str)
                                    and val.startswith("${")
                                    and val.endswith("}")
                                ):
                                    return os.getenv(val[2:-1])
                                return val

                            _base_url = _expand(_base_url)
                            _api_key = _expand(_api_key)

                            # Fall back to env for api_key only
                            if not _api_key:
                                if provider_type == "azure":
                                    _api_key = os.getenv("AZURE_OPENAI_API_KEY")
                                else:
                                    _api_key = os.getenv("OPENAI_API_KEY")

                            # For standard OpenAI (no base_url in config),
                            # explicitly use the default OpenAI endpoint so
                            # the SDK doesn't read OPENAI_BASE_URL from env.
                            if not _base_url:
                                _base_url = "https://api.openai.com/v1"

                            is_azure = (
                                "openai.azure.com" in _base_url
                                or "services.ai.azure.com" in _base_url
                            )

                            if _api_key and is_azure:
                                # Azure needs api-key header instead of Bearer
                                client = AsyncOpenAI(
                                    api_key="dummy",
                                    base_url=_base_url,
                                    default_headers={"api-key": _api_key},
                                )
                                model_provider = OpenAIProvider(openai_client=client)
                            elif _api_key:
                                client = AsyncOpenAI(
                                    api_key=_api_key,
                                    base_url=_base_url,
                                )
                                model_provider = OpenAIProvider(openai_client=client)
                            # else: leave model_provider=None, fall through to defaults
                        except Exception as e:
                            logger.warning(
                                f"Failed to create model provider for session model: {e}"
                            )

                while True:
                    try:
                        # Get token limits from session model config
                        _max_input = (
                            session_model_config.get("max_input_tokens")
                            if session_model_config
                            else None
                        )
                        _max_output = (
                            session_model_config.get("max_output_tokens")
                            if session_model_config
                            else None
                        )
                        _model_label = (
                            session_model_config.get("label")
                            if session_model_config
                            else None
                        )
                        final_details = await stream_agent(
                            agent,
                            prompt,
                            sess,
                            self.channel,
                            service=self,
                            max_turns=self.spec.max_turns,
                            context=runtime_context,
                            max_input_tokens=_max_input,
                            max_output_tokens=_max_output,
                            include_user_message=include_user_message,
                            workflow_name=workflow_name_override,
                            group_id=group_id_override,
                            user_content=content,
                            model_provider=model_provider,
                            model_label=_model_label,
                        )
                        end_reason = sess.cancel_end_reason or "completed"
                        error_info = None
                        break
                    except asyncio.CancelledError:
                        end_reason = "cancelled"
                        raise
                    except MaxTurnsExceeded as e:
                        # MaxTurnsExceeded is a normal completion condition, not an error
                        end_reason = "max_turns"
                        error_info = None
                        logger.info(f"Agent reached max turns: {e}")
                        break
                    except (
                        InputGuardrailTripwireTriggered,
                        OutputGuardrailTripwireTriggered,
                        ToolInputGuardrailTripwireTriggered,
                        ToolOutputGuardrailTripwireTriggered,
                    ) as e:
                        # Guardrail violations - include detailed guardrail information
                        end_reason = "guardrail_violation"
                        error_info = _build_guardrail_error_info(e)
                        logger.warning(f"Guardrail triggered: {e}")
                        break
                    except (ModelBehaviorError, UserError) as e:
                        # Permanent failures - don't retry
                        end_reason = "error"
                        error_info = _build_agent_error_info(e)
                        logger.error(f"Permanent error: {e}", exc_info=True)
                        break
                    except AgentsException as e:
                        # Other agent exceptions - preserve run_data, don't retry
                        end_reason = "error"
                        error_info = _build_agent_error_info(e)
                        logger.error(f"Agent exception: {e}", exc_info=True)
                        break
                    except Exception as e:
                        # Transient errors (network, rate limit, etc.) - may retry
                        attempt += 1
                        if attempt <= max_retries and _is_retryable(e):
                            # Exponential backoff with jitter
                            delay = base_delay * (2 ** (attempt - 1))
                            logger.warning(
                                f"Retryable error (attempt {attempt}/{max_retries}): {e}"
                            )
                            # Announce retry status to client
                            try:
                                await self._broadcast_session(
                                    sess,
                                    "run_status",
                                    {
                                        "run_id": run_name,
                                        "status": "retrying",
                                        "message": str(e),
                                        "attempt": attempt,
                                        "total": max_retries,
                                        "next_delay": delay,
                                    },
                                    track_live=True,
                                )
                            except Exception:
                                pass
                            try:
                                await asyncio.sleep(delay)
                            except asyncio.CancelledError:
                                end_reason = "cancelled"
                                raise
                            # On retries, don't re-append the user message to history
                            include_user_message = False
                            continue
                        # Exhausted retries or non-retryable unknown error
                        end_reason = "error"
                        _msg = str(e).strip()
                        if not _msg:
                            _msg = e.__class__.__name__
                        error_info = {"message": _msg, "type": e.__class__.__name__}
                        logger.error(f"Non-retryable error: {e}", exc_info=True)
                        break
            finally:
                # Inform client the run finished
                payload = {
                    "run_id": run_name,
                    "end_reason": end_reason,
                }
                if error_info is not None:
                    payload["error"] = error_info
                if isinstance(final_details, dict):
                    if "usage" in final_details:
                        payload["usage"] = final_details["usage"]
                    if final_details.get("model") is not None:
                        payload["model"] = final_details.get("model")
                    if final_details.get("max_input_tokens") is not None:
                        payload["max_input_tokens"] = final_details.get(
                            "max_input_tokens"
                        )
                    if final_details.get("max_output_tokens") is not None:
                        payload["max_output_tokens"] = final_details.get(
                            "max_output_tokens"
                        )
                    if final_details.get("truncation") is not None:
                        payload["truncation"] = final_details.get("truncation")
                # Debug log the payload for visibility when debugging (single source)
                _debug_rpc("run_end", payload)
                # Best-effort notify; avoid raising if socket is gone
                await self._broadcast_session(
                    sess, "run_end", payload, queue_if_unseen=True
                )

                if run_name:
                    try:
                        sess.run_channels.pop(run_name, None)
                    except Exception:
                        pass
                sess.cancel_requested = False
                sess.cancel_end_reason = None
                if sess.current_task is task:
                    sess.current_task = None
                if sess.current_stream_result is not None:
                    sess.current_stream_result = None
                self._clear_live_state(sess)

        task_name = f"run_{uuid.uuid4().hex}"
        _debug_rpc(
            "start_run",
            {
                "prompt": prompt,
                "session_id_in": session_id,
                "session_id": sess.id,
                "run_id": task_name,
            },
        )
        task = asyncio.create_task(_worker(), name=task_name)
        self._session_manager.bind_task(task, sess)
        if channel is not None:
            sess.run_channels[task_name] = channel
        # Proactively notify clients that the run has started so UIs can
        # correlate subsequent status and end events even if they arrive
        # before the RPC response is processed client-side.
        await self._broadcast_session(
            sess,
            "run_started",
            {
                "run_id": task_name,
                "session_id": sess.id,
            },
            track_live=True,
        )
        return {"run_id": task_name, "session_id": sess.id}

    async def stop_run(self, run_id: str) -> bool:  # noqa: D401
        _debug_rpc("stop_run", {"run_id": run_id})
        return await self._session_manager.cancel(run_id)

    async def send_user_message(self, run_id: str, content: str) -> None:  # noqa: D401
        _debug_rpc("send_user_message", {"run_id": run_id, "content": content})
        sess = self._session_manager.get_by_run_id(run_id)
        if not sess:
            raise RuntimeError("Unknown run_id")
        msg = {"role": "user", "content": content}
        sess.append_message(msg)
        # No immediate return value; next assistant chunk will arrive via stream.

    # ------------------------------------------------------------------
    #  NEW: expose conversation history to clients ---------------------
    # ------------------------------------------------------------------
    async def get_session_history(self, session_id: str) -> list[dict]:  # noqa: D401
        """Return JSON-serialisable history for *session_id*."""
        _debug_rpc("get_session_history", {"session_id": session_id})
        sess: Session = self._session_manager.get_or_create(session_id)

        # Track this Rpc channel too – allows further live events while
        # a run is still streaming.
        if self.channel not in sess.channels:
            sess.channels.append(self.channel)

        # First, replay any queued events (e.g. unfinished approval
        # requests) so the reconnecting client sees the current state
        # before we hand back the static history.
        if sess.pending_events:
            still_pending: list[tuple[str, dict]] = []
            for meth, payl in list(sess.pending_events):
                delivered_any = False
                # Try every channel currently known for the session
                for ch in list(sess.channels):
                    if await self._safe_notify(ch, meth, payl):
                        delivered_any = True
                if not delivered_any:
                    still_pending.append((meth, payl))
            sess.pending_events = still_pending

        await self._replay_live_state(sess, self.channel)

        from omniagents.core.runtime.bridge import _filter_history_for_api

        result = _filter_history_for_api(sess.history)
        _debug_rpc("get_session_history result", {"history": result})
        return result

    async def fork_session(
        self, session_id: str, *, new_session_id: str | None = None
    ) -> dict:  # noqa: D401
        _debug_rpc(
            "fork_session",
            {"session_id": session_id, "new_session_id": new_session_id},
        )
        sess: Session = self._session_manager.get_or_create(session_id)
        new_sess = self._session_manager.fork_session(
            sess, new_session_id=new_session_id
        )
        try:
            await self._broadcast_session(
                sess,
                "session_forked",
                {"session_id": sess.id, "new_session_id": new_sess.id},
                track_live=True,
            )
        except Exception:
            pass
        return {"session_id": sess.id, "new_session_id": new_sess.id}

    async def list_sessions(self) -> list[dict]:  # noqa: D401
        """Return metadata for all sessions."""
        result = self._session_manager.list_sessions()
        _debug_rpc("list_sessions", {"result": result})
        return result

    async def get_agent_info(self) -> dict:  # noqa: D401
        info = {}
        spec = self.spec
        try:
            info["name"] = getattr(spec, "name", None)
            info["welcome_text"] = getattr(spec, "welcome_text", None)
        except Exception:
            pass
        _debug_rpc("get_agent_info", info)
        return info

    async def archive_session(self, session_id: str) -> bool:  # noqa: D401
        """Archive *session_id* so clients can hide it."""
        _debug_rpc("archive_session", {"session_id": session_id})
        ok = self._session_manager.archive_session(session_id)
        if ok:
            sess = getattr(self._session_manager, "_by_id", {}).get(session_id)
            if sess is not None:
                # Signal MCP lifecycle task to shutdown
                shutdown_event = getattr(sess, "_mcp_shutdown", None)
                if shutdown_event:
                    shutdown_event.set()

                # Wait for lifecycle task to complete cleanup
                lifecycle_task = getattr(sess, "_mcp_lifecycle_task", None)
                if lifecycle_task and not lifecycle_task.done():
                    try:
                        # Give it a reasonable timeout to cleanup gracefully
                        await asyncio.wait_for(lifecycle_task, timeout=5.0)
                    except asyncio.TimeoutError:
                        # If cleanup takes too long, cancel the task
                        lifecycle_task.cancel()
                        try:
                            await lifecycle_task
                        except asyncio.CancelledError:
                            pass
                    except Exception:
                        # Log but don't fail the archive operation
                        pass

                # Clean up session attributes
                setattr(sess, "_mcp_shutdown", None)
                setattr(sess, "_mcp_lifecycle_task", None)
                setattr(sess, "_mcp_servers_active", [])

        manager = getattr(self, "terminal_manager", None)
        if manager is not None:
            await manager.cleanup_session(session_id)
        return ok

    async def delete_session(self, session_id: str) -> bool:  # noqa: D401
        """Permanently delete a session and all its data.

        This deletes:
        - The session record
        - All message history
        - All audio metadata
        - Cleans up any active MCP servers and terminal sessions

        Args:
            session_id: The session ID to delete

        Returns:
            True if the session was deleted, False if it didn't exist
        """
        _debug_rpc("delete_session", {"session_id": session_id})

        # First, cleanup any active resources (similar to archive)
        sess = getattr(self._session_manager, "_by_id", {}).get(session_id)
        if sess is not None:
            # Signal MCP lifecycle task to shutdown
            shutdown_event = getattr(sess, "_mcp_shutdown", None)
            if shutdown_event:
                shutdown_event.set()

            # Wait for lifecycle task to complete cleanup
            lifecycle_task = getattr(sess, "_mcp_lifecycle_task", None)
            if lifecycle_task and not lifecycle_task.done():
                try:
                    await asyncio.wait_for(lifecycle_task, timeout=5.0)
                except asyncio.TimeoutError:
                    lifecycle_task.cancel()
                    try:
                        await lifecycle_task
                    except asyncio.CancelledError:
                        pass
                except Exception:
                    pass

        # Cleanup terminal session if any
        manager = getattr(self, "terminal_manager", None)
        if manager is not None:
            await manager.cleanup_session(session_id)

        # Delete from database
        from omniagents.core.session import history_db

        project_slug = getattr(self._session_manager, "_project_slug", None)
        agent_slug = getattr(self._session_manager, "_agent_slug", None)

        deleted = history_db.delete_session(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        )

        # Remove from in-memory session manager
        if sess is not None and hasattr(self._session_manager, "_by_id"):
            self._session_manager._by_id.pop(session_id, None)

        return deleted

    # (Removed) get_model_info: model metadata is now sent inline with usage events

    async def get_user_history(
        self,
        limit: int | None = None,
        include_archived: bool | None = None,
    ) -> list[dict]:  # noqa: D401
        """Return recent user messages across all sessions.

        Args:
            limit: Maximum number of items to return (default 200)
            include_archived: Whether to include archived sessions (default True)
        """
        lim = int(limit) if isinstance(limit, int) and limit > 0 else 200
        inc = True if include_archived is None else bool(include_archived)
        _debug_rpc(
            "get_user_history",
            {"limit": lim, "include_archived": inc},
        )
        try:
            return self._session_manager.get_user_messages(lim, inc)
        except Exception:
            return []
