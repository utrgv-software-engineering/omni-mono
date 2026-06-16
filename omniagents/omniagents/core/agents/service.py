"""
Centralized AgentService for handling all agent-related JSON-RPC operations.

This service provides a unified interface for agent operations across different
execution modes (CLI, Flet, Server), exposing methods via JSON-RPC protocol.
"""

import os
import asyncio
import contextvars
import time
import uuid
import copy
import logging
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Optional, Dict, Any, Callable
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
from omniagents.core.agents.approval import configure_tool_approval
from omniagents.core.session import Session, SessionManager
from omniagents.core.session.manager import QueuedItem
from omniagents.core.session.history_db import get_user_messages
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.agents.mcp import (
    build_mcp_servers,
    _instantiate_server,
    _resolve_setting_placeholders,
)
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


# Run-scoped handle to the AgentService that "owns" the currently
# executing task tree. Implemented as a ``ContextVar`` so background
# callbacks (worker completion, bash-job watchers, ``Session.set_tick``,
# ``@client_function_tool`` bodies) that don't receive a service via
# ctx can still call back into the *right* service in multi-service
# processes — namely, whichever service started the run that spawned
# their task. ``asyncio.create_task`` snapshots the current context so
# tasks created during a run inherit the value automatically; a
# sub-service constructed in a nested scope (e.g. ``LocalAgent``'s
# in-process ``AgentService``) does not bleed back into the parent task
# the way a module-global would.
#
# Mirrors the same pattern used by ``omniagents.rpc.base._current_channel``
# and ``omniagents.workspace._runtime._workspace_var``.
_current_service: contextvars.ContextVar["AgentService | None"] = (
    contextvars.ContextVar("omniagents_current_service", default=None)
)


def current_service() -> "AgentService | None":
    """Return the AgentService that owns the currently executing task
    tree, or None if no run has activated. Tools should treat None as
    "no host to notify" and drop the call gracefully."""
    return _current_service.get()


class AgentService(RpcMethodsBase):
    """JSON-RPC methods for agent operations.

    This service handles:
    - Starting and stopping agent runs
    - Managing conversation history
    - Session management (list, archive)
    - Tool approval workflows
    - Event broadcasting to multiple channels
    """

    def __init__(
        self,
        spec: AgentSpec,
        *,
        default_workspace_root: str | None = None,
        workspace_root_resolver: "Callable[[str | None], str | None] | None" = None,
    ) -> None:
        """Initialize AgentService with an agent specification.

        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings.
            default_workspace_root: Optional fallback used when ``start_run``
                is invoked without ``workspace_root`` on either context or
                variables. CLI-style backends (ink/web) typically pass
                ``os.getcwd()`` here so an agent author who imports the
                builtin tools "just works" from a terminal. Production
                server deployments should leave this ``None`` so a missing
                ``workspace_root`` raises ``WorkspaceRootRequiredError``
                rather than anchoring the agent to the server process's
                cwd.
            workspace_root_resolver: Maps a *requested* ``workspace_root``
                (the agent's navigable cwd, from context/variables/payload)
                to the *effective* one, and is authoritative — it runs at the
                single point where ``workspace_root`` is written to a session.
                ``omni serve`` injects one that clamps a sandbox cwd to within
                the manifest root (so a host path can never root a sandbox
                run) while still allowing navigation among container
                subdirectories, and that defaults an empty request to the
                workspace root. Returning ``None`` falls back to the normal
                context/variables/``default_workspace_root`` resolution.
        """
        super().__init__()
        # In-process per-session run_end listeners (autopilot loops,
        # /goal server function, scheduled hooks). Fan-out happens
        # inside _broadcast_session before client channels are touched.
        self._run_end_listeners: Dict[str, list] = {}
        self.spec = spec
        self._default_workspace_root: str | None = (
            default_workspace_root
            if isinstance(default_workspace_root, str)
            and default_workspace_root.strip()
            else None
        )
        self._workspace_root_resolver = workspace_root_resolver
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
            # Only initialize tracing when the agent spec carries an explicit
            # tracing_config. Falling back to setup_tracing(None) here would
            # apply a "studio/omniagents" default that tears down any tracing
            # processor configured by an outer context (e.g., the eval pipeline
            # in core/eval/pipeline.py, which initializes studio tracing for
            # the project before constructing per-scenario AgentServices).
            tracing_cfg = getattr(self.spec, "tracing_config", None)
            if tracing_cfg:
                from omniagents.core.tracing import setup_tracing

                setup_tracing(tracing_cfg)
        except Exception:
            pass

        # Auto-activate platform providers if credentials are available.
        # Checks OMNI_PLATFORM_URL env var or ~/.config/omni_code/platform.json.
        # Merges platform security config into the spec so audit, auth, and
        # policy providers activate without manual project.yml configuration.
        try:
            from omniagents.core.providers.platform.bootstrap import (
                maybe_inject_platform_config,
            )

            maybe_inject_platform_config(self.spec)
        except Exception:
            pass

        # Set up session encryption provider if configured
        try:
            sec = getattr(self.spec, "security_config", None) or {}
            enc_cfg = (sec.get("providers") or {}).get("session_encryption")
            if enc_cfg and enc_cfg.get("type", "none") != "none":
                from omniagents.core.providers import resolve_provider
                from omniagents.core.session.history_db import set_encryption_provider

                enc_provider = resolve_provider("session_encryption", enc_cfg)
                set_encryption_provider(enc_provider)
        except Exception:
            pass

        # Purge old sessions if retention policy is configured
        try:
            sec = getattr(self.spec, "security_config", None) or {}
            retention_days = (sec.get("sessions") or {}).get("retention_days", 0)
            if retention_days > 0:
                from omniagents.core.session.history_db import purge_old_sessions

                purged = purge_old_sessions(
                    retention_days,
                    project_slug=project_slug,
                    agent_slug=agent_slug,
                )
                if purged:
                    print(
                        f"Security: purged {purged} session(s) older than {retention_days} days"
                    )
        except Exception:
            pass
        # Client-function request tracking (server→client RPC-style)
        self._pending_client_requests: dict[str, asyncio.Future] = {}
        # Optional capability registry per connected channel
        self._client_capabilities: dict[Any, dict] = {}
        self._client_request_sessions: dict[str, Session] = {}
        # Tool-approval futures keyed by call_id (SDK 0.16 interruption flow)
        self._pending_tool_approvals: dict[str, tuple[asyncio.Future, Session]] = {}
        # MCP-approval futures keyed by request_id (hosted MCP approvals from
        # SDK 0.16 ``HostedMCPTool``). Tracked separately from function-tool
        # approvals so the wire format stays clean.
        self._pending_mcp_approvals: dict[str, tuple[asyncio.Future, Session]] = {}
        # Server functions registry (client→server dynamic functions)
        # Map name -> {"func": callable, "description": str | None, "params_schema": dict | None, "result_schema": dict | None}
        self._server_functions: dict[str, dict] = {}
        self._runtime_hooks: dict[str, list[Any]] = {}

        # Register audit backend if enabled in security config
        try:
            sec = getattr(self.spec, "security_config", None) or {}
            audit_cfg = sec.get("audit") or {}
            if audit_cfg.get("enabled"):
                audit_backend = audit_cfg.get("backend", "file")

                if audit_backend == "file":
                    from omniagents.core.audit.file_backend import create_audit_hook

                    # Resolve secrets provider for HMAC signing (if configured)
                    _secrets_provider = None
                    try:
                        _secrets_cfg = (sec.get("providers") or {}).get("secrets")
                        from omniagents.core.providers import resolve_provider

                        _secrets_provider = resolve_provider("secrets", _secrets_cfg)
                    except Exception:
                        pass

                    self.register_runtime_hook(
                        "audit",
                        create_audit_hook(
                            audit_cfg,
                            project_slug=project_slug,
                            secrets_provider=_secrets_provider,
                        ),
                    )
                elif audit_backend == "remote":
                    from omniagents.core.providers.platform.audit import (
                        create_remote_audit_hook,
                    )

                    self.register_runtime_hook(
                        "audit",
                        create_remote_audit_hook(
                            audit_cfg,
                            project_slug=project_slug,
                        ),
                    )
        except Exception:
            pass

        # Register notification provider as an audit hook (if configured)
        try:
            notif_cfg = (sec.get("providers") or {}).get("notification")
            if notif_cfg and notif_cfg.get("type", "none") != "none":
                # Ensure notification domain is loaded
                import omniagents.core.providers.notification  # noqa: F401
                from omniagents.core.providers import resolve_provider as _rp

                _notif_provider = _rp("notification", notif_cfg)

                async def _notification_hook(
                    *, service: Any = None, event: str = "", **kwargs: Any
                ) -> None:
                    try:
                        await _notif_provider.notify(kwargs)
                    except Exception:
                        pass

                self.register_runtime_hook("audit", _notification_hook)
        except Exception:
            pass

        # Register platform policy hook if configured
        try:
            policy_cfg = (sec.get("providers") or {}).get("policy")
            if policy_cfg and policy_cfg.get("type") == "platform":
                from omniagents.core.providers.platform.policy import (
                    create_policy_hook,
                )

                self.register_runtime_hook(
                    "run_started", create_policy_hook(policy_cfg)
                )
        except Exception:
            pass

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

        # MCP resource access (SDK 0.16): list/read MCP resources from servers
        # already attached to the agent via ``mcp_servers:`` in YAML.
        self.register_server_function(
            "mcp.list_resources",
            self._sf_mcp_list_resources,
            description=(
                "List resources exposed by a configured MCP server. "
                "``server_name`` matches the ``name`` field of an entry in the "
                "agent's ``mcp_servers`` config."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "cursor": {"type": "string"},
                },
                "required": ["server_name"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "resources": {"type": "array"},
                    "next_cursor": {"type": "string"},
                },
                "required": ["server_name", "resources"],
            },
            strict=False,
        )
        self.register_server_function(
            "mcp.read_resource",
            self._sf_mcp_read_resource,
            description=(
                "Read a resource (by URI) from a configured MCP server. "
                "``server_name`` matches the ``name`` field of an entry in the "
                "agent's ``mcp_servers`` config."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "uri": {"type": "string"},
                },
                "required": ["server_name", "uri"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "uri": {"type": "string"},
                    "contents": {"type": "array"},
                },
                "required": ["server_name", "uri", "contents"],
            },
            strict=False,
        )
        # MCP Apps (https://github.com/modelcontextprotocol/ext-apps):
        # list_tools surfaces ``_meta.ui.resourceUri`` so hosts can discover
        # which tools have an embedded UI before invoking them. call_tool is
        # an out-of-band dispatch used for postMessage routing from rendered
        # MCP-Apps UIs back to the server — the agent does not see these.
        self.register_server_function(
            "mcp.list_tools",
            self._sf_mcp_list_tools,
            description=(
                "List tools exposed by a configured MCP server, including "
                "``_meta`` (so MCP-Apps hosts can detect ``_meta.ui.resourceUri``). "
                "``server_name`` matches an entry in the agent's ``mcp_servers``."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "cursor": {"type": "string"},
                },
                "required": ["server_name"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "tools": {"type": "array"},
                    "next_cursor": {"type": "string"},
                },
                "required": ["server_name", "tools"],
            },
            strict=False,
        )
        self.register_server_function(
            "mcp.call_tool",
            self._sf_mcp_call_tool,
            description=(
                "Invoke a tool on a configured MCP server out-of-band. The "
                "agent does NOT observe this call — it is intended for hosts "
                "routing postMessage actions from rendered MCP-Apps UIs back "
                "to the server."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server_name", "tool_name"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "result": {"type": "object"},
                },
                "required": ["server_name", "tool_name", "result"],
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

        # Merge spec-level and session-level (dynamic) MCP server configs
        dynamic_cfgs: list = getattr(session, "_dynamic_mcp_servers", None) or []
        if not self.spec.mcp_servers and not dynamic_cfgs:
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
                    # Build all MCP servers (spec-level + dynamic session-level)
                    servers = build_mcp_servers(self.spec, settings)

                    # Build dynamic MCP servers from session (e.g. launcher-provided)
                    for dyn_cfg in dynamic_cfgs:
                        try:
                            from .specs import MCPServerConfig as _MCPCfg

                            cfg = _MCPCfg(
                                name=dyn_cfg.get("name", "dynamic"),
                                type=dyn_cfg.get("type", "streamable_http"),
                                params=dyn_cfg.get("params", {}),
                                options=dyn_cfg.get("options"),
                            )
                            resolved = _resolve_setting_placeholders(
                                copy.deepcopy(cfg.params), settings
                            )
                            servers.append(_instantiate_server(cfg, resolved))
                        except Exception as dyn_err:
                            from omniagents.core.utils.logging import get_logger as _gl

                            _gl(__name__).debug(
                                f"Dynamic MCP server '{dyn_cfg.get('name', '?')}' "
                                f"failed to build: {dyn_err}"
                            )

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

    def _build_client_tools(self, session: Session) -> list:
        """Build FunctionTool proxies for client-provided tools.

        Each tool, when invoked by the agent, sends a ``client_request`` with
        ``function="tool.call"`` over the existing WebSocket and awaits the
        ``client_response`` containing the tool result.  This avoids any
        additional network connections between container and host.
        """
        from agents.tool import FunctionTool

        defs: list = getattr(session, "_client_tools", None) or []
        if not defs:
            return []

        tools: list = []
        service_ref = self

        async def _never_needs_approval(ctx, args: dict, call_id: str) -> bool:
            return False

        for defn in defs:
            tool_name = defn.get("name")
            description = defn.get("description", "")
            parameters = defn.get("parameters", {"type": "object", "properties": {}})
            if not tool_name:
                continue

            # Each tool gets its own closure so the captured name is correct
            def _make_invoker(_name: str):
                async def _invoke(ctx, params_json: str):
                    import json as _json

                    try:
                        args = _json.loads(params_json) if params_json else {}
                    except Exception:
                        args = {}
                    resp = await service_ref.request_client_function(
                        "tool.call",
                        {"tool": _name, "arguments": args},
                        session=session,
                        expect_result=True,
                        timeout_ms=120_000,
                    )
                    if resp is None:
                        return _json.dumps({"error": "Client tool call timed out"})
                    if not resp.get("ok"):
                        err = resp.get("error", {})
                        return _json.dumps(
                            {"error": err.get("message", "Client tool call failed")}
                        )
                    return _json.dumps(resp.get("result", {}))

                return _invoke

            tools.append(
                FunctionTool(
                    name=tool_name,
                    description=description,
                    params_json_schema=parameters,
                    on_invoke_tool=_make_invoker(tool_name),
                    strict_json_schema=False,
                    needs_approval=(
                        _never_needs_approval if defn.get("safe") is True else False
                    ),
                )
            )

        return tools

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

        # Build client tool proxies (tools executed on the launcher via WebSocket)
        client_tools = self._build_client_tools(session)

        try:
            if self.spec.build_agent:
                primary_server = active_servers[0] if active_servers else None
                agent = await self.spec.build_agent(settings, primary_server, stub_app)
            else:
                agent = await _default_build_agent(
                    settings,
                    active_servers,
                    self.spec,
                    approval_callback,
                    session=session,
                )

            agent = self._clone_agent(agent)

            # Inject client tools (proxied to launcher via WebSocket)
            if client_tools:
                existing = list(getattr(agent, "tools", []) or [])
                existing.extend(client_tools)
                agent.tools = existing

            self._apply_session_tool_policy(session, agent)

            # Wire tool-approval predicates per session if safety is enabled.
            # The SDK pauses the run with ToolApprovalItem interruptions when
            # any predicate returns True; the bridge handles request/response.
            opts = self.spec.safe_agent_options
            _use_safe = self.spec.use_safe_agent
            _skip_approvals = bool(getattr(opts, "skip_approvals", False))
            if (
                self.spec.security_config
                and self.spec.security_config.get("safety_mode") == "enforced"
            ):
                _use_safe = True
                _skip_approvals = False
            if _use_safe:
                configure_tool_approval(
                    agent,
                    session=session,
                    safe_tool_names=opts.safe_tool_names if opts else None,
                    safe_tool_patterns=opts.safe_tool_patterns if opts else None,
                    skip_approvals=_skip_approvals,
                )

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
        # The session's always_allow_tools / always_allow_patterns sets are
        # consulted directly by the per-tool needs_approval predicates wired
        # in configure_tool_approval, so no per-agent state to mirror here.
        # We still surface the merged set on the agent for any external code
        # that introspects it.
        names = getattr(session, "always_allow_tools", None)
        if not names:
            return
        try:
            existing = set(getattr(agent, "always_allow_tools", set()))
            merged = existing | set(names)
            setattr(agent, "always_allow_tools", merged)
        except Exception:
            pass

    @staticmethod
    def _mcp_allow_key(server_label: Any, tool_name: Any) -> tuple[str, str] | None:
        server = str(server_label or "").strip()
        tool = str(tool_name or "").strip()
        if not server or not tool:
            return None
        return (server, tool)

    @classmethod
    def _session_mcp_allowlist(cls, session: Session) -> set[tuple[str, str]]:
        allow_set = getattr(session, "always_allow_mcp_tools", None)
        if allow_set is None:
            allow_set = set()
            setattr(session, "always_allow_mcp_tools", allow_set)
        return allow_set

    @classmethod
    def _apply_safe_tool_overrides(
        cls, session: Session, safe_tool_overrides: dict | None
    ) -> None:
        if safe_tool_overrides is None:
            return

        override_names = safe_tool_overrides.get("safe_tool_names", [])
        override_patterns = safe_tool_overrides.get("safe_tool_patterns", [])
        if override_names:
            session.always_allow_tools.update(override_names)
        if override_patterns:
            session.always_allow_patterns.update(override_patterns)

        mcp_entries = safe_tool_overrides.get("safe_mcp_tools", [])
        if not isinstance(mcp_entries, list):
            return

        allow_set = cls._session_mcp_allowlist(session)
        for entry in mcp_entries:
            if not isinstance(entry, dict):
                continue
            key = cls._mcp_allow_key(entry.get("server_label"), entry.get("tool_name"))
            if key is not None:
                allow_set.add(key)

    @classmethod
    def _is_mcp_tool_always_allowed(
        cls, session: Session, server_label: Any, tool_name: Any
    ) -> bool:
        key = cls._mcp_allow_key(server_label, tool_name)
        if key is None:
            return False
        return key in cls._session_mcp_allowlist(session)

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

    @staticmethod
    def _ws_root_from(d: Any) -> str | None:
        if isinstance(d, dict):
            v = d.get("workspace_root")
            if isinstance(v, str) and v.strip():
                return v
        return None

    def _resolve_workspace_root(self, requested: str | None) -> str | None:
        """Map a requested workspace_root to the effective one.

        Authoritative when a resolver is configured — it runs at the single
        point workspace_root is written, so e.g. a sandbox cwd is always
        clamped to within the manifest root (no host-path leak) while
        navigation among container subdirs is preserved. Without a resolver,
        the requested value passes through unchanged."""
        resolver = self._workspace_root_resolver
        if resolver is None:
            return requested
        try:
            resolved = resolver(requested)
        except Exception:
            return requested
        if isinstance(resolved, str) and resolved.strip():
            return resolved
        return None

    def _apply_session_metadata(
        self,
        session: Session,
        *,
        context: Any | None = None,
        context_provided: bool = False,
        variables: dict | None = None,
        variables_provided: bool = False,
        workspace_root: str | None = None,
    ) -> None:
        """Shared helper that applies context/variables to a session.

        Handles:
        - Merging context and variables onto the session
        - Extracting transient keys (``client_tools``, ``mcp_servers``) before
          they are persisted
        - Injecting ``workspace_root`` into both context and variables
        - Persisting metadata when anything changed
        - Registering the current channel on the session
        """
        if self.channel is not None and self.channel not in session.channels:
            session.channels.append(self.channel)

        context_value = (
            context if context_provided else getattr(session, "context", None)
        )
        variables_value = (
            variables if variables_provided else getattr(session, "variables", None)
        )

        # Resolve the effective workspace_root (the agent's navigable cwd).
        # The requested value is the one passed in, else whatever already sits
        # on the session. This is the single point where workspace_root is
        # written, so applying the resolver here is authoritative: a sandbox
        # cwd is clamped to within the manifest root (a host path can never
        # leak in) while navigation among container subdirs is preserved.
        requested_root = workspace_root
        if requested_root is None:
            requested_root = self._ws_root_from(context_value) or self._ws_root_from(
                variables_value
            )
        workspace_root = self._resolve_workspace_root(requested_root)

        def _ensure_mapping(value):
            if isinstance(value, dict):
                return dict(value)
            if value is None:
                return {}
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            return {}

        updated = context_provided or variables_provided
        if workspace_root:
            context_value = _ensure_mapping(context_value)
            context_value["workspace_root"] = workspace_root
            variables_value = _ensure_mapping(variables_value)
            variables_value["workspace_root"] = workspace_root
            updated = True

        # Stamp session_id into the run context so tools can scope per-session
        # state (e.g. background bash jobs filter their snapshot by session
        # so multiple browser tabs / parallel sessions don't leak state at
        # each other). Mirrors how workspace_root rides on the context.
        if getattr(session, "id", None):
            context_value = _ensure_mapping(context_value)
            context_value.setdefault("session_id", session.id)

        session.context = context_value
        session.variables = variables_value

        # Extract transient keys from variables — these are connection-scoped
        # and must not be persisted to the database.
        if isinstance(variables_value, dict):
            dynamic_mcp = variables_value.pop("mcp_servers", None)
            if dynamic_mcp and isinstance(dynamic_mcp, list):
                session._dynamic_mcp_servers = dynamic_mcp

            client_tools_defs = variables_value.pop("client_tools", None)
            if client_tools_defs and isinstance(client_tools_defs, list):
                session._client_tools = client_tools_defs

        if updated:
            self._session_manager.persist_metadata(
                session,
                context=context_value,
                variables=variables_value,
            )

    def _sf_session_ensure(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        requested_id = payload.get("session_id")

        target_session: Session
        if session is not None and (not requested_id or session.id == requested_id):
            target_session = session
        else:
            target_session = self._session_manager.get_or_create(requested_id)

        self._apply_session_metadata(
            target_session,
            context=payload.get("context") if "context" in payload else None,
            context_provided="context" in payload,
            variables=payload.get("variables") if "variables" in payload else None,
            variables_provided="variables" in payload,
            workspace_root=payload.get("workspace_root"),
        )

        effective_workspace = None
        ctx = getattr(target_session, "context", None)
        if isinstance(ctx, dict):
            effective_workspace = ctx.get("workspace_root")
        if effective_workspace is None:
            vrs = getattr(target_session, "variables", None)
            if isinstance(vrs, dict):
                effective_workspace = vrs.get("workspace_root")

        return {
            "session_id": target_session.id,
            "workspace_root": effective_workspace,
            "context": target_session.context,
            "variables": target_session.variables,
        }

    # ------------------------------------------------------------------
    #  MCP resource server functions (SDK 0.16+)
    # ------------------------------------------------------------------

    async def _resolve_mcp_server_by_name(
        self, session: Session | None, server_name: str
    ):
        """Look up an active MCP server by name for the session/agent.

        Ensures session-level MCP servers are running, then matches by the
        ``name`` attribute (which mirrors the YAML ``mcp_servers[*].name``).
        Returns ``None`` if no server matches.
        """
        if session is None:
            return None
        try:
            servers = await self._ensure_session_mcp_servers(session)
        except Exception:
            servers = list(getattr(session, "_mcp_servers_active", []) or [])
        for server in servers or []:
            if getattr(server, "name", None) == server_name:
                return server
        return None

    async def _sf_mcp_list_resources(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        server_name = str(payload.get("server_name") or "")
        cursor = payload.get("cursor")
        if not server_name:
            raise ValueError("server_name is required")

        server = await self._resolve_mcp_server_by_name(session, server_name)
        if server is None:
            raise ValueError(f"Unknown or inactive MCP server: {server_name}")
        if not hasattr(server, "list_resources"):
            raise ValueError(
                f"MCP server '{server_name}' does not support list_resources "
                "(SDK 0.16+ required)"
            )

        result = (
            await server.list_resources(cursor=cursor)
            if cursor
            else await server.list_resources()
        )
        # ``ListResourcesResult`` from the mcp package is a pydantic model.
        resources_attr = getattr(result, "resources", []) or []
        next_cursor = getattr(result, "next_cursor", None) or getattr(
            result, "nextCursor", None
        )
        out: list[dict] = []
        for res in resources_attr:
            if hasattr(res, "model_dump"):
                try:
                    out.append(res.model_dump(mode="json"))
                    continue
                except Exception:
                    pass
            if hasattr(res, "dict"):
                try:
                    out.append(res.dict())
                    continue
                except Exception:
                    pass
            out.append({"raw": str(res)})
        response: dict[str, Any] = {
            "server_name": server_name,
            "resources": out,
        }
        if next_cursor:
            response["next_cursor"] = next_cursor
        return response

    async def _sf_mcp_read_resource(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        server_name = str(payload.get("server_name") or "")
        uri = str(payload.get("uri") or "")
        if not server_name:
            raise ValueError("server_name is required")
        if not uri:
            raise ValueError("uri is required")

        server = await self._resolve_mcp_server_by_name(session, server_name)
        if server is None:
            raise ValueError(f"Unknown or inactive MCP server: {server_name}")
        if not hasattr(server, "read_resource"):
            raise ValueError(
                f"MCP server '{server_name}' does not support read_resource "
                "(SDK 0.16+ required)"
            )

        result = await server.read_resource(uri)
        contents_attr = getattr(result, "contents", []) or []
        out: list[dict] = []
        for content in contents_attr:
            if hasattr(content, "model_dump"):
                try:
                    out.append(content.model_dump(mode="json", exclude_none=True))
                    continue
                except Exception:
                    pass
            if hasattr(content, "dict"):
                try:
                    out.append(content.dict(exclude_none=True))
                    continue
                except Exception:
                    pass
            out.append({"raw": str(content)})
        return {
            "server_name": server_name,
            "uri": uri,
            "contents": out,
        }

    async def _sf_mcp_list_tools(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        server_name = str(payload.get("server_name") or "")
        cursor = payload.get("cursor")
        if not server_name:
            raise ValueError("server_name is required")

        server = await self._resolve_mcp_server_by_name(session, server_name)
        if server is None:
            raise ValueError(f"Unknown or inactive MCP server: {server_name}")
        if not hasattr(server, "list_tools"):
            raise ValueError(f"MCP server '{server_name}' does not support list_tools")

        # ``list_tools`` on the agents SDK MCPServer subclasses takes an
        # optional ``run_context``/``agent`` kwargs depending on SDK version.
        # We don't have either in this OOB path, so call positionally without
        # arguments — the SDK falls back to listing all tools.
        try:
            result = await server.list_tools()
        except TypeError:
            result = await server.list_tools(run_context=None, agent=None)

        # ``MCPServer.list_tools`` returns ``list[mcp.types.Tool]`` directly.
        # Some transports wrap it as ``ListToolsResult`` (with .tools).
        tools_attr: list[Any]
        if isinstance(result, list):
            tools_attr = list(result)
        else:
            tools_attr = list(getattr(result, "tools", []) or [])
        next_cursor = getattr(result, "next_cursor", None) or getattr(
            result, "nextCursor", None
        )
        out: list[dict] = []
        for tool in tools_attr:
            if hasattr(tool, "model_dump"):
                try:
                    # by_alias preserves ``_meta`` (the Pydantic alias of ``meta``).
                    # exclude_none drops explicit ``null`` for optional fields
                    # like ``annotations``/``_meta`` that MCP-Apps Zod schemas
                    # treat as a union-type validation error.
                    out.append(
                        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    )
                    continue
                except Exception:
                    pass
            if hasattr(tool, "dict"):
                try:
                    out.append(tool.dict(exclude_none=True))
                    continue
                except Exception:
                    pass
            out.append({"raw": str(tool)})
        response: dict[str, Any] = {
            "server_name": server_name,
            "tools": out,
        }
        if next_cursor:
            response["next_cursor"] = next_cursor
        return response

    async def _sf_mcp_call_tool(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        server_name = str(payload.get("server_name") or "")
        tool_name = str(payload.get("tool_name") or "")
        arguments = payload.get("arguments") or {}
        if not server_name:
            raise ValueError("server_name is required")
        if not tool_name:
            raise ValueError("tool_name is required")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")

        server = await self._resolve_mcp_server_by_name(session, server_name)
        if server is None:
            raise ValueError(f"Unknown or inactive MCP server: {server_name}")
        if not hasattr(server, "call_tool"):
            raise ValueError(f"MCP server '{server_name}' does not support call_tool")

        result = await server.call_tool(tool_name, arguments)
        result_dict: dict[str, Any]
        if hasattr(result, "model_dump"):
            try:
                # ``exclude_none`` strips ``None`` values so the host doesn't
                # ship explicit nulls for optional fields like ``annotations``
                # and ``_meta``. Without this, MCP-Apps guests (e.g. Prefab's
                # Zod schema) reject the result with a union-type validation
                # error — ``null`` isn't a valid ``object``/``record``.
                result_dict = result.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            except Exception:
                result_dict = {"raw": str(result)}
        elif hasattr(result, "dict"):
            try:
                result_dict = result.dict(exclude_none=True)
            except Exception:
                result_dict = {"raw": str(result)}
        else:
            result_dict = {"raw": str(result)}
        return {
            "server_name": server_name,
            "tool_name": tool_name,
            "result": result_dict,
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
        if hasattr(session, "pending_tool_approvals"):
            session.pending_tool_approvals.clear()
        pending_mcp = getattr(session, "pending_mcp_approvals", None)
        if pending_mcp is not None:
            pending_mcp.clear()

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
        if hasattr(session, "pending_tool_approvals"):
            for payload in session.pending_tool_approvals.values():
                events.append(("tool_approval_requested", payload))
        pending_mcp = getattr(session, "pending_mcp_approvals", None)
        if pending_mcp:
            for payload in pending_mcp.values():
                events.append(("mcp_approval_requested", payload))
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

    # ------------------------------------------------------------------
    #  In-process event listeners.
    #
    #  Lets server-functions, hooks, schedulers, and the goal/autopilot
    #  loop subscribe to per-session events without going through the
    #  WebSocket channel mechanism. Fires from ``_broadcast_session``
    #  regardless of whether any client channel is connected, so the
    #  autopilot keeps running even when the user closed the TUI.
    # ------------------------------------------------------------------

    def add_run_end_listener(self, session_id: str, cb) -> None:
        """Register an async callback fired once per ``run_end`` event for
        the session. Callback receives the run_end payload dict."""
        self._run_end_listeners.setdefault(session_id, []).append(cb)

    def remove_run_end_listener(self, session_id: str, cb) -> None:
        lst = self._run_end_listeners.get(session_id)
        if not lst:
            return
        try:
            lst.remove(cb)
        except ValueError:
            pass
        if not lst:
            self._run_end_listeners.pop(session_id, None)

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
        # Fire in-process listeners BEFORE the channel fan-out so the
        # autopilot can act even if no client is connected. Listener
        # exceptions are logged but never block the broadcast.
        if method == "run_end":
            for cb in list(self._run_end_listeners.get(session.id, [])):
                try:
                    await cb(payload)
                except Exception:
                    logger.exception(
                        "run_end listener failed",
                        extra={"session": session.id},
                    )
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
        call_id: str,
        decision: str,
        always_approve: bool = False,
        rejection_message: str | None = None,
    ) -> bool:  # noqa: D401
        """Resolve a pending tool approval prompt.

        Parameters
        ----------
        call_id : str
            Identifier of the tool call that paused the run.
        decision : str
            Either ``"approve"`` or ``"reject"``.
        always_approve : bool, optional
            When ``decision == "approve"`` and this flag is set, the tool
            is added to the session's always-allow list so subsequent
            invocations execute without further prompts.
        rejection_message : str | None, optional
            Message returned to the model in place of the tool result when
            the user rejects.
        """
        _debug_rpc(
            "tool_approval_response",
            {
                "call_id": call_id,
                "decision": decision,
                "always_approve": always_approve,
                "rejection_message": rejection_message,
            },
        )
        entry = self._pending_tool_approvals.get(call_id)
        if entry is None:
            return False
        fut, sess = entry

        if decision == "approve" and always_approve:
            allow_set = getattr(sess, "always_allow_tools", None)
            if allow_set is None:
                allow_set = set()
                setattr(sess, "always_allow_tools", allow_set)
            tool_name = getattr(sess, "_pending_approval_tool_names", {}).get(call_id)
            if tool_name:
                allow_set.add(tool_name)

        if not fut.done():
            fut.set_result(
                {
                    "decision": decision,
                    "always_approve": bool(always_approve),
                    "rejection_message": rejection_message,
                }
            )

        # Notify any other connected channels (multi-tab / voice + chat) so
        # their UI can dismiss any visible approval prompt.
        try:
            await self._broadcast_session(
                sess,
                "tool_approval_resolved",
                {"call_id": call_id, "session_id": sess.id},
            )
        except Exception:
            pass

        return True

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
        # Look up the session BEFORE forgetting the request so we can
        # broadcast a dismissal event to all channels (voice + chat).
        sess = self._client_request_sessions.get(request_id)
        fut = self._pending_client_requests.pop(request_id, None)
        if fut and not fut.done():
            # Store a simple envelope
            fut.set_result({"ok": ok, "result": result, "error": error})
        self._forget_pending_request(request_id)
        # Notify all channels that this approval request was resolved
        # so any UI showing it can dismiss the dialog.
        if sess is not None:
            try:
                await self._broadcast_session(
                    sess,
                    "client_request_resolved",
                    {"request_id": request_id},
                )
            except Exception:
                pass
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

    # ------------------------------------------------------------------
    #  Session variables — mutate + broadcast.
    #
    #  Server functions and tools that want subscribers (UI panels,
    #  consumer apps mirroring state) to see variable changes go through
    #  this method instead of mutating ``session.variables`` directly.
    #  Sync mutation lives on ``Session.update_variables``.
    # ------------------------------------------------------------------

    async def update_session_variables(
        self,
        session_id: str,
        updates: Dict[str, Any],
        *,
        silent: bool = False,
    ) -> Dict[str, Any]:
        """Merge ``updates`` into the session's variables dict, persist,
        and broadcast a ``session_variables_changed`` event to channels +
        in-process listeners (unless ``silent=True`` or nothing actually
        changed).

        Returns the merged variables dict.
        """
        sess = self._session_manager.get_or_create(session_id)
        before = dict(sess.variables) if isinstance(sess.variables, dict) else {}
        merged = sess.update_variables(updates, persist=True)
        if silent:
            return merged
        # Compute the keys that actually changed (added, removed, or
        # different value). Skip the broadcast entirely on a no-op write
        # so consumers don't get spammed with redundant events.
        after = merged if isinstance(merged, dict) else {}
        changed: Dict[str, Any] = {}
        for k, v in updates.items():
            if before.get(k) != after.get(k):
                changed[k] = after.get(k)
        if not changed:
            return merged
        try:
            await self._broadcast_session(
                sess,
                "session_variables_changed",
                {
                    "session_id": sess.id,
                    "changed": changed,
                    "variables": dict(after),
                },
            )
        except Exception:
            logger.exception(
                "session_variables_changed broadcast failed",
                extra={"session": sess.id},
            )
        return merged

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
    #  Tool-approval interruption flow (SDK 0.16+).
    #
    #  When the SDK pauses a run with ToolApprovalItem interruptions, the
    #  bridge calls request_tool_approval() per interruption. We emit a
    #  ``tool_approval_requested`` notification, register a future, and
    #  resolve it from ``tool_approval_response``.
    # ------------------------------------------------------------------

    async def request_tool_approval(
        self,
        *,
        session: Session,
        call_id: str,
        tool_name: str,
        arguments: str | None,
        run_id: str | None = None,
    ) -> dict:
        """Emit a ``tool_approval_requested`` event and await the response.

        Returns a dict with ``decision`` (``"approve"`` | ``"reject"``),
        ``always_approve`` (bool), and ``rejection_message`` (str | None).
        """

        if session is None:
            raise RuntimeError(
                "Tool approval requests require an explicit session context"
            )

        # Compute rich metadata for the approval dialog.
        try:
            from omniagents.core.tools import get_approval_metadata

            args_dict = _json.loads(arguments) if arguments else {}
            metadata = get_approval_metadata(tool_name, args_dict)
        except Exception:
            metadata = None

        payload: dict[str, Any] = {
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": arguments or "",
            "session_id": session.id,
        }
        if run_id is not None:
            payload["run_id"] = run_id
        if metadata:
            payload["metadata"] = metadata

        if Debug.enabled():
            logger.debug(
                f"  → tool_approval_requested call_id={call_id}"
                f" tool={tool_name} session={session.id} run_id={run_id}"
            )

        # Track the tool name so the response handler can update the
        # always-allow set without re-deriving it.
        try:
            tracker = getattr(session, "_pending_approval_tool_names", None)
            if tracker is None:
                tracker = {}
                setattr(session, "_pending_approval_tool_names", tracker)
            tracker[call_id] = tool_name
        except Exception:
            pass

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_tool_approvals[call_id] = (fut, session)

        # Track for replay on reconnect.
        if hasattr(session, "pending_tool_approvals"):
            session.pending_tool_approvals[call_id] = dict(payload)

        await self._broadcast_session(
            session,
            "tool_approval_requested",
            payload,
            queue_if_unseen=True,
        )

        try:
            response = await fut
        finally:
            self._pending_tool_approvals.pop(call_id, None)
            try:
                if hasattr(session, "pending_tool_approvals"):
                    session.pending_tool_approvals.pop(call_id, None)
                tracker = getattr(session, "_pending_approval_tool_names", None)
                if tracker is not None:
                    tracker.pop(call_id, None)
            except Exception:
                pass

        if not isinstance(response, dict):
            return {
                "decision": "reject",
                "always_approve": False,
                "rejection_message": "Invalid approval response",
            }

        # Audit hook: emit tool_approved / tool_rejected so file/remote
        # backends keep the record they used to receive from SafeAgent.
        try:
            from omniagents.core.audit.events import make_audit_event

            decision = response.get("decision")
            event_type = "tool_approved" if decision == "approve" else "tool_rejected"
            details: dict[str, Any] = {"tool": tool_name}
            if decision != "approve" and response.get("rejection_message"):
                details["reason"] = response["rejection_message"]
            await self.emit_runtime_hook(
                "audit",
                **make_audit_event(
                    event_type,
                    session_id=session.id,
                    user_id=getattr(session, "user_id", None),
                    run_id=run_id,
                    agent_name=getattr(self.spec, "name", None),
                    details=details,
                ),
            )
        except Exception:
            pass

        return response

    # ------------------------------------------------------------------
    #  MCP-approval interruption flow (SDK 0.16+ HostedMCPTool).
    #
    #  Hosted MCP tool calls that require approval surface as
    #  ``ToolApprovalItem`` interruptions whose ``raw_item`` is a
    #  ``McpApprovalRequest``. The bridge detects them and routes through
    #  ``request_mcp_approval`` so the wire payload carries MCP-specific
    #  context (server label, tool name) instead of generic function-tool
    #  arguments. The response RPC method is ``mcp_approval_response``.
    # ------------------------------------------------------------------

    async def request_mcp_approval(
        self,
        *,
        session: Session,
        request_id: str,
        server_label: str,
        tool_name: str,
        arguments: str | None,
        run_id: str | None = None,
    ) -> dict:
        """Emit an ``mcp_approval_requested`` event and await the response.

        Returns a dict with ``decision`` (``"approve"`` | ``"reject"``) and
        optional ``rejection_message`` (str | None). Requests matching the
        session's hosted-MCP always-allow list are approved immediately
        without emitting an approval prompt.
        """

        if session is None:
            raise RuntimeError(
                "MCP approval requests require an explicit session context"
            )

        if self._is_mcp_tool_always_allowed(session, server_label, tool_name):
            if Debug.enabled():
                logger.debug(
                    f"  ✓ mcp_approval auto-approved request_id={request_id}"
                    f" server={server_label} tool={tool_name}"
                    f" session={session.id} run_id={run_id}"
                )
            return {"decision": "approve", "rejection_message": None}

        payload: dict[str, Any] = {
            "kind": "mcp",
            "request_id": request_id,
            "server_label": server_label,
            "tool_name": tool_name,
            "arguments": arguments or "",
            "session_id": session.id,
        }
        if run_id is not None:
            payload["run_id"] = run_id

        if Debug.enabled():
            logger.debug(
                f"  → mcp_approval_requested request_id={request_id}"
                f" server={server_label} tool={tool_name}"
                f" session={session.id} run_id={run_id}"
            )

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_mcp_approvals[request_id] = (fut, session)

        # Track for replay on reconnect (sessions may not have the attr if
        # they predate this feature; we only register if present).
        pending_attr = getattr(session, "pending_mcp_approvals", None)
        if pending_attr is None and hasattr(session, "pending_tool_approvals"):
            try:
                setattr(session, "pending_mcp_approvals", {})
                pending_attr = session.pending_mcp_approvals
            except Exception:
                pending_attr = None
        if pending_attr is not None:
            pending_attr[request_id] = dict(payload)

        await self._broadcast_session(
            session,
            "mcp_approval_requested",
            payload,
            queue_if_unseen=True,
        )

        try:
            response = await fut
        finally:
            self._pending_mcp_approvals.pop(request_id, None)
            pending_attr = getattr(session, "pending_mcp_approvals", None)
            if pending_attr is not None:
                pending_attr.pop(request_id, None)

        if not isinstance(response, dict):
            return {
                "decision": "reject",
                "rejection_message": "Invalid approval response",
            }

        # Audit hook mirrors the function-tool path so backends see a
        # uniform ``tool_approved`` / ``tool_rejected`` stream regardless of
        # transport.
        try:
            from omniagents.core.audit.events import make_audit_event

            decision = response.get("decision")
            event_type = "tool_approved" if decision == "approve" else "tool_rejected"
            details: dict[str, Any] = {
                "tool": tool_name,
                "transport": "mcp",
                "server_label": server_label,
            }
            if decision != "approve" and response.get("rejection_message"):
                details["reason"] = response["rejection_message"]
            await self.emit_runtime_hook(
                "audit",
                **make_audit_event(
                    event_type,
                    session_id=session.id,
                    user_id=getattr(session, "user_id", None),
                    run_id=run_id,
                    agent_name=getattr(self.spec, "name", None),
                    details=details,
                ),
            )
        except Exception:
            pass

        return response

    async def mcp_approval_response(
        self,
        request_id: str,
        decision: str,
        rejection_message: str | None = None,
    ) -> bool:  # noqa: D401
        """Resolve a pending hosted MCP approval prompt.

        Parameters
        ----------
        request_id : str
            Identifier from the matching ``mcp_approval_requested`` event
            (corresponds to ``McpApprovalRequest.id`` from the model).
        decision : str
            Either ``"approve"`` or ``"reject"``.
        rejection_message : str | None, optional
            Message returned to the model in place of the tool result when
            the user rejects.
        """
        _debug_rpc(
            "mcp_approval_response",
            {
                "request_id": request_id,
                "decision": decision,
                "rejection_message": rejection_message,
            },
        )
        entry = self._pending_mcp_approvals.get(request_id)
        if entry is None:
            return False
        fut, sess = entry

        if not fut.done():
            fut.set_result(
                {
                    "decision": decision,
                    "rejection_message": rejection_message,
                }
            )

        # Notify all channels so multi-tab UIs can dismiss any visible
        # prompt for the same request_id.
        try:
            await self._broadcast_session(
                sess,
                "mcp_approval_resolved",
                {"request_id": request_id, "session_id": sess.id},
            )
        except Exception:
            pass

        return True

    async def start_run(
        self,
        prompt: str,
        session_id: str | None = None,
        variables: dict | None = None,
        context: Any | None = None,
        content: Any | None = None,
        workflow_name: str | None = None,
        group_id: str | None = None,
        safe_tool_overrides: dict | None = None,
        prompt_role: str = "user",
    ) -> dict:  # noqa: D401
        sess: Session = self._session_manager.get_or_create(session_id)
        channel = getattr(self, "channel", None)

        sess.cancel_requested = False
        sess.cancel_end_reason = None
        sess.current_stream_result = None

        # Merge caller-provided variables/context into whatever is already on
        # the session (session.ensure may have run earlier).
        merged_variables = getattr(sess, "variables", None)
        if variables is not None:
            if isinstance(merged_variables, dict) and isinstance(variables, dict):
                merged_variables = {**merged_variables, **variables}
            else:
                merged_variables = variables

        merged_context = getattr(sess, "context", None)
        if context is not None:
            if isinstance(merged_context, dict) and isinstance(context, dict):
                merged_context = {**merged_context, **context}
            else:
                merged_context = context

        # Workspace root resolution. The framework no longer falls back to
        # the server process's cwd silently — that anchored sub-agents and
        # tool subprocesses to whatever directory the server happened to
        # be running in. Order of preference:
        #   1. workspace_root already on context (or variables — the web
        #      UI passes it that way)
        #   2. default_workspace_root configured on this AgentService
        #      (set by ink/web bootstrappers from os.getcwd())
        #   3. raise WorkspaceRootRequiredError
        # A configured workspace_root_resolver supplies/clamps the value
        # itself (e.g. defaulting a sandbox cwd to the manifest root), so it
        # satisfies the requirement on its own — _apply_session_metadata
        # applies it below.
        workspace_root: str | None = None
        if (
            self._workspace_root_resolver is None
            and self._ws_root_from(merged_context) is None
            and self._ws_root_from(merged_variables) is None
        ):
            if self._default_workspace_root is not None:
                workspace_root = self._default_workspace_root
            else:
                from omniagents.core.errors import WorkspaceRootRequiredError

                raise WorkspaceRootRequiredError(where="start_run")

        self._apply_session_metadata(
            sess,
            context=merged_context,
            context_provided=True,
            variables=merged_variables,
            variables_provided=True,
            workspace_root=workspace_root,
        )

        self._apply_safe_tool_overrides(sess, safe_tool_overrides)

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

        agent = await self._build_agent_for_session(sess, None)

        # When the caller supplies a non-user prompt role, write the
        # message to history with that role and tell the worker to
        # resume from history rather than inject a fresh user turn.
        # Used by _notification_flusher to land batched bash-job /
        # worker completion notices as assistant messages while still
        # triggering a run.
        _resume_from_history = prompt_role != "user"
        if _resume_from_history:
            sess.append_message(
                {"role": prompt_role, "content": prompt, "type": "message"}
            )

        async def _worker():
            # Mark this run's task tree as owned by ``self``. Background
            # tasks spawned during the run (worker monitors, bash job
            # watchers, ``Session.set_tick`` loops, drainers triggered
            # by ``enqueue_notification``) snapshot this value via
            # ``asyncio.create_task`` and will resolve ``current_service()``
            # to the right host even when a worker's ``LocalAgent`` has
            # constructed its own ``AgentService`` in a nested scope.
            _current_service.set(self)

            end_reason = "error"  # Default in case of unexpected exception
            final_details: dict | None = None
            error_info: dict | None = None

            current_task = asyncio.current_task()
            run_name = current_task.get_name() if current_task else None

            # Resolve runtime context once
            # When a build_context factory exists, always call it so that
            # dynamic template variables (skills, date, etc.) are generated.
            # Merge any session-level context/variables into the factory input.
            if self.spec.build_context:
                # Determine the variables to pass to the factory
                factory_vars = dict(self.spec.variables or {})
                if hasattr(sess, "variables") and isinstance(sess.variables, dict):
                    factory_vars.update(sess.variables)
                # Also pull workspace_root from sess.context if set
                if hasattr(sess, "context") and isinstance(sess.context, dict):
                    for k, v in sess.context.items():
                        factory_vars.setdefault(k, v)
                runtime_context = self.spec.build_context(factory_vars)
                # Merge any extra session context keys the factory didn't produce
                if hasattr(sess, "context") and isinstance(sess.context, dict):
                    if isinstance(runtime_context, dict):
                        for k, v in sess.context.items():
                            runtime_context.setdefault(k, v)
            elif hasattr(sess, "context") and sess.context is not None:
                runtime_context = sess.context
            elif hasattr(sess, "variables") and sess.variables is not None:
                runtime_context = sess.variables
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
            include_user_message = not _resume_from_history
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

                # Determine model provider. Precedence:
                #   1. Session-level override (UI / RPC sets sess.model_config)
                #   2. Spec-level YAML model_provider block
                #   3. None — let the SDK default MultiProvider handle prefix
                #      routing for ``litellm/...``, ``any-llm/...``, ``openai/...``
                from omniagents.core.agents.run_options import build_model_provider

                model_provider = None
                session_model_config = getattr(sess, "model_config", None)

                # Map session_model_config (legacy "provider" key) onto the
                # build_model_provider shape (keys: type, base_url, api_key).
                # Carry the transport-shaping keys too — without use_responses
                # a provider that only speaks Chat Completions (e.g. Fireworks)
                # would default to the Responses API and POST to a 404 path.
                # build_model_provider ignores None values, so this is safe for
                # every provider type.
                provider_cfg: Optional[Dict[str, Any]] = None
                if session_model_config and session_model_config.get("provider"):
                    provider_cfg = {
                        "type": session_model_config.get("provider"),
                        "base_url": session_model_config.get("base_url"),
                        "api_key": session_model_config.get("api_key"),
                        "api_version": session_model_config.get("api_version"),
                        "use_responses": session_model_config.get("use_responses"),
                        "use_responses_websocket": session_model_config.get(
                            "use_responses_websocket"
                        ),
                    }
                elif getattr(self.spec, "model_provider_config", None):
                    provider_cfg = self.spec.model_provider_config

                if provider_cfg:
                    try:
                        model_provider = build_model_provider(provider_cfg)
                    except ImportError as e:
                        logger.warning(str(e))
                    except Exception as e:
                        logger.warning(f"Failed to create model provider: {e}")

                # If the spec opts in to SDK-native compaction, build (and
                # memoise) a wrapper that delegates reads/writes to the
                # underlying OmniAgents Session and triggers
                # ``responses.compact`` when its threshold hook fires.  When
                # neither a compaction wrapper nor an explicit backend is
                # configured, ``sdk_session`` stays ``None`` and the bridge
                # falls back to passing the OmniAgents Session itself to
                # ``Runner.run_streamed`` — the Session already implements
                # the SDK ``Session`` protocol (``get_items`` / ``add_items``
                # / ``pop_item`` / ``clear_session``).
                _compaction_cfg = getattr(self.spec, "session_compaction_config", None)
                _explicit_backend = getattr(self.spec, "session_backend", None)
                sdk_session = None
                # Order of precedence:
                #   1. Spec-supplied SDK ``Session`` backend (e.g. Redis, Mongo,
                #      EncryptedSession) — already an SDK Session instance.
                #   2. Compaction wrapper around the OmniAgents Session.
                #   3. None — bridge passes the OmniAgents Session directly.
                if _explicit_backend is not None:
                    sdk_session = _explicit_backend
                elif _compaction_cfg:
                    cached = getattr(sess, "_sdk_compaction_session", None)
                    if cached is None:
                        from omniagents.core.session.compaction import (
                            wrap_with_compaction,
                        )

                        wrapped = wrap_with_compaction(
                            sess,
                            _compaction_cfg,
                            model_config=session_model_config,
                            # Compaction inherits the parent session's model
                            # + provider transport (built above) so its
                            # ``responses.compact`` calls hit the same
                            # endpoint/credentials — incl. openai-oauth/Codex
                            # — and its trigger threshold uses the serving
                            # model's real context window.
                            model_provider=model_provider,
                            parent_model=(
                                session_model_config.get("model")
                                if session_model_config
                                else None
                            ),
                            max_input_tokens=(
                                session_model_config.get("max_input_tokens")
                                if session_model_config
                                else None
                            ),
                        )
                        # ``wrap_with_compaction`` returns ``sess`` itself
                        # when wrapping is disabled or fails; only memoise
                        # when an actual wrapper was produced.
                        if wrapped is not sess:
                            sess._sdk_compaction_session = wrapped
                            sdk_session = wrapped
                    else:
                        sdk_session = cached

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
                            nest_handoff_history=getattr(
                                self.spec, "nest_handoff_history", False
                            ),
                            tool_execution_config=getattr(
                                self.spec, "tool_execution_config", None
                            ),
                            error_handlers=getattr(self.spec, "error_handlers", None),
                            run_hooks=getattr(self.spec, "run_hooks", None),
                            sdk_session=sdk_session,
                            sandbox_run_config=getattr(
                                self.spec, "sandbox_run_config", None
                            ),
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
                        # Audit event: guardrail_triggered
                        try:
                            from omniagents.core.audit.events import (
                                make_audit_event,
                                GUARDRAIL_TRIGGERED,
                            )

                            await self.emit_runtime_hook(
                                "audit",
                                **make_audit_event(
                                    GUARDRAIL_TRIGGERED,
                                    session_id=sess.id,
                                    user_id=getattr(sess, "user_id", None),
                                    run_id=run_name,
                                    agent_name=getattr(self.spec, "name", None),
                                    details=error_info or {},
                                ),
                            )
                        except Exception:
                            pass
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
                                        "session_id": sess.id,
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
                    "session_id": sess.id,
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
                # Clear task state BEFORE broadcasting run_end so that
                # clients can immediately start a new run without hitting
                # "Run already active in this session".
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

                # Debug log the payload for visibility when debugging (single source)
                _debug_rpc("run_end", payload)
                # Best-effort notify; avoid raising if socket is gone
                await self._broadcast_session(
                    sess, "run_end", payload, queue_if_unseen=True
                )
                # Audit event: run_end
                try:
                    from omniagents.core.audit.events import make_audit_event, RUN_END

                    await self.emit_runtime_hook(
                        "audit",
                        **make_audit_event(
                            RUN_END,
                            session_id=sess.id,
                            user_id=getattr(sess, "user_id", None),
                            run_id=run_name,
                            agent_name=getattr(self.spec, "name", None),
                            details={
                                k: payload.get(k)
                                for k in ("end_reason", "error", "model")
                                if payload.get(k) is not None
                            },
                        ),
                    )
                except Exception:
                    pass

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
        # ``prompt`` is included so clients whose run wasn't user-initiated
        # (e.g. queued via enqueue_message + drainer, or background
        # automation) can render the originating turn in their transcript —
        # without it, the message_output stream would arrive with no
        # anchoring turn. ``prompt_role`` lets clients render that turn
        # with the right role: notification batches arrive as "assistant"
        # so the wakeup reads as the agent observing its own background
        # activity, not as fake user input.
        await self._broadcast_session(
            sess,
            "run_started",
            {
                "run_id": task_name,
                "session_id": sess.id,
                "prompt": prompt,
                "prompt_role": prompt_role,
            },
            track_live=True,
        )
        # Audit event: run_started
        try:
            from omniagents.core.audit.events import make_audit_event, RUN_STARTED

            await self.emit_runtime_hook(
                "audit",
                **make_audit_event(
                    RUN_STARTED,
                    session_id=sess.id,
                    user_id=getattr(sess, "user_id", None),
                    run_id=task_name,
                    agent_name=getattr(self.spec, "name", None),
                ),
            )
        except Exception:
            pass
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

        from omniagents.core.session.manager import filter_history_for_api

        result = filter_history_for_api(sess.history)
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

    # ------------------------------------------------------------------
    #  Background-injected message queue.
    #
    #  Lets in-process callers (hooks, schedulers, webhooks) and external
    #  RPC clients inject messages into a session's history without
    #  racing against an in-flight run. ``start_run`` enforces "one run
    #  per session" by raising at service.py "Run already active in this
    #  session" — the drainer awaits ``current_task`` so queued work
    #  serializes cleanly against user-initiated runs.
    # ------------------------------------------------------------------

    async def enqueue_message(
        self,
        session_id: str,
        content: str,
        role: str = "user",
        trigger_run: bool = False,
        variables: dict | None = None,
        safe_tool_overrides: dict | None = None,
        source: str | None = None,
    ) -> dict:  # noqa: D401
        """Queue a message for serialized processing on a session.

        - ``trigger_run=False``: append to history, no run.
        - ``trigger_run=True``:  call ``start_run`` with ``content`` as the
          prompt — ``start_run`` writes the user turn and drives the agent.

        Items are processed in FIFO order. The drainer waits for any
        in-flight run (queued or user-initiated) before starting a new one,
        so two runs never overlap on the same session.
        """
        _debug_rpc(
            "enqueue_message",
            {
                "session_id": session_id,
                "role": role,
                "trigger_run": trigger_run,
                "source": source,
            },
        )
        sess = self._session_manager.get_or_create(session_id)
        # Drain pending background notifications first so they keep
        # chronological order in history relative to this real message.
        # The recursive enqueue_message call from _flush_notifications
        # uses source="notifications:batch" and is skipped here, so no
        # infinite loop.
        if source != "notifications:batch" and sess.pending_notifications:
            await self._flush_notifications(sess)
        if len(sess.message_queue) >= sess.message_queue_max:
            return {
                "ok": False,
                "reason": "queue_full",
                "depth": len(sess.message_queue),
                "session_id": sess.id,
            }
        item = QueuedItem(
            content=content,
            role=role,
            trigger_run=trigger_run,
            variables=variables,
            safe_tool_overrides=safe_tool_overrides,
            source=source,
        )
        sess.message_queue.append(item)
        await self._broadcast_queue_changed(sess)
        self._ensure_drainer(sess)
        return {
            "ok": True,
            "id": item.id,
            "depth": len(sess.message_queue),
            "session_id": sess.id,
            "enqueued_at": item.enqueued_at,
        }

    async def queue_status(self, session_id: str) -> dict:  # noqa: D401
        """Return the queue/run state for a session."""
        sess = self._session_manager.get_or_create(session_id)
        return {
            "session_id": sess.id,
            "depth": len(sess.message_queue),
            "draining": bool(sess.drainer_task and not sess.drainer_task.done()),
            "run_active": bool(sess.current_task and not sess.current_task.done()),
            "active_run_id": sess.active_run_id,
        }

    async def list_queue(self, session_id: str) -> dict:  # noqa: D401
        """Return the current queue contents for a session.

        Clients call this on connect / session-switch to seed local state,
        then subscribe to ``queue_changed`` notifications for updates.
        """
        sess = self._session_manager.get_or_create(session_id)
        return {
            "session_id": sess.id,
            "depth": len(sess.message_queue),
            "items": [item.to_dict() for item in sess.message_queue],
        }

    async def cancel_queued_message(
        self, session_id: str, item_id: str
    ) -> dict:  # noqa: D401
        """Remove a queued item by id.

        Returns ``{ok: false, reason: "not_found"}`` if the item has already
        been popped by the drainer (running or completed) — cancelling an
        actively-running turn requires ``stop_run`` instead.
        """
        sess = self._session_manager.get_or_create(session_id)
        for i, item in enumerate(sess.message_queue):
            if item.id == item_id:
                del sess.message_queue[i]
                await self._broadcast_queue_changed(sess)
                return {"ok": True, "id": item_id, "depth": len(sess.message_queue)}
        return {"ok": False, "reason": "not_found", "id": item_id}

    async def _broadcast_queue_changed(self, sess: Session) -> None:
        """Fan a full snapshot of the queue out to every connected channel.

        Snapshot rather than delta because the queue is small (max 100) and
        full-replace is dead-simple for clients to apply. Queue is also
        queued-for-replay on reconnect via ``_broadcast_session(...,
        queue_if_unseen=True)`` so a tab opening mid-drain still sees the
        latest state without an extra ``list_queue`` round-trip.
        """
        await self._broadcast_session(
            sess,
            "queue_changed",
            {
                "session_id": sess.id,
                "depth": len(sess.message_queue),
                "items": [item.to_dict() for item in sess.message_queue],
            },
            queue_if_unseen=True,
        )

    def _ensure_drainer(self, sess: Session) -> None:
        """Spawn a drainer task if one isn't already running for the session.

        Self-scheduling: the drainer exits when the queue empties and is
        respawned by the next ``enqueue_message`` call. No teardown needed.
        """
        if sess.drainer_task and not sess.drainer_task.done():
            return
        sess.drainer_task = asyncio.create_task(
            self._drain_queue(sess), name=f"drain-{sess.id[:8]}"
        )

    async def _drain_queue(self, sess: Session) -> None:
        async def _wait_for_current_task() -> None:
            """Await ``sess.current_task`` but swallow its cancellation/errors.

            ``contextlib.suppress(Exception)`` won't catch ``CancelledError``
            (it's a ``BaseException`` since 3.8). We need to distinguish
            "the awaited task was cancelled" (keep draining) from "the
            drainer itself was cancelled" (propagate).
            """
            task = sess.current_task
            if task is None or task.done():
                return
            try:
                await task
            except asyncio.CancelledError:
                # If our own drainer task is being cancelled, re-raise so
                # the cancellation propagates. Otherwise the inner task
                # was cancelled — fine, move on.
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
            except Exception:
                pass

        while sess.message_queue:
            # Wait for any in-flight run BEFORE popping the next item.
            # This is what makes the queue visible to clients: items
            # stay in ``sess.message_queue`` (and therefore in every
            # client's "Up next" panel) for the entire duration of the
            # current run, not just the microsecond between pop and the
            # next broadcast. Pop only when we're actually ready to
            # process — that's the moment the user sees "this one is
            # starting now". The loop's own re-check at the top
            # subsumes the post-start_run wait we used to do, so the
            # serialization guarantee is unchanged.
            await _wait_for_current_task()
            # The queue may have been emptied by cancel_queued_message
            # while we were waiting — re-check before popping.
            if not sess.message_queue:
                break
            item = sess.message_queue.pop(0)
            await self._broadcast_queue_changed(sess)
            try:
                if item.trigger_run:
                    # start_run appends the prompt to history AND drives a
                    # turn. Don't pre-append — that would duplicate.
                    # prompt_role lets non-user notifications (assistant-
                    # role batches from _notification_flusher) land in
                    # history with their real role while still triggering
                    # a run via the resume-from-history path.
                    await self.start_run(
                        prompt=item.content,
                        session_id=sess.id,
                        variables=item.variables,
                        safe_tool_overrides=item.safe_tool_overrides,
                        prompt_role=item.role,
                    )
                    # Shepherd the run we just scheduled to completion
                    # before exiting the drainer. The loop's outer wait
                    # at the top would catch this on the next iteration
                    # too, but if this was the last queued item we'd
                    # otherwise exit while the run was still in flight.
                    await _wait_for_current_task()
                else:
                    sess.append_message(
                        {
                            "role": item.role,
                            "content": item.content,
                            "type": "message",
                        }
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "drain failed",
                    extra={"session": sess.id, "source": item.source},
                )

    # ------------------------------------------------------------------
    #  Background notifications (batched, role="assistant").
    #
    #  enqueue_notification buffers per-session; _notification_flusher
    #  coalesces a burst into ONE QueuedItem with trigger_run=True at:
    #    - the moment ``current_task`` ends (run-active case)
    #    - after a debounce window (idle case)
    #    - immediately when a real user message arrives (chronological
    #      ordering, via the enqueue_message hook above).
    #
    #  The combined item lands in history as role="assistant" so it
    #  reads as the agent observing its own background activity, then
    #  triggers a new run that continues from history without a fresh
    #  user turn. Works on every model/provider — no capability check.
    # ------------------------------------------------------------------

    _NOTIFICATION_DEBOUNCE_S = 0.5
    _NOTIFICATION_IDLE_EXIT_S = 5.0

    async def enqueue_notification(
        self,
        session_id: str,
        content: str,
        source: str,
    ) -> dict:  # noqa: D401
        """Buffer a background notification for batched delivery.

        Always coalesced with any other notifications in the same
        window and always delivered as a role="assistant" history item
        that triggers a fresh run.
        """
        _debug_rpc(
            "enqueue_notification",
            {"session_id": session_id, "source": source},
        )
        sess = self._session_manager.get_or_create(session_id)
        sess.pending_notifications.append(
            {
                "content": content,
                "source": source,
                "enqueued_at": time.time(),
            }
        )
        sess.notification_signal.set()
        self._ensure_notification_flusher(sess)
        return {
            "ok": True,
            "buffered": len(sess.pending_notifications),
            "session_id": sess.id,
        }

    def _ensure_notification_flusher(self, sess: Session) -> None:
        if sess.notification_flusher_task and not sess.notification_flusher_task.done():
            return
        sess.notification_flusher_task = asyncio.create_task(
            self._notification_flusher(sess),
            name=f"notif-flush-{sess.id[:8]}",
        )

    async def _flush_notifications(self, sess: Session) -> None:
        """Drain ``pending_notifications`` into ONE combined QueuedItem.

        Always role="assistant", always trigger_run=True. Safe to call
        with an empty buffer (no-op). The drainer routes the role
        through ``start_run(prompt_role=...)`` which appends to history
        with the real role and resumes the run without injecting a
        fresh user turn, so the notification reads as the agent
        observing its own background activity rather than as fake user
        input."""
        if not sess.pending_notifications:
            return
        items = sess.pending_notifications[:]
        sess.pending_notifications.clear()
        combined = self._format_notification_batch(items)
        await self.enqueue_message(
            session_id=sess.id,
            content=combined,
            role="assistant",
            trigger_run=True,
            source="notifications:batch",
        )

    def _format_notification_batch(self, items: list[dict]) -> str:
        """Combine buffered notifications into a single message body,
        grouped by source so a burst of bash-job exits reads as one
        section, not N unrelated lines."""
        if len(items) == 1:
            return items[0]["content"]
        groups: dict[str, list[str]] = {}
        for it in items:
            groups.setdefault(it["source"], []).append(it["content"])
        parts: list[str] = []
        for source, contents in groups.items():
            if len(contents) == 1:
                parts.append(contents[0])
            else:
                parts.append(f"[{source}] {len(contents)} events:")
                parts.extend(f"  - {c}" for c in contents)
        return "\n".join(parts)

    @staticmethod
    async def _await_swallow(task: asyncio.Task) -> None:
        try:
            await task
        except BaseException:
            pass

    async def _notification_flusher(self, sess: Session) -> None:
        """Long-lived per-session task that batches notifications and
        turns them into a single QueuedItem at the right moment.

        Loop:
          1. Wait for at least one notification (timeout => idle exit;
             respawned by next enqueue_notification).
          2. Decide WHEN to flush:
               - run active => wait for current_task OR flush_now signal
               - run idle   => debounce briefly to coalesce bursts
          3. Drain buffer into one assistant-role QueuedItem; the
             drainer then pops it and calls start_run with
             prompt_role="assistant" — the agent wakes up and runs.
        """
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        sess.notification_signal.wait(),
                        timeout=self._NOTIFICATION_IDLE_EXIT_S,
                    )
                except asyncio.TimeoutError:
                    if not sess.pending_notifications:
                        return
                sess.notification_signal.clear()

                if sess.current_task and not sess.current_task.done():
                    flush_now = asyncio.create_task(
                        sess.notification_flush_now.wait(), name="flush-now"
                    )
                    wait_run = asyncio.create_task(
                        self._await_swallow(sess.current_task), name="wait-run"
                    )
                    _, pending = await asyncio.wait(
                        {flush_now, wait_run},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    flush_now = asyncio.create_task(
                        sess.notification_flush_now.wait(), name="flush-now"
                    )
                    debounce = asyncio.create_task(
                        asyncio.sleep(self._NOTIFICATION_DEBOUNCE_S),
                        name="debounce",
                    )
                    _, pending = await asyncio.wait(
                        {flush_now, debounce},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                for t in pending:
                    t.cancel()
                sess.notification_flush_now.clear()

                # No-op when enqueue_message's hook already drained.
                await self._flush_notifications(sess)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification flusher failed", extra={"session": sess.id})

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

    async def set_session_hold(
        self, session_id: str, hold: bool = True
    ) -> bool:  # noqa: D401
        """Set or clear legal hold on a session.

        Held sessions are excluded from retention purge and cannot be deleted.

        Args:
            session_id: The session ID
            hold: True to place hold, False to release

        Returns:
            True if the session was found and updated, False if not found
        """
        from omniagents.core.session import history_db

        project_slug = getattr(self._session_manager, "_project_slug", None)
        agent_slug = getattr(self._session_manager, "_agent_slug", None)
        return history_db.set_session_hold(
            session_id, hold, project_slug=project_slug, agent_slug=agent_slug
        )

    def disable_tool(self, tool_name: str) -> None:
        """Runtime kill switch: block a tool by name.

        Takes effect on the next agent build (next ``start_run``).
        """
        sec = getattr(self.spec, "security_config", None)
        if sec is None:
            return
        if isinstance(sec, dict):
            blocked = sec.setdefault("blocked_tools", [])
        else:
            blocked = getattr(sec, "blocked_tools", [])
        if tool_name not in blocked:
            blocked.append(tool_name)

    def disable_mcp_server(self, server_name: str) -> None:
        """Runtime kill switch: remove an MCP server by name.

        Takes effect on the next agent build (next ``start_run``).
        """
        mcp_servers = getattr(self.spec, "mcp_servers", None)
        if mcp_servers:
            self.spec.mcp_servers = [
                s for s in mcp_servers if getattr(s, "name", None) != server_name
            ]

    async def report_incident(
        self,
        session_id: str,
        description: str,
        disable_tools: list[str] | None = None,
        disable_mcp_servers: list[str] | None = None,
    ) -> dict:  # noqa: D401
        """Report a security incident and optionally disable tools/servers.

        Emits an ``INCIDENT`` audit event and applies kill switches.
        """
        for t in disable_tools or []:
            self.disable_tool(t)
        for s in disable_mcp_servers or []:
            self.disable_mcp_server(s)

        from omniagents.core.audit.events import INCIDENT, make_audit_event

        event = make_audit_event(
            INCIDENT,
            session_id=session_id,
            agent_name=getattr(self.spec, "name", None),
            details={
                "description": description,
                "disabled_tools": disable_tools or [],
                "disabled_mcp_servers": disable_mcp_servers or [],
            },
        )
        await self.emit_runtime_hook("audit", **event)
        return {"status": "reported", "event": event}

    async def export_session(
        self, session_id: str, redact: bool = False
    ) -> dict:  # noqa: D401
        """Export a session transcript as structured JSON.

        If *redact* is True, text content is scanned using the configured
        content scanner before inclusion.
        """
        from omniagents.core.audit.export import export_session as _export

        scanner = None
        if redact:
            try:
                sec = getattr(self.spec, "security_config", None) or {}
                scanner_cfg = (sec.get("providers") or {}).get("content_scanner")
                if scanner_cfg and scanner_cfg.get("type", "none") != "none":
                    from omniagents.core.providers import resolve_provider

                    scanner = resolve_provider("content_scanner", scanner_cfg)
            except Exception:
                pass

        project_slug = getattr(self._session_manager, "_project_slug", None)
        agent_slug = getattr(self._session_manager, "_agent_slug", None)
        return await _export(
            session_id,
            project_slug=project_slug,
            agent_slug=agent_slug,
            redactor=scanner,
        )

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
