"""MCP server management behind the GUI protocol's ``mcp_*`` RPCs.

One :class:`McpServerManager` gives GUI clients a single lifecycle
contract for every MCP server the runtime understands — project-manifest
servers (``mcp_servers:`` in agent YAML), user-supplied servers (the
Claude-Code-format ``mcp.json`` files loaded by
:mod:`omniagents.core.config.user_mcp`), and session-scoped dynamic
servers (launcher/app-provided ``variables.mcp_servers``) — by
*promoting* the machinery that already exists rather than inventing a
parallel one:

* Listing/provenance reads the same files and spec state the loader
  merges (:func:`omniagents.core.config.user_mcp.load_user_mcp_config`).
* CRUD owns exactly one file — the user-global ``mcp.json`` — and never
  edits project manifests or the hand-managed project-local file
  (the :mod:`omniagents.core.config.layered` single-writable-layer
  precedent). Writes also apply to the live ``spec.mcp_servers`` (the
  ``disable_mcp_server`` precedent) so the next session build uses them.
* Startup status standardizes what ``_ensure_session_mcp_servers``
  already observes: ``configured → starting → ready | failed`` with a
  typed, actionable reason. Transitions are **broadcast, not
  journaled** (``mcp_server_status_changed``, the ``account_changed``
  precedent): server status is process-global, not session history —
  clients refetch via ``mcp_list_servers`` on reconnect.

SECRETS: this module follows the layered-config secret model. Header
values (HTTP transports) and environment values (stdio transports) are
**write-only**: accepted on create/update, never returned by any read,
status, or error path — snapshots carry ``{"is_set": true}`` markers
only, and validation errors never echo submitted values.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from omniagents.core.agents.specs import MCPServerConfig

__all__ = [
    "MCP_SERVER_TYPES",
    "MCP_STORE_DURABILITY_ENV",
    "MANAGED_MCP_SERVERS_ENV",
    "McpAuthFailed",
    "McpAuthStateInvalid",
    "McpConfigInvalid",
    "McpOperationUnsupported",
    "McpRpcError",
    "McpServerManager",
    "McpServerNotFound",
    "McpServerReadOnly",
    "McpServerUnavailable",
    "RPC_ERROR_MCP_AUTH_FAILED",
    "RPC_ERROR_MCP_AUTH_STATE_INVALID",
    "RPC_ERROR_MCP_CONFIG_INVALID",
    "RPC_ERROR_MCP_OPERATION_UNSUPPORTED",
    "RPC_ERROR_MCP_SERVER_NOT_FOUND",
    "RPC_ERROR_MCP_SERVER_READ_ONLY",
    "RPC_ERROR_MCP_SERVER_UNAVAILABLE",
    "classify_mcp_failure",
    "redact_server_params",
]

#: JSON-RPC error codes reserved for the MCP management surface
#: (``protocol/openrpc`` components.errors; -32050..52 = elicitation,
#: -32060..63 = filesystem, so MCP takes the -32070+ block).
RPC_ERROR_MCP_SERVER_NOT_FOUND = -32070
RPC_ERROR_MCP_CONFIG_INVALID = -32071
RPC_ERROR_MCP_SERVER_READ_ONLY = -32072
RPC_ERROR_MCP_OPERATION_UNSUPPORTED = -32073
RPC_ERROR_MCP_SERVER_UNAVAILABLE = -32074
RPC_ERROR_MCP_AUTH_STATE_INVALID = -32075
RPC_ERROR_MCP_AUTH_FAILED = -32076

MCP_SERVER_TYPES = ("stdio", "http", "sse", "streamable_http")

#: Config sources, highest precedence last (mirrors the loader merge).
SOURCE_PROJECT = "project"
SOURCE_USER = "user"
SOURCE_PROJECT_LOCAL = "project_local"
SOURCE_SESSION = "session"
SOURCE_HOST_MANAGED = "host_managed"

#: Host attestations for the product-scoped MCP configuration and OAuth
#: stores.  Durability is never inferred from writability, and server names
#: are deliberately the only managed-store metadata exposed to clients.
MCP_STORE_DURABILITY_ENV = "OMNIAGENTS_MCP_STORE_DURABILITY"
MANAGED_MCP_SERVERS_ENV = "OMNIAGENTS_MANAGED_MCP_SERVERS"
_MANAGED_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Startup states (``configured → starting → ready | failed``).
STATUS_CONFIGURED = "configured"
STATUS_STARTING = "starting"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"

_OPTION_KEYS = (
    "cache_tools_list",
    "client_session_timeout_seconds",
    "tool_filter",
    "use_structured_content",
    "name",
)


def _managed_server_names() -> set[str]:
    """Strict, redacted host-managed server-name attestation.

    Invalid/empty comma-separated entries are ignored.  A malformed host
    value must never widen the set of configurations protected as managed.
    """
    raw = os.getenv(MANAGED_MCP_SERVERS_ENV) or ""
    return {
        item
        for candidate in raw.split(",")
        if (item := candidate.strip())
        and item == candidate
        and _MANAGED_SERVER_NAME_RE.fullmatch(item)
    }


class McpRpcError(Exception):
    """Base typed MCP management error: JSON-RPC ``code`` + ``data``.

    ``JsonRpcEndpoint`` surfaces these verbatim (code, message, data)
    instead of the generic ``-32603`` internal error — the same contract
    as ``AccountRpcError`` and ``ElicitationError``.
    """

    code: int = -32603
    kind: str = "mcp_error"

    def __init__(
        self,
        message: str,
        *,
        server_name: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.server_name = server_name
        self.extra = {key: value for key, value in extra.items() if value is not None}

    @property
    def data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"kind": self.kind}
        if self.server_name is not None:
            data["server_name"] = self.server_name
        data.update(self.extra)
        return data


class McpServerNotFound(McpRpcError):
    """No configured MCP server has the requested name."""

    code = RPC_ERROR_MCP_SERVER_NOT_FOUND
    kind = "mcp_server_not_found"

    def __init__(self, server_name: str) -> None:
        super().__init__(f"Unknown MCP server: {server_name}", server_name=server_name)


class McpConfigInvalid(McpRpcError):
    """A create/update payload failed validation.

    ``errors`` entries carry ``code``/``message``/``field`` and NEVER echo
    submitted values, so a mistyped secret cannot leak back through a
    validation response.
    """

    code = RPC_ERROR_MCP_CONFIG_INVALID
    kind = "mcp_config_invalid"

    def __init__(
        self, errors: List[Dict[str, Any]], *, server_name: str | None = None
    ) -> None:
        super().__init__(
            "MCP server configuration is invalid",
            server_name=server_name,
            errors=list(errors),
        )
        self.errors = list(errors)


class McpServerReadOnly(McpRpcError):
    """The named server cannot be changed through this API."""

    code = RPC_ERROR_MCP_SERVER_READ_ONLY
    kind = "mcp_server_read_only"

    def __init__(self, server_name: str, *, reason: str, message: str) -> None:
        super().__init__(message, server_name=server_name, reason=reason)
        self.reason = reason


class McpOperationUnsupported(McpRpcError):
    """The server (or its transport) does not support the operation."""

    code = RPC_ERROR_MCP_OPERATION_UNSUPPORTED
    kind = "mcp_operation_unsupported"

    def __init__(
        self, server_name: str, operation: str, *, message: str | None = None
    ) -> None:
        super().__init__(
            message or f"MCP server {server_name} does not support {operation}",
            server_name=server_name,
            operation=operation,
        )


class McpServerUnavailable(McpRpcError):
    """The server could not be reached / started for the operation."""

    code = RPC_ERROR_MCP_SERVER_UNAVAILABLE
    kind = "mcp_server_unavailable"

    def __init__(
        self,
        server_name: str,
        *,
        reason_code: str,
        message: str,
    ) -> None:
        super().__init__(message, server_name=server_name, reason_code=reason_code)
        self.reason_code = reason_code


class McpAuthStateInvalid(McpRpcError):
    """A supported auth operation was requested in a state that forbids it."""

    code = RPC_ERROR_MCP_AUTH_STATE_INVALID
    kind = "mcp_auth_state_invalid"

    def __init__(
        self,
        reason: str,
        *,
        server_name: str | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(
            message or f"MCP auth operation is not valid here: {reason}",
            server_name=server_name,
            reason=reason,
        )


class McpAuthFailed(McpRpcError):
    """The upstream authorization server rejected or failed the flow."""

    code = RPC_ERROR_MCP_AUTH_FAILED
    kind = "mcp_auth_failed"

    def __init__(
        self,
        message: str,
        *,
        server_name: str,
        status: int | None = None,
    ) -> None:
        super().__init__(message, server_name=server_name, status=status)


# ---------------------------------------------------------------------------
# Secret redaction (the write-only header/env model)
# ---------------------------------------------------------------------------


def redact_server_params(params: Any) -> Dict[str, Any]:
    """Copy of ``params`` safe for any read path.

    ``headers`` and ``env`` are the secret-bearing channels of the two
    transport families; their VALUES are replaced with ``{"is_set": true}``
    markers (names stay visible so clients can render/edit them). Every
    other key (url, command, args, cwd, ...) passes through.
    """
    if not isinstance(params, dict):
        return {}
    redacted: Dict[str, Any] = {}
    for key, value in params.items():
        if key in ("headers", "env") and isinstance(value, dict):
            redacted[key] = {str(name): {"is_set": True} for name in value}
        elif key == "auth":
            # Injected httpx.Auth objects are runtime-only; never surface.
            continue
        else:
            redacted[key] = value
    return redacted


def is_external_cancellation(exc: BaseException) -> bool:
    """Whether a ``CancelledError`` reflects OUR task being cancelled.

    The MCP SDK's transport task groups can surface internal failures
    (e.g. an auth flow abort) as a bare ``CancelledError`` from a foreign
    cancel scope. Those must be *classified*, not propagated; only a
    cancellation actually requested on the current task re-raises.
    """
    if not isinstance(exc, asyncio.CancelledError):
        return False
    task = asyncio.current_task()
    return task is not None and bool(task.cancelling())


def classify_mcp_failure(exc: BaseException) -> Tuple[str, str]:
    """Map a server start/connect failure to a typed, actionable reason.

    Returns ``(reason_code, message)``. Codes are part of the wire
    contract (``mcp_server_status_changed.reason_code`` and the
    ``-32074`` error payload): ``auth_required``, ``auth_expired``,
    ``command_not_found``, ``timeout``, ``connect_failed``,
    ``invalid_config``, ``start_failed``.
    """
    from omniagents.core.agents.mcp_oauth import McpInteractiveAuthRequired

    def _walk(err: BaseException) -> List[BaseException]:
        seen: List[BaseException] = []
        stack = [err]
        while stack:
            item = stack.pop()
            if item is None or any(item is s for s in seen):
                continue
            seen.append(item)
            if isinstance(item, BaseExceptionGroup):
                stack.extend(item.exceptions)
            if item.__cause__ is not None:
                stack.append(item.__cause__)
            if item.__context__ is not None:
                stack.append(item.__context__)
        return seen

    chain = _walk(exc)
    for err in chain:
        if isinstance(err, McpInteractiveAuthRequired):
            code = "auth_expired" if err.had_tokens else "auth_required"
            return code, str(err) or "Authorization is required"
    text = str(exc) or exc.__class__.__name__
    for err in chain:
        if isinstance(err, FileNotFoundError):
            return "command_not_found", f"Command not found: {err.filename or text}"
        if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
            return "timeout", "Timed out starting the MCP server"
        status = getattr(getattr(err, "response", None), "status_code", None)
        if status in (401, 403):
            return "auth_required", f"Server rejected the connection (HTTP {status})"
        if isinstance(err, ValueError):
            return "invalid_config", str(err)
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered:
        return "auth_required", text
    if "connect" in lowered or "connection" in lowered or "refused" in lowered:
        return "connect_failed", text
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout", text
    return "start_failed", text


# ---------------------------------------------------------------------------
# User-layer mcp.json store (the ONE file this API writes)
# ---------------------------------------------------------------------------


def _config_to_json_entry(config: MCPServerConfig) -> Dict[str, Any]:
    """Serialize one server back to the Claude-Code mcp.json shape."""
    entry: Dict[str, Any] = {"type": config.type}
    params = config.params or {}
    if config.type == "stdio":
        entry["command"] = params.get("command")
        if "args" in params:
            entry["args"] = params["args"]
        if "env" in params:
            entry["env"] = params["env"]
    else:
        entry["url"] = params.get("url")
        if "headers" in params:
            entry["headers"] = params["headers"]
    for key in _OPTION_KEYS:
        if config.options and key in config.options and key != "name":
            entry[key] = config.options[key]
    return entry


class _UserMcpFileStore:
    """Atomic read/merge/write access to the user-global ``mcp.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_raw(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def server_names(self) -> List[str]:
        servers = self.read_raw().get("mcpServers")
        if not isinstance(servers, dict):
            return []
        return [str(name) for name in servers]

    def get_entry(self, name: str) -> Dict[str, Any] | None:
        servers = self.read_raw().get("mcpServers")
        if isinstance(servers, dict) and isinstance(servers.get(name), dict):
            return dict(servers[name])
        return None

    def upsert(self, name: str, entry: Dict[str, Any]) -> None:
        doc = self.read_raw()
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        servers[name] = entry
        doc["mcpServers"] = servers
        self._write(doc)

    def delete(self, name: str) -> bool:
        doc = self.read_raw()
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict) or name not in servers:
            return False
        servers.pop(name)
        doc["mcpServers"] = servers
        self._write(doc)
        return True

    def _write(self, doc: Dict[str, Any]) -> None:
        """Replace the file atomically with restrictive permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        tmp = Path(tmp_name)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_server_payload(
    server_name: str,
    server_type: Any,
    params: Any,
    server_options: Any,
) -> List[Dict[str, Any]]:
    """Structural validation for create/update. Never echoes values."""
    errors: List[Dict[str, Any]] = []
    name = str(server_name or "").strip()
    if not name:
        errors.append(
            {
                "code": "name_required",
                "field": "server_name",
                "message": "server_name must be a non-empty string",
            }
        )
    if not isinstance(server_type, str) or server_type not in MCP_SERVER_TYPES:
        errors.append(
            {
                "code": "invalid_type",
                "field": "type",
                "message": "type must be one of: " + ", ".join(MCP_SERVER_TYPES),
            }
        )
        return errors
    if not isinstance(params, dict):
        errors.append(
            {
                "code": "invalid_params",
                "field": "params",
                "message": "params must be an object",
            }
        )
        return errors
    if server_type == "stdio":
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(
                {
                    "code": "command_required",
                    "field": "params.command",
                    "message": "stdio servers require params.command",
                }
            )
        args = params.get("args")
        if args is not None and (
            not isinstance(args, list)
            or not all(isinstance(item, str) for item in args)
        ):
            errors.append(
                {
                    "code": "invalid_args",
                    "field": "params.args",
                    "message": "params.args must be a list of strings",
                }
            )
        env = params.get("env")
        if env is not None and (
            not isinstance(env, dict)
            or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env.items()
            )
        ):
            errors.append(
                {
                    "code": "invalid_env",
                    "field": "params.env",
                    "message": "params.env must be a string→string object",
                }
            )
        unknown = set(params) - {"command", "args", "env", "cwd"}
    else:
        url = params.get("url")
        if not isinstance(url, str) or not url.strip():
            errors.append(
                {
                    "code": "url_required",
                    "field": "params.url",
                    "message": f"{server_type} servers require params.url",
                }
            )
        headers = params.get("headers")
        if headers is not None and (
            not isinstance(headers, dict)
            or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            )
        ):
            errors.append(
                {
                    "code": "invalid_headers",
                    "field": "params.headers",
                    "message": "params.headers must be a string→string object",
                }
            )
        unknown = set(params) - {"url", "headers"}
    for key in sorted(unknown):
        errors.append(
            {
                "code": "unknown_param",
                "field": f"params.{key}",
                "message": f"params.{key} is not a recognized parameter",
            }
        )
    if server_options is not None:
        if not isinstance(server_options, dict):
            errors.append(
                {
                    "code": "invalid_options",
                    "field": "server_options",
                    "message": "server_options must be an object",
                }
            )
        else:
            for key in sorted(set(server_options) - set(_OPTION_KEYS)):
                errors.append(
                    {
                        "code": "unknown_option",
                        "field": f"server_options.{key}",
                        "message": f"server_options.{key} is not a recognized option",
                    }
                )
    return errors


def _merge_secret_channels(
    existing: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Write-only merge for one update's ``headers``/``env`` channels.

    Because reads never return secret values, an update payload built
    from a read cannot round-trip them — so for each channel entry:

    * a string sets/replaces the value,
    * ``None`` deletes the entry,
    * an omitted name keeps the existing (unreadable) value.

    Non-secret keys replace wholesale (list/scalar replace semantics).
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if key in ("headers", "env") and isinstance(value, dict):
            channel = dict(merged.get(key) or {})
            for name, item in value.items():
                if item is None:
                    channel.pop(name, None)
                else:
                    channel[name] = item
            if channel:
                merged[key] = channel
            else:
                merged.pop(key, None)
        elif value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _strip_secret_deletes(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` markers from a create payload's secret channels."""
    cleaned = dict(params)
    for key in ("headers", "env"):
        channel = cleaned.get(key)
        if isinstance(channel, dict):
            kept = {k: v for k, v in channel.items() if v is not None}
            if kept:
                cleaned[key] = kept
            else:
                cleaned.pop(key, None)
    return cleaned


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


@dataclass
class _ServerStatus:
    state: str = STATUS_CONFIGURED
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    at: Optional[str] = None
    origin: Optional[str] = None


@dataclass
class _ProbeSnapshot:
    captured_at: str
    server_info: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)
    resource_templates: List[Dict[str, Any]] = field(default_factory=list)
    prompts: List[Dict[str, Any]] = field(default_factory=list)
    supported: Dict[str, bool] = field(default_factory=dict)


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _dump_model(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", by_alias=True, exclude_none=True)
        except Exception:
            pass
    if isinstance(value, dict):
        return value
    return {"raw": str(value)}


def _dump_model_list(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items or []:
        dumped = _dump_model(item)
        if isinstance(dumped, dict):
            out.append(dumped)
    return out


class McpServerManager:
    """Config registry, startup status, and probes for MCP servers.

    One instance per :class:`AgentService`; state is process-global (the
    same posture as ``disable_mcp_server`` / ``write_config``), so
    configuration mutations are classified GLOBAL_MUTATING and refused
    for individual principals in enforced multi-user mode.
    """

    def __init__(
        self,
        spec: Any,
        *,
        settings_provider: Callable[[], Dict[str, Any]] | None = None,
        broadcast: Callable[[Dict[str, Any]], Awaitable[None]] | None = None,
        layered_store_factory: Callable[[], Any] | None = None,
        probe_timeout: float = 15.0,
    ) -> None:
        self.spec = spec
        self._settings_provider = settings_provider or (lambda: {})
        self._broadcast = broadcast
        self._layered_store_factory = layered_store_factory
        self._probe_timeout = probe_timeout
        self._status: Dict[str, _ServerStatus] = {}
        self._probes: Dict[str, _ProbeSnapshot] = {}
        self._disabled: set[str] = set()
        # The manifest-declared servers as they were at boot, so the
        # user-layer merge can be recomputed after CRUD/reload without
        # re-parsing agent YAML. User-file servers that the loader merged
        # into the spec are attributed back to their file by name.
        user_names = set(self._user_store().server_names()) | set(
            self._project_local_names()
        )
        # Tolerate non-config entries (tests inject live server doubles
        # straight into spec.mcp_servers): only MCPServerConfig-shaped
        # entries participate in the management registry.
        self._project_configs: List[MCPServerConfig] = [
            cfg
            for cfg in list(getattr(spec, "mcp_servers", None) or [])
            if isinstance(getattr(cfg, "name", None), str)
            and getattr(cfg, "params", None) is not None
            and cfg.name not in user_names
        ]
        from omniagents.core.agents.mcp_oauth import McpOAuthBroker

        self.oauth = McpOAuthBroker(
            token_dir=self._auth_dir(),
            on_state_change=self._on_auth_state_change,
        )

    # -- paths / product identity ---------------------------------------

    def _product_slug(self) -> str:
        slug = getattr(self.spec, "project_slug", None)
        if isinstance(slug, str) and slug.strip():
            return slug.strip()
        from omniagents.core.product import product_info

        return product_info().slug

    def _project_root(self) -> Path | None:
        manifest = getattr(self.spec, "manifest_path", None)
        if isinstance(manifest, str) and manifest:
            return Path(manifest).resolve().parent
        return None

    def _user_mcp_path(self) -> Path:
        from omniagents.core.config.user_mcp import get_user_config_dir

        return get_user_config_dir(self._product_slug()) / "mcp.json"

    def _project_local_path(self) -> Path | None:
        root = self._project_root()
        if root is None:
            return None
        return root / f".{self._product_slug()}" / "mcp.json"

    def _auth_dir(self) -> Path:
        from omniagents.core.config.user_mcp import get_user_config_dir

        return get_user_config_dir(self._product_slug()) / "mcp-auth"

    def _user_store(self) -> _UserMcpFileStore:
        return _UserMcpFileStore(self._user_mcp_path())

    def _project_local_names(self) -> List[str]:
        path = self._project_local_path()
        if path is None or not path.is_file():
            return []
        return _UserMcpFileStore(path).server_names()

    # -- gates (read hot from the layered config, #304) ------------------

    def _layered_value(self, key: str, default: Any) -> Any:
        try:
            if self._layered_store_factory is not None:
                store = self._layered_store_factory()
            else:
                from omniagents.core.config.layered import LayeredConfigStore

                manifest = getattr(self.spec, "manifest_path", None)
                store = LayeredConfigStore(
                    manifest_path=Path(manifest) if manifest else None
                )
            is_set, value = store.effective_value(key)
            return value if is_set else default
        except Exception:
            return default

    def user_mcp_allowed(self) -> bool:
        return bool(self._layered_value("allow_user_mcp_servers", False))

    def _allowlist(self) -> Optional[set[str]]:
        """The active allowlist, or ``None`` when not required."""
        if not bool(self._layered_value("security.mcp_require_allowlist", False)):
            return None
        allowed = self._layered_value("security.mcp_allowed_servers", []) or []
        return {str(name) for name in allowed}

    # -- provenance ------------------------------------------------------

    def _load_user_configs(self) -> Tuple[List[MCPServerConfig], List[MCPServerConfig]]:
        """``(user_global, project_local)`` parsed with the loader's parser."""
        from omniagents.core.config.user_mcp import _load_mcp_json_file

        user_global = _load_mcp_json_file(self._user_mcp_path())
        local_path = self._project_local_path()
        project_local = (
            _load_mcp_json_file(local_path) if local_path is not None else []
        )
        return user_global, project_local

    def source_of(self, server_name: str) -> str | None:
        """Effective config source of a server (highest precedence wins)."""
        user_global, project_local = self._load_user_configs()
        managed = _managed_server_names()
        if server_name in managed and any(
            cfg.name == server_name for cfg in user_global
        ):
            return SOURCE_HOST_MANAGED
        if any(cfg.name == server_name for cfg in project_local):
            return SOURCE_PROJECT_LOCAL
        if any(cfg.name == server_name for cfg in user_global):
            return SOURCE_USER
        if any(cfg.name == server_name for cfg in self._project_configs):
            return SOURCE_PROJECT
        return None

    def _merged_configs(self) -> Dict[str, Tuple[MCPServerConfig, str]]:
        """name → (config, source), loader merge order (user files win)."""
        merged: Dict[str, Tuple[MCPServerConfig, str]] = {
            cfg.name: (cfg, SOURCE_PROJECT) for cfg in self._project_configs
        }
        managed = _managed_server_names()
        user_global, project_local = self._load_user_configs()
        user_mcp_allowed = self.user_mcp_allowed()
        for cfg in user_global:
            if cfg.name in managed:
                # Host-managed user-file entries remain active even when the
                # ordinary user MCP layer is disabled, and cannot be shadowed
                # by a hand-managed project-local entry of the same name.
                merged[cfg.name] = (cfg, SOURCE_HOST_MANAGED)
            elif user_mcp_allowed:
                merged[cfg.name] = (cfg, SOURCE_USER)
        if user_mcp_allowed:
            for cfg in project_local:
                if (
                    cfg.name in managed
                    and merged.get(cfg.name, (None, None))[1] == SOURCE_HOST_MANAGED
                ):
                    continue
                merged[cfg.name] = (cfg, SOURCE_PROJECT_LOCAL)
        return merged

    def effective_configs(self) -> List[MCPServerConfig]:
        """The configs sessions should run: merge minus gates/kill switch."""
        allowlist = self._allowlist()
        configs: List[MCPServerConfig] = []
        for name, (cfg, _source) in self._merged_configs().items():
            if name in self._disabled:
                continue
            if allowlist is not None and name not in allowlist:
                continue
            configs.append(cfg)
        return configs

    def apply_to_spec(self) -> None:
        """Re-point ``spec.mcp_servers`` at the current effective configs.

        The ``disable_mcp_server`` precedent: session builds read the
        spec, so mutating it applies changes on the next agent build.
        """
        self.spec.mcp_servers = self.effective_configs()

    def mark_disabled(self, server_name: str) -> None:
        """Record a kill-switched server (``disable_mcp_server``)."""
        self._disabled.add(server_name)
        self._status[server_name] = _ServerStatus(
            state=STATUS_DISABLED, at=_utcnow(), origin="kill_switch"
        )

    # -- status ----------------------------------------------------------

    def status_of(self, server_name: str) -> Dict[str, Any]:
        status = self._status.get(server_name) or _ServerStatus()
        payload: Dict[str, Any] = {"state": status.state}
        if status.reason_code is not None:
            payload["reason_code"] = status.reason_code
        if status.reason is not None:
            payload["reason"] = status.reason
        if status.at is not None:
            payload["at"] = status.at
        if status.origin is not None:
            payload["origin"] = status.origin
        return payload

    async def record_transition(
        self,
        server_name: str,
        state: str,
        *,
        reason_code: str | None = None,
        reason: str | None = None,
        origin: str | None = None,
    ) -> None:
        """Record a startup transition and broadcast it when it changes.

        Broadcast, not journaled: server status is process-global (the
        ``account_changed`` decision) — clients refetch the full picture
        via ``mcp_list_servers`` after a reconnect.
        """
        previous = self._status.get(server_name)
        previous_state = previous.state if previous is not None else None
        self._status[server_name] = _ServerStatus(
            state=state,
            reason_code=reason_code,
            reason=reason,
            at=_utcnow(),
            origin=origin,
        )
        if previous_state == state and state != STATUS_FAILED:
            return
        if self._broadcast is None:
            return
        payload: Dict[str, Any] = {"server_name": server_name, "status": state}
        if previous_state is not None:
            payload["previous_status"] = previous_state
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if reason is not None:
            payload["reason"] = reason
        payload["auth_state"] = self.auth_state(server_name)["state"]
        payload["at"] = _utcnow()
        try:
            await self._broadcast(payload)
        except Exception:
            pass

    async def record_failure(
        self, server_name: str, exc: BaseException, *, origin: str
    ) -> Tuple[str, str]:
        reason_code, reason = classify_mcp_failure(exc)
        if reason_code not in ("auth_required", "auth_expired"):
            # The SDK's transport task groups may swallow the interactive-
            # auth signal into a bare cancel-scope error; the broker's
            # side-channel preserves the typed truth for this attempt.
            had_tokens = self.oauth.take_noninteractive_failure(server_name)
            if had_tokens is not None:
                reason_code = "auth_expired" if had_tokens else "auth_required"
                reason = (
                    "Stored credentials are expired; reauthentication required"
                    if had_tokens
                    else "The server requires authorization"
                )
        if reason_code in ("auth_required", "auth_expired"):
            self.oauth.note_auth_failure(
                server_name, expired=reason_code == "auth_expired"
            )
        await self.record_transition(
            server_name,
            STATUS_FAILED,
            reason_code=reason_code,
            reason=reason,
            origin=origin,
        )
        return reason_code, reason

    async def _on_auth_state_change(self, server_name: str, state: str) -> None:
        """OAuth broker hook: reflect auth movements in server status."""
        if state == "authenticated":
            await self.record_transition(server_name, STATUS_CONFIGURED, origin="auth")
        elif state == "expired":
            await self.record_transition(
                server_name,
                STATUS_FAILED,
                reason_code="auth_expired",
                reason="Stored credentials are expired; reauthentication required",
                origin="auth",
            )

    # -- auth ------------------------------------------------------------

    def auth_state(self, server_name: str) -> Dict[str, Any]:
        config = self._find_config(server_name)
        if config is None:
            return {"kind": "none", "state": "unknown"}
        if config.type == "stdio":
            env = (config.params or {}).get("env") or {}
            return {
                "kind": "env" if env else "none",
                "state": "configured" if env else "not_required",
            }
        headers = (config.params or {}).get("headers") or {}
        oauth_state = self.oauth.state_of(server_name)
        if oauth_state != "none":
            return {"kind": "oauth", "state": oauth_state}
        if headers:
            return {"kind": "headers", "state": "configured"}
        return {"kind": "none", "state": "not_authenticated"}

    def auth_resolver(self, cfg: MCPServerConfig) -> Any:
        """httpx.Auth for HTTP-family servers (None for stdio/explicit)."""
        if cfg.type == "stdio":
            return None
        headers = (cfg.params or {}).get("headers") or {}
        if any(str(name).lower() == "authorization" for name in headers):
            return None  # explicit credential wins; never double-attach
        url = (cfg.params or {}).get("url")
        if not isinstance(url, str) or not url:
            return None
        return self.oauth.noninteractive_auth(cfg.name, url)

    # -- config lookups --------------------------------------------------

    def _find_config(self, server_name: str) -> MCPServerConfig | None:
        entry = self._merged_configs().get(server_name)
        if entry is not None:
            return entry[0]
        # Gated-out servers are still *configured* (listed as disabled).
        user_global, project_local = self._load_user_configs()
        for cfg in project_local + user_global + self._project_configs:
            if cfg.name == server_name:
                return cfg
        return None

    def require_config(self, server_name: str) -> MCPServerConfig:
        config = self._find_config(server_name)
        if config is None:
            raise McpServerNotFound(server_name)
        return config

    def _enablement(self, server_name: str) -> Tuple[bool, str | None]:
        if server_name in self._disabled:
            return False, "kill_switch"
        allowlist = self._allowlist()
        if allowlist is not None and server_name not in allowlist:
            return False, "allowlist_blocked"
        source = self.source_of(server_name)
        if source in (SOURCE_USER, SOURCE_PROJECT_LOCAL):
            if not self.user_mcp_allowed():
                return False, "user_mcp_disabled"
        return True, None

    # -- snapshots -------------------------------------------------------

    def _snapshot_config(self, cfg: MCPServerConfig, source: str) -> Dict[str, Any]:
        enabled, disabled_reason = self._enablement(cfg.name)
        writable = source == SOURCE_USER and self.user_mcp_allowed()
        read_only_reason: str | None = None
        if not writable:
            read_only_reason = {
                SOURCE_PROJECT: "project_manifest",
                SOURCE_PROJECT_LOCAL: "project_local_file",
                SOURCE_SESSION: "session_scoped",
                SOURCE_USER: "user_mcp_disabled",
                SOURCE_HOST_MANAGED: "host_managed",
            }.get(source, "read_only")
        snapshot: Dict[str, Any] = {
            "server_name": cfg.name,
            "source": source,
            "transport": cfg.type,
            "params": redact_server_params(cfg.params or {}),
            "server_options": dict(cfg.options or {}),
            "enabled": enabled,
            "read_only": not writable,
            "status": self.status_of(cfg.name),
            "auth": self.auth_state(cfg.name),
        }
        if disabled_reason is not None:
            snapshot["disabled_reason"] = disabled_reason
        if read_only_reason is not None:
            snapshot["read_only_reason"] = read_only_reason
        probe = self._probes.get(cfg.name)
        if probe is not None:
            snapshot["server_info"] = probe.server_info
            snapshot["capabilities"] = probe.capabilities
            snapshot["counts"] = {
                "tools": len(probe.tools),
                "resources": len(probe.resources),
                "resource_templates": len(probe.resource_templates),
                "prompts": len(probe.prompts),
            }
            snapshot["captured_at"] = probe.captured_at
        return snapshot

    def list_servers(
        self, *, session_servers: List[Dict[str, Any]] | None = None
    ) -> Dict[str, Any]:
        """The ``mcp_list_servers`` payload. Secrets never appear."""
        servers: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for name, (cfg, source) in self._merged_configs().items():
            servers.append(self._snapshot_config(cfg, source))
            seen.add(name)
        # Servers present in files but gated out of the merge still list
        # (disabled) so settings UIs can show and re-enable them.
        user_global, project_local = self._load_user_configs()
        for cfg, source in [
            *((c, SOURCE_PROJECT_LOCAL) for c in project_local),
            *((c, SOURCE_USER) for c in user_global),
        ]:
            if cfg.name not in seen:
                servers.append(self._snapshot_config(cfg, source))
                seen.add(cfg.name)
        for entry in session_servers or []:
            servers.append(entry)
        servers.sort(key=lambda item: str(item.get("server_name", "")))
        return {
            "servers": servers,
            "user_mcp_allowed": self.user_mcp_allowed(),
            "write_target": str(self._user_mcp_path()),
            "mutation_persistence": self.mutation_persistence(),
        }

    def mutation_persistence(self) -> Dict[str, Any]:
        """Redacted deployment guarantees for Desktop-owned mutations."""
        durable = os.getenv(MCP_STORE_DURABILITY_ENV) == "host"
        host_scope = "host" if durable else None
        return {
            "user_config": {"durable": durable, "scope": host_scope},
            "oauth_tokens": {"durable": durable, "scope": host_scope},
            "pending_auth": {"durable": False, "scope": "process"},
            "managed_servers": sorted(_managed_server_names()),
        }

    def session_server_snapshot(self, dyn_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Redacted snapshot for one session-scoped (dynamic) server."""
        name = str(dyn_cfg.get("name") or "dynamic")
        return {
            "server_name": name,
            "source": SOURCE_SESSION,
            "transport": str(dyn_cfg.get("type") or "streamable_http"),
            "params": redact_server_params(dyn_cfg.get("params") or {}),
            "server_options": dict(dyn_cfg.get("options") or {}),
            "enabled": True,
            "read_only": True,
            "read_only_reason": "session_scoped",
            "status": self.status_of(name),
            "auth": {"kind": "none", "state": "not_authenticated"},
        }

    def get_server(self, server_name: str) -> Dict[str, Any]:
        config = self.require_config(server_name)
        source = self.source_of(server_name) or SOURCE_PROJECT
        snapshot = self._snapshot_config(config, source)
        probe = self._probes.get(server_name)
        if probe is not None:
            snapshot["tools"] = probe.tools
            snapshot["resources"] = probe.resources
            snapshot["resource_templates"] = probe.resource_templates
            snapshot["prompts"] = probe.prompts
            snapshot["supported"] = probe.supported
        return snapshot

    # -- CRUD ------------------------------------------------------------

    def _require_writable(self, server_name: str, *, creating: bool) -> None:
        if server_name in _managed_server_names():
            raise McpServerReadOnly(
                server_name,
                reason="host_managed",
                message=(
                    f"MCP server {server_name!r} is managed by the host and "
                    "cannot be changed through this API"
                ),
            )
        if not self.user_mcp_allowed():
            raise McpServerReadOnly(
                server_name,
                reason="user_mcp_disabled",
                message=(
                    "User MCP servers are disabled "
                    "(allow_user_mcp_servers is false or an MCP allowlist "
                    "is enforced)"
                ),
            )
        source = self.source_of(server_name)
        if creating:
            if source is not None:
                raise McpConfigInvalid(
                    [
                        {
                            "code": "name_conflict",
                            "field": "server_name",
                            "message": (
                                f"An MCP server named {server_name!r} is already "
                                f"configured (source: {source})"
                            ),
                        }
                    ],
                    server_name=server_name,
                )
            return
        if source is None:
            raise McpServerNotFound(server_name)
        if source != SOURCE_USER:
            raise McpServerReadOnly(
                server_name,
                reason={
                    SOURCE_PROJECT: "project_manifest",
                    SOURCE_PROJECT_LOCAL: "project_local_file",
                }.get(source, "read_only"),
                message=(
                    f"MCP server {server_name!r} is defined by the "
                    f"{source.replace('_', '-')} configuration and cannot be "
                    "changed through this API"
                ),
            )

    def create_server(
        self,
        server_name: str,
        server_type: str,
        params: Dict[str, Any],
        server_options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        errors = _validate_server_payload(
            server_name, server_type, params, server_options
        )
        if errors:
            raise McpConfigInvalid(errors, server_name=str(server_name or ""))
        self._require_writable(server_name, creating=True)
        config = MCPServerConfig(
            name=server_name,
            type=server_type,
            params=_strip_secret_deletes(params),
            options=dict(server_options) if server_options else None,
        )
        self._user_store().upsert(server_name, _config_to_json_entry(config))
        self._disabled.discard(server_name)
        self.apply_to_spec()
        self._status.setdefault(server_name, _ServerStatus(at=_utcnow()))
        return {"ok": True, "server": self.get_server(server_name)}

    def update_server(
        self,
        server_name: str,
        server_type: str | None = None,
        params: Dict[str, Any] | None = None,
        server_options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self._require_writable(server_name, creating=False)
        existing = self.require_config(server_name)
        new_type = server_type if server_type is not None else existing.type
        base_params = existing.params or {}
        if server_type is not None and server_type != existing.type:
            # A transport change invalidates the old params wholesale.
            base_params = {}
        new_params = (
            _merge_secret_channels(dict(base_params), params)
            if params is not None
            else dict(base_params)
        )
        new_options = (
            dict(server_options)
            if server_options is not None
            else (dict(existing.options) if existing.options else None)
        )
        errors = _validate_server_payload(
            server_name, new_type, new_params, new_options
        )
        if errors:
            raise McpConfigInvalid(errors, server_name=server_name)
        config = MCPServerConfig(
            name=server_name, type=new_type, params=new_params, options=new_options
        )
        self._user_store().upsert(server_name, _config_to_json_entry(config))
        self.apply_to_spec()
        self._probes.pop(server_name, None)
        return {"ok": True, "server": self.get_server(server_name)}

    def delete_server(self, server_name: str) -> Dict[str, Any]:
        self._require_writable(server_name, creating=False)
        self._user_store().delete(server_name)
        self.apply_to_spec()
        self._probes.pop(server_name, None)
        self._status.pop(server_name, None)
        self.oauth.forget(server_name)
        return {"ok": True, "server_name": server_name}

    # -- probe (management-plane discovery) ------------------------------

    async def probe_server(self, server_name: str) -> Dict[str, Any]:
        """Connect once out-of-session and capture the full snapshot.

        Startup transitions flow through the same
        ``configured → starting → ready | failed`` contract session
        builds use, so a probe from the settings UI and a session build
        report identical status shapes.
        """
        config = self.require_config(server_name)
        enabled, disabled_reason = self._enablement(server_name)
        if not enabled:
            raise McpServerUnavailable(
                server_name,
                reason_code="disabled",
                message=f"MCP server is disabled ({disabled_reason})",
            )
        from omniagents.core.agents.mcp import (
            _instantiate_server,
            _resolve_setting_placeholders,
        )
        import copy as _copy

        settings = dict(self._settings_provider())
        resolved = _resolve_setting_placeholders(
            _copy.deepcopy(config.params), settings
        )
        if not isinstance(resolved, dict):
            resolved = {}
        auth = self.auth_resolver(config)
        if auth is not None:
            resolved = dict(resolved)
            resolved["auth"] = auth
        await self.record_transition(server_name, STATUS_STARTING, origin="probe")
        server = _instantiate_server(config, resolved)
        try:
            await asyncio.wait_for(server.connect(), timeout=self._probe_timeout)
            snapshot = await asyncio.wait_for(
                self._collect_probe(server), timeout=self._probe_timeout
            )
        except BaseException as exc:
            try:
                await server.cleanup()
            except BaseException:
                pass
            if is_external_cancellation(exc):
                raise
            reason_code, reason = await self.record_failure(
                server_name, exc, origin="probe"
            )
            raise McpServerUnavailable(
                server_name, reason_code=reason_code, message=reason
            ) from exc
        try:
            await server.cleanup()
        except Exception:
            pass
        self._probes[server_name] = snapshot
        await self.record_transition(server_name, STATUS_READY, origin="probe")
        return self.get_server(server_name)

    async def _collect_probe(self, server: Any) -> _ProbeSnapshot:
        snapshot = _ProbeSnapshot(captured_at=_utcnow())
        init_result = getattr(server, "server_initialize_result", None)
        if init_result is not None:
            snapshot.server_info = _dump_model(getattr(init_result, "serverInfo", None))
            snapshot.capabilities = _dump_model(
                getattr(init_result, "capabilities", None)
            )
        capabilities = snapshot.capabilities or {}
        session = getattr(server, "session", None)
        try:
            listed = await server.list_tools()
            tools = listed if isinstance(listed, list) else getattr(listed, "tools", [])
            snapshot.tools = _dump_model_list(tools)
            snapshot.supported["tools"] = True
        except Exception:
            snapshot.supported["tools"] = False
        if session is not None and capabilities.get("resources") is not None:
            try:
                listed = await session.list_resources()
                snapshot.resources = _dump_model_list(getattr(listed, "resources", []))
                snapshot.supported["resources"] = True
            except Exception:
                snapshot.supported["resources"] = False
            try:
                listed = await session.list_resource_templates()
                snapshot.resource_templates = _dump_model_list(
                    getattr(listed, "resourceTemplates", [])
                )
                snapshot.supported["resource_templates"] = True
            except Exception:
                snapshot.supported["resource_templates"] = False
        else:
            snapshot.supported["resources"] = False
            snapshot.supported["resource_templates"] = False
        if session is not None and capabilities.get("prompts") is not None:
            try:
                listed = await session.list_prompts()
                snapshot.prompts = _dump_model_list(getattr(listed, "prompts", []))
                snapshot.supported["prompts"] = True
            except Exception:
                snapshot.supported["prompts"] = False
        else:
            snapshot.supported["prompts"] = False
        return snapshot
