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
import hashlib
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Awaitable, Optional, Dict, Any, Callable
from pathlib import Path
import inspect
from dataclasses import replace as dataclass_replace

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
from omniagents.rpc.auth import (
    AGENT_HOST_ADMIN_METHODS,
    ADMIN_ROLE,
    GLOBAL_MUTATING_METHODS,
    OWNER_UNKNOWN,
    SESSION_CREATING_SERVER_FUNCTIONS,
    AuthorizationDenied,
    AuthorizationPolicy,
)
from omniagents.rpc.auth import _OwnerUnknown as _OwnerUnknownType
from omniagents.rpc.filesystem import FilesystemManager
from omniagents.rpc.git import GitManager
from omniagents.rpc.protocol_catalog import (
    OPERATION_BY_NAME,
    ExecutionRequirement,
    ResourceScope,
)
from omniagents.execution import (
    AgentHostResources,
    AutomaticEnvironment,
    EnvironmentLease,
    ExistingEnvironment,
    NoEnvironment,
    OperationExecutionDescriptor,
    RequestContext,
    ThreadRuntimeBinding,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from omniagents.workspace.base import Workspace
from omniagents.workspace.host import HostWorkspace
from omniagents.core.providers.accounts import AccountManager
from omniagents.core.providers.auth.base import UserIdentity
from omniagents.core.conversation import service as _conversation_service_module
from omniagents.core.conversation.model import UNSCOPED, ThreadFilter
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.approval import configure_tool_approval
from omniagents.core.session import Session, SessionManager
from omniagents.core.session.event_log import (
    channel_accepts_notification,
    stamp_session_event,
)
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

#: Owner value used to scope a self-scoped listing when authorization is
#: enforced but no principal resolved: it matches no row, so the listing is
#: empty rather than unscoped.
_NO_PRINCIPAL_SCOPE = "\x00-no-principal"

#: Re-exported so ``update_thread``'s signature can default to it. The
#: sentinel is what lets an omitted ``title`` mean "leave it alone" while an
#: explicit ``null`` means "clear it" — the RPC layer dumps params with
#: ``exclude_unset=True``, so an omitted field never reaches the handler.
_ConversationUnset = _conversation_service_module._Unset
_CONVERSATION_UNSET = _conversation_service_module.UNSET


def _session_row_epoch(row: dict) -> float:
    """Epoch seconds for a ``list_sessions`` row's creation time.

    The listing reports ``created_at`` as the storage backend formats it
    (a SQLite ``TIMESTAMP`` string, a Postgres datetime). Retention has to
    compare it against a cutoff, and an unparseable value must read as
    *recent* — a parse failure that read as "epoch 0" would make a dry run
    propose deleting every session it could not decode.
    """
    from datetime import datetime, timezone

    value = row.get("created_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return float("inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return float("inf")


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


def _set_future_result(fut: "asyncio.Future", result: Any) -> None:
    """Resolve *fut* on the loop that owns it.

    asyncio futures must be completed from their own event loop. The
    resolver usually shares the requester's loop, but embedded hosts (and
    the TestClient harness) can dispatch the response RPC on a different
    loop — schedule through ``call_soon_threadsafe`` in that case so the
    waiter always wakes.
    """
    try:
        owner = fut.get_loop()
    except Exception:
        owner = None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    def _resolve() -> None:
        if not fut.done():
            fut.set_result(result)

    if owner is not None and owner is not running:
        try:
            owner.call_soon_threadsafe(_resolve)
            return
        except Exception:
            pass
    _resolve()


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


def _is_retryable_error(ex: Exception) -> bool:
    """Check if an exception is transient and worth retrying.

    Uses exception types first (reliable), falls back to string matching
    for unknown errors.
    """
    from omniagents.core.session.compaction import CompactionTimeoutError
    from omniagents.core.session.compaction_retry import CompactionRetriesExhausted

    if isinstance(ex, (CompactionTimeoutError, CompactionRetriesExhausted)):
        return False

    # Never retry AgentsException types - these are permanent failures
    if isinstance(ex, AgentsException):
        return False

    # Check OpenAI SDK exception types (most reliable)
    if RateLimitError is not None and isinstance(ex, RateLimitError):
        return True
    if APIConnectionError is not None and isinstance(ex, APIConnectionError):
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
    if isinstance(ex, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True

    # httpx transport failures (stream died mid-response, connect
    # refused, socket write failed) subclass neither the OpenAI
    # SDK types nor builtin ConnectionError — but they're exactly
    # the transient network class: a dropped SSE stream surfaced
    # as httpx.RemoteProtocolError ("peer closed connection")
    # must retry, not kill the turn.
    try:
        import httpx

        if isinstance(
            ex,
            (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
                httpx.WriteError,
            ),
        ):
            return True
    except ImportError:
        pass

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


def bind_current_service(service: "AgentService") -> contextvars.Token:
    """Mark the current task tree as owned by ``service``.

    ``start_run`` does this for every text Run. Drivers that execute tools
    OUTSIDE a Run — the realtime (voice) session, whose tool calls run in
    the SDK's model-listener task — must do it themselves, or every
    ``current_service()`` consumer degrades silently: worker sub-agents get
    no AgentHost resources (and their runs then fail to resolve the
    parent's environment), completion notifications never reach the
    session, and the subagent event bus drops every relay.
    """
    return _current_service.set(service)


def reset_current_service(token: contextvars.Token) -> None:
    """Restore the previous owner bound by :func:`bind_current_service`."""
    _current_service.reset(token)


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
        agent_host_resources: AgentHostResources,
    ) -> None:
        """Initialize AgentService with an agent specification.

        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings.
            agent_host_resources: The explicit workspace, environment, Thread
                binding, and operation-routing registries for this AgentHost.
        """
        super().__init__()
        if not isinstance(agent_host_resources, AgentHostResources):
            raise TypeError("agent_host_resources must be an AgentHostResources")
        self.agent_host_resources = agent_host_resources
        self.workspace_registry = agent_host_resources.workspaces
        self.environment_manager = agent_host_resources.environments
        self.thread_bindings = agent_host_resources.thread_bindings
        self.operation_router = agent_host_resources.router
        # One AgentHost-level terminal registry authorizes socket attachment
        # and owns descriptor lifecycle. Per-terminal placement/defaults come
        # from EnvironmentLease, never from mutable service configuration.
        self.terminal_manager: Any | None = None
        # In-process per-session run_end listeners (autopilot loops,
        # /goal server function, scheduled hooks). Fan-out happens
        # inside _broadcast_session before client channels are touched.
        self._run_end_listeners: Dict[str, list] = {}
        # Authorization mode. Permissive by default: an AgentService with no
        # server in front of it (embedded/CLI/eval, in-memory transports)
        # has one principal and no authentication. ``build_app`` replaces
        # this with the policy implied by the configured AuthProvider.
        self._authorization_policy = AuthorizationPolicy(enforced=False)
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
                # Routed through the retention module, not straight at the
                # history backend: the domain rows and the replay journal
                # hold the same transcript, and a sweep that only cleared
                # ``history`` was retaining exactly what it claimed to purge.
                from omniagents.core.session.retention import purge_expired_sessions

                purged = purge_expired_sessions(
                    retention_days,
                    project_slug=project_slug,
                    agent_slug=agent_slug,
                )
                if purged:
                    print(
                        f"Security: purged {len(purged)} session(s) older than "
                        f"{retention_days} days"
                    )
        except Exception:
            pass
        # Client-function request tracking (server→client RPC-style)
        self._pending_client_requests: dict[str, asyncio.Future] = {}
        # Optional capability registry per connected channel
        self._client_capabilities: dict[Any, dict] = {}
        self._client_request_sessions: dict[str, Session] = {}
        # Bounded, service-lifetime receipts: response fingerprint (None for
        # cancellation) and lightweight ownership, never a retained Session.
        self._resolved_client_requests: dict[str, tuple[str | None, Any]] = {}
        # Approval correlation IDs are server-issued tokens, never provider IDs.
        # The tuple binds each token to exactly one session and waiter.
        self._pending_tool_approvals: dict[str, tuple[asyncio.Future, Session]] = {}
        # MCP-approval futures keyed by opaque request_id tokens (approvals from
        # SDK 0.16 ``HostedMCPTool``). Tracked separately from function-tool
        # approvals so the wire format stays clean.
        self._pending_mcp_approvals: dict[str, tuple[asyncio.Future, Session]] = {}
        # Bounded retry receipts, including the owner for authorization after
        # waiter cleanup. Missing/expired receipts fail closed, never retarget.
        self._resolved_approvals: dict[
            tuple[str, str], tuple[dict, SimpleNamespace]
        ] = {}
        # Structured user-elicitation futures keyed by elicitation_id
        # (tkt_F9tu2m9PjdpY). Parallel non-security input lane — see
        # omniagents.core.agents.elicitation.
        self._pending_elicitations: dict[str, tuple[asyncio.Future, Session]] = {}
        # Server functions registry (client→server dynamic functions)
        # Map name -> {"func": callable, "description": str | None, "params_schema": dict | None, "result_schema": dict | None}
        self._server_functions: dict[str, dict] = {}
        # name -> SlashDeclaration for the typeable subset (see core/server_functions/slash.py)
        self._slash_commands: dict[str, Any] = {}
        self._runtime_hooks: dict[str, list[Any]] = {}
        # Every currently connected /ws channel (registered by
        # JsonRpcEndpoint). Connection-wide broadcasts (``account_changed``)
        # fan out here — unlike session events they are not journaled.
        self._connected_channels: list[Any] = []
        # Provider account & usage state behind the ``account_*`` RPCs.
        self.accounts = AccountManager()
        # MCP server registry/status/auth behind the ``mcp_*`` management
        # RPCs (config CRUD, startup transitions, OAuth flows). Process-
        # global, like the account surface.
        from omniagents.core.agents.mcp_admin import McpServerManager

        self.mcp_manager = McpServerManager(
            self.spec,
            settings_provider=lambda: dict(self._default_settings),
            broadcast=self._broadcast_mcp_server_status,
        )

        # Workspace filesystem watch/transfer state behind the ``fs_*`` RPCs.
        # The resolver accepts an explicit environment identity; there is no
        # session-derived workspace factory and no host fallback.
        self.filesystem = FilesystemManager(
            workspace_resolver=self._filesystem_workspace,
            source_writability_resolver=self._filesystem_source_writable,
        )

        # Typed git operations behind the ``git_*`` RPCs. Shares the
        # environment resolver with the filesystem surface, so the manager
        # never assumes a host subprocess in the server's cwd.
        self.git = GitManager(
            workspace_resolver=self._filesystem_workspace,
            source_provider=self._execution_sources,
        )

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

        self._register_slash_catalog()
        self.register_server_function(
            "session.ensure",
            self._sf_session_ensure,
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
            description="Ensure a session exists and persist non-execution metadata.",
            params_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "context": {"type": "object"},
                    "variables": {"type": "object"},
                },
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
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
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
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
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
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
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
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
            resource_scope=ResourceScope.THREAD,
            execution_requirements=ExecutionRequirement.NONE,
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

        self._register_capability_server_functions()

    def _register_capability_server_functions(self) -> None:
        """Register server functions declared by the agent's capabilities.

        Capability packs may bundle server functions (a builtin ``goal``
        capability ships ``/goal`` alongside the ``goal_complete`` tool);
        they activate only for agents that list the capability. Runs at
        service construction, BEFORE the project registrar the backend
        invokes — so a project redefinition of the same name overwrites
        the capability's version (project wins, consistent with every
        other override direction). Between two active capabilities, the
        first registration wins and the duplicate logs a warning
        (mirrors duplicate-tool discovery behavior).
        """
        try:
            caps = getattr(self.spec, "resolved_capabilities", None) or []
            caps = list(caps)
        except Exception:
            return
        seen_from_caps: set[str] = set()
        for cap in caps:
            sf_getter = getattr(cap, "server_functions", None)
            if not callable(sf_getter):
                continue
            try:
                entries = sf_getter() or []
            except Exception:
                continue
            for meta in entries:
                if not isinstance(meta, dict):
                    continue
                name = meta.get("name")
                func = meta.get("func")
                if not name or func is None:
                    continue
                if name in seen_from_caps:
                    cap_label = getattr(cap, "capability_name", "?")
                    logger.warning(
                        "Duplicate server function %r from capability %r; "
                        "keeping the first registration.",
                        name,
                        cap_label,
                    )
                    continue
                try:
                    self.register_server_function(
                        name,
                        func,
                        description=meta.get("description"),
                        params_schema=meta.get("params_schema"),
                        result_schema=meta.get("result_schema"),
                        on_invoke=meta.get("on_invoke"),
                        strict=meta.get("strict"),
                        resource_scope=meta.get("resource_scope"),
                        execution_requirements=meta.get("execution_requirements"),
                    )
                    seen_from_caps.add(name)
                except Exception as e:
                    logger.warning(
                        "Failed to register capability server function %r: %s",
                        name,
                        e,
                    )

    async def _ensure_session_mcp_servers(
        self,
        session: Session,
        environment_lease: EnvironmentLease | None = None,
        *,
        hold: list | None = None,
    ) -> list[Any]:
        """The session's live MCP servers, started if need be.

        With ``hold`` (a list), a use of the returned set is taken and its
        token appended: the set is never closed while held. Release each
        token with :meth:`_mcp_release` (a run does so as it ends).
        """
        if environment_lease is None:
            context = current_request_context()
            environment_lease = context.environment_lease if context else None
        owner = (
            (environment_lease.environment_id, environment_lease.generation)
            if environment_lease is not None
            else None
        )
        lock = getattr(session, "_mcp_start_lock", None)
        if lock is None:
            lock = session._mcp_start_lock = asyncio.Lock()
        async with lock:
            task = getattr(session, "_mcp_lifecycle_task", None)
            if task is not None and not task.done():
                # A caller with no environment lease (out of band: an MCP App
                # reading its page or calling a tool through mcp_read_resource
                # / mcp_call_tool) has no environment of its own to ask for:
                # it uses the session's live servers as they are.
                if getattr(session, "_mcp_owner", None) == owner or environment_lease is None:
                    await self._wait_session_mcp_ready(session, task)
                    if hold is not None:
                        hold.append(self._mcp_hold(session, task))
                    return session._mcp_servers_active
                # A Thread moving to another lease must not reuse workers
                # configured against its previous workspace/environment; the
                # old set stays open for whoever still holds it.
                await self._retire_session_mcp(session)
            servers = await self._start_session_mcp_servers(session, environment_lease)
            if hold is not None:
                hold.append(self._mcp_hold(session, getattr(session, "_mcp_lifecycle_task", None)))
            return servers

    # ------------------------------------------------------------------
    #  MCP server sets in use: a set is closed only when nothing holds it.
    #  A run holds the set its agent was built with until it ends; an
    #  out-of-band call holds it for the call. Replacing a held set (a new
    #  lease, a reload, a server removed) retires it instead: the session
    #  moves on to a new set, and the old one closes when its last holder
    #  lets go. Closing it at once left a run holding closed servers, and
    #  the run failed on them.
    # ------------------------------------------------------------------
    @staticmethod
    def _mcp_hold(session: Session, task: Any) -> Any:
        if task is None:
            return None
        users = getattr(session, "_mcp_users", None)
        if users is None:
            users = session._mcp_users = {}
        users[task] = users.get(task, 0) + 1
        return task

    @staticmethod
    def _mcp_release(session: Session, task: Any) -> None:
        if task is None:
            return
        users = getattr(session, "_mcp_users", None) or {}
        left = users.get(task, 0) - 1
        if left > 0:
            users[task] = left
            return
        users.pop(task, None)
        retired = getattr(session, "_mcp_retired", None) or {}
        shutdown = retired.pop(task, None)
        if shutdown is not None:
            # (its lifecycle task closes its servers itself once told)
            shutdown.set()

    async def _retire_session_mcp(self, session: Session) -> None:
        """Move the session off its current MCP server set: closed now when
        nothing holds it, else when its last holder lets go."""
        task = getattr(session, "_mcp_lifecycle_task", None)
        shutdown = getattr(session, "_mcp_shutdown", None)
        if task is not None and not task.done() and shutdown is not None:
            if (getattr(session, "_mcp_users", None) or {}).get(task):
                retired = getattr(session, "_mcp_retired", None)
                if retired is None:
                    retired = session._mcp_retired = {}
                retired[task] = shutdown
            else:
                await self._close_session_mcp_task(shutdown, task)
        setattr(session, "_mcp_shutdown", None)
        setattr(session, "_mcp_lifecycle_task", None)
        setattr(session, "_mcp_servers_active", [])

    @staticmethod
    def _close_retired_session_mcp(session: Session) -> None:
        """The session is going: every retired set closes, held or not."""
        retired = getattr(session, "_mcp_retired", None) or {}
        for shutdown in retired.values():
            shutdown.set()
        retired.clear()

    @staticmethod
    async def _close_session_mcp_task(shutdown_event, task) -> None:
        shutdown_event.set()
        # Do not cancel AnyIO's owner task from a second caller or erase a
        # timed-out worker. Environment retirement retains it for retry.
        _, pending = await asyncio.wait({task}, timeout=5.0)
        if pending:
            raise RuntimeError("MCP resource cleanup is still pending")
        await task

    @staticmethod
    async def _wait_session_mcp_ready(session, task) -> None:
        ready = asyncio.create_task(session._mcp_ready.wait())
        try:
            await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)

    async def _start_session_mcp_servers(
        self, session: Session, environment_lease: EnvironmentLease | None
    ) -> list[Any]:
        """Ensure MCP servers are initialized and running for this session.

        Creates a dedicated asyncio task to manage the MCP lifecycle, ensuring
        that AsyncExitStack.__aenter__ and __aexit__ are called from the same
        task. This prevents AnyIO "cancel scope in different task" violations.

        Servers are reused within a session's exact execution lease. The
        environment owns their retirement even when the GUI archives locally.

        Args:
            session: The session to initialize MCP servers for

        Returns:
            List of active MCP server instances
        """
        # Merge spec-level and session-level (dynamic) MCP server configs
        dynamic_cfgs: list = getattr(session, "_dynamic_mcp_servers", None) or []
        if not self.spec.mcp_servers and not dynamic_cfgs:
            return []

        # Create Event to signal when servers are ready
        servers_ready = asyncio.Event()
        shutdown_event = asyncio.Event()
        session._mcp_ready = servers_ready
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
            from functools import partial as _partial

            from omniagents.core.agents.mcp_elicitation import (
                elicitation_scope,
                make_mcp_elicitation_handler,
            )

            async with AsyncExitStack() as stack:
                active_servers = []

                try:
                    # Build all MCP servers (spec-level + dynamic session-level)
                    servers = build_mcp_servers(
                        self.spec,
                        settings,
                        auth_resolver=self.mcp_manager.auth_resolver,
                    )

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
                        # Standardized startup transitions (configured →
                        # starting → ready | failed) — recorded process-wide
                        # and broadcast as ``mcp_server_status_changed``.
                        await self.mcp_manager.record_transition(
                            server_name,
                            "starting",
                            origin=f"session:{session.id[:8]}",
                        )
                        # MCP server-initiated elicitations route through
                        # the #308 protocol, scoped to this session.
                        handler = make_mcp_elicitation_handler(
                            _partial(self.request_elicitation, session=session),
                            server_name=server_name,
                        )
                        try:
                            with elicitation_scope(handler):
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
                            await self.mcp_manager.record_transition(
                                server_name,
                                "ready",
                                origin=f"session:{session.id[:8]}",
                            )
                        except BaseException as server_err:
                            from omniagents.core.agents.mcp_admin import (
                                is_external_cancellation,
                            )

                            if is_external_cancellation(server_err):
                                raise
                            await self.mcp_manager.record_failure(
                                server_name,
                                server_err,
                                origin=f"session:{session.id[:8]}",
                            )
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
                    await shutdown_event.wait()

                except Exception as e:
                    # If initialization fails catastrophically, signal error state
                    if (
                        getattr(session, "_mcp_lifecycle_task", None)
                        is asyncio.current_task()
                    ):
                        session._mcp_servers_active = []
                    active_servers_holder["servers"] = []
                    servers_ready.set()
                    raise

                finally:
                    # AsyncExitStack cleanup happens here when context exits
                    if (
                        getattr(session, "_mcp_lifecycle_task", None)
                        is asyncio.current_task()
                    ):
                        session._mcp_servers_active = []

        # Create shutdown event for this session
        session._mcp_shutdown = shutdown_event
        session._mcp_owner = (
            (environment_lease.environment_id, environment_lease.generation)
            if environment_lease is not None
            else None
        )

        task = None

        def start_task():
            nonlocal task
            task = asyncio.create_task(
                mcp_lifecycle_manager(), name=f"mcp_lifecycle_{session.id[:8]}"
            )
            session._mcp_lifecycle_task = task
            return task

        async def close_task():
            await self._close_session_mcp_task(shutdown_event, task)

        task = (
            start_task()
            if environment_lease is None
            else await self.environment_manager.start_resource_task(
                environment_lease, start_task, close_task
            )
        )

        # Wait for servers to be initialized
        await self._wait_session_mcp_ready(session, task)

        return active_servers_holder["servers"]

    def _build_client_tools(self, session: Session, defs: list | None = None) -> list:
        """Build FunctionTool proxies for client-provided tools.

        Each tool, when invoked by the agent, sends a ``client_request`` with
        ``function="tool.call"`` over the existing WebSocket and awaits the
        ``client_response`` containing the tool result.  This avoids any
        additional network connections between container and host.

        ``defs`` overrides the session's registered definitions (used by the
        realtime bridge to pass a filtered subset).
        """
        from agents.tool import FunctionTool

        if defs is None:
            defs = getattr(session, "_client_tools", None) or []
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

    async def prepare_environment_context_for_session(
        self,
        session_id: str,
        principal=None,
        default_environment_id: Optional[str] = None,
    ) -> "RequestContext | None":
        """Resolve a routed request context for out-of-band execution paired
        to a GUI session — the realtime (voice) pipeline, which runs outside
        RPC dispatch and so never receives one from the endpoint.

        Mirrors ``start_run``'s inherit routing: the session's thread binding
        selects the environment (existing or automatic) and ``materialize``
        provisions an automatic selection into a ready lease.

        A session that has never run a text turn has a targetless binding, so
        inherit resolves to no environment. ``default_environment_id`` covers
        that: it is the same server default the GUI handshake advertises
        (``agent_host.default_environment_id``) and is applied here as an
        explicit selection, exactly as the GUI client would on its first
        ``start_run``.

        The WHOLE context is returned, not just its ``environment_lease``:
        the caller binds it for its task tree (``bind_request_context``) so
        lease-routed helpers — ``require_environment_lease``, worker
        isolation sources — resolve there exactly as they do inside a Run.
        Returns ``None`` when neither the binding nor the default names an
        environment; a targetless context would only mask that.
        """
        request_context = await self.operation_router.prepare_protocol(
            OPERATION_BY_NAME["start_run"],
            {
                "session_id": session_id,
                "environment_selection": {"mode": "inherit"},
            },
            principal=principal,
        )
        request_context = await self.operation_router.materialize(request_context)
        if request_context.environment_lease is None and default_environment_id:
            request_context = await self.operation_router.prepare_protocol(
                OPERATION_BY_NAME["start_run"],
                {
                    "session_id": session_id,
                    "environment_selection": {
                        "mode": "explicit",
                        "environment_id": default_environment_id,
                    },
                },
                principal=principal,
            )
        return request_context if request_context.environment_lease else None

    def build_realtime_client_tools(self, session_id: str, principal=None) -> list:
        """Client tools bridged into a realtime (voice) session that shares
        this GUI session's id.

        The voice UI starts its realtime session with the GUI session id, so
        client tools the GUI client registered via ``variables.client_tools``
        (see ``_sf_session_ensure``) can serve the voice agent too: the proxy
        broadcasts ``client_request {tool.call}`` over the GUI session's
        channels exactly as in text runs, and whichever connected client
        answers first supplies the result.

        The realtime pipeline has no tool-approval flow, so only tools the
        client explicitly registered with ``safe: true`` are bridged;
        approval-gated tools stay text-run only rather than silently losing
        their gate. Returns ``[]`` when the session isn't live, has no safe
        client tools, or ``principal`` may not access it.
        """
        session = self._session_manager.get(session_id)
        if session is None:
            return []
        if not self.authorization_policy.can_access(
            principal, getattr(session, "user_id", None)
        ):
            return []
        defs = getattr(session, "_client_tools", None) or []
        safe_defs = [d for d in defs if isinstance(d, dict) and d.get("safe") is True]
        return self._build_client_tools(session, defs=safe_defs)

    def _spec_for_environment(
        self, environment_lease: EnvironmentLease | None
    ) -> AgentSpec:
        """Return a Run-scoped spec adapted to one immutable environment lease.

        ``AgentSpec`` is an agent definition shared by every Thread in an
        AgentHost.  SDK sandbox sessions are live environment resources, so
        storing one on that shared object makes concurrent Runs race and makes
        profile switches retarget already-started Runs.  The shallow copy keeps
        the declarative definition shared while replacing only the SDK adapter
        fields for this Run.
        """

        run_spec = copy.copy(self.spec)
        run_spec.sandbox_use_sandbox_agent = False
        run_spec.sandbox_run_as = None
        run_spec.sandbox_run_config = None

        runtime = (
            environment_lease.sandbox_runtime if environment_lease is not None else None
        )
        if runtime is None:
            return run_spec

        base_config = getattr(self.spec, "sandbox_run_config", None)
        if base_config is None:
            from agents.run_config import SandboxRunConfig

            run_config = SandboxRunConfig(session=runtime.session)
        else:
            try:
                run_config = dataclass_replace(base_config, session=runtime.session)
            except (TypeError, ValueError):
                run_config = copy.copy(base_config)
                run_config.session = runtime.session

        run_spec.sandbox_use_sandbox_agent = True
        run_spec.sandbox_run_as = runtime.run_as
        run_spec.sandbox_run_config = run_config
        return run_spec

    async def _build_agent_for_session(
        self,
        session: Session,
        approval_callback,
        *,
        spec: AgentSpec | None = None,
        environment_lease: EnvironmentLease | None = None,
        mcp_holds: list | None = None,
    ) -> Any:
        """The session's agent for a run. With ``mcp_holds`` (a list), the
        MCP servers it's built with are held (their tokens appended) until
        the caller releases them: a run holds them to its end."""
        build_spec = spec or self.spec
        stub_app = SimpleNamespace(
            spec=build_spec,
            _request_tool_approval=approval_callback,
            # Custom builders that want session-scoped settings (model
            # override, provider gates) read it from here — the positional
            # build_agent contract (settings, server, app) predates sessions.
            session=session,
        )

        settings = dict(self._default_settings)

        # Use session-scoped MCP servers (managed by dedicated task)
        holds: list = []
        active_servers = await self._ensure_session_mcp_servers(
            session, environment_lease, hold=holds
        )

        setattr(stub_app, "mcp_servers", active_servers)

        # Build client tool proxies (tools executed on the launcher via WebSocket)
        client_tools = self._build_client_tools(session)

        try:
            if build_spec.build_agent:
                primary_server = active_servers[0] if active_servers else None
                agent = await build_spec.build_agent(settings, primary_server, stub_app)
            else:
                agent = await _default_build_agent(
                    settings,
                    active_servers,
                    build_spec,
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
            opts = build_spec.safe_agent_options
            _use_safe = build_spec.use_safe_agent
            _skip_approvals = bool(getattr(opts, "skip_approvals", False))
            if (
                build_spec.security_config
                and build_spec.security_config.get("safety_mode") == "enforced"
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

            # (the last agent built: what an out-of-band tool call's guardrails
            # are given as the agent acting, _sf_mcp_call_tool)
            session._mcp_guardrail_agent = agent
            if mcp_holds is not None:
                mcp_holds.extend(holds)
            else:
                for token in holds:
                    self._mcp_release(session, token)
            return agent
        except Exception:
            for token in holds:
                self._mcp_release(session, token)
            # If agent building fails, cleanup MCP lifecycle if it was just created
            # (unless something else holds it: an app's call in flight)
            lifecycle_task = getattr(session, "_mcp_lifecycle_task", None)
            if (
                lifecycle_task
                and not lifecycle_task.done()
                and not (getattr(session, "_mcp_users", None) or {}).get(lifecycle_task)
            ):
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

    @staticmethod
    def _apply_approvals_reviewer(session: Session, reviewer: str | None) -> None:
        """Set the session's per-conversation approval reviewer override.

        Unknown values are ignored rather than raised: run starts must not
        fail on a stale client sending a reviewer this runtime predates.
        """
        from omniagents.core.agents.approval_policy import REVIEWERS

        if reviewer in REVIEWERS:
            session.approvals_reviewer = reviewer

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
        resource_scope: ResourceScope,
        execution_requirements: ExecutionRequirement,
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
        if not isinstance(resource_scope, ResourceScope):
            raise TypeError(
                f"Server function '{name}' must declare resource_scope "
                "as a ResourceScope"
            )
        if not isinstance(execution_requirements, ExecutionRequirement):
            raise TypeError(
                f"Server function '{name}' must declare execution_requirements "
                "as an ExecutionRequirement"
            )
        if on_invoke is None:
            on_invoke = getattr(func, "_server_function_on_invoke", None)
        if strict is None:
            strict = getattr(func, "_server_function_strict", None)

        from omniagents.core.server_functions.slash import slash_declaration_of

        declaration = slash_declaration_of(func)
        slash_commands = self.__dict__.setdefault("_slash_commands", {})
        if declaration is not None:
            slash_commands[name] = declaration
        else:
            slash_commands.pop(name, None)
        self._server_functions[name] = {
            "func": func,
            "description": description,
            "params_schema": params_schema,
            "result_schema": result_schema,
            "on_invoke": on_invoke,
            "strict": strict,
            "resource_scope": resource_scope,
            "execution_requirements": execution_requirements,
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
                    "resource_scope": meta["resource_scope"].value,
                    "execution_requirements": [
                        requirement.name.lower()
                        for requirement in ExecutionRequirement
                        if requirement.value
                        and meta["execution_requirements"] & requirement
                    ],
                }
            )
        _debug_rpc("list_server_functions", {"count": len(result)})
        return result

    async def server_call(
        self,
        function: str,
        args: dict | None = None,
        session_id: str | None = None,
        environment_id: str | None = None,
        workspace_id: str | None = None,
        environment_generation: int | None = None,
    ) -> Any:  # noqa: D401
        """Invoke a registered server function.

        Args:
            function: Registered function name
            args: Arguments dict passed to the function
            session_id: Optional session identifier, provided to the function if it accepts a session
        """
        _debug_rpc(
            "server_call",
            {
                "function": function,
                "has_args": bool(args),
                "session_id": session_id,
                "environment_id": environment_id,
                "workspace_id": workspace_id,
                "environment_generation": environment_generation,
            },
        )
        entry = self._server_functions.get(function)
        if not entry:
            raise ValueError(f"Unknown server function: {function}")
        fn = entry["func"]
        on_invoke = entry.get("on_invoke")

        sess: Session | None = None
        if session_id:
            try:
                sess = self._session_manager.get_or_create(
                    session_id, user_id=self._current_user_id()
                )
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

    def _apply_session_metadata(
        self,
        session: Session,
        *,
        context: Any | None = None,
        context_provided: bool = False,
        variables: dict | None = None,
        variables_provided: bool = False,
        environment_lease: EnvironmentLease | None = None,
    ) -> None:
        """Shared helper that applies context/variables to a session.

        Handles:
        - Merging context and variables onto the session
        - Extracting transient keys (``client_tools``, ``mcp_servers``) before
          they are persisted
        - Rejecting path/id metadata as an execution-selection mechanism
        - Publishing execution identity only from an immutable lease
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

        def _ensure_mapping(value):
            if isinstance(value, dict):
                return dict(value)
            if value is None:
                return {}
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            return {}

        updated = context_provided or variables_provided
        execution_keys = {
            "workspace_root",
            "workspace_id",
            "environment_id",
            "environment_generation",
        }
        if isinstance(context_value, dict):
            context_value = dict(context_value)
            removed = execution_keys.intersection(context_value)
            for key in removed:
                context_value.pop(key, None)
            updated = updated or bool(removed)
        if isinstance(variables_value, dict):
            variables_value = dict(variables_value)
            removed = execution_keys.intersection(variables_value)
            for key in removed:
                variables_value.pop(key, None)
            updated = updated or bool(removed)

        if environment_lease is not None:
            context_value = _ensure_mapping(context_value)
            context_value.update(
                {
                    "workspace_root": environment_lease.workspace.root,
                    "workspace_id": environment_lease.workspace_id,
                    "environment_id": environment_lease.environment_id,
                    "environment_generation": environment_lease.generation,
                }
            )
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

    # ------------------------------------------------------------------
    #  Slash commands: the typeable subset of server functions.
    # ------------------------------------------------------------------

    def list_slash_commands(self) -> list[dict]:
        """The catalog clients render in popups and /help: only functions
        that carry a slash declaration, in presentation order."""
        rows = [
            declaration.to_dict(function)
            for function, declaration in self.__dict__.get(
                "_slash_commands", {}
            ).items()
            if function in self._server_functions
        ]
        rows.sort(key=lambda row: (row["order"], row["name"]))
        return rows

    async def _sf_slash_list(self, session: Session | None = None, args=None):
        return {"commands": self.list_slash_commands()}

    def _register_slash_catalog(self) -> None:
        """``slash.list`` plus one server function per manifest binding.

        A product's ``slash_commands:`` block binds a name to a project
        agent run. Each binding becomes an ordinary server function that
        runs the bound agent, carrying a slash declaration so it appears in
        the catalog; nothing about it goes through the model.
        """
        from omniagents.core.server_functions.slash import (
            SlashDeclaration,
            none as _no_args,
            text as _text_args,
        )

        self.register_server_function(
            "slash.list",
            self._sf_slash_list,
            resource_scope=ResourceScope.AGENT_HOST,
            execution_requirements=ExecutionRequirement.NONE,
            description="The slash commands a client may offer for typing.",
            params_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            result_schema={"type": "object"},
            strict=False,
        )
        try:
            from omniagents.core.runtime.slash_agents import (
                manifest_slash_bindings,
                run_slash_agent,
            )

            bindings = manifest_slash_bindings()
        except Exception:
            bindings = {}
        for command_name, binding in bindings.items():
            if command_name in self._server_functions:
                continue  # a Python-registered function of that name wins

            def _make(command: str, spec: dict):
                async def _run(session: Session | None = None, args=None):
                    payload = args or {}
                    arg = payload.get("text") if isinstance(payload, dict) else None
                    message = await run_slash_agent(
                        command, spec, arg, service=self, session=session
                    )
                    return {"message": message}

                return _run

            handler = _make(command_name, binding)
            takes_text = bool(binding.get("args") == "text")
            setattr(
                handler,
                "_slash_command",
                SlashDeclaration(
                    name=command_name,
                    description=str(
                        binding.get("description")
                        or f"Run the {binding.get('agent', command_name)} agent"
                    ),
                    usage=str(binding.get("usage") or ""),
                    args=_text_args() if takes_text else _no_args(),
                    during_run=bool(binding.get("during_run", False)),
                    order=int(binding.get("order", 100)),
                ),
            )
            self.register_server_function(
                command_name,
                handler,
                resource_scope=ResourceScope.AGENT_HOST,
                execution_requirements=ExecutionRequirement.NONE,
                description=str(binding.get("description") or ""),
                params_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "additionalProperties": False,
                },
                result_schema={"type": "object"},
                strict=False,
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
            target_session = self._session_manager.get_or_create(
                requested_id, user_id=self._current_user_id()
            )

        self._apply_session_metadata(
            target_session,
            context=payload.get("context") if "context" in payload else None,
            context_provided="context" in payload,
            variables=payload.get("variables") if "variables" in payload else None,
            variables_provided="variables" in payload,
        )

        return {
            "session_id": target_session.id,
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

    @asynccontextmanager
    async def _held_mcp_server(self, session: Session | None, server_name: str):
        """A session's live MCP server by name, held for the block (an
        out-of-band call: the set can't close under it). None if absent."""
        holds: list = []
        server = None
        if session is not None:
            try:
                servers = await self._ensure_session_mcp_servers(session, hold=holds)
            except Exception:
                servers = list(getattr(session, "_mcp_servers_active", []) or [])
            for candidate in servers or []:
                if getattr(candidate, "name", None) == server_name:
                    server = candidate
                    break
        try:
            yield server
        finally:
            for token in holds:
                self._mcp_release(session, token)

    def _mcp_public_name(self, session: Session, server_name: str, tool_name: str) -> str:
        """The tool's name as the agent knows it (``mcp_<server>__<tool>``):
        approvals and guardrails see an app's call as they'd see the agent's."""
        from omniagents.core.agents.mcp_identity import get_session_identities

        for public, identity in get_session_identities(session).items():
            if tuple(identity) == (server_name, tool_name):
                return public
        return f"mcp_{server_name}__{tool_name}"

    def _app_tool_needs_approval(
        self, session: Session, server_name: str, tool_name: str, public_name: str
    ) -> bool:
        """The rule a run's MCP tool call is held to (approval._make_mcp_predicate,
        wired in _build_agent_for_session), for an app's out-of-band call."""
        import re as _re

        from omniagents.core.agents.approval import session_allows_mcp_tool

        spec = self.spec
        opts = spec.safe_agent_options
        use_safe = spec.use_safe_agent
        skip = bool(getattr(opts, "skip_approvals", False))
        if spec.security_config and spec.security_config.get("safety_mode") == "enforced":
            use_safe = True
            skip = False
        if not use_safe or skip:
            return False
        if session_allows_mcp_tool(session, server_name, tool_name):
            return False
        names = set(getattr(opts, "safe_tool_names", None) or []) if opts else set()
        if public_name in names:
            return False
        for pattern in (getattr(opts, "safe_tool_patterns", None) or []) if opts else []:
            if _re.compile(pattern).match(public_name):
                return False
        return True

    async def _gate_app_tool_call(
        self, session: Session, server_name: str, tool_name: str, arguments: dict
    ) -> str | None:
        """An app's call to its server's tool, through the agent's tool-input
        guardrails and its approvals: None to go ahead, else why not (given
        back to the app as the call's error)."""
        from agents.tool_context import ToolContext
        from agents.tool_guardrails import ToolInputGuardrailData

        from omniagents.core.agents.mcp_identity import get_session_identities

        public = self._mcp_public_name(session, server_name, tool_name)
        args_json = json.dumps(arguments or {})
        call_id = f"app_{uuid.uuid4().hex}"

        guardrails = list(getattr(self.spec, "tool_input_guardrails", None) or [])
        if guardrails:
            data = ToolInputGuardrailData(
                context=ToolContext(
                    context=None,
                    tool_name=public,
                    tool_call_id=call_id,
                    tool_arguments=args_json,
                ),
                agent=getattr(session, "_mcp_guardrail_agent", None),
            )
            for guardrail in guardrails:
                try:
                    output = await guardrail.run(data)
                except Exception as e:
                    # (a guardrail that can't decide doesn't let the call through)
                    return f"Not run: a guardrail couldn't check it ({e})"
                behavior = output.behavior
                kind = behavior.get("type") if isinstance(behavior, dict) else getattr(behavior, "type", None)
                if kind == "reject_content":
                    message = behavior.get("message") if isinstance(behavior, dict) else getattr(behavior, "message", None)
                    return str(message or "Not run: a guardrail refused it")
                if kind == "raise_exception":
                    return "Not run: a guardrail stopped it"

        if self._app_tool_needs_approval(session, server_name, tool_name, public):
            # (its identity known to the approval, so an "always" answer is
            # the same (server, tool) grant a run's call would make)
            get_session_identities(session).setdefault(public, (server_name, tool_name))
            response = await self.request_tool_approval(
                session=session,
                call_id=call_id,
                tool_name=public,
                arguments=args_json,
            )
            if response.get("decision") != "approve":
                return str(response.get("rejection_message") or "Not run: it wasn't allowed")
        return None

    async def _sf_mcp_list_resources(
        self, session: Session | None, args: dict | None = None
    ) -> dict:
        payload = args or {}
        server_name = str(payload.get("server_name") or "")
        cursor = payload.get("cursor")
        if not server_name:
            raise ValueError("server_name is required")

        async with self._held_mcp_server(session, server_name) as server:
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

        async with self._held_mcp_server(session, server_name) as server:
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

        async with self._held_mcp_server(session, server_name) as server:
            if server is None:
                raise ValueError(f"Unknown or inactive MCP server: {server_name}")
            if not hasattr(server, "call_tool"):
                raise ValueError(f"MCP server '{server_name}' does not support call_tool")
            # An app calls its server's tools out of band, outside any run: the
            # checks a run's tool call passes are made here instead. First, the
            # tool must be one its app may call (MCP Apps' visibility)
            from omniagents.core.agents.mcp import tool_visibility

            visible = tool_visibility(server_name, tool_name)
            if visible is None:
                try:
                    await server.list_tools()  # (the wrapper records visibility)
                except Exception:
                    logger.debug("list_tools for visibility failed", exc_info=True)
                visible = tool_visibility(server_name, tool_name)
            if visible is None:
                raise ValueError(f"MCP server '{server_name}' has no tool '{tool_name}'")
            if "app" not in visible:
                raise PermissionError(
                    f"'{tool_name}' on '{server_name}' isn't one its app may call"
                )
            # Then the agent's guardrails and its approvals, as for its own calls
            refusal = await self._gate_app_tool_call(
                session, server_name, tool_name, arguments
            )
            if refusal is not None:
                return {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "result": {
                        "content": [{"type": "text", "text": refusal}],
                        "isError": True,
                    },
                }
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
            self._retire_client_request(req_id)
        session.pending_client_requests.clear()
        # Delegated approvals belong to worker tasks, which may outlive
        # this parent run. Keep them available for reconnect replay.
        for attr in ("pending_tool_approvals", "pending_mcp_approvals"):
            pending = getattr(session, attr, None)
            if pending is not None:
                for token, payload in list(pending.items()):
                    if not payload.get("worker_session_id"):
                        pending.pop(token, None)
        # Elicitations are terminal-resolved (journal + broadcast) by
        # ``_resolve_session_elicitations`` on the run-end path before this
        # runs; the sweep here only drops stragglers' bookkeeping.
        for elicitation_id in list(session.pending_elicitations.keys()):
            self._pending_elicitations.pop(elicitation_id, None)
        session.pending_elicitations.clear()

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
        for payload in session.pending_elicitations.values():
            events.append(("elicitation_requested", payload))
        for method, payl in events:
            await self._safe_notify(channel, method, payl)

    def _forget_pending_request(self, request_id: str) -> None:
        sess = self._client_request_sessions.pop(request_id, None)
        pending = getattr(sess, "pending_client_requests", None) if sess else None
        if pending is not None:
            pending.pop(request_id, None)

    def _remember_client_response(self, request_id, fingerprint, session):
        receipts = getattr(self, "_resolved_client_requests", None)
        if receipts is None:
            receipts = self._resolved_client_requests = {}
        owner = (
            SimpleNamespace(
                id=session.id,
                user_id=getattr(session, "user_id", None),
                owner_lookup_failed=getattr(session, "owner_lookup_failed", False),
            )
            if session is not None
            else None
        )
        receipts[request_id] = (fingerprint, owner)
        while len(receipts) > 2048:
            receipts.pop(next(iter(receipts)))

    def _retire_client_request(self, request_id):
        """Remove all pending ownership synchronously before any notification."""
        fut = self._pending_client_requests.pop(request_id, None)
        session = self._client_request_sessions.get(request_id)
        self._forget_pending_request(request_id)
        if fut is not None:
            self._remember_client_response(request_id, None, session)
            if not fut.done():
                fut.cancel()
        return session

    async def _cancel_client_request(self, request_id, reason):
        session = self._retire_client_request(request_id)
        if session is not None:
            try:
                await self._broadcast_session(
                    session,
                    "client_request_resolved",
                    {
                        "request_id": request_id,
                        "session_id": session.id,
                        "reason": reason,
                    },
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    #  Helper: best-effort async_call that tolerates disconnected sockets.
    #  Returns True if the notification was *consumed* by the channel —
    #  either put on the wire or dropped by the connection's notification
    #  policy — and False only if the socket had already been closed.
    #
    #  "A policy drop counts as consumed" is long-standing behaviour, not
    #  something introduced here: ``async_call`` returns silently on a
    #  gated method, so this helper has always reported True for it. The
    #  explicit check below only makes that contract visible at the call
    #  site and independent of ``async_call``'s internals; it is
    #  behaviour-identical to letting the call through (tkt_m3nx8D3Rfngw).
    # ------------------------------------------------------------------

    async def _safe_notify(
        self, channel, method: str, payload: dict
    ) -> bool:  # noqa: D401
        if not channel_accepts_notification(channel, method):
            return True
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
        # Stamp the replay envelope (session_id, stream_id, seq) and append
        # to the durable journal BEFORE any delivery attempt so events that
        # fire while no client is connected are still replayable.
        payload = stamp_session_event(session, method, payload)
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
            event_log = getattr(session, "event_log", None)
            if event_log is not None:
                event_log.drop_consumer(ch)

        if queue_if_unseen and not delivered_any and hasattr(session, "pending_events"):
            session.pending_events.append((method, payload))

        return delivered_any

    # ------------------------------------------------------------------
    #  RPC method invoked by the *client* when the user answers the
    #  approval prompt.  Resolves the Future we saved when we asked.
    # ------------------------------------------------------------------

    def _claim_approval(self, kind: str, token: str, response: dict):
        """Synchronous first-writer-wins boundary, before ANY permission effect.

        Exact retries acknowledge the original decision without repeating effects.
        Conflicting retries fail, even while the original waiter is resuming.
        """
        if response["decision"] not in ("approve", "reject"):
            raise ValueError("Approval decision must be approve or reject")
        receipts = getattr(self, "_resolved_approvals", None)
        if receipts is None:
            receipts = self._resolved_approvals = {}
        previous = receipts.get((kind, token))
        if previous is not None:
            if previous[0] != response:
                raise ValueError("Approval already resolved with a different decision")
            return previous[1], False
        pending = (
            self._pending_tool_approvals
            if kind == "tool"
            else self._pending_mcp_approvals
        )
        entry = pending.get(token)
        if entry is None or entry[0].cancelled():
            return None
        fut, session = entry
        if fut.done():
            if fut.result() != response:
                raise ValueError("Approval already resolved with a different decision")
            return session, False
        # No await between durable commit and local claim. Persistence failure
        # leaves the original future and permission state untouched for retry.
        self._persist_approval_decision(session, kind, token, response)
        # Receipts need only identity/ownership, not a retained Session with
        # its transcript, runtime, channels and tool caches.
        owner = SimpleNamespace(
            id=session.id,
            user_id=getattr(session, "user_id", None),
            owner_lookup_failed=getattr(session, "owner_lookup_failed", False),
        )
        receipts[(kind, token)] = (dict(response), owner)
        while len(receipts) > 2048:
            receipts.pop(next(iter(receipts)))
        fut.set_result(response)
        return session, True

    def _persist_approval_decision(self, session, kind, token, response):
        from omniagents.core.conversation.recorder import recorder_for_session
        from omniagents.core.conversation.model import derive_item_id

        recorder = recorder_for_session(session, service=self)
        if recorder is None:
            raise RuntimeError(
                "Cannot persist approval decision; retry when storage is available"
            )
        item_id = derive_item_id(session.id, f"approval:{kind}:{token}")
        if recorder.store.get_item(session.id, item_id) is None:
            # A prior best-effort request projection may have failed. Repair
            # only this live prompt from its server-owned replay payload.
            payload = getattr(session, f"pending_{kind}_approvals", {}).get(token)
            if payload is not None:
                if recorder.store.get_thread(session.id) is None:
                    recorder.ensure_thread(
                        session.id, user_id=getattr(session, "user_id", None)
                    )
                recorder.record(session.id, f"{kind}_approval_requested", payload)
        recorder.commit_approval_decision(
            session.id, approval_kind=kind, resource_id=token, response=response
        )

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
            Opaque server-issued approval token from tool_approval_requested.
            The provider's tool call ID is metadata, not an approval identity.
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
        claim = self._claim_approval(
            "tool",
            call_id,
            {
                "decision": decision,
                "always_approve": (
                    bool(always_approve) if decision == "approve" else False
                ),
                "rejection_message": rejection_message,
            },
        )
        if claim is None:
            return False
        sess, claimed = claim
        if not claimed:
            return True

        if decision == "approve" and always_approve:
            tool_ref = getattr(sess, "_pending_approval_tool_names", {}).get(call_id)
            if isinstance(tool_ref, tuple):
                # MCP-derived tool: grant is keyed by (server, tool) so a
                # same-named tool on a different server still prompts.
                self._session_mcp_allowlist(sess).add(
                    (str(tool_ref[0]).strip(), str(tool_ref[1]).strip())
                )
            elif tool_ref:
                allow_set = getattr(sess, "always_allow_tools", None)
                if allow_set is None:
                    allow_set = set()
                    setattr(sess, "always_allow_tools", allow_set)
                allow_set.add(tool_ref)

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
        envelope = {"ok": ok, "result": result, "error": error}
        fingerprint = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt = getattr(self, "_resolved_client_requests", {}).get(request_id)
        if receipt is not None:
            if receipt[0] is None:
                return False
            if receipt[0] != fingerprint:
                raise ValueError(
                    "Client request already resolved with a different response"
                )
            return True
        fut = self._pending_client_requests.get(request_id)
        if fut is None or fut.done():
            return False
        # Look up the session BEFORE forgetting the request so we can
        # broadcast a dismissal event to all channels (voice + chat).
        sess = self._client_request_sessions.get(request_id)
        if ok and isinstance(result, dict) and result.get("input_content"):
            pending = getattr(sess, "pending_client_requests", {}).get(request_id, {})
            if pending.get("function") == "escalate":
                from omniagents.core.tools.client_function import human_reply_outputs

                human_reply_outputs(
                    result
                )  # Validate before consuming the pending question.
        # First response wins before yielding. Retries remain authorized and
        # acknowledge the same result even after the waiter has resumed.
        self._remember_client_response(request_id, fingerprint, sess)
        self._pending_client_requests.pop(request_id, None)
        fut.set_result(envelope)
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

        if session is not None:
            payload = stamp_session_event(session, "client_request", payload)

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

        # Publish only after the waiter and its authorization/replay ownership
        # exist. Notification delivery may yield to an immediate client reply.
        fut = None
        if expect_result:
            fut = asyncio.get_running_loop().create_future()
            self._pending_client_requests[req_id] = fut
            if session is not None:
                self._client_request_sessions[req_id] = session
                pending = getattr(session, "pending_client_requests", None)
                if pending is not None:
                    pending[req_id] = dict(payload)
        reason = "cancelled"
        try:
            delivered_any = False
            for ch in channels:
                try:
                    delivered = await self._safe_notify(ch, "client_request", payload)
                    delivered_any = delivered_any or delivered
                except Exception:
                    pass
            if not expect_result:
                return delivered_any
            if timeout_ms is not None and timeout_ms > 0:
                res = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            else:
                res = await fut
            return res
        except asyncio.TimeoutError:
            reason = "timeout"
            return None
        finally:
            if fut is not None and req_id in self._pending_client_requests:
                await self._cancel_client_request(req_id, reason)

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
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
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

    async def _resolve_approval_without_client(
        self,
        *,
        session: Session,
        kind: str,
        call_id: str,
        tool_name: str,
        arguments: str | None,
        server_label: str | None = None,
        metadata: dict | None = None,
        run_id: str | None = None,
    ) -> dict | None:
        """Resolve an approval via the sandbox tier or guardian, or ``None``.

        ``None`` means "route to the human" — the caller proceeds with the
        ordinary broadcast-and-await prompt. A non-None return is a complete
        response dict (``decision``/``always_approve``/``rejection_message``)
        that never reached the client; the decision is audited and broadcast
        as ``tool_approval_reviewed`` so transcripts and replay show it.
        """
        from omniagents.core.agents import guardian
        from omniagents.core.agents.approval_policy import (
            ApprovalRoute,
            resolve_approval_route,
        )

        route, environment = await resolve_approval_route(self, session)
        if route is ApprovalRoute.HUMAN:
            return None

        if route is ApprovalRoute.AUTO_APPROVE:
            worker_policy = getattr(self, "_worker_approval_context", None)
            rationale = (
                "Explicit worker auto-approval setting"
                if worker_policy is not None and worker_policy.auto_approve
                else (
                    f"Contained environment ({environment.kind})"
                    f"{' without network egress' if not environment.has_network else ''}"
                    " — auto-approved by sandbox policy"
                )
            )
            await self._record_reviewed_approval(
                session,
                kind=kind,
                call_id=call_id,
                tool_name=tool_name,
                server_label=server_label,
                run_id=run_id,
                reviewer=(
                    "worker-policy" if worker_policy is not None else "sandbox-policy"
                ),
                arguments=arguments,
                outcome="allow",
                risk_level=None,
                rationale=rationale,
            )
            return {
                "decision": "approve",
                "always_approve": False,
                "rejection_message": None,
            }

        # GUARDIAN — codex-style: allow approves, deny rejects with the
        # rationale; unsure/timeout/error and a tripped circuit breaker fall
        # through to the human prompt.
        if guardian.breaker_tripped(session, run_id):
            return None
        request = guardian.GuardianRequest(
            kind=kind,
            tool_name=tool_name,
            arguments=arguments or "",
            server_label=server_label,
            metadata=metadata,
            environment_kind=environment.kind,
            environment_has_network=environment.has_network,
        )
        assessment = await guardian.review_approval(self, session, request)
        if assessment is None:
            return None
        if assessment.allowed:
            response = {
                "decision": "approve",
                "always_approve": False,
                "rejection_message": None,
            }
        else:
            response = {
                "decision": "reject",
                "always_approve": False,
                "rejection_message": (
                    f"Denied by the approval reviewer: {assessment.rationale}"
                ),
            }
        await self._record_reviewed_approval(
            session,
            kind=kind,
            call_id=call_id,
            tool_name=tool_name,
            server_label=server_label,
            run_id=run_id,
            reviewer="guardian",
            arguments=arguments,
            outcome=assessment.outcome,
            risk_level=assessment.risk_level,
            rationale=assessment.rationale,
        )
        if assessment.allowed:
            guardian.record_allow(session, run_id)
        else:
            guardian.record_denial(session, run_id)
        return response

    async def _record_reviewed_approval(
        self,
        session: Session,
        *,
        kind: str,
        call_id: str,
        tool_name: str,
        server_label: str | None,
        run_id: str | None,
        reviewer: str,
        arguments: str | None,
        outcome: str,
        risk_level: str | None,
        rationale: str,
    ) -> None:
        """Broadcast + audit one reviewer/sandbox-resolved approval decision."""
        payload: dict[str, Any] = {
            "call_id": f"apr_{uuid.uuid4().hex}",
            "source_call_id": call_id,
            "arguments": arguments,
            "kind": kind,
            "tool_name": tool_name,
            "session_id": session.id,
            "reviewer": reviewer,
            "outcome": outcome,
            "rationale": rationale,
        }
        if server_label:
            payload["server_label"] = server_label
        if run_id is not None:
            payload["run_id"] = run_id
        if risk_level is not None:
            payload["risk_level"] = risk_level
        from omniagents.core.conversation.recorder import recorder_for_session

        recorder = recorder_for_session(session, service=self)
        if recorder is None:
            raise RuntimeError("Cannot persist automatic approval decision")
        if recorder.store.get_thread(session.id) is None:
            recorder.ensure_thread(
                session.id, user_id=getattr(session, "user_id", None)
            )
        recorder.commit_reviewed_approval(session.id, payload)
        try:
            await self._broadcast_session(
                session,
                "tool_approval_reviewed",
                payload,
                queue_if_unseen=True,
            )
        except Exception:
            logger.exception("tool_approval_reviewed broadcast failed")
        try:
            from omniagents.core.audit.events import make_audit_event

            event_type = "tool_approved" if outcome == "allow" else "tool_rejected"
            details: dict[str, Any] = {"tool": tool_name, "reviewer": reviewer}
            if outcome != "allow":
                details["reason"] = rationale
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
            import json as _json

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

        # (server, tool) identity: MCP-derived tools carry structured
        # identity alongside the flat wire name so clients can display
        # the original name + server without parsing the prefix.
        from omniagents.core.agents.mcp_identity import lookup_identity

        mcp_identity = lookup_identity(session, tool_name)
        if mcp_identity is not None:
            payload["server_label"], payload["tool_label"] = mcp_identity

        resolved = await self._resolve_approval_without_client(
            session=session,
            kind="tool",
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            server_label=payload.get("server_label"),
            metadata=metadata if isinstance(metadata, dict) else None,
            run_id=run_id,
        )
        if resolved is not None:
            return resolved

        worker = getattr(self, "_worker_approval_context", None)
        if worker is not None:
            delegated = await worker.escalate(
                kind="tool", session=session, payload=payload
            )
            if delegated is not None:
                return delegated

        # Keep provider correlation as metadata; only the new token authorizes
        # a decision. Reused provider IDs (even within one session) cannot alias.
        payload["source_call_id"] = call_id
        call_id = f"apr_{uuid.uuid4().hex}"
        payload["call_id"] = call_id

        if Debug.enabled():
            logger.debug(
                f"  → tool_approval_requested call_id={call_id}"
                f" tool={tool_name} session={session.id} run_id={run_id}"
            )

        # Track the tool identity so the response handler can update the
        # right always-allow set without re-deriving it: the (server, tool)
        # tuple for MCP-derived tools, the bare name for everything else.
        try:
            tracker = getattr(session, "_pending_approval_tool_names", None)
            if tracker is None:
                tracker = {}
                setattr(session, "_pending_approval_tool_names", tracker)
            tracker[call_id] = mcp_identity if mcp_identity is not None else tool_name
        except Exception:
            pass

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_tool_approvals[call_id] = (fut, session)

        await self._broadcast_session(
            session,
            "tool_approval_requested",
            payload,
            queue_if_unseen=True,
        )

        # Track for replay on reconnect. Stored AFTER the broadcast so the
        # copy carries the replay envelope (seq/stream_id) stamped there —
        # re-delivery then dedupes cleanly on sequence-aware clients.
        if hasattr(session, "pending_tool_approvals"):
            session.pending_tool_approvals[call_id] = dict(payload)

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

        resolved = await self._resolve_approval_without_client(
            session=session,
            kind="mcp",
            call_id=request_id,
            tool_name=tool_name,
            arguments=arguments,
            server_label=server_label,
            run_id=run_id,
        )
        if resolved is not None:
            return {
                "decision": resolved["decision"],
                "rejection_message": resolved.get("rejection_message"),
            }

        payload: dict[str, Any] = {
            "kind": "mcp",
            "source_request_id": request_id,
            "server_label": server_label,
            "tool_name": tool_name,
            "arguments": arguments or "",
            "session_id": session.id,
        }
        worker = getattr(self, "_worker_approval_context", None)
        if worker is not None:
            delegated = await worker.escalate(
                kind="mcp", session=session, payload={**payload, "run_id": run_id}
            )
            if delegated is not None:
                return delegated

        request_id = f"apr_{uuid.uuid4().hex}"
        payload["request_id"] = request_id
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

        await self._broadcast_session(
            session,
            "mcp_approval_requested",
            payload,
            queue_if_unseen=True,
        )

        # Track for replay on reconnect (sessions may not have the attr if
        # they predate this feature; we only register if present). Stored
        # AFTER the broadcast so the copy carries the replay envelope
        # stamped there.
        pending_attr = getattr(session, "pending_mcp_approvals", None)
        if pending_attr is None and hasattr(session, "pending_tool_approvals"):
            try:
                setattr(session, "pending_mcp_approvals", {})
                pending_attr = session.pending_mcp_approvals
            except Exception:
                pending_attr = None
        if pending_attr is not None:
            pending_attr[request_id] = dict(payload)

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
            Opaque server-issued token from mcp_approval_requested, not the
            provider's McpApprovalRequest.id (available as source_request_id).
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
        claim = self._claim_approval(
            "mcp",
            request_id,
            {
                "decision": decision,
                "rejection_message": rejection_message,
            },
        )
        if claim is None:
            return False
        sess, claimed = claim
        if not claimed:
            return True

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

    # ------------------------------------------------------------------
    #  Structured user elicitation (tkt_F9tu2m9PjdpY).
    #
    #  The non-security input lane: agents/tools/server code ask the user
    #  for a decision or information (question, confirm, select, form,
    #  url) via ``request_elicitation``; the first ``elicitation_response``
    #  from any connected tab wins. Shares the approval/client_request
    #  plumbing — journaled replay, pending-map reconnect replay,
    #  run-scoped clearing — but never routes through the approval RPCs.
    # ------------------------------------------------------------------

    async def request_elicitation(
        self,
        *,
        session: Session,
        kind: str,
        message: str,
        title: str | None = None,
        input_schema: dict | None = None,
        options: list | None = None,
        url: str | None = None,
        run_id: str | None = None,
        item_id: str | None = None,
        source: str | None = None,
        timeout_ms: int | None = None,
        persist_response: bool = False,
    ) -> dict:
        """Emit an ``elicitation_requested`` event and await the response.

        Returns ``{"action", "value", "reason", "status"}`` where
        ``action`` is ``"accept"`` / ``"decline"`` / ``"cancel"`` /
        ``"expire"``. The submitted ``value`` is always returned to this
        in-process caller; it enters the journaled ``elicitation_resolved``
        event (and thus durable, replayable history) only when
        ``persist_response=True`` — flag sensitive prompts ``False`` and
        keep the answer out of model-visible context.
        """
        from omniagents.core.agents.elicitation import validate_elicitation_request

        if session is None:
            raise RuntimeError("Elicitations require an explicit session context")
        validate_elicitation_request(kind, options=options, url=url)

        import time as _time
        import uuid as _uuid

        elicitation_id = f"elc_{_uuid.uuid4().hex}"
        payload: dict[str, Any] = {
            "elicitation_id": elicitation_id,
            "kind": kind,
            "message": message,
            "session_id": session.id,
            "persist_response": bool(persist_response),
        }
        if title is not None:
            payload["title"] = title
        if input_schema is not None:
            payload["input_schema"] = input_schema
        if options is not None:
            payload["options"] = list(options)
        if url is not None:
            payload["url"] = url
        if run_id is not None:
            payload["run_id"] = run_id
        if item_id is not None:
            payload["item_id"] = item_id
        if source is not None:
            payload["source"] = source
        if timeout_ms is not None and timeout_ms > 0:
            payload["timeout_ms"] = int(timeout_ms)
            payload["expires_at"] = _time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                _time.gmtime(_time.time() + timeout_ms / 1000.0),
            )

        _debug_rpc("elicitation_requested", payload)

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_elicitations[elicitation_id] = (fut, session)

        await self._broadcast_session(
            session,
            "elicitation_requested",
            payload,
            queue_if_unseen=True,
        )

        # Track for replay on reconnect. Stored AFTER the broadcast so the
        # copy carries the replay envelope (seq/stream_id) stamped there —
        # re-delivery then dedupes cleanly on sequence-aware clients.
        session.pending_elicitations[elicitation_id] = dict(payload)

        try:
            if timeout_ms is not None and timeout_ms > 0:
                response = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            else:
                response = await fut
        except asyncio.TimeoutError:
            await self._resolve_elicitation(
                elicitation_id, session, status="expired", action="expire"
            )
            return {
                "action": "expire",
                "value": None,
                "reason": "no response within timeout",
                "status": "expired",
            }
        except asyncio.CancelledError:
            # The requesting run/task was cancelled while the prompt was
            # open. Journal + broadcast the terminal resolution so no
            # client is left holding a prompt nothing backs, then let the
            # cancellation propagate.
            try:
                await self._resolve_elicitation(
                    elicitation_id, session, status="cancelled", action="cancel"
                )
            except Exception:
                pass
            raise
        finally:
            self._pending_elicitations.pop(elicitation_id, None)
            session.pending_elicitations.pop(elicitation_id, None)

        if not isinstance(response, dict):
            return {
                "action": "decline",
                "value": None,
                "reason": "invalid elicitation response",
                "status": "declined",
            }
        return response

    async def elicitation_response(
        self,
        elicitation_id: str,
        action: str,
        value: dict | None = None,
        reason: str | None = None,
    ) -> dict:  # noqa: D401
        """Resolve a pending elicitation. First response wins.

        Raises typed errors (surfaced as structured JSON-RPC errors):
        ``ElicitationNotFound`` (-32050) for unknown ids,
        ``ElicitationAlreadyResolved`` (-32051) for duplicate responses,
        ``ElicitationInvalidResponse`` (-32052) when the response violates
        the request's contract or validation schema.
        """
        from omniagents.core.agents.elicitation import (
            ElicitationAlreadyResolved,
            ElicitationInvalidResponse,
            ElicitationNotFound,
            validate_response_value,
        )

        _debug_rpc(
            "elicitation_response",
            {
                "elicitation_id": elicitation_id,
                "action": action,
                "reason": reason,
            },
        )
        entry = self._pending_elicitations.get(elicitation_id)
        if entry is None:
            resolved = self._find_resolved_elicitation(elicitation_id)
            if resolved is not None:
                raise ElicitationAlreadyResolved(
                    elicitation_id, status=str(resolved.get("status", "resolved"))
                )
            raise ElicitationNotFound(elicitation_id)
        fut, sess = entry
        request_payload = sess.pending_elicitations.get(elicitation_id, {})

        problems = validate_response_value(request_payload, action, value)
        if problems:
            raise ElicitationInvalidResponse(elicitation_id, problems)

        status = {
            "accept": "accepted",
            "decline": "declined",
            "cancel": "cancelled",
        }[action]
        result = {
            "action": action,
            "value": value,
            "reason": reason,
            "status": status,
        }
        # Resolve BEFORE waking the waiter. ``_set_future_result`` may
        # schedule cross-loop (``call_soon_threadsafe``); once the waiter
        # resumes, its ``finally`` block pops the pending maps — if that
        # wins the race against ``_resolve_elicitation``'s own pops, the
        # resolution is never journaled, broadcast, or registered (no
        # ``elicitation_resolved`` for any tab, ``-32050`` instead of
        # ``-32051`` for late duplicates, and a ``persist_response`` value
        # silently dropped). Journal + broadcast first; only then unblock
        # the in-process caller.
        await self._resolve_elicitation(
            elicitation_id,
            sess,
            status=status,
            action=action,
            value=value,
            reason=reason,
        )
        _set_future_result(fut, dict(result))
        return {"elicitation_id": elicitation_id, "status": status}

    async def _resolve_elicitation(
        self,
        elicitation_id: str,
        session: Session,
        *,
        status: str,
        action: str | None = None,
        value: dict | None = None,
        reason: str | None = None,
    ) -> bool:
        """Single terminal chokepoint for one elicitation.

        Pops the pending maps, records the bounded resolved entry (for
        the typed already-resolved rejection), and journals + broadcasts
        ``elicitation_resolved`` — with the submitted ``value`` included
        only when the request opted into ``persist_response``. Returns
        False (no-op) when the elicitation is already resolved.
        """
        request_payload = session.pending_elicitations.get(elicitation_id)
        entry = self._pending_elicitations.pop(elicitation_id, None)
        if entry is None and request_payload is None:
            return False
        session.pending_elicitations.pop(elicitation_id, None)
        self._record_resolved_elicitation(
            session, elicitation_id, status=status, action=action
        )

        resolved: dict[str, Any] = {
            "elicitation_id": elicitation_id,
            "session_id": session.id,
            "status": status,
        }
        if action is not None:
            resolved["action"] = action
        if reason is not None:
            resolved["reason"] = reason
        run_id = (request_payload or {}).get("run_id")
        if isinstance(run_id, str):
            resolved["run_id"] = run_id
        if value is not None and bool((request_payload or {}).get("persist_response")):
            resolved["value"] = value
        try:
            await self._broadcast_session(session, "elicitation_resolved", resolved)
        except Exception:
            pass
        return True

    @staticmethod
    def _record_resolved_elicitation(
        session: Session,
        elicitation_id: str,
        *,
        status: str,
        action: str | None,
    ) -> None:
        """Remember a resolution for duplicate-response rejection.

        Bounded FIFO (256 per session) so late duplicates get a typed
        already-resolved error without unbounded growth. Values are never
        retained here regardless of the persistence flag.
        """
        registry = session.resolved_elicitations
        registry[elicitation_id] = {"status": status, "action": action}
        while len(registry) > 256:
            registry.pop(next(iter(registry)))

    def _find_resolved_elicitation(self, elicitation_id: str) -> dict | None:
        for sess in self._session_manager.iter_active():
            resolved = getattr(sess, "resolved_elicitations", {}).get(elicitation_id)
            if resolved is not None:
                return resolved
        return None

    async def _resolve_session_elicitations(
        self, session: Session, *, status: str
    ) -> None:
        """Terminal-resolve every pending elicitation for *session*.

        Run-end chokepoint: prompts must never outlive the run context
        that asked for them (acceptance: pending requests clear on run
        cancellation/termination). Waiting callers are unblocked with a
        ``cancel`` result; clients receive journaled ``elicitation_resolved``
        events so replays never resurrect the prompt.
        """
        for elicitation_id in list(session.pending_elicitations.keys()):
            entry = self._pending_elicitations.get(elicitation_id)
            # Same ordering invariant as ``elicitation_response``: journal
            # + broadcast the terminal resolution before waking the waiter,
            # so the waiter's ``finally`` cleanup can never swallow it.
            await self._resolve_elicitation(
                elicitation_id, session, status=status, action="cancel"
            )
            if entry is not None:
                _set_future_result(
                    entry[0],
                    {
                        "action": "cancel",
                        "value": None,
                        "reason": "run ended",
                        "status": status,
                    },
                )

    async def start_run(
        self,
        prompt: str,
        environment_selection: dict,
        session_id: str | None = None,
        variables: dict | None = None,
        context: Any | None = None,
        content: Any | None = None,
        workflow_name: str | None = None,
        group_id: str | None = None,
        safe_tool_overrides: dict | None = None,
        approvals_reviewer: str | None = None,
        prompt_role: str = "user",
        submission_id: str | None = None,
    ) -> dict:  # noqa: D401
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        if submission_id is not None:
            from omniagents.core.session.submissions import submit_once

            params = dict(
                prompt=prompt,
                environment_selection=environment_selection,
                session_id=sess.id,
                variables=variables,
                context=context,
                content=content,
                workflow_name=workflow_name,
                group_id=group_id,
                safe_tool_overrides=safe_tool_overrides,
                approvals_reviewer=approvals_reviewer,
                prompt_role=prompt_role,
            )
            return await submit_once(
                sess,
                self._conversation_service().store,
                submission_id,
                {"method": "start_run", **params},
                lambda: self.start_run(**params),
            )
        from omniagents.core.runtime.ownership import reserve_run

        if sess.current_task is not None and not sess.current_task.done():
            raise RuntimeError("Run already active in this session")
        self._recover_local_run_ownership(sess)
        ownership = reserve_run(self._queue_store(sess), sess.id)
        sess.runtime_ownership = ownership
        from omniagents.core.models import bind_run_models, reset_run_models

        model_scope = model_token = None
        try:
            store = self._model_catalog_store()
            if store is not None and store.single_provider:
                model_scope, model_token = bind_run_models(store.snapshot_for_run(sess))
            ownership.start_heartbeat()
            if ownership.took_over_from is not None:
                self._close_interrupted_run(sess, ownership.took_over_from)
            sess.refresh_owned_history(ownership)
            return await self._start_owned_run(
                sess,
                ownership,
                prompt,
                environment_selection,
                variables=variables,
                context=context,
                content=content,
                workflow_name=workflow_name,
                group_id=group_id,
                safe_tool_overrides=safe_tool_overrides,
                approvals_reviewer=approvals_reviewer,
                prompt_role=prompt_role,
                session_id=session_id,
            )
        finally:
            if model_token is not None:
                reset_run_models(model_token)
            if model_scope is not None:
                if ownership.transferred and sess.current_task is not None:
                    # Queue drainers may inherit this context: once the run ends,
                    # they must see current configuration for the next turn.
                    sess.current_task.add_done_callback(
                        lambda _: setattr(model_scope, "active", False)
                    )
                else:
                    model_scope.active = False
            # Preparation failure/cancellation never dispatched a worker.
            if not ownership.transferred:
                ownership.release()

    async def _start_owned_run(
        self,
        sess,
        ownership,
        prompt,
        environment_selection,
        *,
        variables,
        context,
        content,
        workflow_name,
        group_id,
        safe_tool_overrides,
        approvals_reviewer,
        prompt_role,
        session_id,
    ):
        channel = getattr(self, "channel", None)

        # The endpoint's OperationRouter resolves the run selection before
        # authorization and binds that immutable lease for this handler. Direct
        # in-process callers use the same router explicitly; neither path may
        # infer execution from session metadata or a workspace path.
        request_context = current_request_context()
        if request_context is None or request_context.operation.name != "start_run":
            request_context = await self.operation_router.prepare_protocol(
                OPERATION_BY_NAME["start_run"],
                {
                    "prompt": prompt,
                    "session_id": sess.id,
                    "environment_selection": environment_selection,
                },
            )
        request_context = await self.operation_router.materialize(request_context)
        environment_lease = request_context.environment_lease
        ownership.record_recovery_scope(
            "host"
            if environment_lease is not None
            and isinstance(environment_lease.workspace, HostWorkspace)
            else "remote"
        )
        # Reject before changing the active run's binding, cancellation flags
        # or stream handle (including while its request cleanup is finishing).
        if sess.current_task is not None and not sess.current_task.done():
            raise RuntimeError("Run already active in this session")
        selection_mode = environment_selection.get("mode")
        if selection_mode == "explicit":
            if environment_lease is None:
                raise RuntimeError("explicit environment selection produced no lease")
            await self.thread_bindings.bind(
                sess.id,
                ThreadRuntimeBinding(
                    workspace_id=environment_lease.workspace_id,
                    environment_selection=ExistingEnvironment(
                        environment_lease.environment_id,
                        environment_lease.generation,
                    ),
                ),
            )
        elif selection_mode == "none":
            await self.thread_bindings.bind(sess.id, ThreadRuntimeBinding())
        elif selection_mode != "inherit":
            raise TypeError(
                f"unsupported environment selection mode: {selection_mode!r}"
            )

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

        # Execution metadata comes only from the resolved lease. Remove any
        # stale path-derived routing left in persisted session metadata, then
        # publish the selected environment to the Run context for tools and
        # background resources that need to tag their ownership.
        if isinstance(merged_context, dict):
            merged_context = dict(merged_context)
            merged_context.pop("workspace_root", None)
            merged_context.pop("environment_id", None)
            merged_context.pop("environment_generation", None)
            merged_context.pop("workspace_id", None)
        if isinstance(merged_variables, dict):
            merged_variables = dict(merged_variables)
            merged_variables.pop("workspace_root", None)
            merged_variables.pop("environment_id", None)
            merged_variables.pop("environment_generation", None)
            merged_variables.pop("workspace_id", None)

        workspace_root = (
            environment_lease.workspace.root if environment_lease is not None else None
        )
        if environment_lease is not None:
            if not isinstance(merged_context, dict):
                merged_context = {}
            merged_context.update(
                {
                    "workspace_root": workspace_root,
                    "workspace_id": environment_lease.workspace_id,
                    "environment_id": environment_lease.environment_id,
                    "environment_generation": environment_lease.generation,
                }
            )

        self._apply_session_metadata(
            sess,
            context=merged_context,
            context_provided=True,
            variables=merged_variables,
            variables_provided=True,
            environment_lease=environment_lease,
        )

        self._apply_safe_tool_overrides(sess, safe_tool_overrides)
        self._apply_approvals_reviewer(sess, approvals_reviewer)

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

        catalog_store = self._model_catalog_store()
        if catalog_store is not None:
            catalog_store.reconcile_session_model(sess)

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

        run_spec = self._spec_for_environment(environment_lease)
        mcp_holds: list = []
        agent = await self._build_agent_for_session(
            sess, None, spec=run_spec, environment_lease=environment_lease, mcp_holds=mcp_holds
        )
        # The run holds the MCP servers its agent was built with until its
        # reservation is released (however it ends, started or not)
        for token in mcp_holds:
            ownership.add_release_callback(
                lambda token=token: self._mcp_release(sess, token)
            )

        # When the caller supplies a non-user prompt role, write the
        # message to history with that role and tell the worker to
        # resume from history rather than inject a fresh user turn.
        # Used by _notification_flusher to land batched bash-job /
        # worker completion notices as assistant messages while still
        # triggering a run.
        _resume_from_history = prompt_role != "user"
        if _resume_from_history:
            # Store assistant-role items with EXPLICIT output parts. Bare
            # string content gets coerced to ``input_text`` parts by the
            # SDK's input conversion, and ``responses.compact`` rejects
            # assistant items with input_text ("Invalid value: 'input_text'.
            # Supported values are: 'output_text' and 'refusal'." — probed
            # live 2026-07-07). Already-typed parts pass through untouched.
            content: Any = prompt
            if prompt_role == "assistant":
                content = [{"type": "output_text", "text": prompt, "annotations": []}]
            sess.append_message(
                {"role": prompt_role, "content": content, "type": "message"}
            )

        async def _worker():
            # Mark this run's task tree as owned by ``self``. Background
            # tasks spawned during the run (worker monitors, bash job
            # watchers, ``Session.set_tick`` loops, drainers triggered
            # by ``enqueue_notification``) snapshot this value via
            # ``asyncio.create_task`` and will resolve ``current_service()``
            # to the right host even when a worker's ``LocalAgent`` has
            # constructed its own ``AgentService`` in a nested scope.
            bind_current_service(self)

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
                session_model_config = (
                    catalog_store.get_model_config(catalog_store._run_model_ref)
                    if catalog_store is not None and catalog_store._run_model_ref
                    else getattr(sess, "model_config", None)
                )

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

                if catalog_store is not None and catalog_store.single_provider:
                    from omniagents.core.models import provider_from_runtime

                    runtime = catalog_store.resolve_model_for_runtime(
                        catalog_store._run_model_ref or sess.active_model
                    )
                    model_provider, _ = provider_from_runtime(runtime)
                elif provider_cfg:
                    try:
                        model_provider = build_model_provider(provider_cfg)
                    except ImportError as e:
                        if catalog_store is not None and catalog_store.single_provider:
                            raise
                        logger.warning(str(e))
                    except Exception as e:
                        if catalog_store is not None and catalog_store.single_provider:
                            raise
                        logger.warning(f"Failed to create model provider: {e}")
                if (
                    catalog_store is not None
                    and catalog_store.single_provider
                    and model_provider is None
                ):
                    raise ValueError(
                        "The workspace provider could not be initialized. Check its connection in Settings."
                    )

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
                    from omniagents.core.session.compaction import (
                        native_compaction_for_session,
                    )

                    sdk_session = native_compaction_for_session(
                        self,
                        sess,
                        model_config=session_model_config,
                        model_provider=model_provider,
                    )
                else:
                    sess._sdk_compaction_session = None

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
                            sandbox_run_config=run_spec.sandbox_run_config,
                            environment_lease=environment_lease,
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
                        if attempt <= max_retries and _is_retryable_error(e):
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
                    # Catalog identity of the serving model, so run-level
                    # usage joins the model catalog (see protocol.md).
                    if getattr(sess, "active_model", None):
                        payload["model_ref"] = (
                            catalog_store._run_model_ref
                            if catalog_store is not None
                            else None
                        ) or sess.active_model
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
                if sess.current_stream_result is not None:
                    sess.current_stream_result = None
                # Terminal-resolve pending elicitations BEFORE clearing
                # live state: the prompts must not outlive the run, and
                # clients need journaled ``elicitation_resolved`` events
                # (not a silent wipe) so replays converge.
                try:
                    await self._resolve_session_elicitations(sess, status="cancelled")
                except Exception:
                    pass
                for request_id in list(sess.pending_client_requests):
                    await self._cancel_client_request(request_id, "run_ended")
                self._clear_live_state(sess)
                # Keep the session occupied through the awaited request
                # cleanup above: a new run's requests must not be cleared by
                # its predecessor. Release it before broadcasting run_end.
                if sess.current_task is task:
                    ownership.release()
                    sess.current_task = None

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

        task_name = ownership.run_id
        _debug_rpc(
            "start_run",
            {
                "prompt": prompt,
                "session_id_in": session_id,
                "session_id": sess.id,
                "run_id": task_name,
            },
        )

        async def _routed_worker() -> None:
            # RPC dispatch binds the context while ``start_run`` is executing,
            # but the Run is its own lifetime and direct in-process callers do
            # not pass through that dispatch scope. Bind the resolved snapshot
            # explicitly so tools and child tasks always see the same immutable
            # Workspace/Environment lease.
            token = bind_request_context(request_context)
            from omniagents.core.runtime.ownership import current_ownership

            ownership.entered = True
            owner_token = current_ownership.set(ownership)
            try:
                ownership.assert_current()
                await _worker()
            finally:
                reset_request_context(token)
                current_ownership.reset(owner_token)
                ownership.release()

        def start_task() -> asyncio.Task:
            # Preparation above can await; another start may have won since
            # the initial session check. Publish exactly one task per session.
            if sess.current_task is not None and not sess.current_task.done():
                raise RuntimeError("Run already active in this session")
            created = asyncio.create_task(_routed_worker(), name=task_name)
            self._session_manager.bind_task(created, sess)
            ownership.transferred = True

            def release_unstarted(done):
                # Cancellation before the coroutine enters skips its finally.
                if not ownership.entered:
                    ownership.release()

            created.add_done_callback(release_unstarted)
            return created

        if environment_lease is None:
            task = start_task()
        else:
            task = await self.environment_manager.start_run_task(
                environment_lease,
                start_task,
                lambda: self._session_manager.cancel(task_name),
            )
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
        # ``Session.append_message`` materializes the canonical thread before
        # the row lands in history (see _materialize_conversation_thread), so
        # the projection cannot see this row and record it a second time. The
        # recorder handle is resolved here for the explicit item below.
        from omniagents.core.conversation.recorder import ensure_conversation_thread

        recorder = ensure_conversation_thread(sess)
        if recorder is None:
            raise RuntimeError("Cannot persist message: canonical thread unavailable")
        # Drain first so identical consecutive messages reserve different
        # occurrence identities even after an interrupted projection.
        recorder.recover_pending_messages(sess.id)
        from omniagents.core.conversation.model import content_key

        natural_key = recorder._next_occurrence_key(
            sess.id, f"user:{run_id}:{content_key(content)}"
        )
        sess.append_message(
            msg,
            projection={
                "version": 1,
                "kind": "user_message",
                "run_id": run_id,
                "natural_key": natural_key,
            },
        )
        # No immediate return value; next assistant chunk will arrive via stream.

    async def get_run_state(self, session_id: str) -> dict:
        """Light run-in-flight probe for *session_id*.

        The full reconnect path (``get_session_history``) replays the live
        payloads too, but drags the entire transcript along with them. A
        supervising client that only derives presence — the launcher's
        resident watcher — asks this at connect time instead of assuming
        idle, closing the "run already in flight when the watcher attached"
        gap. Registers the calling channel for live events, same as the
        history path, so the eventual ``run_end`` reaches the caller.
        """
        _debug_rpc("get_run_state", {"session_id": session_id})
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        if self.channel is not None and self.channel not in sess.channels:
            sess.channels.append(self.channel)
        return {
            "session_id": session_id,
            "active_run_id": getattr(sess, "active_run_id", None),
        }

    # ------------------------------------------------------------------
    #  Durable sequenced replay (``replay`` capability).
    #
    #  Clients track (stream_id, seq) from the replay envelope stamped on
    #  every replayable notification. On reconnect they call
    #  ``resume_session`` with their last applied cursor and receive the
    #  retained events they missed, in order; ``ack_events`` advances the
    #  server-side cursor so fully-acknowledged events can be compacted.
    #  A cursor that cannot be served (stream epoch changed, cursor below
    #  the compaction floor, or ahead of the stream) raises
    #  ``ResyncRequiredError`` → JSON-RPC error -32030 with kind
    #  "resync_required"; the client must refetch authoritative state via
    #  ``get_session_history`` and adopt the advertised ``last_seq``.
    # ------------------------------------------------------------------

    async def resume_session(
        self,
        session_id: str,
        stream_id: str | None = None,
        after_seq: int | None = None,
    ) -> dict:  # noqa: D401
        """Replay retained events after ``after_seq`` for *session_id*.

        Registers the calling channel for live events (same as
        ``get_session_history``) and registers its replay cursor so
        acknowledgement-driven compaction never outruns this consumer.
        Returns the retained events *in the result* (not as notifications)
        so ordering relative to subsequent live events is explicit:
        clients apply ``events`` first, adopt ``last_seq``, then process
        live notifications with sequence-based dedup.

        The returned events honour the calling connection's notification
        policy exactly as the live path does: anything the client disabled
        at ``initialize``, or whose experimental capability it never
        granted, is filtered out (tkt_m3nx8D3Rfngw). Filtering does not
        hold the cursor back — ``last_seq`` still spans the filtered
        events, so a policy drop on replay is delivered-and-discarded just
        like a policy drop on the live path. See ``protocol.md``.
        """
        _debug_rpc(
            "resume_session",
            {
                "session_id": session_id,
                "stream_id": stream_id,
                "after_seq": after_seq,
            },
        )
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        if self.channel is not None and self.channel not in sess.channels:
            sess.channels.append(self.channel)
        log = sess.event_log
        cursor = int(after_seq or 0)
        events = log.events_after(cursor, stream_id=stream_id)
        # Every live channel is a consumer: registering the others at
        # cursor 0 (no-op for already-known cursors) keeps acknowledgement
        # -driven compaction from outrunning a lagging tab. The caller's
        # ``after_seq`` is an implicit ack of everything at or below it.
        self._register_replay_consumers(sess)
        if self.channel is not None:
            log.acknowledge(self.channel, cursor)
        return {
            "session_id": sess.id,
            "stream_id": log.stream_id,
            "first_seq": log.first_retained_seq,
            "last_seq": log.last_seq,
            "active_run_id": getattr(sess, "active_run_id", None),
            "events": [
                {"method": method, "params": payload}
                for method, payload in events
                # fail_open=False: a raising policy on the live path costs
                # one notification, but here it would hand the client a
                # whole result full of gated events.
                if channel_accepts_notification(self.channel, method, fail_open=False)
            ],
        }

    def _register_replay_consumers(self, sess: Session) -> None:
        """Register every live channel as a replay consumer (cursor 0 for
        channels that have not resumed/acked yet) so compaction never runs
        ahead of a lagging tab. Existing cursors are never regressed."""
        log = sess.event_log
        for ch in sess.channels:
            log.register_consumer(ch, acked_seq=0)

    async def ack_events(
        self, session_id: str, stream_id: str, seq: int
    ) -> dict:  # noqa: D401
        """Acknowledge delivery of all events up to ``seq``.

        Advances the calling channel's cursor; events acknowledged by every
        live consumer become eligible for compaction. Raises
        ``ResyncRequiredError`` when ``stream_id`` does not match the
        session's current stream epoch.
        """
        _debug_rpc(
            "ack_events",
            {"session_id": session_id, "stream_id": stream_id, "seq": seq},
        )
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        log = sess.event_log
        self._register_replay_consumers(sess)
        consumer = self.channel if self.channel is not None else "__ackless__"
        effective = log.acknowledge(consumer, seq, stream_id=stream_id)
        return {
            "session_id": sess.id,
            "stream_id": log.stream_id,
            "acknowledged_seq": effective,
            "first_seq": log.first_retained_seq,
            "last_seq": log.last_seq,
        }

    # ------------------------------------------------------------------
    #  NEW: expose conversation history to clients ---------------------
    # ------------------------------------------------------------------
    async def get_session_history(self, session_id: str) -> list[dict]:  # noqa: D401
        """Return JSON-serialisable history for *session_id*."""
        _debug_rpc("get_session_history", {"session_id": session_id})
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )

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

    # ------------------------------------------------------------------
    #  Canonical conversation domain objects (tkt_a-QF2Oc4OAsD).
    #
    #  Thread / Turn / Item reads over the persisted domain store. The
    #  write path is the notification chokepoint (see
    #  ``omniagents.core.conversation.recorder``); the two facts that never
    #  broadcast — mid-run user messages and compaction checkpoints — are
    #  recorded from their own code paths via ``_record_conversation``.
    #  See protocol.md § "Conversation Domain Objects".
    # ------------------------------------------------------------------

    def _record_conversation(self, session: Session, action: Any) -> None:
        """Run *action* against a recorder bound to *session*'s shard.

        Best-effort by design: the domain store is a derived projection, and
        a fault there must not abort the user's turn. Failures are logged,
        never swallowed silently.
        """
        from omniagents.core.conversation.recorder import recorder_for_session

        try:
            # ``service=self`` matters: ``current_service()`` is a contextvar
            # set on a RUN's task tree, so it is None in an RPC handler like
            # archive_session — the one place a plan lands terminally.
            recorder = recorder_for_session(session, service=self)
            if recorder is None:
                return
            recorder.ensure_thread(
                session.id,
                user_id=getattr(session, "user_id", None),
                archived=bool(getattr(session, "archived", False)),
            )
            action(recorder)
        except Exception:
            logger.exception(
                "conversation domain recording failed",
                extra={"session": getattr(session, "id", None)},
            )

    def _conversation_service(self):
        """Read-side service bound to this process's (project, agent) shard."""
        from omniagents.core.conversation.service import ConversationService

        manager = self._session_manager
        return ConversationService(
            project_slug=getattr(manager, "_project_slug", None),
            agent_slug=getattr(manager, "_agent_slug", None),
        )

    async def get_thread(self, thread_id: str) -> dict:  # noqa: D401
        """The canonical Thread for *thread_id* (== ``session_id``)."""
        _debug_rpc("get_thread", {"thread_id": thread_id})
        return self._conversation_service().get_thread(thread_id)

    async def list_turns(
        self,
        thread_id: str,
        limit: int | None = None,
        cursor: str | None = None,
        order: str | None = None,
    ) -> dict:  # noqa: D401
        """Turns of *thread_id*, keyset-paginated by ``ordinal``."""
        _debug_rpc(
            "list_turns",
            {"thread_id": thread_id, "limit": limit, "cursor": cursor, "order": order},
        )
        return self._conversation_service().list_turns(
            thread_id,
            limit=limit,
            cursor=cursor,
            descending=(order == "desc"),
        )

    async def list_items(
        self,
        thread_id: str,
        turn_id: str | None = None,
        kinds: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        order: str | None = None,
    ) -> dict:  # noqa: D401
        """Items of *thread_id*, keyset-paginated by ``seq``.

        Optionally scoped to one ``turn_id`` and/or filtered to a set of
        item kinds. Everything needed to render the conversation is in the
        returned items — no event replay.
        """
        _debug_rpc(
            "list_items",
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "kinds": kinds,
                "limit": limit,
                "cursor": cursor,
                "order": order,
            },
        )
        return self._conversation_service().list_items(
            thread_id,
            turn_id=turn_id,
            kinds=kinds,
            limit=limit,
            cursor=cursor,
            descending=(order == "desc"),
        )

    async def get_item(self, thread_id: str, item_id: str) -> dict:  # noqa: D401
        """One item by canonical id."""
        _debug_rpc("get_item", {"thread_id": thread_id, "item_id": item_id})
        return self._conversation_service().get_item(thread_id, item_id)

    # ------------------------------------------------------------------
    #  Conversation organization & branching (tkt_eZldW-Gje6kn).
    #
    #  Listing, search, metadata, ancestry, export and retention over the
    #  same canonical objects. See protocol.md § "Conversation
    #  Organization, Search & Branching".
    # ------------------------------------------------------------------

    def _thread_filter(
        self,
        *,
        status: str | None = None,
        pinned: bool | None = None,
        source: str | None = None,
        model: str | None = None,
        parent_thread_id: str | None = None,
        created_after: float | None = None,
        created_before: float | None = None,
        updated_after: float | None = None,
        updated_before: float | None = None,
    ) -> ThreadFilter:
        """Build the listing predicate, ownership scope already applied.

        The scope is stamped HERE, in the one constructor both the listing
        and the search go through, rather than being a parameter each
        handler remembers to pass. A caller cannot ask for an unscoped
        listing because there is no code path that builds one."""
        from omniagents.core.conversation.model import ThreadStatus

        parsed_status: ThreadStatus | None = None
        if status is not None:
            try:
                parsed_status = ThreadStatus(status)
            except ValueError as exc:
                raise ValueError(f"Unknown thread status: {status}") from exc
        # ``_listing_scope_user_id`` returns None for "do not scope" — the
        # single-user and admin cases. Translate that into the explicit
        # UNSCOPED sentinel HERE, at the one place a filter is built, so a
        # missing scope can never be mistaken for a deliberate one
        # downstream (ThreadFilter.user_id has no default).
        scope = self._listing_scope_user_id()
        return ThreadFilter(
            user_id=UNSCOPED if scope is None else scope,
            status=parsed_status,
            pinned=pinned,
            source=source,
            model=model,
            parent_thread_id=parent_thread_id,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
        )

    def _descendant_access_predicate(self):
        """``owner -> bool`` for the principal driving this request.

        Built once, here, because this is the only layer that knows who is
        calling. Handed to every cascade so acting on a descendant is an
        ACCESS decision made deliberately, rather than the accident of an
        integrity walk that is (correctly) owner-agnostic.
        """
        policy = self.authorization_policy
        principal = self._current_principal()

        def can_access(owner: str | None) -> bool:
            return policy.can_access(principal, owner)

        return can_access

    def _thread_live_state(self, thread_id: str) -> dict:
        """Runtime state a persisted thread row cannot know.

        ``loaded`` / ``active`` / pending-attention counts live in the
        process, not the store, and a sidebar that shows a stale "waiting
        for your approval" badge is worse than one that shows none. Merged
        into every listing entry so a client never has to correlate a
        listing against a second live-state call.
        """
        sess = self._session_manager.get(thread_id)
        if sess is None:
            return {
                "loaded": False,
                "active": False,
                "pending_attention": 0,
                "attention": {
                    "tool_approvals": 0,
                    "mcp_approvals": 0,
                    "elicitations": 0,
                    "client_requests": 0,
                },
            }
        attention = {
            "tool_approvals": len(getattr(sess, "pending_tool_approvals", {}) or {}),
            "mcp_approvals": len(getattr(sess, "pending_mcp_approvals", {}) or {}),
            "elicitations": len(getattr(sess, "pending_elicitations", {}) or {}),
            "client_requests": len(getattr(sess, "pending_client_requests", {}) or {}),
        }
        task = getattr(sess, "current_task", None)
        return {
            "loaded": True,
            "active": task is not None and not task.done(),
            "pending_attention": sum(attention.values()),
            "attention": attention,
        }

    async def list_threads(
        self,
        status: str | None = None,
        pinned: bool | None = None,
        source: str | None = None,
        model: str | None = None,
        parent_thread_id: str | None = None,
        created_after: float | None = None,
        created_before: float | None = None,
        updated_after: float | None = None,
        updated_before: float | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        order: str | None = None,
    ) -> dict:  # noqa: D401
        """Keyset-paginated thread listing, filtered and self-scoped.

        Authorization: **globally safe** in the ``list_sessions`` sense —
        the result is scoped to the caller's own threads inside the SQL
        that applies ``LIMIT``, so it names no resource and leaks none.
        """
        _debug_rpc(
            "list_threads",
            {
                "status": status,
                "pinned": pinned,
                "source": source,
                "model": model,
                "parent_thread_id": parent_thread_id,
                "limit": limit,
                "cursor": cursor,
                "order": order,
            },
        )
        result = self._conversation_service().list_threads(
            thread_filter=self._thread_filter(
                status=status,
                pinned=pinned,
                source=source,
                model=model,
                parent_thread_id=parent_thread_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
            ),
            limit=limit,
            cursor=cursor,
            descending=(order != "asc"),
        )
        for thread in result["threads"]:
            thread.update(self._thread_live_state(thread["thread_id"]))
        return result

    async def search_threads(
        self,
        query: str,
        status: str | None = None,
        pinned: bool | None = None,
        source: str | None = None,
        model: str | None = None,
        parent_thread_id: str | None = None,
        created_after: float | None = None,
        created_before: float | None = None,
        updated_after: float | None = None,
        updated_before: float | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:  # noqa: D401
        """Full-text search over the caller's own conversations.

        Same authorization posture as ``list_threads``: the ownership scope
        is part of the SQL predicate the match is drawn under, so a query
        cannot surface another principal's transcript — not even as a
        preview snippet.
        """
        _debug_rpc("search_threads", {"query": query, "limit": limit})
        result = self._conversation_service().search_threads(
            query,
            thread_filter=self._thread_filter(
                status=status,
                pinned=pinned,
                source=source,
                model=model,
                parent_thread_id=parent_thread_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
            ),
            limit=limit,
            cursor=cursor,
        )
        for entry in result["results"]:
            entry["thread"].update(self._thread_live_state(entry["thread_id"]))
        return result

    async def list_thread_descendants(
        self,
        thread_id: str,
        max_depth: int | None = None,
        limit: int | None = None,
    ) -> dict:  # noqa: D401
        """The fork subtree below *thread_id*, plus its own branch point.

        Ownership is checked **per node**, so a fork of a fork of the
        caller's own thread is returned even when a stranger's fork sits
        between the two — and the stranger's is still omitted. This is the
        same rule and the same walk the archive cascade and the delete
        guard use.

        The rule is shared; the BOUNDS are not. This read caps at
        ``max_depth``/``limit`` and the cascade caps at nothing, so the
        branch tree a client draws here equals the set
        ``cascaded_thread_ids`` will report **only while ``truncated`` is
        false**. When it is true the client is looking at a subset of what
        an archive would move, and the flag is how it knows.
        """
        _debug_rpc(
            "list_thread_descendants",
            {"thread_id": thread_id, "max_depth": max_depth, "limit": limit},
        )
        result = self._conversation_service().list_descendants(
            thread_id,
            # The SAME per-node predicate the archive cascade and the
            # delete guard use, over the same walk: the branch tree a
            # client draws here has to be the node set those act on.
            can_access=self._descendant_access_predicate(),
            max_depth=max_depth,
            limit=limit,
        )
        for child in result["descendants"]:
            child.update(self._thread_live_state(child["thread_id"]))
        return result

    async def update_thread(
        self,
        thread_id: str,
        title: Any = _CONVERSATION_UNSET,
        pinned: bool | None = None,
        status: str | None = None,
        metadata: dict | None = None,
    ) -> dict:  # noqa: D401
        """Rename, pin, archive or annotate a conversation.

        ``title`` distinguishes "absent" from an explicit ``null`` (which
        clears it); ``metadata`` merges, with a ``null`` value removing a
        key. Archiving cascades to the fork subtree — see
        ``ConversationService.set_status`` for why that policy and not
        another.

        Every change is broadcast as ``thread_updated`` to the connected
        clients allowed to see it, because the surface that has to stay
        consistent is the *listing* in every open column, and those columns
        are attached to other sessions or to none at all.
        """
        _debug_rpc(
            "update_thread",
            {
                "thread_id": thread_id,
                "pinned": pinned,
                "status": status,
                "has_title": not isinstance(title, _ConversationUnset),
                "metadata_keys": sorted(metadata) if metadata else [],
            },
        )
        service = self._conversation_service()
        result = service.update_thread(
            thread_id,
            can_access=self._descendant_access_predicate(),
            title=title,
            pinned=pinned,
            status=status,
            metadata=metadata,
        )
        if "status" in result["changed"]:
            # The legacy ``sessions.archived`` flag and ``Thread.status``
            # are documented as one fact. Two writers of one fact drift, so
            # the RPC layer that owns the session objects mirrors it for the
            # thread AND every descendant the cascade touched.
            archived = result["thread"]["status"] == "archived"
            for target in [thread_id, *result["cascaded_thread_ids"]]:
                self._mirror_archive_flag(target, archived)
        if result["changed"]:
            await self._broadcast_thread_updated(
                result["thread"], result["changed"], result["cascaded_thread_ids"]
            )
        return result

    def _mirror_archive_flag(self, session_id: str, archived: bool) -> None:
        """Keep ``sessions.archived`` and the live Session in step."""
        from omniagents.core.session import history_db

        try:
            history_db.set_archived(
                session_id,
                archived,
                project_slug=getattr(self._session_manager, "_project_slug", None),
                agent_slug=getattr(self._session_manager, "_agent_slug", None),
            )
        except Exception:
            logger.exception(
                "session archive flag not mirrored", extra={"session": session_id}
            )
        sess = self._session_manager.get(session_id)
        if sess is not None:
            sess.archived = archived

    async def _broadcast_thread_updated(
        self,
        thread: dict,
        changed: list[str],
        cascaded: list[str],
    ) -> None:
        """Push a thread-metadata change to every channel entitled to it.

        Connection-wide (the ``account_changed`` mechanism), not
        session-scoped: a renamed or archived thread has to disappear from
        the sidebar of columns that are *not* displaying it, and a
        session-scoped broadcast reaches only the one column that already
        knows.

        Not journaled, for the same reason ``account_changed`` is not:
        thread metadata is current state, not conversation history, and
        replaying a stale rename after a reconnect would be wrong. A
        reconnecting client refetches ``list_threads``.

        **Per-channel authorization.** A connection-wide fan-out of
        per-owner data is a leak unless each recipient is checked: the
        payload carries a title, which is derived from the user's first
        message. Every channel is tested against the thread's owner with
        the same policy the request path uses.
        """
        payload = {
            "thread_id": thread["thread_id"],
            "changed": list(changed),
            "thread": thread,
            "cascaded_thread_ids": list(cascaded),
        }
        _debug_rpc("thread_updated", {"thread_id": thread["thread_id"]})
        policy = self.authorization_policy
        owner = thread.get("user_id")
        for ch in list(self._connected_channels):
            if not policy.can_access(getattr(ch, "principal", None), owner):
                continue
            try:
                await ch.async_call("thread_updated", payload)
            except Exception:
                logger.debug("thread_updated broadcast failed", exc_info=True)

    async def export_thread(
        self,
        thread_id: str,
        limit: int | None = None,
        cursor: str | None = None,
        include_descendants: bool | None = None,
    ) -> dict:  # noqa: D401
        """Export a conversation's canonical objects, page by page.

        Exports the domain objects rather than raw history so approvals,
        elicitations and artifacts — which never reach the history table —
        are in the export. Iterate ``next_cursor`` until it is null; there
        is no silent cap.
        """
        _debug_rpc(
            "export_thread",
            {
                "thread_id": thread_id,
                "limit": limit,
                "include_descendants": include_descendants,
            },
        )
        return self._conversation_service().export_thread(
            thread_id,
            can_access=self._descendant_access_predicate(),
            limit=limit,
            cursor=cursor,
            include_descendants=bool(include_descendants),
        )

    async def purge_threads(
        self, retention_days: int, dry_run: bool | None = None
    ) -> dict:  # noqa: D401
        """Apply a retention window: delete conversations older than N days.

        Deletes from EVERY store that holds the transcript — history, the
        canonical domain rows (and their search index) and the durable
        replay journal. Sessions under legal hold (``set_session_hold``) are
        never purged.

        ``dry_run`` reports what the same window would remove without
        removing it. Irreversible bulk deletion gets a preview.

        Authorization: **global-mutating**. The window is a deployment-wide
        policy, not a per-principal preference, and one principal running it
        would destroy other principals' conversations — so, following the
        ``write_config``/``disable_tool`` precedent, it is refused outright
        in enforced multi-user deployments and available in the single
        principal (desktop/local) topology it exists for.
        """
        _debug_rpc(
            "purge_threads", {"retention_days": retention_days, "dry_run": dry_run}
        )
        project_slug = getattr(self._session_manager, "_project_slug", None)
        agent_slug = getattr(self._session_manager, "_agent_slug", None)
        days = int(retention_days)
        if days <= 0:
            return {"purged_thread_ids": [], "count": 0, "dry_run": bool(dry_run)}
        if dry_run:
            from omniagents.core.session import history_db

            cutoff = time.time() - days * 86400.0
            # ``list_sessions`` keys the id as "id" (the wire shape), not
            # "session_id".
            candidates = [
                row["id"]
                for row in history_db.list_sessions(
                    project_slug=project_slug, agent_slug=agent_slug
                )
                if _session_row_epoch(row) < cutoff
                and not history_db.get_session_hold(
                    row["id"],
                    project_slug=project_slug,
                    agent_slug=agent_slug,
                )
            ]
            return {
                "purged_thread_ids": candidates,
                "count": len(candidates),
                "dry_run": True,
            }
        from omniagents.core.session.retention import purge_expired_sessions

        purged = purge_expired_sessions(
            days, project_slug=project_slug, agent_slug=agent_slug
        )
        for session_id in purged:
            self._session_manager.drop(session_id)
        return {
            "purged_thread_ids": purged,
            "count": len(purged),
            "dry_run": False,
        }

    async def get_plan(
        self, thread_id: str, scope: str | None = None
    ) -> dict:  # noqa: D401
        """The live structured plan for *thread_id*, plus its history.

        ``plan`` is the plan a client should render right now (``None`` when
        the conversation has never planned); ``plans`` is every plan the
        thread has held, so a finished run still exposes the plan it ran
        under. Absence is ``None`` rather than a typed error: "this
        conversation has no plan" is an ordinary state, not a fault.
        """
        _debug_rpc("get_plan", {"thread_id": thread_id, "scope": scope})
        return self._conversation_service().get_plan(thread_id, scope=scope)

    async def get_run_diff(
        self, thread_id: str, turn_id: str | None = None
    ) -> dict:  # noqa: D401
        """The aggregated unified diff for one turn (default: the latest).

        Raises ``-32100`` when ``turn_id`` names a turn this thread does not
        have — an unknown address is a client bug worth surfacing, unlike a
        known turn that simply changed no files (``run_diff: None``).
        """
        _debug_rpc("get_run_diff", {"thread_id": thread_id, "turn_id": turn_id})
        return self._conversation_service().get_run_diff(thread_id, turn_id=turn_id)

    async def notify_item_updated(self, session: Session, item: Any) -> None:
        """Push ``item_updated`` for a mutated conversation item.

        The canonical-objects PR shipped ``item_updated``-less on purpose:
        every item kind it recorded either never mutated after creation or
        mutated exactly once, at a moment the client already saw
        (``tool_result``, ``*_resolved``). Plans and run diffs break that —
        both revise many times inside a run with no other event marking the
        change — so without a push a client either polls or renders stale
        state.

        Deliberately kind-agnostic: it carries the item's identity, lifecycle
        and content, so approvals or elicitations can start pushing through
        it later without a second notification or a protocol break.

        ``item_seq`` (not ``seq``) is the item's thread ordinal: ``seq`` on a
        notification is already the replay envelope's per-session sequence
        id, and two different numbers must not share a field name.
        """
        payload = {
            "session_id": session.id,
            "thread_id": item.thread_id,
            "item_id": item.item_id,
            "turn_id": item.turn_id,
            "kind": item.kind.value,
            "status": item.status.value,
            "revision": item.revision,
            "item_seq": item.seq,
            "updated_at": item.updated_at,
            "content": dict(item.content),
        }
        try:
            await self._broadcast_session(session, "item_updated", payload)
        except Exception:
            logger.exception(
                "item_updated broadcast failed",
                extra={"session": session.id, "item": item.item_id},
            )

    async def fork_session(
        self,
        session_id: str,
        *,
        new_session_id: str | None = None,
        from_item_id: str | None = None,
        from_turn_id: str | None = None,
    ) -> dict:  # noqa: D401
        """Branch a conversation, optionally from a point inside it.

        With neither ``from_item_id`` nor ``from_turn_id`` this is the
        historical whole-transcript fork. With either, the child contains
        exactly the parent's items **up to and including** the branch point
        — and its raw history is truncated to the same turn, so the model
        the child resumes with sees the same conversation the transcript
        shows. A branch whose context runs past its visible transcript is
        not a branch.

        ``from_turn_id`` resolves to that turn's last item and refuses a
        turn that is still running.
        """
        _debug_rpc(
            "fork_session",
            {
                "session_id": session_id,
                "new_session_id": new_session_id,
                "from_item_id": from_item_id,
                "from_turn_id": from_turn_id,
            },
        )
        sess: Session = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        service = self._conversation_service()
        branch_item = None
        history: list[dict] | None = None
        if from_item_id or from_turn_id:
            # Materialize a legacy thread first: without domain rows there
            # are no item/turn ids to name a branch point with, and
            # answering "unknown item" for a session that plainly has one
            # would be a lie about the data rather than about the request.
            service.ensure_thread(session_id)
            branch_item = service.resolve_branch_point(
                session_id, from_item_id=from_item_id, from_turn_id=from_turn_id
            )
            if branch_item is not None:
                from omniagents.core.conversation.adapter import (
                    truncate_history_at_user_row,
                )

                # The child's raw history is the model's context, so it is
                # cut at the SAME point as its transcript. Counted in USER
                # ROWS — one per user_message item, mid-run ones included —
                # never in turns: send_user_message appends a user row
                # inside a running turn, so a turn count and a user-row cut
                # land in different places.
                user_rows, include_next = service.history_cut_for(
                    session_id, branch_item
                )
                history = truncate_history_at_user_row(
                    sess.history,
                    user_rows,
                    include_next_row=include_next,
                )
        new_sess = self._session_manager.fork_session(
            sess, new_session_id=new_session_id, history=history
        )
        # Materialize the child's domain rows HERE rather than relying on
        # the notification below. ``_broadcast_session`` is best-effort, so
        # the recorder hook is a reachable no-op — and a truncated fork that
        # silently fell back to the whole-history projection would produce a
        # child holding conversation the user branched away from.
        # ``record_fork`` is idempotent, so the notification path re-running
        # it is a no-op.
        self._record_conversation(
            sess,
            lambda rec: rec.record_fork(
                sess.id,
                new_sess.id,
                up_to_seq=branch_item.seq if branch_item is not None else None,
            ),
        )
        payload: dict[str, Any] = {
            "session_id": sess.id,
            "new_session_id": new_sess.id,
        }
        if branch_item is not None:
            payload["from_item_id"] = branch_item.item_id
        try:
            await self._broadcast_session(
                sess, "session_forked", payload, track_live=True
            )
        except Exception:
            logger.exception(
                "session_forked broadcast failed", extra={"session": sess.id}
            )
        return {
            "session_id": sess.id,
            "new_session_id": new_sess.id,
            "branched_from_item_id": (
                branch_item.item_id if branch_item is not None else None
            ),
        }

    async def list_sessions(
        self, limit: int | None = None, offset: int = 0
    ) -> list[dict]:  # noqa: D401
        """Return session metadata, newest-first.

        ``limit``/``offset`` page the listing; omitting them returns
        everything (the historical behavior, kept for old clients).
        """
        # Ownership scoping is pushed into SQL so it composes with
        # LIMIT/OFFSET. Filtering a page in Python after the database
        # applied the limit silently under-delivers (a page of N can come
        # back with fewer, or none, of the caller's own sessions).
        rows = self._session_manager.list_sessions(
            limit=limit, offset=offset, user_id=self._listing_scope_user_id()
        )
        result: list[dict] = []
        for row in rows:
            # ``user_id`` is authorization metadata, not part of the wire shape.
            row.pop("user_id", None)
            result.append(row)
        _debug_rpc(
            "list_sessions",
            {"limit": limit, "offset": offset, "count": len(result)},
        )
        return result

    def _listing_scope_user_id(self) -> str | None:
        """The ``user_id`` self-scoped listings must filter on, or ``None``.

        ``None`` means "do not scope": either authorization is not enforced
        (single-user deployment) or the caller holds the ``admin`` role.
        """
        policy = self.authorization_policy
        if not policy.enforced:
            return None
        principal = self._current_principal()
        if principal is None:
            # Enforced mode with no resolved principal: scope to an identity
            # that owns nothing rather than returning everything.
            return _NO_PRINCIPAL_SCOPE
        if ADMIN_ROLE in (principal.roles or []):
            return None
        return principal.user_id

    # -- Python-facing session access (not RPC; returns Session objects) --
    # Tools and host apps previously reached into ``_session_manager._by_id``
    # for these; use this API instead.

    def get_session(self, session_id: str | None) -> Optional[Session]:
        """Look up a live session by id without creating one."""
        return self._session_manager.get(session_id)

    def get_or_create_session(self, session_id: str | None = None) -> Session:
        """Return the live session for ``session_id``, creating it if needed."""
        return self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )

    # ------------------------------------------------------------------
    #  Principal resolution + central RPC authorization
    # ------------------------------------------------------------------

    def _current_principal(self) -> "UserIdentity | None":
        """The authenticated identity of the channel driving this task."""
        channel = self.channel
        if channel is None:
            return None
        return getattr(channel, "principal", None)

    def _current_user_id(self) -> str | None:
        principal = self._current_principal()
        return principal.user_id if principal is not None else None

    @property
    def authorization_policy(self) -> AuthorizationPolicy:
        """The deployment's authorization policy.

        Installed by ``build_app`` from the connection authenticator. The
        default is permissive because an ``AgentService`` constructed
        without a server (embedded/CLI/eval use, in-memory transports)
        has no authentication and exactly one principal.
        """
        return self._authorization_policy

    @authorization_policy.setter
    def authorization_policy(self, policy: AuthorizationPolicy) -> None:
        self._authorization_policy = policy

    def _session_owner(self, session_id: str) -> "str | None | _OwnerUnknownType":
        """Owner of *session_id*, live or persisted.

        Returns ``None`` only when the session is *definitively* unowned.
        A lookup that cannot answer returns :data:`OWNER_UNKNOWN`, which
        denies — an ownership check must never fail open.
        """
        sess = self._session_manager.get(session_id)
        if sess is not None:
            if getattr(sess, "owner_lookup_failed", False):
                return OWNER_UNKNOWN
            return getattr(sess, "user_id", None)
        try:
            from omniagents.core.session.history_db import get_session_user_id

            return get_session_user_id(
                session_id,
                project_slug=getattr(self._session_manager, "_project_slug", None),
                agent_slug=getattr(self._session_manager, "_agent_slug", None),
            )
        except Exception:
            logger.exception(
                "session owner lookup failed; denying access",
                extra={"session": session_id},
            )
            return OWNER_UNKNOWN

    def _session_exists(self, session_id: str) -> bool:
        """Whether *session_id* is known, live or persisted. Fails closed.

        ``_session_owner`` answers ``None`` for two situations that must be
        authorized differently: a session that exists with no recorded owner
        (a legacy row — deny, and migrate with
        ``python -m omniagents.core.session.backfill``), and a session that
        does not exist at all, where the request IS the creation and the
        handler will stamp the caller as its owner. This distinguishes them.
        A lookup that cannot answer reports "exists", so the ownership check
        still runs.
        """
        if self._session_manager.get(session_id) is not None:
            return True
        try:
            from omniagents.core.session.history_db import session_exists

            return session_exists(
                session_id,
                project_slug=getattr(self._session_manager, "_project_slug", None),
                agent_slug=getattr(self._session_manager, "_agent_slug", None),
            )
        except Exception:
            logger.exception(
                "session existence lookup failed; treating as existing",
                extra={"session": session_id},
            )
            return True

    @staticmethod
    def _resource_owner(resource: Any) -> "str | None | _OwnerUnknownType":
        """Owner of a live session-like object, failing closed."""
        if resource is None:
            return OWNER_UNKNOWN
        if getattr(resource, "owner_lookup_failed", False):
            return OWNER_UNKNOWN
        return getattr(resource, "user_id", None)

    def _resolve_rpc_resource_owner(
        self, method: str, params: dict
    ) -> tuple["str | None | _OwnerUnknownType", str, str | None]:
        """Map an incoming RPC to ``(owner, resource, resource_id)``.

        Resolution is by resource-identifying parameter, so every current
        and future method that names a session/run/approval inherits the
        ownership check without per-handler code. A named resource that
        cannot be resolved yields :data:`OWNER_UNKNOWN` (deny), never
        ``None`` (unowned). ``resource == "method"`` means the request
        named no resource at all — see :meth:`authorize_rpc`.
        """
        params = params or {}
        session_id = params.get("session_id")
        if isinstance(session_id, str) and session_id:
            return self._session_owner(session_id), "session", session_id
        # A thread IS a session (``thread_id == session_id``), so the
        # conversation reads inherit session ownership rather than getting
        # a parallel authorization story. Resolved before ``run_id`` for the
        # same reason ``session_id`` is: it is the most specific handle.
        thread_id = params.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return self._session_owner(thread_id), "thread", thread_id
        run_id = params.get("run_id")
        if isinstance(run_id, str) and run_id:
            sess = self._session_manager.get_by_run_id(run_id)
            return self._resource_owner(sess), "run", run_id
        call_id = params.get("call_id")
        if isinstance(call_id, str) and call_id:
            entry = self._pending_tool_approvals.get(call_id)
            if entry is None:
                entry = getattr(self, "_resolved_approvals", {}).get(("tool", call_id))
            owner = self._resource_owner(entry[1] if entry is not None else None)
            return owner, "tool_approval", call_id
        request_id = params.get("request_id")
        if isinstance(request_id, str) and request_id:
            entry = self._pending_mcp_approvals.get(request_id)
            if entry is None:
                entry = getattr(self, "_resolved_approvals", {}).get(
                    ("mcp", request_id)
                )
            if entry is not None:
                return self._resource_owner(entry[1]), "mcp_approval", request_id
            sess = self._client_request_sessions.get(request_id)
            if sess is not None:
                return self._resource_owner(sess), "client_request", request_id
            receipt = getattr(self, "_resolved_client_requests", {}).get(request_id)
            if receipt is not None:
                return self._resource_owner(receipt[1]), "client_request", request_id
            return OWNER_UNKNOWN, "request", request_id
        elicitation_id = params.get("elicitation_id")
        if isinstance(elicitation_id, str) and elicitation_id:
            entry = self._pending_elicitations.get(elicitation_id)
            if entry is not None:
                return self._resource_owner(entry[1]), "elicitation", elicitation_id
            for sess in self._session_manager.iter_active():
                if elicitation_id in getattr(sess, "resolved_elicitations", {}):
                    return self._resource_owner(sess), "elicitation", elicitation_id
            return OWNER_UNKNOWN, "elicitation", elicitation_id
        return OWNER_UNKNOWN, "method", None

    async def prepare_rpc_context(
        self, principal: "UserIdentity | None", method: str, params: dict
    ):
        """Resolve one typed RPC or dynamic server function centrally."""

        # AgentHost registry administration is the control plane that creates
        # resources for later routed operations. In particular,
        # ``agent_host_register_workspace`` necessarily names a Workspace that
        # is not registered yet, so resolving it through OperationRouter before
        # dispatch would reject the creation request as ``workspace_not_found``.
        # These methods are still centrally authorized as admin-only by
        # ``authorize_rpc``; they simply have no execution-environment context.
        if method in AGENT_HOST_ADMIN_METHODS:
            return None

        if method == "server_call":
            function = params.get("function")
            entry = self._server_functions.get(function)
            if entry is None:
                raise ValueError(f"Unknown server function: {function}")
            descriptor = OperationExecutionDescriptor(
                name=str(function),
                resource_scope=entry["resource_scope"],
                execution_requirements=entry["execution_requirements"],
            )
            return await self.operation_router.prepare(
                descriptor, params, principal=principal
            )
        operation = OPERATION_BY_NAME.get(method)
        if operation is None:
            raise ValueError(f"Unclassified RPC operation: {method}")
        return await self.operation_router.prepare_protocol(
            operation, params, principal=principal
        )

    async def authorize_rpc(
        self, principal: "UserIdentity | None", method: str, params: dict
    ) -> None:
        """Authorize one request before it is dispatched.

        Invoked by :class:`~omniagents.rpc.server.JsonRpcEndpoint` for every
        request. Two rules, both default-deny in enforced (multi-user) mode:

        * A request naming a resource (``session_id``/``run_id``/``call_id``/
          ``request_id``) must be made by that resource's owner.
        * A request naming *no* resource must be explicitly classified as
          globally safe (:data:`~omniagents.rpc.auth.GLOBAL_SAFE_METHODS`).
          Unclassified and global-mutating methods (``disable_tool``,
          ``disable_mcp_server`` — they mutate the spec process-wide for
          every connected user) are refused.

        Raises :class:`~omniagents.rpc.auth.AuthorizationDenied`, surfaced
        to the client as JSON-RPC error ``-32014``.
        """
        policy = self.authorization_policy
        if method in AGENT_HOST_ADMIN_METHODS:
            if not policy.enforced:
                return
            if principal is not None and ADMIN_ROLE in (principal.roles or []):
                return
            raise AuthorizationDenied(
                f"{method} requires AgentHost administrator authority",
                resource="agent_host",
                resource_id=self.agent_host_resources.agent_host_id,
            )
        if method == "agent_host_bind_thread":
            binding = (params or {}).get("binding")
            workspace_id = (
                binding.get("workspace_id") if isinstance(binding, dict) else None
            )
            if isinstance(workspace_id, str) and workspace_id:
                workspace = self.workspace_registry.get(workspace_id)
                thread_id = (params or {}).get("thread_id")
                thread_owner = (
                    self._session_owner(thread_id)
                    if isinstance(thread_id, str) and thread_id
                    else OWNER_UNKNOWN
                )
                thread_exists = False
                if isinstance(thread_id, str) and thread_id:
                    thread_exists = self._session_manager.get(thread_id) is not None
                    if not thread_exists:
                        try:
                            from omniagents.core.session.history_db import (
                                session_exists,
                            )

                            thread_exists = session_exists(
                                thread_id,
                                project_slug=getattr(
                                    self._session_manager, "_project_slug", None
                                ),
                                agent_slug=getattr(
                                    self._session_manager, "_agent_slug", None
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "thread existence lookup failed; denying rebind",
                                extra={"thread": thread_id},
                            )
                            thread_exists = True
                            thread_owner = OWNER_UNKNOWN
                if thread_exists and not policy.can_access(principal, thread_owner):
                    raise AuthorizationDenied(
                        "Not authorized to rebind this Thread",
                        resource="thread",
                        resource_id=thread_id,
                    )
                if policy.can_access(principal, workspace.owner_user_id):
                    return
                raise AuthorizationDenied(
                    "Not authorized to bind this Workspace",
                    resource="workspace",
                    resource_id=workspace_id,
                )
        request_context = current_request_context()
        if request_context is not None and request_context.workspace is not None:
            owner = request_context.workspace.owner_user_id
            if request_context.environment is not None:
                resource = "environment"
                resource_id = request_context.environment.environment_id
            else:
                resource = "workspace"
                resource_id = request_context.workspace.workspace_id
        else:
            environment_id = (params or {}).get("environment_id")
            if isinstance(environment_id, str) and environment_id:
                try:
                    descriptor = await self.environment_manager.describe(environment_id)
                    workspace = self.workspace_registry.get(descriptor.workspace_id)
                    owner = workspace.owner_user_id
                except Exception:
                    logger.exception(
                        "environment owner lookup failed; denying access",
                        extra={"environment": environment_id},
                    )
                    owner = OWNER_UNKNOWN
                resource = "environment"
                resource_id = environment_id
            else:
                owner, resource, resource_id = self._resolve_rpc_resource_owner(
                    method, params
                )

        if resource == "method":
            if policy.can_call_resourceless(method):
                return
            if method in GLOBAL_MUTATING_METHODS:
                if principal is not None and ADMIN_ROLE in (principal.roles or []):
                    return
                raise AuthorizationDenied(
                    f"{method} changes state for every user and is not "
                    "available to individual principals",
                    resource="global",
                    resource_id=method,
                )
            raise AuthorizationDenied(
                f"{method} is not authorized without an owned resource",
                resource="global",
                resource_id=method,
            )

        if policy.can_access(principal, owner):
            return

        # Creating a session is not accessing someone else's. ``server_call
        # {session.ensure}`` names the session it is about to create — a
        # resident's day session (``resident-<id>-<date>``) is a new id every
        # day — and the handler stamps the caller as its owner. Without this
        # the create path could never run under enforced authorization: the
        # owner lookup answers "unowned", unowned is never consent, and the
        # very first call of the day is refused.
        #
        # Narrow on purpose. Every other session-naming method keeps failing
        # closed on an id it cannot find, ``resume_session`` above all: its
        # ``get_or_create`` would re-materialize a deleted session on every
        # reconnect. An unowned session that DOES exist stays denied either
        # way — that is the legacy row the backfill is for.
        if (
            owner is None
            and resource in {"session", "thread"}
            and isinstance(resource_id, str)
            and resource_id
            and method == "server_call"
            and (params or {}).get("function") in SESSION_CREATING_SERVER_FUNCTIONS
            and not self._session_exists(resource_id)
        ):
            return

        raise AuthorizationDenied(
            f"Not authorized to access this {resource}",
            resource=resource,
            resource_id=resource_id,
        )

    def active_sessions(self) -> list[Session]:
        """Snapshot of all live sessions in this process."""
        return self._session_manager.iter_active()

    def active_run_ids(self) -> list[str]:
        """Run ids of all currently tracked runs (cancel via stop_run)."""
        return self._session_manager.active_run_ids()

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

    async def agent_host_register_workspace(
        self,
        workspace_id: str,
        materialization_path: str,
        snapshot_ref: str | None = None,
        sources: list[dict] | None = None,
        owner_user_id: str | None = None,
    ) -> dict:
        effective_owner_user_id = (
            self._current_user_id() if owner_user_id is None else owner_user_id
        )
        resource = self.agent_host_resources.provisioning.register_workspace(
            workspace_id=workspace_id,
            owner_user_id=effective_owner_user_id,
            materialization_path=materialization_path,
            snapshot_ref=snapshot_ref,
            sources=sources or (),
        )
        return self._workspace_payload(resource)

    async def agent_host_register_profile(
        self,
        profile_id: str,
        definition: dict,
        owner_user_id: str | None = None,
    ) -> dict:
        effective_owner_user_id = (
            self._current_user_id() if owner_user_id is None else owner_user_id
        )
        registered = self.agent_host_resources.provisioning.register_profile(
            profile_id,
            definition,
            owner_user_id=effective_owner_user_id,
        )
        return {"profile_id": profile_id, "definition": registered}

    async def agent_host_bind_thread(self, thread_id: str, binding: dict) -> dict:
        workspace_id = binding.get("workspace_id")
        raw_selection = binding.get("environment_selection")
        if not isinstance(raw_selection, dict):
            raise TypeError("binding.environment_selection must be an object")
        mode = raw_selection.get("mode")
        if mode == "none":
            selection = NoEnvironment()
        elif mode == "automatic":
            selection = AutomaticEnvironment(raw_selection.get("profile_id"))
        elif mode == "existing":
            selection = ExistingEnvironment(
                raw_selection.get("environment_id"),
                raw_selection.get("environment_generation"),
            )
        else:
            raise TypeError(f"unsupported environment selection mode: {mode!r}")

        runtime_binding = ThreadRuntimeBinding(
            workspace_id=workspace_id,
            environment_selection=selection,
        )
        if workspace_id is not None:
            workspace = self.workspace_registry.get(workspace_id)
            if not self.authorization_policy.can_access(
                self._current_principal(), workspace.owner_user_id
            ):
                raise AuthorizationDenied(
                    "Not authorized to bind this Workspace",
                    resource="workspace",
                    resource_id=workspace_id,
                )
            if isinstance(selection, AutomaticEnvironment):
                self.agent_host_resources.provisioning.profile(
                    selection.profile_id,
                    owner_user_id=workspace.owner_user_id,
                )
        bound = await self.thread_bindings.bind(thread_id, runtime_binding)

        # A Thread is the conversation Session (thread_id == session_id).
        # Binding is the control-plane operation that makes a new conversation
        # routable, so it also establishes the Session's owner before the GUI
        # performs its first history read.  The SessionManager never rewrites
        # an existing owner's identity, which keeps rebinds safe.
        owner_user_id = (
            workspace.owner_user_id
            if workspace_id is not None
            else self._current_user_id()
        )
        self._session_manager.get_or_create(thread_id, user_id=owner_user_id)
        return self._thread_binding_payload(thread_id, bound)

    async def agent_host_list_resources(self) -> dict:
        principal = self._current_principal()
        owner_user_id = self._current_user_id()
        workspaces = [
            workspace
            for workspace in self.workspace_registry.list()
            if self.authorization_policy.can_access(principal, workspace.owner_user_id)
        ]
        workspace_ids = {workspace.workspace_id for workspace in workspaces}
        environments = [
            descriptor
            for descriptor in await self.environment_manager.list()
            if descriptor.workspace_id in workspace_ids
        ]
        return {
            "agent_host_id": self.agent_host_resources.agent_host_id,
            "workspaces": [self._workspace_payload(item) for item in workspaces],
            "profiles": self.agent_host_resources.provisioning.list_profiles(
                owner_user_id=owner_user_id
            ),
            # Owner-attributed view spanning every owner the caller may
            # access, so an admin/control principal can authoritatively
            # inspect token-user profile definitions. The legacy "profiles"
            # map above stays caller-scoped for existing consumers.
            "profiles_by_owner": [
                {
                    "owner_user_id": owner,
                    "profile_id": profile_id,
                    "definition": definition,
                }
                for owner, profile_id, definition in (
                    self.agent_host_resources.provisioning.profiles_by_owner()
                )
                if self.authorization_policy.can_access(principal, owner)
            ],
            "environments": [self._environment_payload(item) for item in environments],
        }

    async def agent_host_stop_environment(self, environment_id: str) -> dict:
        descriptor = await self.environment_manager.stop(environment_id)
        return self._environment_payload(descriptor)

    async def agent_host_materialize_environment(
        self, workspace_id: str, profile_id: str
    ) -> dict:
        workspace = self.workspace_registry.get(workspace_id)
        self.agent_host_resources.provisioning.profile(
            profile_id,
            owner_user_id=workspace.owner_user_id,
        )
        descriptor = await self.environment_manager.resolve_automatic(
            workspace_id, profile_id
        )
        lease = await self.environment_manager.acquire(descriptor.environment_id)
        payload = self._environment_payload(descriptor)
        controller = lease.lifecycle_controller
        payload.update(
            {
                "backend": getattr(controller, "backend", None),
                "container_id": getattr(controller, "container_id", None),
                "services": dict(getattr(controller, "service_urls", {}) or {}),
                "workspace_root": lease.workspace.root,
                "default_cwd": lease.terminal_defaults.cwd,
                "mounts": self._workspace_mounts(workspace, lease.workspace.root),
            }
        )
        return payload

    @staticmethod
    def _workspace_mounts(workspace, workspace_root: str) -> list:
        """Authoritative mount table for one materialized environment.

        Reports where every registered source landed relative to
        ``workspace_root``: ``"."`` when the root IS the source (the
        single-local-source host layout keeps the folder as its own
        root), ``"<mountName>"`` everywhere else (container manifests,
        mounted-host symlinks, and composite host layouts all key the
        source under the root by mount name). This is materialization
        reporting its own decision — clients must never re-derive the
        layout from ``default_cwd`` or path heuristics.
        """
        normalized_root = os.path.normpath(str(workspace_root))
        mounts = []
        for source in workspace.sources:
            name = source.get("mountName")
            if not isinstance(name, str) or not name:
                continue
            source_path = source.get("path")
            direct = (
                isinstance(source_path, str)
                and bool(source_path)
                and os.path.normpath(source_path) == normalized_root
            )
            mounts.append(
                {
                    "name": name,
                    "path": "." if direct else name,
                    "writable": source.get("writable") is not False,
                    "kind": source.get("kind"),
                }
            )
        return mounts

    @staticmethod
    def _workspace_payload(workspace) -> dict:
        return {
            "workspace_id": workspace.workspace_id,
            "owner_user_id": workspace.owner_user_id,
            "snapshot_ref": workspace.snapshot_ref,
            "sources": [dict(source) for source in workspace.sources],
        }

    @staticmethod
    def _environment_payload(environment) -> dict:
        return {
            "environment_id": environment.environment_id,
            "workspace_id": environment.workspace_id,
            "kind": environment.kind.value,
            "state": environment.state.value,
            "generation": environment.generation,
            "capabilities": sorted(
                capability.value for capability in environment.capabilities
            ),
        }

    @staticmethod
    def _thread_binding_payload(thread_id: str, binding: ThreadRuntimeBinding) -> dict:
        selection = binding.environment_selection
        if isinstance(selection, NoEnvironment):
            serialized_selection = {"mode": "none"}
        elif isinstance(selection, AutomaticEnvironment):
            serialized_selection = {
                "mode": "automatic",
                "profile_id": selection.profile_id,
            }
        else:
            serialized_selection = {
                "mode": "existing",
                "environment_id": selection.environment_id,
            }
            if selection.environment_generation is not None:
                serialized_selection["environment_generation"] = (
                    selection.environment_generation
                )
        return {
            "thread_id": thread_id,
            "workspace_id": binding.workspace_id,
            "environment_selection": serialized_selection,
        }

    # ------------------------------------------------------------------
    #  Model & capability catalog.
    #
    #  Typed, provider-neutral catalog reads plus session model/reasoning
    #  application — the promoted model-picker surface (the builtin
    #  /models /model /reasoning server functions share the same logic
    #  via omniagents.core.models.catalog_api). See protocol.md
    #  § "Model & Capability Catalog".
    # ------------------------------------------------------------------

    @staticmethod
    def _model_catalog_store():
        from omniagents.core.models import model_store

        return model_store()

    @staticmethod
    def _no_model_store_reasons() -> list[dict]:
        from omniagents.core.models import model_setup_hint

        return [
            {
                "code": "no_model_store",
                "message": "No model registry is configured for this product.",
                "action": model_setup_hint(),
            }
        ]

    async def list_models(
        self,
        include_hidden: bool = False,
        modality: str | None = None,
        session_id: str | None = None,
    ) -> dict:  # noqa: D401
        """The full provider-neutral model catalog, deterministically ordered.

        With ``session_id``, the result also carries that session's active
        selection so pickers can render the current choice atomically.
        """
        from omniagents.core.models.catalog_api import build_model_catalog

        store = self._model_catalog_store()
        if store is None:
            return {
                "models": [],
                "default_model": None,
                "voice_default_model": None,
                "errors": [],
                "reasons": self._no_model_store_reasons(),
            }
        result = build_model_catalog(
            store, include_hidden=include_hidden, modality=modality
        )
        result["reasons"] = []
        if store.single_provider:
            result["models"] = [
                m
                for m in result["models"]
                if m["provider"]["name"] == store.selected_provider()
            ]
        if session_id:
            sess = self._session_manager.get_or_create(
                session_id, user_id=self._current_user_id()
            )
            if sess.current_task is None or sess.current_task.done():
                store.reconcile_session_model(sess)
            active = getattr(sess, "active_model", None)
            result["session"] = {
                "session_id": sess.id,
                "active_model": (store.normalize_model_ref(active) if active else None),
                "reasoning_effort": getattr(sess, "reasoning_effort", None),
                "approvals_reviewer": getattr(sess, "approvals_reviewer", None),
                "workflow_reviewer": getattr(sess, "workflow_reviewer", None),
            }
        return result

    async def get_model(self, model: str) -> dict:  # noqa: D401
        """One catalog model's descriptor, or typed not-found reasons."""
        from omniagents.core.models.catalog_api import describe_model

        store = self._model_catalog_store()
        if store is None:
            return {
                "found": False,
                "model": None,
                "reasons": self._no_model_store_reasons(),
            }
        return describe_model(store, model)

    async def list_providers(self) -> dict:  # noqa: D401
        """Per-provider capability + health reads, failures isolated."""
        from omniagents.core.models.catalog_api import build_provider_overview

        store = self._model_catalog_store()
        if store is None:
            return {
                "providers": [],
                "errors": [],
                "reasons": self._no_model_store_reasons(),
            }
        result = build_provider_overview(store)
        result["reasons"] = []
        return result

    async def set_session_model(self, session_id: str, model: str) -> dict:
        """Switch a session to a catalog model (typed ``/model`` surface).

        On success the session's next runs (and their usage events) carry
        the new catalog identity; connected clients receive the updated
        ``ui.set_session_state``. Refusals carry typed, actionable
        reasons (``unknown_model``, ``realtime_only``).
        """
        from omniagents.core.models.catalog_api import apply_session_model
        from omniagents.core.server_functions.helpers import notify_session_state

        store = self._model_catalog_store()
        if store is None:
            return {"ok": False, "reasons": self._no_model_store_reasons()}
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        result = apply_session_model(store, sess, model)
        result["session_id"] = sess.id
        if result.get("ok"):
            await notify_session_state(self, sess)
        return result

    async def set_session_approvals(self, session_id: str, reviewer: str) -> dict:
        """Set a session's approval reviewer ("Approve for me" toggle).

        ``reviewer`` is ``"user"`` (prompt the human) or ``"auto"`` (route
        approvals to the guardian reviewer). The setting is per conversation
        and overrides the layered-config ``approvals.reviewer`` default.
        """
        from omniagents.core.agents.approval_policy import REVIEWERS

        if reviewer not in REVIEWERS:
            return {
                "ok": False,
                "session_id": session_id,
                "reason": f"unknown reviewer: {reviewer!r}",
            }
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        sess.approvals_reviewer = reviewer
        from omniagents.core.server_functions.helpers import notify_session_state

        await notify_session_state(self, sess)
        return {"ok": True, "session_id": sess.id, "reviewer": reviewer}

    async def set_session_workflow(self, session_id: str, reviewer: str) -> dict:
        """Set a session's workflow completion reviewer.

        ``reviewer`` is ``"guardian"`` (route criteria-bearing plan-step
        completions to the one-shot completion reviewer) or ``"off"``
        (waive all reviews). The setting is per conversation and
        overrides the layered-config ``workflow.completion_reviewer``
        default.
        """
        from omniagents.core.agents.workflow_policy import WORKFLOW_REVIEWERS

        if reviewer not in WORKFLOW_REVIEWERS:
            return {
                "ok": False,
                "session_id": session_id,
                "reason": f"unknown reviewer: {reviewer!r}",
            }
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        sess.workflow_reviewer = reviewer
        from omniagents.core.server_functions.helpers import notify_session_state

        await notify_session_state(self, sess)
        return {"ok": True, "session_id": sess.id, "reviewer": reviewer}

    async def set_session_reasoning(self, session_id: str, effort: str) -> dict:
        """Set a session's reasoning effort (typed ``/reasoning`` surface)."""
        from omniagents.core.models.catalog_api import apply_session_reasoning
        from omniagents.core.server_functions.helpers import notify_session_state

        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        result = apply_session_reasoning(sess, effort)
        result["session_id"] = sess.id
        if result.get("ok"):
            await notify_session_state(self, sess)
        return result

    async def set_voice_model(self, model: str) -> dict:  # noqa: D401
        """Set the process-wide realtime voice default to a catalog model.

        Global-mutating: refused per-principal in multi-user deployments
        (see GLOBAL_MUTATING_METHODS). Reconnect voice mode to apply.
        """
        from omniagents.core.models.catalog_api import set_voice_default_model

        store = self._model_catalog_store()
        if store is None:
            return {"ok": False, "reasons": self._no_model_store_reasons()}
        return set_voice_default_model(store, model)

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
        variables: dict | None = None,
        safe_tool_overrides: dict | None = None,
        source: str | None = None,
        input_content: Any | None = None,
        submission_id: str | None = None,
    ) -> dict:  # noqa: D401
        """Queue a message for serialized processing on a session.

        Every accepted message starts a turn when the recipient is available.
        Transcript recording is a separate operation, not a delivery option.

        Items are processed in FIFO order. The drainer waits for any
        in-flight run (queued or user-initiated) before starting a new one,
        so two runs never overlap on the same session.
        """
        _debug_rpc(
            "enqueue_message",
            {
                "session_id": session_id,
                "role": role,
                "source": source,
            },
        )
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        # Drain pending background notifications first so they keep
        # chronological order in history relative to this real message.
        # The recursive enqueue_message call from _flush_notifications
        # uses source="notifications:batch" and is skipped here, so no
        # infinite loop.
        self._refresh_queue(sess)
        if source != "notifications:batch" and sess.pending_notifications:
            await self._flush_notifications(sess)
        item = QueuedItem(
            content=content,
            input_content=input_content,
            role=role,
            variables=variables,
            safe_tool_overrides=safe_tool_overrides,
            source=source,
        )
        from omniagents.core.session import queue_inbox

        result = queue_inbox.accept(
            self._queue_store(sess),
            sess.id,
            item.to_dict(),
            submission_id=submission_id,
            payload={
                "method": "enqueue_message",
                "session_id": session_id,
                "content": content,
                "role": role,
                "variables": variables,
                "safe_tool_overrides": safe_tool_overrides,
                "source": source,
                "input_content": input_content,
            },
            limit=sess.message_queue_max,
        )
        self._refresh_queue(sess)
        await self._broadcast_queue_changed(sess)
        self._ensure_drainer(sess)
        return result

    def _queue_store(self, sess):
        from omniagents.core.conversation.recorder import recorder_for_session

        recorder = recorder_for_session(sess)
        if recorder is None:
            raise RuntimeError(
                "Cannot accept queued work: canonical thread unavailable"
            )
        if recorder.store.get_thread(sess.id) is None:
            recorder.ensure_thread(
                sess.id, user_id=sess.user_id, archived=sess.archived
            )
        else:
            recorder.recover_pending_messages(sess.id)
        return recorder.store

    def _close_interrupted_run(self, sess, displaced: dict) -> None:
        """Land the turn a dead runtime left open, so the transcript is honest.

        The displaced reservation stopped heartbeating, so its run is over
        whether or not it finished. Closing the turn with ``end_reason="error"``
        sweeps every item still open under it: a tool call that never
        reported lands as failed with "outcome unknown", which is what the
        model is told on the next turn (``refresh_owned_history`` mirrors the
        same note into the SDK context). Nothing is replayed.
        """
        from omniagents.core.conversation.recorder import recorder_for_session
        from omniagents.core.conversation.model import TurnStatus

        run_id = displaced.get("run_id")
        recorder = recorder_for_session(sess)
        if recorder is None or not run_id:
            return
        try:
            turn = recorder.store.get_turn(sess.id, run_id)
            if turn is None or turn.status != TurnStatus.RUNNING:
                return
            recorder.close_turn(
                sess.id,
                run_id,
                end_reason="error",
                error={
                    "message": "The runtime executing this run stopped before it finished."
                },
                usage=None,
                model=None,
                model_ref=None,
            )
        except Exception:
            logger.exception(
                "could not close interrupted run", extra={"session": sess.id}
            )

    @staticmethod
    def _recover_local_run_ownership(sess):
        """Retry only this process's proven-finished release, never foreign takeover."""
        ownership = getattr(sess, "runtime_ownership", None)
        task = getattr(sess, "current_task", None)
        if (
            ownership is not None
            and ownership.closing
            and not ownership.released
            and ownership.active_operations == 0
            and (task is None or task.done())
        ):
            ownership.release()

    def _refresh_queue(self, sess, *, reveal_claims=False):
        from omniagents.core.session import queue_inbox
        from omniagents.core.conversation.model import derive_item_id

        self._recover_local_run_ownership(sess)
        store = self._queue_store(sess)
        sess.archived = store.get_thread(sess.id).status.value == "archived"
        from omniagents.core.workers.recovery import reconcile_session

        reconcile_session(self, sess, store)
        sess.pending_notifications = queue_inbox.notifications(store, sess.id)
        if sess.pending_notifications and not sess.archived:
            sess.notification_signal.set()
            self._ensure_notification_flusher(sess)
        visible = []
        for entry in queue_inbox.entries(store, sess.id):
            item = dict(entry["item"])
            owner = entry["owner"]
            if owner is not None:
                receipt = store.submission_receipt(
                    sess.id, f"queue-dispatch:{item['id']}"
                )
                if receipt and receipt.get("status") == "completed":
                    run = store.get_turn(sess.id, receipt["result"].get("run_id"))
                    if run is not None and run.status.value != "running":
                        queue_inbox.finish(store, sess.id, item["id"], owner)
                        continue
                    if run is not None and run.status.value == "running":
                        from omniagents.core.runtime.ownership import lease_expired

                        if lease_expired(queue_inbox.runtime_owner(store, sess.id)):
                            self._close_interrupted_run(sess, {"run_id": run.turn_id})
                            reason = "Queued run was interrupted; inspect recorded effects before sending follow-up work"
                            queue_inbox.fail(store, sess.id, item["id"], owner, reason)
                            item.update(state="failed", error=reason)
                            owner = None
                elif receipt and receipt.get("status") == "failed":
                    queue_inbox.fail(
                        store,
                        sess.id,
                        item["id"],
                        owner,
                        receipt.get("error", "Run was rejected"),
                    )
                    item.update(state="failed", error=receipt.get("error"))
                    owner = None
            if owner is not None:
                # A canonical queued item proves the append committed. The
                # thread ensure above has already drained history intents.
                recorded = store.get_item(
                    sess.id, derive_item_id(sess.id, f"queue:{item['id']}")
                )
                if recorded is not None:
                    queue_inbox.finish(store, sess.id, item["id"], owner)
                    continue
            if owner is not None:
                if (
                    not reveal_claims
                    and owner == sess.queue_owner
                    and sess.drainer_task
                    and not sess.drainer_task.done()
                ):
                    continue
                # A claim whose dispatcher stopped refreshing lapses and the
                # item is simply pending again. A live foreign claim is a run
                # about to start; it is shown as pending too.
                queue_inbox.release_claim_if_stale(store, sess.id, item["id"])
            item.pop("trigger_run", None)  # Decode old persisted queue entries.
            visible.append(QueuedItem(**item))
        sess.message_queue = visible

    async def queue_status(
        self,
        session_id: str,
        include_snapshot: bool = False,
        submission_id: str | None = None,
    ) -> dict:  # noqa: D401
        """Return the queue/run state for a session."""
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        self._refresh_queue(sess)
        self._ensure_drainer(sess)
        result = {
            "session_id": sess.id,
            "depth": len(sess.message_queue),
            "draining": bool(sess.drainer_task and not sess.drainer_task.done()),
            "run_active": bool(sess.current_task and not sess.current_task.done()),
            "active_run_id": sess.active_run_id,
        }
        if include_snapshot:
            # The database pins item/inbox reads against other processes; no
            # await pins this serving runtime's state and event watermark.
            import copy
            from omniagents.core.conversation.recorder import ensure_conversation_thread
            from omniagents.core.conversation.runtime_snapshot import (
                reconcile_runtime_controls,
                snapshot_queue,
                snapshot_state_events,
            )

            ensure_conversation_thread(sess)

            stored = self._conversation_service().store.read_snapshot(sess.id)
            items = [item.to_dict() for item in stored["items"]]
            queue = snapshot_queue(stored["inbox"], sess)
            result["depth"] = len(queue)
            if self.channel is not None and self.channel not in sess.channels:
                sess.channels.append(self.channel)
            self._register_replay_consumers(sess)
            result["snapshot"] = copy.deepcopy(
                {
                    "items": reconcile_runtime_controls(items, sess, self),
                    "queue": queue,
                    "pending_requests": list(sess.pending_client_requests.values()),
                    "state_events": snapshot_state_events(sess),
                    "stream_id": sess.event_log.stream_id,
                    "last_seq": sess.event_log.last_seq,
                }
            )
        if submission_id is not None:
            receipt = self._conversation_service().store.submission_receipt(
                sess.id, submission_id
            )
            result["submission"] = (
                {key: value for key, value in receipt.items() if key != "owner"}
                if receipt
                else None
            )
        return result

    async def list_queue(self, session_id: str) -> dict:  # noqa: D401
        """Return the current queue contents for a session.

        Clients call this on connect / session-switch to seed local state,
        then subscribe to ``queue_changed`` notifications for updates.
        """
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        self._refresh_queue(sess)
        self._ensure_drainer(sess)
        return {
            "session_id": sess.id,
            "depth": len(sess.message_queue),
            "items": [item.to_dict() for item in sess.message_queue],
        }

    async def cancel_queued_message(
        self, session_id: str, item_id: str
    ) -> dict:  # noqa: D401
        """Remove a queued item by id.

        Absent (already dispatched or cancelled) items return ``not_found``.
        Cancelling a known actively-running turn requires ``stop_run``
        instead.
        """
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        from omniagents.core.session import queue_inbox

        result = queue_inbox.cancel(self._queue_store(sess), sess.id, item_id)
        self._refresh_queue(sess)
        await self._broadcast_queue_changed(sess)
        return result

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
        if sess.archived:
            return
        if sess.drainer_task and not sess.drainer_task.done():
            return
        if not any(item.state == "pending" for item in sess.message_queue):
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
            from omniagents.core.session import queue_inbox

            item = next(
                (item for item in sess.message_queue if item.state == "pending"), None
            )
            if item is None:
                return
            store = self._queue_store(sess)
            if not queue_inbox.claim(store, sess.id, item.id, sess.queue_owner):
                self._refresh_queue(sess)
                return
            sess.message_queue.remove(item)
            try:
                await self._broadcast_queue_changed(sess)
                dispatch_token = queue_inbox.current_dispatch.set(
                    (sess.id, item.id, sess.queue_owner)
                )
                try:
                    # Worker lifecycle admission wraps the same serialized queue.
                    dispatch = getattr(self, "_worker_queue_dispatch", self.start_run)
                    await dispatch(
                        prompt=item.content,
                        content=item.input_content,
                        environment_selection={"mode": "inherit"},
                        session_id=sess.id,
                        variables=item.variables,
                        safe_tool_overrides=item.safe_tool_overrides,
                        prompt_role=item.role,
                        submission_id=f"queue-dispatch:{item.id}",
                    )
                finally:
                    queue_inbox.current_dispatch.reset(dispatch_token)
                await _wait_for_current_task()
                queue_inbox.finish(store, sess.id, item.id, sess.queue_owner)
                self._refresh_queue(sess)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "drain failed",
                    extra={"session": sess.id, "source": item.source},
                )
                # Settle by receipt first (the run may have started and its
                # receipt says so). Whatever is still ours after that did not
                # start: mark it failed with the reason, visibly, and move on.
                self._refresh_queue(sess, reveal_claims=True)
                queue_inbox.fail(
                    store,
                    sess.id,
                    item.id,
                    sess.queue_owner,
                    f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                )
                self._refresh_queue(sess, reveal_claims=True)
                await self._broadcast_queue_changed(sess)
                # A durable rejection is known not to have started work;
                # preserve it visibly but let subsequent commands proceed.
                if any(
                    entry.id == item.id and entry.state == "failed"
                    for entry in sess.message_queue
                ):
                    continue
                return

    # ------------------------------------------------------------------
    #  Background notifications (batched, role="assistant").
    #
    #  enqueue_notification buffers per-session; _notification_flusher
    #  coalesces a burst into ONE QueuedItem that starts a run at:
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
        safe_tool_overrides: dict | None = None,
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
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        from omniagents.core.session import queue_inbox

        result = queue_inbox.buffer_notification(
            self._queue_store(sess),
            sess.id,
            {
                "content": content,
                "source": source,
                "safe_tool_overrides": safe_tool_overrides,
                "enqueued_at": time.time(),
            },
        )
        self._refresh_queue(sess)
        return result

    def _ensure_notification_flusher(self, sess: Session) -> None:
        if sess.notification_flusher_task and not sess.notification_flusher_task.done():
            return
        sess.notification_flusher_task = asyncio.create_task(
            self._notification_flusher(sess),
            name=f"notif-flush-{sess.id[:8]}",
        )

    async def _flush_notifications(self, sess: Session) -> None:
        """Drain ``pending_notifications`` into ONE combined QueuedItem.

        Always role="assistant", always starts a run. Safe to call
        with an empty buffer (no-op). The drainer routes the role
        through ``start_run(prompt_role=...)`` which appends to history
        with the real role and resumes the run without injecting a
        fresh user turn, so the notification reads as the agent
        observing its own background activity rather than as fake user
        input."""
        from omniagents.core.session import queue_inbox

        def make_item(items):
            return QueuedItem(
                content=self._format_notification_batch(items),
                role="assistant",
                source="notifications:batch",
                safe_tool_overrides=self._merge_notification_safe_tool_overrides(items),
            ).to_dict()

        promoted = queue_inbox.promote_notifications(
            self._queue_store(sess), sess.id, make_item, limit=sess.message_queue_max
        )
        self._refresh_queue(sess)
        if promoted:
            await self._broadcast_queue_changed(sess)
            self._ensure_drainer(sess)

    def _merge_notification_safe_tool_overrides(self, items: list[dict]) -> dict | None:
        names: list[Any] = []
        patterns: list[Any] = []
        mcp_tools: list[Any] = []
        for item in items:
            overrides = item.get("safe_tool_overrides")
            if not isinstance(overrides, dict):
                continue
            override_names = overrides.get("safe_tool_names")
            if isinstance(override_names, list):
                names.extend(override_names)
            override_patterns = overrides.get("safe_tool_patterns")
            if isinstance(override_patterns, list):
                patterns.extend(override_patterns)
            override_mcp_tools = overrides.get("safe_mcp_tools")
            if isinstance(override_mcp_tools, list):
                mcp_tools.extend(override_mcp_tools)
        merged = {}
        if names:
            merged["safe_tool_names"] = list(dict.fromkeys(names))
        if patterns:
            merged["safe_tool_patterns"] = list(dict.fromkeys(patterns))
        if mcp_tools:
            merged["safe_mcp_tools"] = mcp_tools
        return merged or None

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
        """Archive *session_id* so clients can hide it.

        Archiving **cascades to the fork subtree** — the same policy
        ``update_thread(status="archived")`` applies, routed through the
        same code, because two methods that set one flag with two different
        descendant semantics is how a client ends up with branches that
        reappear depending on which button was pressed.
        """
        _debug_rpc("archive_session", {"session_id": session_id})
        ok = self._session_manager.archive_session(session_id)
        if ok:
            cascaded = self._cascade_archive(session_id, True)
            for target in cascaded:
                self._mirror_archive_flag(target, True)
            # Archive is a metadata change like any other, and this is the
            # STABLE path shipped clients actually call — so it has to reach
            # the other columns' listings too. Broadcasting only from
            # update_thread would have left the acceptance criterion
            # ("multi-column navigation stays consistent after metadata
            # changes") satisfied on the experimental path and broken on the
            # one in use.
            try:
                thread = self._conversation_service().get_thread(session_id)
            except Exception:
                thread = None
            if thread is not None:
                await self._broadcast_thread_updated(
                    thread, ["status"], [t for t in cascaded if t != session_id]
                )
            sess = self.get_session(session_id)
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

                # (the session is going: the sets it retired go too)
                self._close_retired_session_mcp(sess)
                # Clean up session attributes
                setattr(sess, "_mcp_shutdown", None)
                setattr(sess, "_mcp_lifecycle_task", None)
                setattr(sess, "_mcp_servers_active", [])

        manager = getattr(self, "terminal_manager", None)
        if manager is not None:
            await manager.cleanup_session(session_id)
        return ok

    def _cascade_archive(self, session_id: str, archived: bool) -> list[str]:
        """Move a thread and its fork subtree to the archived/active status.

        Returns every id whose status moved, the named thread included, so
        the caller can mirror the legacy ``sessions.archived`` flag for all
        of them. Best-effort: an archive must not fail because the domain
        store is momentarily unavailable.
        """
        from omniagents.core.conversation.model import ThreadStatus

        sess = self.get_session(session_id)
        if sess is not None:
            # Goes through the recorder because it MATERIALIZES the thread:
            # archiving a session that has never been read as a domain
            # object still has to record the status, and the recorder is the
            # component that knows how to create the row from the session.
            self._record_conversation(
                sess, lambda rec: rec.record_archived(session_id, archived)
            )
        status = ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE
        try:
            outcome = self._conversation_service().set_status(
                session_id,
                status,
                # archive_session is the STABLE path shipped clients call,
                # so it needs the same access scoping update_thread got —
                # otherwise the cascade writes through to another
                # principal's Thread.status, their sessions.archived row
                # and their live Session.archived.
                can_access=self._descendant_access_predicate(),
            )
        except Exception:
            logger.exception(
                "thread archive status not cascaded", extra={"session": session_id}
            )
            return [session_id]
        if outcome.skipped:
            logger.info(
                "archive cascade skipped %s descendant thread(s) the caller "
                "cannot access",
                outcome.skipped,
                extra={"session": session_id},
            )
        return [session_id, *outcome.moved]

    async def delete_session(
        self, session_id: str, cascade: bool | None = None
    ) -> bool:  # noqa: D401
        """Permanently delete a session and all its data.

        This deletes, for every session removed:
        - The session record and all message history
        - All audio metadata
        - The canonical conversation rows and their search index
        - The durable replay journal
        - Any active MCP servers and terminal sessions

        **Descendant policy: REFUSE, unless ``cascade`` is set.** A fork
        records ``parent_thread_id`` pointing at this thread. Deleting the
        parent while its branches remain leaves every child advertising a
        branch point that no longer resolves — ``get_thread`` on the child
        reports an ancestor the server cannot produce, and an ancestry read
        on the missing parent is unanswerable. Orphaning is therefore not
        on the table: a protocol that documents ``parent_thread_id`` as a
        real relationship cannot also permit deletes that break it.

        Cascading is not the default either, because delete is
        irreversible and the branches are conversations the caller may not
        have in view. So the refusal is typed (``-32111``) and carries the
        blocking ids, and ``cascade=true`` is the one-parameter opt-in that
        removes the whole subtree, children first.

        (Archive takes the opposite default — it cascades — because it is
        reversible. See ``archive_session``.)

        Args:
            session_id: The session ID to delete
            cascade: Also delete every forked descendant of this session

        Returns:
            True if the named session was deleted, False if it didn't exist
        """
        _debug_rpc("delete_session", {"session_id": session_id, "cascade": cascade})

        try:
            # UNBOUNDED and owner-agnostic. A capped walk used as a delete
            # guard answers "these are all the children" when it is not, and
            # the caller then orphans the overflow; an owner-scoped one
            # cannot see a descendant owned by someone else, or by nobody on
            # a partially backfilled shard, and lets a plain delete orphan it
            # with no error at all.
            descendants = self._conversation_service().descendant_nodes(session_id)
        except Exception:
            logger.exception(
                "descendant lookup failed; refusing to delete",
                extra={"session": session_id},
            )
            # Fail closed: an unanswerable descendant lookup must not read
            # as "no descendants" and silently orphan a subtree.
            raise
        descendant_ids = [node.thread_id for node in descendants]
        can_access = self._descendant_access_predicate()
        # The guard COUNTS every descendant (integrity) but only NAMES the
        # ones the caller may read: the owner-agnostic walk must not become
        # a side channel for the ids of threads that
        # ``list_thread_descendants`` deliberately hides. The per-node test
        # is the one that read applies too, so the names in a -32111 are
        # the same branch tree the client already has — up to each
        # surface's own cap, which both report as ``truncated``.
        visible = [node.thread_id for node in descendants if can_access(node.user_id)]
        if descendant_ids and not cascade:
            raise _conversation_service_module.ThreadHasDescendants(
                session_id, len(descendant_ids), visible
            )

        if descendant_ids:
            # A cascade ACTS on the descendants, so now ownership matters:
            # the guard above is an integrity question, this is an access
            # one. Refusing outright (rather than skipping the foreign ones)
            # keeps the two possible outcomes honest — everything goes, or
            # nothing does. Archive takes the other branch and skips,
            # because it is reversible; see ConversationService.set_status.
            foreign = len(descendant_ids) - len(visible)
            if foreign:
                raise AuthorizationDenied(
                    f"Cannot cascade-delete {session_id}: "
                    f"{foreign} descendant thread(s) belong to another "
                    "principal",
                    resource="thread",
                    # The NAMED thread, never a foreign descendant's id.
                    resource_id=session_id,
                )

        # Legal hold is checked for the WHOLE subtree before anything is
        # deleted. The history backend raises on a held session, but a
        # cascade only reaches it after the branches are already gone — so
        # a held parent produced a failed call and permanently destroyed
        # data. A hold that takes effect after the deletion is not a hold.
        held = self._sessions_on_legal_hold([session_id, *descendant_ids])
        if held:
            raise _conversation_service_module.ThreadOnLegalHold(session_id, held)

        # Deepest first, so a parent is never removed before a child that
        # still points at it — an interrupted cascade then leaves a shorter
        # valid chain rather than a broken one.
        for child in descendant_ids:
            await self._delete_one_session(child)
        deleted = await self._delete_one_session(session_id)
        # Once for the whole operation, not once per row: the cost is
        # proportional to the index, and a 500-thread cascade must not pay
        # it 500 times.
        from omniagents.core.session.retention import compact_conversation_index

        compact_conversation_index(
            project_slug=getattr(self._session_manager, "_project_slug", None),
            agent_slug=getattr(self._session_manager, "_agent_slug", None),
        )
        return deleted

    def _sessions_on_legal_hold(self, session_ids: list[str]) -> list[str]:
        """Which of *session_ids* are under legal hold. Fails CLOSED.

        A hold lookup that cannot answer reports the session as held: the
        alternative is deleting data the deployment marked undeletable
        because a storage read hiccupped.
        """
        from omniagents.core.session import history_db

        project_slug = getattr(self._session_manager, "_project_slug", None)
        agent_slug = getattr(self._session_manager, "_agent_slug", None)
        held: list[str] = []
        for session_id in session_ids:
            try:
                on_hold = history_db.get_session_hold(
                    session_id, project_slug=project_slug, agent_slug=agent_slug
                )
            except Exception:
                logger.exception(
                    "legal hold lookup failed; treating the session as held",
                    extra={"session": session_id},
                )
                on_hold = True
            if on_hold:
                held.append(session_id)
        return held

    async def _delete_one_session(self, session_id: str) -> bool:
        """Delete exactly one session from every store that holds it."""
        # First, cleanup any active resources (similar to archive)
        sess = self.get_session(session_id)
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

        # Drop the canonical domain rows (and their search index) and the
        # durable replay journal alongside the history. All three hold the
        # full transcript — the most privacy-sensitive data this protocol
        # carries — so leaving any of them behind after an explicit delete
        # would be a retention defect, not an oversight.
        #
        # Routed through the retention module rather than reaching for
        # ``sess.event_log``: the journal was previously destroyed only when
        # the session happened to be LOADED in this process, so deleting a
        # session the user had not opened left its journalled notification
        # payloads — the same conversation, verbatim — on disk.
        from omniagents.core.session.retention import purge_session_derivatives

        purge_session_derivatives(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        )

        # Remove from in-memory session manager
        if sess is not None:
            self._session_manager.drop(session_id)

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
        # Keep the management registry in sync so ``mcp_list_servers``
        # reports the kill switch instead of silently dropping the entry.
        manager = getattr(self, "mcp_manager", None)
        if manager is not None:
            manager.mark_disabled(server_name)

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

    # ------------------------------------------------------------------
    #  Layered configuration (get_config / validate_config / write_config)
    # ------------------------------------------------------------------

    def _layered_config_store(self):
        """The layered-config store for this process's project manifest.

        Resolves the base project.yml from the spec (project mode), then
        from the installed product identity; a missing manifest leaves
        the project layer absent while user overlays still resolve.
        Built per call so reads always reflect the files on disk.
        """
        from pathlib import Path as _Path

        from omniagents.core.config.layered import LayeredConfigStore

        manifest = getattr(self.spec, "manifest_path", None)
        if not manifest:
            from omniagents.core.product import product_info

            manifest = product_info().manifest_path
        return LayeredConfigStore(manifest_path=_Path(manifest) if manifest else None)

    async def get_config(self) -> dict:  # noqa: D401
        """Return the effective layered configuration with provenance.

        Every registered field carries its effective value, the layer it
        came from, every layer that sets it, constraints, reload
        classification, and read-only reasons. Secret fields never
        include values — only ``is_set`` flags.
        """
        result = self._layered_config_store().describe()
        _debug_rpc("get_config", {"fields": len(result.get("fields", []))})
        return result

    async def validate_config(self, updates: dict) -> dict:  # noqa: D401
        """Dry-run validation of a batch of configuration updates.

        Returns structured field errors (unknown key, read-only,
        type/value/range violations) and the reload classification the
        batch would have, without writing anything.
        """
        _debug_rpc("validate_config", {"keys": sorted(updates)})
        return self._layered_config_store().validate(updates)

    async def write_config(self, updates: dict) -> dict:  # noqa: D401
        """Atomically apply configuration updates to the user layer.

        All-or-nothing: any validation error aborts the whole batch and
        nothing is written. A ``null`` value clears the key from the
        user layer (reverting to lower layers). Fields classified
        ``session`` are additionally applied to the live spec so the
        next agent build picks them up without a restart.
        """
        _debug_rpc("write_config", {"keys": sorted(updates)})
        store = self._layered_config_store()
        result = store.write(updates)
        if result.get("ok"):
            self._hot_apply_config(store, result)
        return result

    def _hot_apply_config(self, store, result: dict) -> None:
        """Apply session-reloadable written keys to the live spec.

        Mirrors the ``disable_tool`` precedent of mutating
        ``spec.security_config`` in place: ``security.safety_mode`` is
        consulted on every agent build, so updating the dict makes the
        write effective for the next run without a restart.
        """
        from omniagents.core.config.layered import (
            CONFIG_FIELDS_BY_KEY,
            RELOAD_SESSION,
        )

        session_keys = result.get("reload", {}).get(RELOAD_SESSION, [])
        for key in session_keys:
            field = CONFIG_FIELDS_BY_KEY[key]
            if field.secret or not key.startswith("security."):
                continue
            sec = getattr(self.spec, "security_config", None)
            if not isinstance(sec, dict):
                continue
            is_set, value = store.effective_value(key)
            subpath = key.removeprefix("security.")
            node = sec
            parts = subpath.split(".")
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            if is_set:
                node[parts[-1]] = value
            else:
                node.pop(parts[-1], None)

    # ------------------------------------------------------------------
    #  Provider accounts & usage (``account_*`` RPCs).
    #
    #  Thin wrappers over ``self.accounts`` (AccountManager): the manager
    #  owns flow state and typed errors; this layer owns broadcasting.
    #  Authorization: reads are GLOBAL_SAFE, mutations GLOBAL_MUTATING —
    #  see omniagents/rpc/auth.py. Snapshots and notifications are
    #  redacted by construction (no token/key material ever enters them).
    # ------------------------------------------------------------------

    def register_channel(self, channel: Any) -> None:
        """Track a connected channel for connection-wide broadcasts."""
        if channel is not None and channel not in self._connected_channels:
            self._connected_channels.append(channel)

    def unregister_channel(self, channel: Any) -> None:
        try:
            self._connected_channels.remove(channel)
        except ValueError:
            pass
        # Watches and transfers are per-connection: a dropped socket tears
        # them down (clients re-subscribe and rescan after reconnecting).
        self.filesystem.cleanup_channel(channel)
        self.git.cleanup_channel(channel)

    async def _broadcast_account_changed(
        self, provider: str, reason: str, account: Optional[dict] = None
    ) -> None:
        """Notify every connected client that account state moved.

        Not journaled: account state is server-global (not session
        history), and replaying stale credential states after reconnect
        would be wrong — clients refetch via ``account_status``.
        """
        payload: dict[str, Any] = {"provider": provider, "reason": reason}
        if account is None:
            try:
                account = self.accounts.provider_snapshot(provider)
            except Exception:
                account = None
        if account is not None:
            payload["account"] = account
        for ch in list(self._connected_channels):
            try:
                await ch.async_call("account_changed", payload)
            except Exception:
                logger.debug("account_changed broadcast failed", exc_info=True)

    async def account_status(self) -> dict:  # noqa: D401
        return self.accounts.status()

    async def account_usage(self, provider: str | None = None) -> dict:  # noqa: D401
        return self.accounts.usage(provider)

    async def account_login_start(
        self,
        provider: str,
        mode: str,
        api_key: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict:  # noqa: D401
        result = await self.accounts.login_start(
            provider, mode, api_key=api_key, redirect_uri=redirect_uri
        )
        if result.get("state") == "completed":
            await self._broadcast_account_changed(
                provider, "login", result.get("account")
            )
        return result

    async def account_login_complete(
        self, login_id: str, code: str | None = None
    ) -> dict:  # noqa: D401
        result = await self.accounts.login_complete(login_id, code=code)
        if result.get("state") == "completed":
            await self._broadcast_account_changed(
                str(result.get("provider")), "login", result.get("account")
            )
        return result

    async def account_login_cancel(self, login_id: str) -> bool:  # noqa: D401
        return self.accounts.login_cancel(login_id)

    async def account_logout(self, provider: str) -> dict:  # noqa: D401
        result = self.accounts.logout(provider)
        await self._broadcast_account_changed(provider, "logout", result.get("account"))
        return result

    async def account_refresh(self, provider: str) -> dict:  # noqa: D401
        result = await self.accounts.refresh(provider)
        await self._broadcast_account_changed(
            provider, "refresh", result.get("account")
        )
        return result

    async def account_select(self, provider: str) -> dict:  # noqa: D401
        result = self.accounts.select(provider)
        await self._broadcast_account_changed(provider, "select", result.get("account"))
        return result

    # ------------------------------------------------------------------
    #  MCP management, status & OAuth (``mcp_*`` RPCs).
    #
    #  Thin wrappers over ``self.mcp_manager`` (McpServerManager) — the
    #  promoted, single lifecycle contract for configured MCP servers.
    #  Reads are redacted (write-only header/env secrets), config
    #  mutations follow the ``disable_tool``/``write_config`` posture
    #  (process-wide → GLOBAL_MUTATING), and status transitions are
    #  broadcast (not journaled) via ``mcp_server_status_changed``.
    #  See omniagents/rpc/protocol.md § "MCP Server Management".
    # ------------------------------------------------------------------

    async def _broadcast_mcp_server_status(self, payload: dict) -> None:
        """Notify every connected client of a startup/auth transition.

        Not journaled: server status is process-global (not session
        history) — the ``account_changed`` decision. Clients refetch via
        ``mcp_list_servers`` after a reconnect; live sockets get pushes.
        """
        _debug_rpc("mcp_server_status_changed", payload)
        for ch in list(self._connected_channels):
            try:
                await ch.async_call("mcp_server_status_changed", payload)
            except Exception:
                logger.debug(
                    "mcp_server_status_changed broadcast failed", exc_info=True
                )

    async def mcp_list_servers(self, session_id: str | None = None) -> dict:
        """List configured MCP servers (redacted config + status + auth).

        With ``session_id``, that session's dynamic (app-scoped) servers
        are appended as ``source: "session"`` entries — and the request
        becomes resource-scoped to the session owner.
        """
        session_entries: list[dict] = []
        if session_id:
            sess = self.get_session(session_id)
            for dyn_cfg in list(getattr(sess, "_dynamic_mcp_servers", None) or []):
                if isinstance(dyn_cfg, dict):
                    session_entries.append(
                        self.mcp_manager.session_server_snapshot(dyn_cfg)
                    )
        result = self.mcp_manager.list_servers(session_servers=session_entries)
        _debug_rpc("mcp_list_servers", {"servers": len(result.get("servers", []))})
        return result

    async def mcp_get_server(
        self, server_name: str, refresh: bool | None = None
    ) -> dict:
        """Full snapshot of one server: config (redacted), status, auth,
        and — after a probe — server info, capabilities, tools,
        resources, resource templates, and prompts.

        ``refresh=True`` connects out-of-session now (a management
        probe); otherwise the cached discovery snapshot is returned.
        """
        _debug_rpc("mcp_get_server", {"server_name": server_name, "refresh": refresh})
        if refresh:
            return await self.mcp_manager.probe_server(server_name)
        return self.mcp_manager.get_server(server_name)

    async def mcp_create_server(
        self,
        server_name: str,
        type: str,
        params: dict,
        server_options: dict | None = None,
    ) -> dict:
        """Create a user-layer MCP server (validated; secrets write-only)."""
        _debug_rpc("mcp_create_server", {"server_name": server_name, "type": type})
        return self.mcp_manager.create_server(server_name, type, params, server_options)

    async def mcp_update_server(
        self,
        server_name: str,
        type: str | None = None,
        params: dict | None = None,
        server_options: dict | None = None,
    ) -> dict:
        """Update a user-layer MCP server.

        ``params.headers`` / ``params.env`` merge write-only: a string
        sets a value, ``null`` deletes it, omission keeps the stored
        (unreadable) value. Other params replace wholesale.
        """
        _debug_rpc("mcp_update_server", {"server_name": server_name})
        return self.mcp_manager.update_server(server_name, type, params, server_options)

    async def mcp_delete_server(self, server_name: str) -> dict:
        """Delete a user-layer MCP server (config, status, stored auth)."""
        _debug_rpc("mcp_delete_server", {"server_name": server_name})
        result = self.mcp_manager.delete_server(server_name)
        await self._shutdown_active_mcp_instances(server_name)
        return result

    async def mcp_reload_server(self, server_name: str | None = None) -> dict:
        """Re-read MCP config files and restart active instances.

        The pick-up-changes chokepoint: re-merges the user-layer files
        into the live spec (so hand-edits apply) and tears down active
        per-session instances of the named server (or all servers) —
        the next agent build reconnects from current configuration.
        """
        _debug_rpc("mcp_reload_server", {"server_name": server_name})
        if server_name is not None:
            self.mcp_manager.require_config(server_name)
        self.mcp_manager.apply_to_spec()
        reloaded = await self._shutdown_active_mcp_instances(server_name)
        names = (
            [server_name]
            if server_name is not None
            else [cfg.name for cfg in self.mcp_manager.effective_configs()]
        )
        for name in names:
            await self.mcp_manager.record_transition(
                name, "configured", origin="reload"
            )
        return {"ok": True, "reloaded_sessions": reloaded}

    async def _shutdown_active_mcp_instances(
        self, server_name: str | None = None
    ) -> int:
        """Tear down live per-session MCP instances so builds restart them.

        Sessions rebuild lazily: the next ``start_run`` /
        ``_ensure_session_mcp_servers`` call reconnects from the current
        spec. Returns the number of sessions torn down. The whole
        session lifecycle stack is shut down even for a single named
        server — instances share one AsyncExitStack per session.
        """
        count = 0
        for sess in self._session_manager.iter_active():
            active = list(getattr(sess, "_mcp_servers_active", []) or [])
            if not active:
                continue
            if server_name is not None and not any(
                getattr(server, "name", None) == server_name for server in active
            ):
                continue
            # (a set a run is using is retired, not closed under it: the run
            # finishes on it, and the next build connects afresh)
            lock = getattr(sess, "_mcp_start_lock", None)
            if lock is None:
                lock = sess._mcp_start_lock = asyncio.Lock()
            async with lock:
                lifecycle_task = getattr(sess, "_mcp_lifecycle_task", None)
                try:
                    await self._retire_session_mcp(sess)
                except Exception:
                    if lifecycle_task and not lifecycle_task.done():
                        lifecycle_task.cancel()
                        try:
                            await lifecycle_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    setattr(sess, "_mcp_shutdown", None)
                    setattr(sess, "_mcp_lifecycle_task", None)
                    setattr(sess, "_mcp_servers_active", [])
            count += 1
        return count

    def _require_single_user_for_session_auth(self) -> None:
        """Guard: ``mcp_auth_start(session_id=...)`` is single-user only.

        MCP OAuth rewrites process-wide credentials, so it follows the
        account posture (GLOBAL_MUTATING — refused per-principal in
        enforced mode). A ``session_id`` would let the central authorizer
        resolve the request as session-scoped instead; this explicit
        check keeps the credential-mutation rule airtight.
        """
        if self.authorization_policy.enforced:
            raise AuthorizationDenied(
                "mcp_auth_start changes credentials for every user and is "
                "not available to individual principals",
                resource="global",
                resource_id="mcp_auth_start",
            )

    async def mcp_auth_start(
        self,
        server_name: str,
        redirect_uri: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Begin an OAuth authorization for an HTTP-family MCP server.

        Returns at the URL-handoff point (``auth_url``), the #306
        account-flow pattern — no tokens or verifiers in the result.
        With ``session_id`` (single-user topology only), the URL is also
        surfaced to that session as a ``url``-kind elicitation via the
        #308 protocol; accepting it with ``{"code": ...}`` (or the full
        redirect URL as ``{"url": ...}``) completes the flow without a
        separate ``mcp_auth_complete`` call.
        """
        from omniagents.core.agents.mcp_admin import McpOperationUnsupported

        if session_id:
            self._require_single_user_for_session_auth()
        config = self.mcp_manager.require_config(server_name)
        if config.type == "stdio":
            raise McpOperationUnsupported(
                server_name,
                "oauth",
                message="stdio MCP servers authenticate via env, not OAuth",
            )
        url = (config.params or {}).get("url")
        if not isinstance(url, str) or not url:
            raise McpOperationUnsupported(server_name, "oauth")
        result = await self.mcp_manager.oauth.start(
            server_name, url, redirect_uri=redirect_uri
        )
        if result.get("state") == "completed":
            return result
        sess = self.get_session(session_id) if session_id else None
        if sess is not None:
            auth_id = str(result.get("auth_id"))
            asyncio.create_task(
                self._elicit_mcp_auth(sess, server_name, auth_id, result),
                name=f"mcp_auth_elicit_{auth_id[:8]}",
            )
        return result

    async def _elicit_mcp_auth(
        self, session: Session, server_name: str, auth_id: str, start_result: dict
    ) -> None:
        """Surface a pending OAuth flow as a ``url``-kind elicitation."""
        try:
            response = await self.request_elicitation(
                session=session,
                kind="url",
                message=(
                    f"Authorize the MCP server '{server_name}' in your "
                    "browser, then paste the code or redirect URL."
                ),
                title=f"Connect {server_name}",
                url=str(start_result.get("auth_url") or ""),
                source=f"mcp:{server_name}",
                persist_response=False,
            )
        except Exception:
            logger.debug("mcp auth elicitation failed", exc_info=True)
            return
        if response.get("action") != "accept":
            self.mcp_manager.oauth.cancel(auth_id)
            return
        value = response.get("value") or {}
        code = value.get("code") or value.get("url") or ""
        try:
            await self.mcp_manager.oauth.complete(auth_id, str(code))
        except Exception:
            logger.debug("mcp auth completion via elicitation failed", exc_info=True)

    async def mcp_auth_complete(self, auth_id: str, code: str) -> dict:
        """Finish a pending OAuth flow with the authorization code (or
        the full redirect URL). Tokens are stored server-side only."""
        _debug_rpc("mcp_auth_complete", {"auth_id": auth_id})
        return await self.mcp_manager.oauth.complete(auth_id, code)

    async def mcp_auth_cancel(self, auth_id: str) -> bool:
        """Drop a pending OAuth flow."""
        _debug_rpc("mcp_auth_cancel", {"auth_id": auth_id})
        return self.mcp_manager.oauth.cancel(auth_id)

    async def mcp_read_resource(
        self, server_name: str, uri: str, session_id: str | None = None
    ) -> dict:
        """Read one resource — the promoted ``mcp.read_resource``.

        With ``session_id`` the session's live server instance is used
        (resource-scoped); without one, a management probe connection
        serves the read.
        """
        _debug_rpc("mcp_read_resource", {"server_name": server_name, "uri": uri})
        if session_id:
            sess = self.get_session(session_id)
            return await self._sf_mcp_read_resource(
                sess, {"server_name": server_name, "uri": uri}
            )
        return await self._mcp_management_call(server_name, "read_resource", uri=uri)

    async def mcp_call_tool(
        self,
        server_name: str,
        tool_name: str,
        session_id: str,
        args: dict | None = None,
    ) -> dict:
        """Invoke a tool out-of-band — the promoted ``mcp.call_tool``.

        Session-scoped by contract: tool calls act on behalf of a
        session (MCP-Apps postMessage routing), so ``session_id`` is
        required and ownership of that session authorizes the call.
        """
        _debug_rpc(
            "mcp_call_tool", {"server_name": server_name, "tool_name": tool_name}
        )
        # Same session resolution as ``server_call``: create-if-missing
        # (ownership was already checked by ``authorize_rpc``) and track
        # the invoking channel for session notifications.
        sess = self._session_manager.get_or_create(
            session_id, user_id=self._current_user_id()
        )
        if self.channel is not None and self.channel not in sess.channels:
            sess.channels.append(self.channel)
        return await self._sf_mcp_call_tool(
            sess,
            {
                "server_name": server_name,
                "tool_name": tool_name,
                "arguments": args or {},
            },
        )

    async def mcp_get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        args: dict | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Fetch a prompt from a server that supports prompt discovery."""
        from omniagents.core.agents.mcp_admin import (
            McpOperationUnsupported,
            McpServerUnavailable,
        )

        _debug_rpc(
            "mcp_get_prompt",
            {"server_name": server_name, "prompt_name": prompt_name},
        )
        arguments = {str(k): str(v) for k, v in (args or {}).items()}
        if session_id:
            sess = self.get_session(session_id)
            server = await self._resolve_mcp_server_by_name(sess, server_name)
            if server is None:
                raise McpServerUnavailable(
                    server_name,
                    reason_code="not_active",
                    message="No active MCP server with this name in the session",
                )
            client_session = getattr(server, "session", None)
            if client_session is None or not hasattr(client_session, "get_prompt"):
                raise McpOperationUnsupported(server_name, "prompts")
            result = await client_session.get_prompt(
                prompt_name, arguments=arguments or None
            )
            from omniagents.core.agents.mcp_admin import _dump_model

            dumped = _dump_model(result)
            return {
                "server_name": server_name,
                "prompt_name": prompt_name,
                "result": dumped if isinstance(dumped, dict) else {},
            }
        return await self._mcp_management_call(
            server_name, "get_prompt", prompt_name=prompt_name, args=arguments
        )

    async def _mcp_management_call(
        self, server_name: str, operation: str, **kwargs: Any
    ) -> dict:
        """Serve a sessionless read via a short-lived probe connection."""
        import copy as _copy

        from omniagents.core.agents.mcp import (
            _instantiate_server,
            _resolve_setting_placeholders,
        )
        from omniagents.core.agents.mcp_admin import (
            McpOperationUnsupported,
            McpServerUnavailable,
            _dump_model,
            _dump_model_list,
        )

        config = self.mcp_manager.require_config(server_name)
        settings = dict(self._default_settings)
        resolved = _resolve_setting_placeholders(
            _copy.deepcopy(config.params), settings
        )
        if not isinstance(resolved, dict):
            resolved = {}
        auth = self.mcp_manager.auth_resolver(config)
        if auth is not None:
            resolved = dict(resolved)
            resolved["auth"] = auth
        server = _instantiate_server(config, resolved)
        try:
            await asyncio.wait_for(server.connect(), timeout=15.0)
            client_session = getattr(server, "session", None)
            if operation == "read_resource":
                if client_session is None or not hasattr(
                    client_session, "read_resource"
                ):
                    raise McpOperationUnsupported(server_name, "resources")
                result = await client_session.read_resource(kwargs["uri"])
                return {
                    "server_name": server_name,
                    "uri": kwargs["uri"],
                    "contents": _dump_model_list(getattr(result, "contents", [])),
                }
            if operation == "get_prompt":
                if client_session is None or not hasattr(client_session, "get_prompt"):
                    raise McpOperationUnsupported(server_name, "prompts")
                result = await client_session.get_prompt(
                    kwargs["prompt_name"], arguments=kwargs.get("args") or None
                )
                dumped = _dump_model(result)
                return {
                    "server_name": server_name,
                    "prompt_name": kwargs["prompt_name"],
                    "result": dumped if isinstance(dumped, dict) else {},
                }
            raise McpOperationUnsupported(server_name, operation)
        except McpOperationUnsupported:
            raise
        except BaseException as exc:
            from omniagents.core.agents.mcp_admin import is_external_cancellation

            if is_external_cancellation(exc):
                raise
            reason_code, reason = await self.mcp_manager.record_failure(
                server_name, exc, origin="probe"
            )
            raise McpServerUnavailable(
                server_name, reason_code=reason_code, message=reason
            ) from exc
        finally:
            try:
                await server.cleanup()
            except BaseException:
                pass

    #  Workspace filesystem watch & transfer (``fs_*`` RPCs).
    #
    #  Thin wrappers over ``self.filesystem`` (FilesystemManager): the
    #  manager owns watches, transfers, canonical-path confinement and
    #  typed -3206x errors; this layer owns workspace resolution and
    #  channel binding. Authorization: every method names a environment_id and
    #  is RESOURCE_SCOPED (see omniagents/rpc/auth.py) — another
    #  principal's workspace is refused with -32014 before any I/O.
    #  Notifications (fs_events/fs_rescan_required/fs_transfer_progress)
    #  are per-connection and deliberately NOT journaled in the replay
    #  log: on reconnect, clients re-subscribe and rescan.
    # ------------------------------------------------------------------

    async def _filesystem_workspace(self, environment_id: str) -> "Workspace":
        """Resolve one explicitly named environment's immutable workspace.

        The environment manager is the only execution lookup. A missing
        identity never consults a Thread, a process-global sandbox, the
        server's cwd, or a host workspace fallback.
        """

        lease = await self.environment_manager.acquire(environment_id)
        return lease.workspace

    async def _filesystem_source_writable(
        self, environment_id: str, workspace: "Workspace", rel: str
    ) -> bool:
        """Resolve launcher source policy for a workspace-relative path.

        Host paths are matched canonically against every declared source root,
        so a symlink crossing from a writable source into a read-only one
        inherits the destination policy. Generic backends have no portable
        realpath operation: they use the manifest's exact first-component
        mount name and fail closed when any existing path component is a
        symlink whose destination policy cannot be proven.
        """
        sources = [
            source
            for source in await self._execution_sources(environment_id)
            if isinstance(source.get("mountName"), str) and source["mountName"].strip()
        ]
        if not sources or rel == ".":
            return True

        if isinstance(workspace, HostWorkspace):
            workspace_root = Path(workspace.root).resolve(strict=False)
            lexical_target = Path(os.path.normpath(str(workspace_root / rel)))
            resolved_target = Path(workspace.resolve(rel)).resolve(strict=False)
            candidates: list[tuple[int, bool]] = []
            for source in sources:
                roots: list[tuple[Path, Path]] = []
                lexical_root = workspace_root / source["mountName"]
                roots.append((lexical_root, lexical_root.resolve(strict=False)))
                local_path = source.get("path")
                if source.get("kind") not in ("local", "local-git"):
                    local_path = None
                if not isinstance(local_path, str) or not local_path.strip():
                    local_path = None
                if local_path is not None:
                    local_root = Path(local_path)
                    roots.append((local_root, local_root.resolve(strict=False)))
                for lexical_root, canonical_root in roots:
                    for target, root in (
                        (lexical_target, lexical_root),
                        (resolved_target, canonical_root),
                    ):
                        try:
                            target.relative_to(root)
                        except ValueError:
                            continue
                        writable = source.get("writable")
                        candidates.append(
                            (
                                len(root.parts),
                                writable if isinstance(writable, bool) else True,
                            )
                        )
            if not candidates:
                return True
            # Both the path spelling the client chose and its canonical target
            # are security boundaries. A read-only policy on either side wins;
            # this prevents symlinks crossing RO -> RW as well as RW -> RO.
            return all(writable for _, writable in candidates)

        parts = rel.split("/")
        # A root mount ('.') IS the workspace: its policy governs every
        # path. Otherwise the manifest's exact first component names the
        # governing mount.
        source = next(
            (
                source
                for source in sources
                if source["mountName"] == "." or source["mountName"] == parts[0]
            ),
            None,
        )
        if source is not None and source.get("writable") is False:
            return False

        # Even an unrecognised first component can be an alias into a declared
        # read-only mount. Generic backends cannot canonicalize that target, so
        # any existing symlink component is an unprovable policy boundary and
        # must fail closed.
        parent = "."
        for index, component in enumerate(parts):
            try:
                entries = await workspace.list_dir(
                    parent, include_hidden=True, respect_gitignore=False
                )
            except OSError:
                return False
            entry = next((item for item in entries if item.name == component), None)
            if entry is None:
                break
            if entry.is_symlink:
                return False
            if index < len(parts) - 1 and not entry.is_dir:
                return False
            parent = entry.path
        return True

    async def _execution_sources(
        self, environment_id: str
    ) -> tuple[dict[str, Any], ...]:
        """Return source metadata for one explicitly identified environment."""

        descriptor = await self.environment_manager.describe(environment_id)
        workspace_resource = self.workspace_registry.get(descriptor.workspace_id)
        return tuple(dict(source) for source in workspace_resource.sources)

    async def fs_watch(
        self,
        environment_id: str,
        path: str,
        recursive: bool | None = None,
        poll_interval_ms: int | None = None,
    ) -> dict:  # noqa: D401
        return await self.filesystem.watch(
            environment_id,
            path,
            recursive=True if recursive is None else recursive,
            poll_interval_ms=poll_interval_ms,
            channel=self.channel,
        )

    async def fs_unwatch(
        self, environment_id: str, watch_id: str
    ) -> bool:  # noqa: D401
        return await self.filesystem.unwatch(
            environment_id, watch_id, channel=self.channel
        )

    async def fs_list(
        self, environment_id: str, path: str, recursive: bool = False
    ) -> dict:  # noqa: D401
        return await self.filesystem.list(environment_id, path, recursive=recursive)

    async def fs_stat(self, environment_id: str, path: str) -> dict:  # noqa: D401
        return await self.filesystem.stat(environment_id, path)

    async def fs_download_open(
        self, environment_id: str, path: str
    ) -> dict:  # noqa: D401
        return await self.filesystem.download_open(
            environment_id, path, channel=self.channel
        )

    async def fs_download_read(
        self,
        environment_id: str,
        transfer_id: str,
        offset: int | None = None,
        length: int | None = None,
    ) -> dict:  # noqa: D401
        return await self.filesystem.download_read(
            environment_id,
            transfer_id,
            offset=offset,
            length=length,
            channel=self.channel,
        )

    async def fs_download_close(
        self, environment_id: str, transfer_id: str
    ) -> bool:  # noqa: D401
        return await self.filesystem.download_close(
            environment_id, transfer_id, channel=self.channel
        )

    async def fs_upload_open(
        self,
        environment_id: str,
        path: str,
        size: int,
        sha256: str | None = None,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> dict:  # noqa: D401
        return await self.filesystem.upload_open(
            environment_id,
            path,
            size,
            sha256=sha256,
            expected_sha256=expected_sha256,
            overwrite=overwrite,
            channel=self.channel,
        )

    async def fs_upload_chunk(
        self, environment_id: str, transfer_id: str, offset: int, data: str
    ) -> dict:  # noqa: D401
        return await self.filesystem.upload_chunk(
            environment_id, transfer_id, offset, data, channel=self.channel
        )

    async def fs_upload_commit(
        self, environment_id: str, transfer_id: str
    ) -> dict:  # noqa: D401
        return await self.filesystem.upload_commit(
            environment_id, transfer_id, channel=self.channel
        )

    async def fs_upload_abort(
        self, environment_id: str, transfer_id: str
    ) -> bool:  # noqa: D401
        return await self.filesystem.upload_abort(
            environment_id, transfer_id, channel=self.channel
        )

    async def fs_delete(
        self, environment_id: str, path: str, recursive: bool = False
    ) -> dict:  # noqa: D401
        return await self.filesystem.delete(environment_id, path, recursive=recursive)

    async def fs_rename(
        self, environment_id: str, path: str, to: str
    ) -> dict:  # noqa: D401
        return await self.filesystem.rename(environment_id, path, to)

    async def fs_mkdir(self, environment_id: str, path: str) -> dict:  # noqa: D401
        return await self.filesystem.make_directory(environment_id, path)

    # ------------------------------------------------------------------
    #  Typed git operations (``git_*`` RPCs, tkt_l_l0lUi4A5V2).
    #
    #  Thin wrappers over ``self.git`` (GitManager): the manager owns
    #  repository confinement, machine-format parsing, the hunk model,
    #  index rollback and typed -3209x errors; this layer owns nothing but
    #  argument pass-through and the progress-notification channel.
    #  Authorization: every method names a environment_id and is
    #  RESOURCE_SCOPED (see omniagents/rpc/auth.py) — another principal's
    #  repository is refused with -32014 before any git process starts.
    #  ``git_operation_progress`` is per-connection and deliberately NOT
    #  journaled: a reconnecting client refetches git_status.
    # ------------------------------------------------------------------

    def _git_progress(self) -> "Callable[[dict], Awaitable[None]]":
        channel = self.channel

        async def emit(payload: dict) -> None:
            if channel is None:
                return
            await channel.async_call("git_operation_progress", payload)

        return emit

    async def git_list_repositories(
        self,
        environment_id: str,
        path: str | None = None,
        max_depth: int | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.list_repositories(
            environment_id, path=path, max_depth=max_depth
        )

    async def git_status(
        self,
        environment_id: str,
        repo: str,
        include_untracked: bool | None = None,
        include_ignored: bool | None = None,
        paths: list | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.status(
            environment_id,
            repo,
            include_untracked=include_untracked,
            include_ignored=include_ignored,
            paths=paths,
        )

    async def git_status_watch(
        self,
        environment_id: str,
        repo: str,
        poll_interval_ms: int | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.status_watch(
            environment_id,
            repo,
            poll_interval_ms=poll_interval_ms,
            channel=self.channel,
        )

    async def git_status_unwatch(
        self, environment_id: str, watch_id: str
    ) -> bool:  # noqa: D401
        return await self.git.status_unwatch(
            environment_id, watch_id, channel=self.channel
        )

    async def git_diff(
        self,
        environment_id: str,
        repo: str,
        mode: str | None = None,
        paths: list | None = None,
        context_lines: int | None = None,
        from_rev: str | None = None,
        to_rev: str | None = None,
        include_untracked: bool = False,
    ) -> dict:  # noqa: D401
        return await self.git.diff(
            environment_id,
            repo,
            mode=mode,
            paths=paths,
            context_lines=context_lines,
            from_rev=from_rev,
            to_rev=to_rev,
            include_untracked=include_untracked,
        )

    async def git_log(
        self,
        environment_id: str,
        repo: str,
        rev: str | None = None,
        max_count: int | None = None,
        skip: int | None = None,
        paths: list | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.log(
            environment_id, repo, rev=rev, max_count=max_count, skip=skip, paths=paths
        )

    async def git_list_branches(
        self, environment_id: str, repo: str, include_remote: bool | None = None
    ) -> dict:  # noqa: D401
        return await self.git.list_branches(
            environment_id, repo, include_remote=include_remote
        )

    async def git_list_worktrees(
        self, environment_id: str, repo: str
    ) -> dict:  # noqa: D401
        return await self.git.list_worktrees(environment_id, repo)

    async def git_conflicts(
        self, environment_id: str, repo: str, paths: list | None = None
    ) -> dict:  # noqa: D401
        return await self.git.conflicts(environment_id, repo, paths=paths)

    async def git_stage(
        self,
        environment_id: str,
        repo: str,
        paths: list | None = None,
        hunks: list | None = None,
        context_lines: int | None = None,
        mode: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.stage(
            environment_id,
            repo,
            paths=paths,
            hunks=hunks,
            context_lines=context_lines,
            mode=mode,
        )

    async def git_unstage(
        self,
        environment_id: str,
        repo: str,
        paths: list | None = None,
        hunks: list | None = None,
        context_lines: int | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.unstage(
            environment_id, repo, paths=paths, hunks=hunks, context_lines=context_lines
        )

    async def git_discard(
        self,
        environment_id: str,
        repo: str,
        paths: list | None = None,
        hunks: list | None = None,
        context_lines: int | None = None,
        confirmation_token: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.discard(
            environment_id,
            repo,
            paths=paths,
            hunks=hunks,
            context_lines=context_lines,
            confirmation_token=confirmation_token,
        )

    async def git_commit(
        self,
        environment_id: str,
        repo: str,
        message: str,
        amend: bool | None = None,
        allow_empty: bool | None = None,
        author: str | None = None,
        confirmation_token: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.commit(
            environment_id,
            repo,
            message,
            amend=amend,
            allow_empty=allow_empty,
            author=author,
            confirmation_token=confirmation_token,
        )

    async def git_checkout(
        self,
        environment_id: str,
        repo: str,
        branch: str,
        create: bool | None = None,
        start_point: str | None = None,
        detach: bool | None = None,
        discard_changes: bool | None = None,
        confirmation_token: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.checkout(
            environment_id,
            repo,
            branch=branch,
            create=create,
            start_point=start_point,
            detach=detach,
            discard_changes=discard_changes,
            confirmation_token=confirmation_token,
        )

    async def git_reset(
        self,
        environment_id: str,
        repo: str,
        mode: str | None = None,
        rev: str | None = None,
        paths: list | None = None,
        confirmation_token: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.reset(
            environment_id,
            repo,
            mode=mode,
            rev=rev,
            paths=paths,
            confirmation_token=confirmation_token,
        )

    async def git_fetch(
        self,
        environment_id: str,
        repo: str,
        remote: str | None = None,
        refspec: str | None = None,
        prune: bool | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.fetch(
            environment_id,
            repo,
            remote=remote,
            refspec=refspec,
            prune=prune,
            progress=self._git_progress(),
        )

    async def git_pull(
        self,
        environment_id: str,
        repo: str,
        remote: str | None = None,
        refspec: str | None = None,
        rebase: bool | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.pull(
            environment_id,
            repo,
            remote=remote,
            refspec=refspec,
            rebase=rebase,
            progress=self._git_progress(),
        )

    async def git_push(
        self,
        environment_id: str,
        repo: str,
        remote: str | None = None,
        refspec: str | None = None,
        force: bool | None = None,
        force_with_lease: bool | None = None,
        set_upstream: bool | None = None,
        confirmation_token: str | None = None,
    ) -> dict:  # noqa: D401
        return await self.git.push(
            environment_id,
            repo,
            remote=remote,
            refspec=refspec,
            force=force,
            force_with_lease=force_with_lease,
            set_upstream=set_upstream,
            confirmation_token=confirmation_token,
            progress=self._git_progress(),
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
        # Scoped in SQL (like list_sessions) so the caller's ``limit`` is
        # spent on rows they can actually see.
        try:
            return self._session_manager.get_user_messages(
                lim, inc, user_id=self._listing_scope_user_id()
            )
        except Exception:
            return []
