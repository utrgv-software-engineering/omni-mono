"""Layered configuration engine behind the GUI config RPCs.

Models the layered project-manifest configuration the loader actually
applies (:func:`omniagents.core.project.runtime.load_project`), so GUI
settings surfaces can read the *effective* configuration with full
provenance, dry-run validate changes, and write them back atomically.

Layers, lowest to highest precedence — identical to the loader's merge
order (dicts merge recursively, lists and scalars replace):

1. ``defaults`` — framework built-in defaults (the field registry).
2. ``project`` — the base ``project.yml`` manifest. Read-only through
   this API: for an installed product it is packaged/read-only by
   design.
3. ``user`` — ``<product-config>/project.d/*.yml`` overlays in lexical
   order. This is the ONE writable layer: the API owns a single
   ``project.d/90-gui.yml`` overlay file and never edits other files.
4. ``managed`` — overlays named by ``OMNIAGENTS_PROJECT_OVERLAYS``
   (operator/enterprise policy, e.g. a container image selecting its
   security overlay). Applied after the user layer, so a managed value
   makes the field read-only for this API.
5. ``environment`` — process environment variables that override config
   keys at read time (e.g. ``OMNIAGENTS_LEGACY_QUERY_AUTH``). Highest
   precedence and read-only.

The API exposes only the fields registered in :data:`CONFIG_FIELDS` —
never the raw merged document — so secret handling is tractable: a
field marked ``secret`` NEVER has its value included in any response
(effective value, per-layer provenance, validation errors, or write
results); only ``is_set`` booleans are reported.

Writes are atomic: every update in a batch is validated first and any
error aborts the whole batch with structured field errors; the overlay
file itself is replaced via ``os.replace`` so a failed write never
leaves a partial document behind.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

__all__ = [
    "CONFIG_FIELDS",
    "CONFIG_FIELDS_BY_KEY",
    "GUI_OVERLAY_FILENAME",
    "ConfigField",
    "LayeredConfigStore",
    "RELOAD_HOT",
    "RELOAD_RESTART",
    "RELOAD_SESSION",
]

#: Filename of the single user-layer overlay file this API owns. The
#: ``90-`` prefix places GUI-written values after conventionally-numbered
#: hand-written overlays in the user layer's lexical merge order.
GUI_OVERLAY_FILENAME = "90-gui.yml"

# Reload classification: how a written value reaches a running server.
RELOAD_HOT = "hot"  # re-read from config on every use; applies immediately
RELOAD_SESSION = "session"  # applies on the next agent build (next run)
RELOAD_RESTART = "restart"  # read once at process boot; needs a server restart

_LAYER_DEFAULTS = "defaults"
_LAYER_PROJECT = "project"
_LAYER_USER = "user"
_LAYER_MANAGED = "managed"
_LAYER_ENVIRONMENT = "environment"

_TRUTHY_ENV = ("1", "true", "yes", "on")


class _Unset:
    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


_UNSET = _Unset()


@dataclass(frozen=True)
class ConfigField:
    """One registered configuration field (a dot path into the manifest)."""

    key: str
    type: str  # "string" | "boolean" | "integer" | "string_list"
    label: str
    description: str
    default: Any = _UNSET
    secret: bool = False
    structural_read_only: bool = False
    allowed_values: Optional[Tuple[str, ...]] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    reload: str = RELOAD_RESTART
    env_var: Optional[str] = None


CONFIG_FIELDS: Tuple[ConfigField, ...] = (
    ConfigField(
        key="product.name",
        type="string",
        label="Product name",
        description=(
            "Distribution name seeding the whole product identity (slug, "
            "config dir, env prefix). Sessions and registries scope by it, "
            "so it cannot be changed through this API."
        ),
        default="omniagents",
        structural_read_only=True,
    ),
    ConfigField(
        key="agents.entrypoint",
        type="string",
        label="Agent entrypoint",
        description="Which agent key `serve` runs (agents/<key>/agent.yml).",
    ),
    ConfigField(
        key="agents.voice_entrypoint",
        type="string",
        label="Voice agent entrypoint",
        description="Agent key serving voice sessions; defaults to the entrypoint.",
    ),
    ConfigField(
        key="allow_user_mcp_servers",
        type="boolean",
        label="Allow user MCP servers",
        description="Load user-supplied mcp.json server configurations.",
        default=False,
    ),
    ConfigField(
        key="security.safety_mode",
        type="string",
        label="Safety mode",
        description=(
            "'enforced' forces SafeAgent approval wiring on every agent; "
            "'recommended' respects per-agent config; 'off' disables it."
        ),
        default="recommended",
        allowed_values=("recommended", "enforced", "off"),
        reload=RELOAD_SESSION,
    ),
    ConfigField(
        key="security.blocked_tools",
        type="string_list",
        label="Blocked tools",
        description="Tool names removed from every agent at load time.",
        default=(),
    ),
    ConfigField(
        key="security.blocked_tool_patterns",
        type="string_list",
        label="Blocked tool patterns",
        description="fnmatch patterns for tools removed from every agent.",
        default=(),
    ),
    ConfigField(
        key="security.mcp_require_allowlist",
        type="boolean",
        label="Require MCP allowlist",
        description="Only MCP servers on the allowlist may be used.",
        default=False,
    ),
    ConfigField(
        key="security.mcp_allowed_servers",
        type="string_list",
        label="Allowed MCP servers",
        description="MCP server names permitted when the allowlist is required.",
        default=(),
    ),
    ConfigField(
        key="security.legacy_query_auth",
        type="boolean",
        label="Legacy query-string auth",
        description=(
            "Accept the deprecated ?token= WebSocket credential transport. "
            "The OMNIAGENTS_LEGACY_QUERY_AUTH environment variable overrides "
            "this key."
        ),
        default=False,
        env_var="OMNIAGENTS_LEGACY_QUERY_AUTH",
    ),
    ConfigField(
        key="security.audit.enabled",
        type="boolean",
        label="Audit logging",
        description="Emit structured audit events.",
        default=False,
    ),
    ConfigField(
        key="security.audit.backend",
        type="string",
        label="Audit backend",
        description="Where audit events are written.",
        default="file",
        allowed_values=("file", "none"),
    ),
    ConfigField(
        key="security.audit.retention_days",
        type="integer",
        label="Audit retention (days)",
        description="Purge audit events older than this; 0 keeps everything.",
        default=0,
        minimum=0,
    ),
    ConfigField(
        key="security.audit.max_detail_chars",
        type="integer",
        label="Audit detail truncation",
        description="Truncation limit for tool input/output in audit events.",
        default=4096,
        minimum=0,
    ),
    ConfigField(
        key="security.sessions.retention_days",
        type="integer",
        label="Session retention (days)",
        description="Purge stored sessions older than this; 0 keeps everything.",
        default=0,
        minimum=0,
    ),
    ConfigField(
        key="security.providers.auth.type",
        type="string",
        label="Auth provider",
        description="Connection authentication provider (none/token/oidc/...).",
        default="none",
    ),
    ConfigField(
        key="security.providers.auth.token",
        type="string",
        label="Auth token",
        description=(
            "Shared bearer token for the token auth provider. Write-only: "
            "the value is never returned by this API."
        ),
        secret=True,
    ),
    ConfigField(
        key="approvals.reviewer",
        type="string",
        label="Approval reviewer",
        description=(
            "Who reviews tool-approval requests by default: 'user' prompts "
            "the human; 'auto' routes them to the guardian reviewer, which "
            "approves or denies against the shipped risk policy. Sessions "
            "may override per conversation."
        ),
        default="user",
        allowed_values=("user", "auto"),
        reload=RELOAD_HOT,
    ),
    ConfigField(
        key="approvals.sandbox_auto_approve",
        type="string",
        label="Sandbox auto-approval",
        description=(
            "When a session's execution environment is a genuine containment "
            "boundary (it carries the 'contained' capability — docker, the "
            "process sandbox, cloud micro-VMs; never the host or unix_local), "
            "auto-approve tool calls without prompting: "
            "'no_network' only when the environment also lacks the network "
            "capability; 'always' for every contained environment; 'never' "
            "disables the tier. Uncontained environments always prompt or "
            "route to the reviewer."
        ),
        default="no_network",
        allowed_values=("never", "no_network", "always"),
        reload=RELOAD_HOT,
    ),
    ConfigField(
        key="approvals.guardian_model",
        type="string",
        label="Guardian model",
        description=(
            "Model reference powering the guardian reviewer. Empty resolves "
            "through the utility-agent preference order (session model, then "
            "the agent's configured model)."
        ),
        default="",
        reload=RELOAD_HOT,
    ),
    ConfigField(
        key="workflow.completion_reviewer",
        type="string",
        label="Completion reviewer",
        description=(
            "Who reviews plan-step completions that carry exit criteria: "
            "'guardian' routes them to the one-shot completion reviewer, "
            "which accepts or rejects against the shipped policy; 'off' "
            "waives all reviews. Steps without exit criteria are never "
            "reviewed. Sessions may override per conversation."
        ),
        default="guardian",
        allowed_values=("guardian", "off"),
        reload=RELOAD_HOT,
    ),
    ConfigField(
        key="workflow.guardian_model",
        type="string",
        label="Completion reviewer model",
        description=(
            "Model reference powering the plan-completion reviewer. Empty "
            "resolves through the utility-agent preference order (session "
            "model, then the agent's configured model)."
        ),
        default="",
        reload=RELOAD_HOT,
    ),
    ConfigField(
        key="tracing.provider",
        type="string",
        label="Tracing provider",
        description="Tracing backend; the framework defaults to 'studio'.",
        default="studio",
    ),
    ConfigField(
        key="tracing.project",
        type="string",
        label="Tracing project",
        description="Tracing project name; derived from the product slug when unset.",
    ),
    ConfigField(
        key="tracing.name",
        type="string",
        label="Trace name",
        description="Trace display name; derived from the product slug when unset.",
    ),
)

CONFIG_FIELDS_BY_KEY: Dict[str, ConfigField] = {
    field.key: field for field in CONFIG_FIELDS
}

_TYPE_LABELS = {
    "string": "a string",
    "boolean": "a boolean",
    "integer": "an integer",
    "string_list": "a list of strings",
}


def _get_path(doc: Any, dotted: str) -> Tuple[bool, Any]:
    """Resolve *dotted* in a nested mapping; ``(found, value)``."""
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _delete_path(doc: Dict[str, Any], dotted: str) -> None:
    """Remove *dotted* and prune emptied parent mappings."""
    parts = dotted.split(".")
    stack: List[Dict[str, Any]] = [doc]
    node: Any = doc
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
        stack.append(node)
    if not isinstance(node, dict) or parts[-1] not in node:
        return
    del node[parts[-1]]
    for index in range(len(stack) - 1, 0, -1):
        if stack[index]:
            break
        del stack[index - 1][parts[index - 1]]


def _read_yaml_doc(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_env_value(field: ConfigField, raw: str) -> Any:
    if field.type == "boolean":
        return raw.strip().lower() in _TRUTHY_ENV
    if field.type == "integer":
        try:
            return int(raw.strip())
        except ValueError:
            return raw
    return raw


def _valid_type(field: ConfigField, value: Any) -> bool:
    if field.type == "string":
        return isinstance(value, str)
    if field.type == "boolean":
        return isinstance(value, bool)
    if field.type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field.type == "string_list":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False  # pragma: no cover - registry types are closed


@dataclass
class _LayerEntry:
    layer: str
    source: Optional[str]
    value: Any


def _inherited_model_control(key: str) -> bool:
    if key not in {"approvals.guardian_model", "workflow.guardian_model"}:
        return False
    from omniagents.core.models import model_store

    store = model_store()
    return store is not None and store.single_provider


class LayeredConfigStore:
    """Reads, validates, and writes the layered project configuration.

    ``manifest_path`` is the base ``project.yml`` (``None`` when the
    process serves a bare agent with no project manifest — the project
    layer is then simply absent and user overlays resolve through the
    installed product identity).
    """

    def __init__(self, manifest_path: Optional[Path] = None) -> None:
        self.manifest_path = (
            Path(manifest_path).resolve() if manifest_path is not None else None
        )

    # -- source resolution ---------------------------------------------

    def _config_dir_name(self) -> str:
        """The product config-dir name scoping the user overlay layer.

        Mirrors the loader: the BASE manifest's product identity (with
        the directory-name fallback) decides where overlays are found;
        without a manifest the installed product identity applies.
        """
        if self.manifest_path is not None and self.manifest_path.is_file():
            from omniagents.core.project.runtime import manifest_product

            proj = _read_yaml_doc(self.manifest_path)
            return manifest_product(proj, self.manifest_path.parent).config_dir_name
        from omniagents.core.product import product_info

        return product_info().config_dir_name

    def _user_overlay_paths(self) -> List[Path]:
        from omniagents.core.project.runtime import user_overlay_paths

        return user_overlay_paths(self._config_dir_name())

    def _managed_overlay_paths(self) -> List[Path]:
        from omniagents.core.project.runtime import managed_overlay_paths

        return [path for path in managed_overlay_paths() if path.is_file()]

    def user_write_target(self) -> Path:
        """The API-owned user-layer overlay file (may not exist yet)."""
        from omniagents.core.config.dirs import product_config_dir

        return (
            product_config_dir(self._config_dir_name())
            / "project.d"
            / GUI_OVERLAY_FILENAME
        )

    # -- provenance -----------------------------------------------------

    def _layer_entries(self, field: ConfigField) -> List[_LayerEntry]:
        """Every layer entry that sets *field*, lowest precedence first."""
        entries: List[_LayerEntry] = []
        if not isinstance(field.default, _Unset):
            default = field.default
            if isinstance(default, tuple):
                default = list(default)
            entries.append(_LayerEntry(_LAYER_DEFAULTS, None, default))
        if self.manifest_path is not None and self.manifest_path.is_file():
            found, value = _get_path(_read_yaml_doc(self.manifest_path), field.key)
            if found:
                entries.append(
                    _LayerEntry(_LAYER_PROJECT, str(self.manifest_path), value)
                )
        for path in self._user_overlay_paths():
            found, value = _get_path(_read_yaml_doc(path), field.key)
            if found:
                entries.append(_LayerEntry(_LAYER_USER, str(path), value))
        for path in self._managed_overlay_paths():
            found, value = _get_path(_read_yaml_doc(path), field.key)
            if found:
                entries.append(_LayerEntry(_LAYER_MANAGED, str(path), value))
        if field.env_var is not None:
            raw = os.environ.get(field.env_var)
            if raw is not None:
                entries.append(
                    _LayerEntry(
                        _LAYER_ENVIRONMENT,
                        field.env_var,
                        _parse_env_value(field, raw),
                    )
                )
        return entries

    @staticmethod
    def _read_only_reason(
        field: ConfigField, entries: List[_LayerEntry]
    ) -> Optional[str]:
        """Why a user-layer write could not take effect, or ``None``."""
        if field.structural_read_only:
            return "structural"
        reason = None
        for entry in entries:
            if entry.layer == _LAYER_MANAGED:
                reason = "managed_policy"
            elif entry.layer == _LAYER_ENVIRONMENT:
                reason = "environment"
        return reason

    def _field_payload(
        self, field: ConfigField, entries: List[_LayerEntry]
    ) -> Dict[str, Any]:
        """The wire descriptor for one field. Secret values NEVER appear."""
        read_only_reason = self._read_only_reason(field, entries)
        payload: Dict[str, Any] = {
            "key": field.key,
            "type": field.type,
            "label": field.label,
            "description": field.description,
            "secret": field.secret,
            "reload": field.reload,
            "read_only": read_only_reason is not None,
            "read_only_reason": read_only_reason,
            "is_set": bool(entries),
            "effective_layer": entries[-1].layer if entries else None,
            "layers": [],
        }
        if field.allowed_values is not None:
            payload["allowed_values"] = list(field.allowed_values)
        if field.minimum is not None:
            payload["minimum"] = field.minimum
        if field.maximum is not None:
            payload["maximum"] = field.maximum
        for entry in entries:
            layer_payload: Dict[str, Any] = {
                "layer": entry.layer,
                "source": entry.source,
                "is_set": True,
            }
            if not field.secret:
                layer_payload["value"] = entry.value
            payload["layers"].append(layer_payload)
        if not field.secret:
            payload["value"] = entries[-1].value if entries else None
        return payload

    def describe(self) -> Dict[str, Any]:
        """The full effective-configuration document for ``get_config``."""
        manifest = (
            str(self.manifest_path)
            if self.manifest_path is not None and self.manifest_path.is_file()
            else None
        )
        layers: List[Dict[str, Any]] = [
            {"name": _LAYER_DEFAULTS, "writable": False, "sources": []},
            {
                "name": _LAYER_PROJECT,
                "writable": False,
                "sources": [manifest] if manifest else [],
            },
            {
                "name": _LAYER_USER,
                "writable": True,
                "sources": [str(path) for path in self._user_overlay_paths()],
                "write_target": str(self.user_write_target()),
            },
            {
                "name": _LAYER_MANAGED,
                "writable": False,
                "sources": [str(path) for path in self._managed_overlay_paths()],
            },
            {"name": _LAYER_ENVIRONMENT, "writable": False, "sources": []},
        ]
        fields = [
            self._field_payload(field, self._layer_entries(field))
            for field in CONFIG_FIELDS
            if not _inherited_model_control(field.key)
        ]
        return {"layers": layers, "fields": fields}

    # -- validation -----------------------------------------------------

    def validate_updates(
        self, updates: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Validate a batch of updates without writing anything.

        Returns ``(errors, normalized)`` where *normalized* maps each
        valid key to its new value (or :data:`_UNSET` for a clear).
        Error payloads never echo submitted values, so a mistyped secret
        cannot leak back through a validation response.
        """
        errors: List[Dict[str, Any]] = []
        normalized: Dict[str, Any] = {}
        for key, value in updates.items():
            if _inherited_model_control(key):
                errors.append(
                    {
                        "key": key,
                        "code": "read_only",
                        "message": "Reviewers inherit the conversation model in this product.",
                    }
                )
                continue
            field = CONFIG_FIELDS_BY_KEY.get(key)
            if field is None:
                errors.append(
                    {
                        "key": key,
                        "code": "unknown_key",
                        "message": f"'{key}' is not a configurable setting",
                    }
                )
                continue
            reason = self._read_only_reason(field, self._layer_entries(field))
            if reason is not None:
                explanations = {
                    "structural": "it is part of the product identity",
                    "managed_policy": "a managed policy overlay sets it",
                    "environment": "an environment variable overrides it",
                }
                errors.append(
                    {
                        "key": key,
                        "code": "read_only",
                        "message": (f"'{key}' is read-only: {explanations[reason]}"),
                        "reason": reason,
                    }
                )
                continue
            if value is None:
                normalized[key] = _UNSET
                continue
            if not _valid_type(field, value):
                errors.append(
                    {
                        "key": key,
                        "code": "invalid_type",
                        "message": (
                            f"'{key}' must be {_TYPE_LABELS[field.type]} "
                            "(or null to clear the user-layer value)"
                        ),
                        "expected_type": field.type,
                    }
                )
                continue
            if field.allowed_values is not None and value not in field.allowed_values:
                errors.append(
                    {
                        "key": key,
                        "code": "invalid_value",
                        "message": (
                            f"'{key}' must be one of: "
                            + ", ".join(field.allowed_values)
                        ),
                        "allowed_values": list(field.allowed_values),
                    }
                )
                continue
            if (field.minimum is not None and value < field.minimum) or (
                field.maximum is not None and value > field.maximum
            ):
                error: Dict[str, Any] = {
                    "key": key,
                    "code": "out_of_range",
                    "message": f"'{key}' is out of range",
                }
                if field.minimum is not None:
                    error["minimum"] = field.minimum
                if field.maximum is not None:
                    error["maximum"] = field.maximum
                errors.append(error)
                continue
            normalized[key] = value
        return errors, normalized

    @staticmethod
    def _reload_summary(keys: List[str]) -> Dict[str, List[str]]:
        summary: Dict[str, List[str]] = {
            RELOAD_HOT: [],
            RELOAD_SESSION: [],
            RELOAD_RESTART: [],
        }
        for key in keys:
            field = CONFIG_FIELDS_BY_KEY[key]
            summary[field.reload].append(key)
        return summary

    def validate(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Dry-run validation result for ``validate_config``."""
        errors, normalized = self.validate_updates(updates)
        return {
            "valid": not errors,
            "errors": errors,
            "reload": self._reload_summary(sorted(normalized)),
        }

    # -- writes ---------------------------------------------------------

    def write(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically apply a batch of updates to the user overlay file.

        All-or-nothing: any validation error aborts the whole batch
        before anything is written, and the overlay file itself is
        replaced atomically, so a failure never leaves partial state.
        """
        errors, normalized = self.validate_updates(updates)
        if errors:
            return {
                "ok": False,
                "errors": errors,
                "written": [],
                "cleared": [],
                "reload": self._reload_summary([]),
                "restart_required": False,
                "fields": [],
            }

        target = self.user_write_target()
        doc = _read_yaml_doc(target) if target.is_file() else {}
        written: List[str] = []
        cleared: List[str] = []
        for key in sorted(normalized):
            value = normalized[key]
            if isinstance(value, _Unset):
                _delete_path(doc, key)
                cleared.append(key)
            else:
                _set_path(doc, key, value)
                written.append(key)
        self._write_overlay(target, doc)

        reload_summary = self._reload_summary(written + cleared)
        touched_fields = [
            self._field_payload(
                CONFIG_FIELDS_BY_KEY[key],
                self._layer_entries(CONFIG_FIELDS_BY_KEY[key]),
            )
            for key in sorted(normalized)
        ]
        return {
            "ok": True,
            "errors": [],
            "written": written,
            "cleared": cleared,
            "reload": reload_summary,
            "restart_required": bool(reload_summary[RELOAD_RESTART]),
            "fields": touched_fields,
        }

    @staticmethod
    def _write_overlay(target: Path, doc: Dict[str, Any]) -> None:
        """Replace *target* atomically with restrictive permissions."""
        target.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        tmp = Path(tmp_name)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def effective_value(self, key: str) -> Tuple[bool, Any]:
        """``(is_set, value)`` for one registered field's effective value.

        Internal helper for hot-application of written values; NOT a
        read path for clients (it does not redact secrets).
        """
        field = CONFIG_FIELDS_BY_KEY[key]
        entries = self._layer_entries(field)
        if not entries:
            return False, None
        return True, entries[-1].value
