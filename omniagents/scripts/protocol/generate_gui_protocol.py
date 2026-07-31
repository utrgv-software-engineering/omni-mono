import argparse
import black
import hashlib
import json
from pathlib import Path
from runpy import run_path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
_CATALOG = run_path(str(ROOT / "omniagents/rpc/protocol_catalog.py"))
OPERATIONS = _CATALOG["OPERATIONS"]
CORRELATED_NOTIFICATIONS = _CATALOG["CORRELATED_NOTIFICATIONS"]
OPENRPC_PATH = ROOT / "protocol/openrpc/omniagents-gui-v1.json"
SCHEMA_PATH = ROOT / "protocol/openrpc/schemas/gui-v1.schema.json"
PYTHON_PATH = ROOT / "omniagents/rpc/generated/gui_v1/models.py"
PYTHON_SCHEMA_PATH = ROOT / "omniagents/rpc/generated/gui_v1/schema_models.py"
PYTHON_INIT_PATH = ROOT / "omniagents/rpc/generated/gui_v1/__init__.py"
TYPESCRIPT_PATH = ROOT / "omniagents/backends/web/ui/src/protocol/generated/gui-v1.ts"
INK_TYPESCRIPT_PATH = (
    ROOT / "omniagents/backends/ink/tui/src/protocol/generated/gui-v1.ts"
)
TYPESCRIPT_SCHEMA_PATH = (
    ROOT / "omniagents/backends/web/ui/src/protocol/generated/schema-types.ts"
)
MANIFEST_PATH = ROOT / "protocol/openrpc/manifest.json"
INVENTORY_PATH = ROOT / "protocol/openrpc/inventory.json"
GENERATOR_VERSION = "1"
PYTHON_GENERATOR_VERSION = "0.71.0"
TYPESCRIPT_GENERATOR_VERSION = "15.0.4"

REQUIRED_FIELDS = {
    "initialize": ("protocol_version", "identity", "platform", "capabilities"),
    "start_run": ("prompt",),
    "stop_run": ("run_id",),
    "send_user_message": ("run_id", "content"),
    "get_session_history": ("session_id",),
    "archive_session": ("session_id",),
    "delete_session": ("session_id",),
    "tool_approval_response": ("call_id", "decision"),
    "mcp_approval_response": ("request_id", "decision"),
    "client_functions": ("functions",),
    "client_response": ("request_id", "ok"),
    "server_call": ("function",),
    "enqueue_message": ("session_id", "content"),
    "list_queue": ("session_id",),
    "cancel_queued_message": ("session_id", "item_id"),
    "fork_session": ("session_id",),
    "set_session_hold": ("session_id", "hold"),
    "queue_status": ("session_id",),
    "enqueue_notification": ("session_id", "content"),
    "resume_session": ("session_id",),
    "ack_events": ("session_id", "stream_id", "seq"),
    "get_model": ("model",),
    "set_session_model": ("session_id", "model"),
    "set_session_reasoning": ("session_id", "effort"),
    "set_voice_model": ("model",),
    "update_session_variables": ("session_id", "updates"),
    "set_client_status": ("text",),
    "disable_tool": ("tool_name",),
    "disable_mcp_server": ("server_name",),
    "report_incident": ("session_id", "description"),
    "export_session": ("session_id",),
    "validate_config": ("updates",),
    "write_config": ("updates",),
    "account_login_start": ("provider", "mode"),
    "account_login_complete": ("login_id",),
    "account_login_cancel": ("login_id",),
    "account_logout": ("provider",),
    "account_refresh": ("provider",),
    "account_select": ("provider",),
    "account_changed": ("provider", "reason"),
    "fs_watch": ("session_id", "path"),
    "fs_unwatch": ("session_id", "watch_id"),
    "fs_list": ("session_id", "path"),
    "fs_stat": ("session_id", "path"),
    "fs_download_open": ("session_id", "path"),
    "fs_download_read": ("session_id", "transfer_id"),
    "fs_download_close": ("session_id", "transfer_id"),
    "fs_upload_open": ("session_id", "path", "size"),
    "fs_upload_chunk": ("session_id", "transfer_id", "offset", "data"),
    "fs_upload_commit": ("session_id", "transfer_id"),
    "fs_upload_abort": ("session_id", "transfer_id"),
    "fs_events": ("session_id", "watch_id", "events"),
    "fs_rescan_required": ("session_id", "watch_id", "reason"),
    "fs_transfer_progress": (
        "session_id",
        "transfer_id",
        "direction",
        "transferred",
        "total",
    ),
    "git_list_repositories": ("session_id",),
    "git_status": ("session_id", "repo"),
    "git_diff": ("session_id", "repo"),
    "git_log": ("session_id", "repo"),
    "git_list_branches": ("session_id", "repo"),
    "git_list_worktrees": ("session_id", "repo"),
    "git_conflicts": ("session_id", "repo"),
    "git_stage": ("session_id", "repo"),
    "git_unstage": ("session_id", "repo"),
    "git_discard": ("session_id", "repo"),
    "git_commit": ("session_id", "repo", "message"),
    "git_checkout": ("session_id", "repo", "branch"),
    "git_reset": ("session_id", "repo"),
    "git_fetch": ("session_id", "repo"),
    "git_pull": ("session_id", "repo"),
    "git_push": ("session_id", "repo"),
    "git_operation_progress": (
        "session_id",
        "operation_id",
        "repo",
        "operation",
        "phase",
    ),
    "run_started": ("run_id", "session_id"),
    "run_status": ("run_id", "session_id", "status", "message"),
    "tool_called": ("run_id", "session_id", "tool", "input", "call_id"),
    "tool_result": ("run_id", "session_id", "tool", "output", "call_id"),
    "message_output": ("run_id", "session_id", "content"),
    "token": ("run_id", "session_id", "delta", "totals"),
    "run_end": ("run_id", "session_id", "end_reason"),
    "tool_approval_requested": ("call_id", "tool_name", "arguments"),
    "tool_approval_resolved": ("call_id",),
    "mcp_approval_requested": (
        "kind",
        "request_id",
        "server_label",
        "tool_name",
        "arguments",
    ),
    "mcp_approval_resolved": ("request_id",),
    "client_request": ("request_id", "function", "args"),
    "client_request_resolved": ("request_id",),
    "elicitation_response": ("elicitation_id", "action"),
    "elicitation_requested": ("elicitation_id", "kind", "message"),
    "elicitation_resolved": ("elicitation_id", "status"),
    "mcp_get_server": ("server_name",),
    "mcp_create_server": ("server_name", "type", "params"),
    "mcp_update_server": ("server_name",),
    "mcp_delete_server": ("server_name",),
    "mcp_auth_start": ("server_name",),
    "mcp_auth_complete": ("auth_id", "code"),
    "mcp_auth_cancel": ("auth_id",),
    "mcp_read_resource": ("server_name", "uri"),
    "mcp_call_tool": ("server_name", "tool_name", "session_id"),
    "mcp_get_prompt": ("server_name", "prompt_name"),
    "mcp_server_status_changed": ("server_name", "status"),
    "get_thread": ("thread_id",),
    "list_turns": ("thread_id",),
    "list_items": ("thread_id",),
    "get_item": ("thread_id", "item_id"),
    "queue_changed": ("session_id", "depth", "items"),
    "session_forked": ("session_id", "new_session_id"),
    "session_variables_changed": ("session_id", "changed", "variables"),
}

# Optional replay envelope stamped onto durable (replayable) server
# notifications: ``seq`` is the per-session monotonic sequence id and
# ``stream_id`` identifies the event-stream epoch. ``token`` is the one
# transient notification that never carries ``seq`` (usage totals are
# cumulative and ``run_end`` supersedes them), but it shares the schema
# fields for uniformity.
REPLAY_ENVELOPE_FIELDS = ("seq", "stream_id")

OPTIONAL_FIELDS = {
    "start_run": (
        "session_id",
        "variables",
        "context",
        "content",
        "workflow_name",
        "group_id",
        "safe_tool_overrides",
        "prompt_role",
    ),
    "list_sessions": ("limit", "offset"),
    "get_user_history": ("limit", "include_archived"),
    "tool_approval_response": ("always_approve", "rejection_message"),
    "mcp_approval_response": ("rejection_message",),
    "client_functions": ("version",),
    "client_response": ("result", "error"),
    "server_call": ("args", "session_id"),
    "enqueue_message": (
        "role",
        "trigger_run",
        "variables",
        "safe_tool_overrides",
        "source",
    ),
    "fork_session": ("new_session_id",),
    "enqueue_notification": ("source", "safe_tool_overrides"),
    "report_incident": ("disable_tools", "disable_mcp_servers"),
    "export_session": ("redact",),
    "message_output": ("is_final", "message_id"),
    "run_started": ("prompt", "prompt_role"),
    "run_status": ("attempt", "total", "next_delay"),
    "tool_result": ("metadata",),
    "token": (
        "turn",
        "response_id",
        "model",
        "model_ref",
        "max_input_tokens",
        "max_output_tokens",
        "truncation",
    ),
    "run_end": (
        "usage",
        "model",
        "model_ref",
        "max_input_tokens",
        "max_output_tokens",
        "truncation",
        "error",
    ),
    "tool_approval_requested": ("metadata", "session_id", "run_id"),
    "tool_approval_resolved": ("session_id", "reason"),
    "mcp_approval_requested": ("session_id", "run_id"),
    "mcp_approval_resolved": ("session_id", "reason"),
    "client_request": ("session_id", "idempotency_key", "run_id"),
    "client_request_resolved": ("session_id", "reason"),
    "elicitation_response": ("value", "reason"),
    "elicitation_requested": (
        "title",
        "input_schema",
        "options",
        "url",
        "session_id",
        "run_id",
        "item_id",
        "source",
        "timeout_ms",
        "expires_at",
        "persist_response",
    ),
    "elicitation_resolved": ("session_id", "run_id", "action", "value", "reason"),
    "resume_session": ("stream_id", "after_seq"),
    "list_models": ("include_hidden", "modality", "session_id"),
    "account_login_start": ("api_key", "redirect_uri"),
    "account_login_complete": ("code",),
    "account_usage": ("provider",),
    "account_changed": ("account",),
    "mcp_list_servers": ("session_id",),
    "mcp_get_server": ("refresh",),
    "mcp_create_server": ("server_options",),
    "mcp_update_server": ("type", "params", "server_options"),
    "mcp_reload_server": ("server_name",),
    "mcp_auth_start": ("redirect_uri", "session_id"),
    "mcp_read_resource": ("session_id",),
    "mcp_call_tool": ("args",),
    "mcp_get_prompt": ("args", "session_id"),
    "mcp_server_status_changed": (
        "previous_status",
        "reason_code",
        "reason",
        "auth_state",
        "at",
    ),
    "fs_watch": ("recursive", "poll_interval_ms"),
    "fs_list": ("recursive",),
    "fs_download_read": ("offset", "length"),
    "fs_upload_open": ("sha256", "overwrite"),
    "list_turns": ("limit", "cursor", "order"),
    "list_items": ("turn_id", "kinds", "limit", "cursor", "order"),
    "git_list_repositories": ("path", "max_depth"),
    "git_status": ("include_untracked", "include_ignored", "paths"),
    "git_diff": ("mode", "paths", "context_lines", "from_rev", "to_rev"),
    "git_log": ("rev", "max_count", "skip", "paths"),
    "git_list_branches": ("include_remote",),
    "git_conflicts": ("paths",),
    "git_stage": ("paths", "hunks", "context_lines", "mode"),
    "git_unstage": ("paths", "hunks", "context_lines"),
    "git_discard": ("paths", "hunks", "context_lines", "confirmation_token"),
    "git_commit": ("amend", "allow_empty", "author", "confirmation_token"),
    "git_checkout": (
        "create",
        "start_point",
        "detach",
        "discard_changes",
        "confirmation_token",
    ),
    "git_reset": ("mode", "rev", "paths", "confirmation_token"),
    "git_fetch": ("remote", "refspec", "prune"),
    "git_pull": ("remote", "refspec", "rebase"),
    "git_push": (
        "remote",
        "refspec",
        "force",
        "force_with_lease",
        "set_upstream",
        "confirmation_token",
    ),
    "git_operation_progress": ("detail",),
}

# Every server→client notification carries the optional replay envelope.
for _operation in OPERATIONS:
    if _operation.direction == "server_to_client":
        OPTIONAL_FIELDS[_operation.name] = (
            tuple(OPTIONAL_FIELDS.get(_operation.name, ())) + REPLAY_ENVELOPE_FIELDS
        )

ARRAY_FIELDS = {
    "disable_mcp_servers",
    "disable_tools",
    "events",
    "functions",
    "hunks",
    "items",
    "kinds",
    "options",
    "paths",
}
BOOLEAN_FIELDS = {
    "allow_empty",
    "always_approve",
    "amend",
    "create",
    "detach",
    "discard_changes",
    "force",
    "force_with_lease",
    "hold",
    "include_ignored",
    "include_remote",
    "include_untracked",
    "prune",
    "rebase",
    "set_upstream",
    "include_archived",
    "include_hidden",
    "is_final",
    "ok",
    "overwrite",
    "persist_response",
    "recursive",
    "redact",
    "refresh",
    "trigger_run",
}
INTEGER_FIELDS = {
    "after_seq",
    "attempt",
    "context_lines",
    "depth",
    "length",
    "limit",
    "max_count",
    "max_depth",
    "max_input_tokens",
    "max_output_tokens",
    "next_delay",
    "offset",
    "poll_interval_ms",
    "seq",
    "size",
    "skip",
    "timeout_ms",
    "total",
    "transferred",
    "turn",
    "version",
}
OBJECT_FIELDS = {
    "account",
    "args",
    "changed",
    "context",
    "delta",
    "detail",
    "error",
    "input_schema",
    "metadata",
    "params",
    "result",
    "safe_tool_overrides",
    "server_options",
    "totals",
    "updates",
    "usage",
    "value",
    "variables",
}

RESULT_TYPES = {
    "initialize": ("InitializeResult", "InitializeResult"),
    "start_run": ("dict", "{ run_id: string; session_id: string }"),
    "stop_run": ("bool", "boolean"),
    "send_user_message": ("None", "null"),
    "get_session_history": ("list[dict[str, Any]]", "Array<Record<string, unknown>>"),
    "list_sessions": ("list[dict[str, Any]]", "Array<Record<string, unknown>>"),
    "archive_session": ("bool", "boolean"),
    "delete_session": ("bool", "boolean"),
    "get_user_history": ("list[dict[str, Any]]", "Array<Record<string, unknown>>"),
    "get_agent_info": ("dict[str, Any]", "Record<string, unknown>"),
    "tool_approval_response": ("bool", "boolean"),
    "mcp_approval_response": ("bool", "boolean"),
    "client_functions": ("bool", "boolean"),
    "client_response": ("bool", "boolean"),
    "list_server_functions": ("list[dict[str, Any]]", "Array<Record<string, unknown>>"),
    "server_call": ("dict[str, Any]", "Record<string, unknown>"),
    "enqueue_message": ("dict[str, Any]", "Record<string, unknown>"),
    "list_queue": ("dict[str, Any]", "Record<string, unknown>"),
    "cancel_queued_message": ("dict[str, Any]", "Record<string, unknown>"),
    "resume_session": ("dict[str, Any]", "Record<string, unknown>"),
    "ack_events": ("dict[str, Any]", "Record<string, unknown>"),
    "fork_session": ("dict[str, Any]", "Record<string, unknown>"),
    "set_session_hold": ("dict[str, Any]", "Record<string, unknown>"),
    "queue_status": ("dict[str, Any]", "Record<string, unknown>"),
    "enqueue_notification": ("dict[str, Any]", "Record<string, unknown>"),
    "update_session_variables": ("dict[str, Any]", "Record<string, unknown>"),
    "set_client_status": ("None", "null"),
    "disable_tool": ("None", "null"),
    "disable_mcp_server": ("None", "null"),
    "report_incident": ("None", "null"),
    "export_session": ("dict[str, Any]", "Record<string, unknown>"),
    "get_config": ("dict[str, Any]", "Record<string, unknown>"),
    "validate_config": ("dict[str, Any]", "Record<string, unknown>"),
    "write_config": ("dict[str, Any]", "Record<string, unknown>"),
    "list_models": ("dict[str, Any]", "Record<string, unknown>"),
    "get_model": ("dict[str, Any]", "Record<string, unknown>"),
    "list_providers": ("dict[str, Any]", "Record<string, unknown>"),
    "set_session_model": ("dict[str, Any]", "Record<string, unknown>"),
    "set_session_reasoning": ("dict[str, Any]", "Record<string, unknown>"),
    "set_voice_model": ("dict[str, Any]", "Record<string, unknown>"),
    "account_status": ("dict[str, Any]", "Record<string, unknown>"),
    "account_login_start": ("dict[str, Any]", "Record<string, unknown>"),
    "account_login_complete": ("dict[str, Any]", "Record<string, unknown>"),
    "account_login_cancel": ("bool", "boolean"),
    "account_logout": ("dict[str, Any]", "Record<string, unknown>"),
    "account_refresh": ("dict[str, Any]", "Record<string, unknown>"),
    "account_usage": ("dict[str, Any]", "Record<string, unknown>"),
    "account_select": ("dict[str, Any]", "Record<string, unknown>"),
    "elicitation_response": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_list_servers": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_get_server": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_create_server": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_update_server": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_delete_server": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_reload_server": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_auth_start": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_auth_complete": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_auth_cancel": ("bool", "boolean"),
    "mcp_read_resource": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_call_tool": ("dict[str, Any]", "Record<string, unknown>"),
    "mcp_get_prompt": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_watch": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_unwatch": ("bool", "boolean"),
    "fs_list": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_stat": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_download_open": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_download_read": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_download_close": ("bool", "boolean"),
    "fs_upload_open": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_upload_chunk": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_upload_commit": ("dict[str, Any]", "Record<string, unknown>"),
    "fs_upload_abort": ("bool", "boolean"),
    "get_thread": ("dict[str, Any]", "Record<string, unknown>"),
    "list_turns": ("dict[str, Any]", "Record<string, unknown>"),
    "list_items": ("dict[str, Any]", "Record<string, unknown>"),
    "get_item": ("dict[str, Any]", "Record<string, unknown>"),
    "git_list_repositories": ("dict[str, Any]", "Record<string, unknown>"),
    "git_status": ("dict[str, Any]", "Record<string, unknown>"),
    "git_diff": ("dict[str, Any]", "Record<string, unknown>"),
    "git_log": ("dict[str, Any]", "Record<string, unknown>"),
    "git_list_branches": ("dict[str, Any]", "Record<string, unknown>"),
    "git_list_worktrees": ("dict[str, Any]", "Record<string, unknown>"),
    "git_conflicts": ("dict[str, Any]", "Record<string, unknown>"),
    "git_stage": ("dict[str, Any]", "Record<string, unknown>"),
    "git_unstage": ("dict[str, Any]", "Record<string, unknown>"),
    "git_discard": ("dict[str, Any]", "Record<string, unknown>"),
    "git_commit": ("dict[str, Any]", "Record<string, unknown>"),
    "git_checkout": ("dict[str, Any]", "Record<string, unknown>"),
    "git_reset": ("dict[str, Any]", "Record<string, unknown>"),
    "git_fetch": ("dict[str, Any]", "Record<string, unknown>"),
    "git_pull": ("dict[str, Any]", "Record<string, unknown>"),
    "git_push": ("dict[str, Any]", "Record<string, unknown>"),
}

IDENTITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "version"],
    "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
}
PLATFORM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["os", "arch"],
    "properties": {"os": {"type": "string"}, "arch": {"type": "string"}},
}
CAPABILITIES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "realtime",
        "mcp_apps",
        "client_functions",
        "approvals",
        "artifacts",
        "replay",
        "terminal",
        "experimental_operations",
        "disabled_notifications",
    ],
    "properties": {
        "realtime": {"type": "boolean"},
        "mcp_apps": {"type": "boolean"},
        "client_functions": {"type": "boolean"},
        "approvals": {"type": "boolean"},
        "artifacts": {"type": "boolean"},
        "replay": {"type": "boolean"},
        "terminal": {"type": "boolean"},
        "experimental_operations": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "disabled_notifications": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
    },
}
INITIALIZE_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["protocol_version", "identity", "platform", "capabilities"],
    "properties": {
        "protocol_version": {"type": "string"},
        "identity": {"$ref": "#/$defs/identity"},
        "platform": {"$ref": "#/$defs/platform"},
        "capabilities": {"$ref": "#/$defs/capabilities"},
    },
}

PROTOCOL_ERRORS = {
    "InitializationRequired": {
        "code": -32010,
        "message": "Initialization required",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "state"],
            "properties": {
                "kind": {"const": "initialization_required"},
                "state": {"type": "string"},
            },
        },
    },
    "AlreadyInitialized": {
        "code": -32011,
        "message": "Already initialized",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "state"],
            "properties": {
                "kind": {"const": "invalid_initialization_state"},
                "state": {"type": "string"},
            },
        },
    },
    "ProtocolVersionMismatch": {
        "code": -32012,
        "message": "Protocol version mismatch",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "client_version", "server_version"],
            "properties": {
                "kind": {"const": "protocol_version_mismatch"},
                "client_version": {"type": "string"},
                "server_version": {"type": "string"},
            },
        },
    },
    "CapabilityMismatch": {
        "code": -32013,
        "message": "Capability mismatch",
        "data": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "disabled"],
                    "properties": {
                        "kind": {"const": "mandatory_capability_disabled"},
                        "disabled": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "unsupported_capabilities"],
                    "properties": {
                        "kind": {"const": "capability_not_negotiated"},
                        "unsupported_capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "capability"],
                    "properties": {
                        "kind": {"const": "capability_not_negotiated"},
                        "capability": {"type": "string"},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind"],
                    "properties": {"kind": {"const": "invalid_capabilities"}},
                },
            ],
        },
    },
    "ResyncRequired": {
        "code": -32030,
        "message": "Resync required",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "kind",
                "session_id",
                "stream_id",
                "retained_first_seq",
                "retained_last_seq",
            ],
            "properties": {
                "kind": {"const": "resync_required"},
                "session_id": {"type": "string"},
                "stream_id": {"type": "string"},
                "retained_first_seq": {"type": "integer", "minimum": 0},
                "retained_last_seq": {"type": "integer", "minimum": 0},
            },
        },
    },
    "AccountCapabilityUnsupported": {
        "code": -32040,
        "message": "Account capability unsupported",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "provider", "operation"],
            "properties": {
                "kind": {"const": "account_capability_unsupported"},
                "provider": {"type": "string"},
                "operation": {"type": "string"},
                "supported": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "AccountStateInvalid": {
        "code": -32041,
        "message": "Account state invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {
                "kind": {"const": "account_state_invalid"},
                "provider": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "AccountAuthFailed": {
        "code": -32042,
        "message": "Account authentication failed",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "provider"],
            "properties": {
                "kind": {"const": "account_auth_failed"},
                "provider": {"type": "string"},
                "status": {"type": ["integer", "null"]},
            },
        },
    },
    "ElicitationNotFound": {
        "code": -32050,
        "message": "Elicitation not found",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "elicitation_id"],
            "properties": {
                "kind": {"const": "elicitation_not_found"},
                "elicitation_id": {"type": "string"},
            },
        },
    },
    "ElicitationAlreadyResolved": {
        "code": -32051,
        "message": "Elicitation already resolved",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "elicitation_id", "status"],
            "properties": {
                "kind": {"const": "elicitation_already_resolved"},
                "elicitation_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    "ElicitationInvalidResponse": {
        "code": -32052,
        "message": "Elicitation response invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "elicitation_id", "errors"],
            "properties": {
                "kind": {"const": "elicitation_invalid_response"},
                "elicitation_id": {"type": "string"},
                "errors": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "FsPathInvalid": {
        "code": -32060,
        "message": "Filesystem path invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "fs_path_invalid"},
                "path": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "FsNotFound": {
        "code": -32061,
        "message": "File or directory not found",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "fs_not_found"},
                "path": {"type": "string"},
                "session_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "FsTransferInvalid": {
        "code": -32062,
        "message": "Filesystem transfer invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "fs_transfer_invalid"},
                "transfer_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "FsWatchInvalid": {
        "code": -32063,
        "message": "Filesystem watch invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "fs_watch_invalid"},
                "watch_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "McpServerNotFound": {
        "code": -32070,
        "message": "MCP server not found",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "server_name"],
            "properties": {
                "kind": {"const": "mcp_server_not_found"},
                "server_name": {"type": "string"},
            },
        },
    },
    "McpConfigInvalid": {
        "code": -32071,
        "message": "MCP server configuration is invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "errors"],
            "properties": {
                "kind": {"const": "mcp_config_invalid"},
                "server_name": {"type": "string"},
                "errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["code", "message"],
                        "properties": {
                            "code": {"type": "string"},
                            "field": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
        },
    },
    "McpServerReadOnly": {
        "code": -32072,
        "message": "MCP server is read-only",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "server_name", "reason"],
            "properties": {
                "kind": {"const": "mcp_server_read_only"},
                "server_name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "McpOperationUnsupported": {
        "code": -32073,
        "message": "MCP operation unsupported",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "server_name", "operation"],
            "properties": {
                "kind": {"const": "mcp_operation_unsupported"},
                "server_name": {"type": "string"},
                "operation": {"type": "string"},
            },
        },
    },
    "McpServerUnavailable": {
        "code": -32074,
        "message": "MCP server unavailable",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "server_name", "reason_code"],
            "properties": {
                "kind": {"const": "mcp_server_unavailable"},
                "server_name": {"type": "string"},
                "reason_code": {"type": "string"},
            },
        },
    },
    "McpAuthStateInvalid": {
        "code": -32075,
        "message": "MCP auth state invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {
                "kind": {"const": "mcp_auth_state_invalid"},
                "server_name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "McpAuthFailed": {
        "code": -32076,
        "message": "MCP authorization failed",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "server_name"],
            "properties": {
                "kind": {"const": "mcp_auth_failed"},
                "server_name": {"type": "string"},
                "status": {"type": ["integer", "null"]},
            },
        },
    },
    "ThreadNotFound": {
        "code": -32080,
        "message": "Unknown thread",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "thread_id"],
            "properties": {
                "kind": {"const": "thread_not_found"},
                "thread_id": {"type": "string"},
            },
        },
    },
    "ConversationItemNotFound": {
        "code": -32081,
        "message": "Unknown conversation item",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "thread_id", "item_id"],
            "properties": {
                "kind": {"const": "item_not_found"},
                "thread_id": {"type": "string"},
                "item_id": {"type": "string"},
            },
        },
    },
    "ConversationCursorInvalid": {
        "code": -32082,
        "message": "Malformed pagination cursor",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "cursor"],
            "properties": {
                "kind": {"const": "cursor_invalid"},
                "cursor": {"type": "string"},
            },
        },
    },
    "GitRepositoryNotFound": {
        "code": -32090,
        "message": "Git repository not found",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "git_repository_not_found"},
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "reason": {"type": "string"},
                "toplevel": {"type": "string"},
            },
        },
    },
    "GitPathInvalid": {
        "code": -32091,
        "message": "Git path invalid",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"const": "git_path_invalid"},
                "path": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "GitOperationFailed": {
        "code": -32092,
        "message": "Git operation failed",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "operation"],
            "properties": {
                "kind": {"const": "git_operation_failed"},
                "operation": {"type": "string"},
                "repo": {"type": "string"},
                "command": {"type": "string"},
                "exit_code": {"type": "integer"},
                "stderr": {"type": "string"},
                "reason": {"type": "string"},
                "rolled_back": {"type": "boolean"},
            },
        },
    },
    "GitConfirmationRequired": {
        "code": -32093,
        "message": "Git operation requires explicit confirmation",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "operation", "confirmation_token", "impact"],
            "properties": {
                "kind": {"const": "git_confirmation_required"},
                "operation": {"type": "string"},
                "repo": {"type": "string"},
                "confirmation_token": {"type": "string"},
                "impact": {"type": "object"},
                "reason": {"type": "string"},
            },
        },
    },
    "GitStateInvalid": {
        "code": -32094,
        "message": "Git repository state invalid for this operation",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {
                "kind": {"const": "git_state_invalid"},
                "reason": {"type": "string"},
                "repo": {"type": "string"},
                "operation": {"type": "string"},
                "path": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "change": {"type": "string"},
                "mode": {"type": "string"},
                "scope": {"type": "string"},
                "value": {"type": "string"},
                "supported": {"type": "array", "items": {"type": "string"}},
                "stale": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path", "hunk_id"],
                        "properties": {
                            "path": {"type": "string"},
                            "hunk_id": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    "GitCapabilityUnavailable": {
        "code": -32095,
        "message": "Git capability unavailable on this substrate",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {
                "kind": {"const": "git_capability_unavailable"},
                "operation": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    "TransportOverloaded": {
        "code": -32020,
        "message": "Transport overloaded",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resource", "unit", "limit", "retryable", "retry_after_ms"],
            "properties": {
                "resource": {"type": "string"},
                "unit": {"type": "string", "enum": ["messages", "bytes"]},
                "limit": {"type": "integer", "minimum": 1},
                "retryable": {"type": "boolean"},
                "retry_after_ms": {"type": ["integer", "null"], "minimum": 0},
            },
        },
    },
}


# The canonical conversation surface (tkt_a-QF2Oc4OAsD): requests that can
# raise the -32080..-32082 typed errors.
CONVERSATION_METHODS = frozenset({"get_thread", "list_turns", "list_items", "get_item"})


# The MCP management surface (tkt_kVK7F-iaL-OA): requests that can raise
# the -32070..-32076 typed MCP errors. Deliberately NOT "starts with
# mcp_" — mcp_approval_response predates this surface and keeps its
# original error contract.
MCP_MANAGEMENT_METHODS = frozenset(
    {
        "mcp_list_servers",
        "mcp_get_server",
        "mcp_create_server",
        "mcp_update_server",
        "mcp_delete_server",
        "mcp_reload_server",
        "mcp_auth_start",
        "mcp_auth_complete",
        "mcp_auth_cancel",
        "mcp_read_resource",
        "mcp_call_tool",
        "mcp_get_prompt",
    }
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _operation_schema(name: str) -> dict:
    return {
        "$ref": f"./schemas/gui-v1.schema.json#/$defs/{name}",
    }


def build_openrpc() -> dict:
    methods = []
    for operation in OPERATIONS:
        required_fields = REQUIRED_FIELDS.get(operation.name, ())
        optional_fields = OPTIONAL_FIELDS.get(operation.name, ())
        entry = {
            "name": operation.name,
            "summary": f"OmniAgents GUI protocol operation {operation.name}",
            "paramStructure": "by-name",
            "params": [
                {
                    "name": field,
                    "required": True,
                    "schema": {
                        "$ref": (
                            "./schemas/gui-v1.schema.json#/$defs/"
                            f"{operation.name}/properties/{field}"
                        )
                    },
                }
                for field in required_fields
            ]
            + [
                {
                    "name": field,
                    "required": False,
                    "schema": {
                        "$ref": (
                            "./schemas/gui-v1.schema.json#/$defs/"
                            f"{operation.name}/properties/{field}"
                        )
                    },
                }
                for field in optional_fields
            ],
            "result": {
                "name": f"{operation.name}_result",
                "schema": (
                    {
                        "$ref": (
                            "./schemas/gui-v1.schema.json#/$defs/initializeResult"
                            if operation.name == "initialize"
                            else "./schemas/gui-v1.schema.json#/$defs/result"
                        )
                    }
                    if operation.kind == "request"
                    else {"type": "null"}
                ),
            },
            "x-omni-params-schema": _operation_schema(operation.name),
            "x-omni-direction": operation.direction,
            "x-omni-kind": operation.kind,
            "x-omni-stability": operation.stability,
        }
        if operation.kind == "request":
            entry["errors"] = [
                {"$ref": "#/components/errors/InvalidRequest"},
                {"$ref": "#/components/errors/MethodNotFound"},
                {"$ref": "#/components/errors/InvalidParams"},
                {"$ref": "#/components/errors/InternalError"},
                {"$ref": "#/components/errors/TransportOverloaded"},
            ]
            if operation.name == "initialize":
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/AlreadyInitialized"},
                        {"$ref": "#/components/errors/ProtocolVersionMismatch"},
                        {"$ref": "#/components/errors/CapabilityMismatch"},
                    ]
                )
            else:
                entry["errors"].append(
                    {"$ref": "#/components/errors/InitializationRequired"}
                )
            if operation.name in ("resume_session", "ack_events"):
                entry["errors"].append({"$ref": "#/components/errors/ResyncRequired"})
            if operation.name == "elicitation_response":
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/ElicitationNotFound"},
                        {"$ref": "#/components/errors/ElicitationAlreadyResolved"},
                        {"$ref": "#/components/errors/ElicitationInvalidResponse"},
                    ]
                )
            if operation.name.startswith("account_"):
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/AccountCapabilityUnsupported"},
                        {"$ref": "#/components/errors/AccountStateInvalid"},
                        {"$ref": "#/components/errors/AccountAuthFailed"},
                    ]
                )
            if operation.name.startswith("fs_"):
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/FsPathInvalid"},
                        {"$ref": "#/components/errors/FsNotFound"},
                        {"$ref": "#/components/errors/FsTransferInvalid"},
                        {"$ref": "#/components/errors/FsWatchInvalid"},
                    ]
                )
            if operation.name in CONVERSATION_METHODS:
                entry["errors"].append({"$ref": "#/components/errors/ThreadNotFound"})
                if operation.name == "get_item":
                    entry["errors"].append(
                        {"$ref": "#/components/errors/ConversationItemNotFound"}
                    )
                if operation.name in ("list_turns", "list_items"):
                    entry["errors"].append(
                        {"$ref": "#/components/errors/ConversationCursorInvalid"}
                    )
            if operation.name.startswith("git_"):
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/GitRepositoryNotFound"},
                        {"$ref": "#/components/errors/GitPathInvalid"},
                        {"$ref": "#/components/errors/GitOperationFailed"},
                        {"$ref": "#/components/errors/GitConfirmationRequired"},
                        {"$ref": "#/components/errors/GitStateInvalid"},
                        {"$ref": "#/components/errors/GitCapabilityUnavailable"},
                    ]
                )
            if operation.name in MCP_MANAGEMENT_METHODS:
                entry["errors"].extend(
                    [
                        {"$ref": "#/components/errors/McpServerNotFound"},
                        {"$ref": "#/components/errors/McpConfigInvalid"},
                        {"$ref": "#/components/errors/McpServerReadOnly"},
                        {"$ref": "#/components/errors/McpOperationUnsupported"},
                        {"$ref": "#/components/errors/McpServerUnavailable"},
                        {"$ref": "#/components/errors/McpAuthStateInvalid"},
                        {"$ref": "#/components/errors/McpAuthFailed"},
                    ]
                )
        if operation.kind == "correlated_notification":
            entry["x-omni-correlation"] = dict(CORRELATED_NOTIFICATIONS[operation.name])
        methods.append(entry)
    return {
        "openrpc": "1.3.2",
        "info": {
            "title": "OmniAgents GUI Protocol",
            "version": "1.0.0",
            "description": "JSON-RPC 2.0 protocol for the main OmniAgents /ws endpoint.",
            "x-omni-compatibility": "additive-only-v1",
        },
        "methods": methods,
        "components": {
            "errors": {
                "InvalidRequest": {"code": -32600, "message": "Invalid Request"},
                "MethodNotFound": {"code": -32601, "message": "Method not found"},
                "InvalidParams": {"code": -32602, "message": "Invalid params"},
                "InternalError": {"code": -32603, "message": "Internal error"},
                **PROTOCOL_ERRORS,
            }
        },
        "x-omni-jsonrpc-version": "2.0",
        "x-omni-schema-dialect": "https://json-schema.org/draft/2020-12/schema",
    }


def build_schema() -> dict:
    definitions = {
        "result": {},
        "identity": IDENTITY_SCHEMA,
        "platform": PLATFORM_SCHEMA,
        "capabilities": CAPABILITIES_SCHEMA,
        "initializeResult": INITIALIZE_RESULT_SCHEMA,
        "jsonRpcError": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "integer"},
                "message": {"type": "string"},
                "data": {},
            },
            "additionalProperties": False,
        },
    }
    for operation in OPERATIONS:
        required = list(REQUIRED_FIELDS.get(operation.name, ()))
        optional = list(OPTIONAL_FIELDS.get(operation.name, ()))
        properties = {}
        for field in required + optional:
            if operation.name == "initialize" and field == "protocol_version":
                properties[field] = {"type": "string"}
            elif operation.name == "initialize" and field in {
                "identity",
                "platform",
                "capabilities",
            }:
                properties[field] = {"$ref": f"#/$defs/{field}"}
            elif field in ARRAY_FIELDS:
                properties[field] = {"type": "array", "items": {}}
            elif field in BOOLEAN_FIELDS:
                properties[field] = {"type": "boolean"}
            elif field in INTEGER_FIELDS:
                properties[field] = {"type": "integer"}
            elif field in OBJECT_FIELDS:
                properties[field] = {"type": "object"}
            else:
                properties[field] = {"type": "string"}
        definitions[operation.name] = {
            "type": "object",
            "additionalProperties": False,
            "x-omni-stability": operation.stability,
            "properties": properties,
        }
        if required:
            definitions[operation.name]["required"] = required
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://omniagents.dev/protocol/gui/v1/schema.json",
        "$defs": definitions,
        "oneOf": [{"$ref": f"#/$defs/{operation.name}"} for operation in OPERATIONS],
    }


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _python_field_type(field: str) -> str:
    if field in ARRAY_FIELDS:
        return "list[Any]"
    if field in BOOLEAN_FIELDS:
        return "bool"
    if field in INTEGER_FIELDS:
        return "int"
    if field in OBJECT_FIELDS:
        return "dict[str, Any]"
    return "str"


def _typescript_field_type(field: str) -> str:
    if field in ARRAY_FIELDS:
        return "unknown[]"
    if field in BOOLEAN_FIELDS:
        return "boolean"
    if field in INTEGER_FIELDS:
        return "number"
    if field in OBJECT_FIELDS:
        return "Record<string, unknown>"
    return "string"


def _python_model_map(models: dict[str, str]) -> str:
    entries = ", ".join(f"{name!r}: {model}" for name, model in models.items())
    return "{" + entries + "}"


def build_python() -> bytes:
    stable_requests = [
        op.name
        for op in OPERATIONS
        if op.kind == "request" and op.stability == "stable"
    ]
    experimental_requests = [
        op.name
        for op in OPERATIONS
        if op.kind == "request" and op.stability == "experimental"
    ]
    stable_notifications = [
        op.name
        for op in OPERATIONS
        if op.kind != "request" and op.stability == "stable"
    ]
    experimental_notifications = [
        op.name
        for op in OPERATIONS
        if op.kind != "request" and op.stability == "experimental"
    ]
    lines = [
        "from typing import Any, Literal",
        "",
        "from pydantic import BaseModel, ConfigDict, RootModel",
        "",
        "",
        "class JsonRpcError(BaseModel):",
        '    model_config = ConfigDict(extra="forbid")',
        "    code: int",
        "    message: str",
        "    data: Any | None = None",
        "",
        "class Identity(BaseModel):",
        '    model_config = ConfigDict(extra="forbid")',
        "    name: str",
        "    version: str",
        "",
        "class Platform(BaseModel):",
        '    model_config = ConfigDict(extra="forbid")',
        "    os: str",
        "    arch: str",
        "",
        "class Capabilities(BaseModel):",
        '    model_config = ConfigDict(extra="forbid")',
        "    realtime: bool",
        "    mcp_apps: bool",
        "    client_functions: bool",
        "    approvals: bool",
        "    artifacts: bool",
        "    replay: bool",
        "    terminal: bool",
        "    experimental_operations: list[str]",
        "    disabled_notifications: list[str]",
        "",
    ]
    request_models = {}
    result_models = {}
    server_notification_models = {}
    client_notification_models = {}
    for operation in OPERATIONS:
        class_name = _class_name(operation.name)
        params_name = f"{class_name}Params"
        lines.extend(
            [
                f"class {params_name}(BaseModel):",
                '    model_config = ConfigDict(extra="forbid")',
            ]
        )
        fields = REQUIRED_FIELDS.get(operation.name, ())
        optional_fields = OPTIONAL_FIELDS.get(operation.name, ())
        if operation.name == "initialize":
            lines.extend(
                [
                    "    protocol_version: str",
                    "    identity: Identity",
                    "    platform: Platform",
                    "    capabilities: Capabilities",
                ]
            )
        elif fields:
            lines.extend(
                f"    {field}: {_python_field_type(field)}" for field in fields
            )
        lines.extend(
            f"    {field}: {_python_field_type(field)} | None = None"
            for field in optional_fields
        )
        if not fields and not optional_fields:
            lines.append("    pass")
        lines.append("")
        if operation.kind == "request":
            result_name = f"{class_name}Result"
            python_result, _ = RESULT_TYPES[operation.name]
            if operation.name == "initialize":
                lines.extend(
                    [
                        f"class {result_name}(BaseModel):",
                        '    model_config = ConfigDict(extra="forbid")',
                        "    protocol_version: str",
                        "    identity: Identity",
                        "    platform: Platform",
                        "    capabilities: Capabilities",
                        "",
                    ]
                )
            elif operation.name == "start_run":
                lines.extend(
                    [
                        f"class {result_name}(BaseModel):",
                        "    run_id: str",
                        "    session_id: str",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"class {result_name}(RootModel[{python_result}]):",
                        "    pass",
                        "",
                    ]
                )
            request_models[operation.name] = params_name
            result_models[operation.name] = result_name
        else:
            target = (
                client_notification_models
                if operation.direction == "client_to_server"
                else server_notification_models
            )
            target[operation.name] = params_name
    lines.extend(
        [
            f"CLIENT_REQUEST_MODELS = {_python_model_map(request_models)}",
            f"CLIENT_RESULT_MODELS = {_python_model_map(result_models)}",
            f"CLIENT_NOTIFICATION_MODELS = {_python_model_map(client_notification_models)}",
            f"SERVER_NOTIFICATION_MODELS = {_python_model_map(server_notification_models)}",
            f"STABLE_REQUESTS = frozenset({stable_requests!r})",
            f"EXPERIMENTAL_REQUESTS = frozenset({experimental_requests!r})",
            f"STABLE_NOTIFICATIONS = frozenset({stable_notifications!r})",
            f"EXPERIMENTAL_NOTIFICATIONS = frozenset({experimental_notifications!r})",
            "",
        ]
    )
    return black.format_str("\n".join(lines), mode=black.Mode()).encode()


def build_typescript() -> bytes:
    requests = [op.name for op in OPERATIONS if op.kind == "request"]
    server_notifications = [
        op.name
        for op in OPERATIONS
        if op.kind != "request" and op.direction == "server_to_client"
    ]
    client_notifications = [
        op.name
        for op in OPERATIONS
        if op.kind != "request" and op.direction == "client_to_server"
    ]
    lines = [
        "export type JsonRpcId = string | number;",
        "export type JsonRpcError = { code: number; message: string; data?: unknown };",
        "export interface Identity { name: string; version: string }",
        "export interface Platform { os: string; arch: string }",
        "export interface Capabilities { realtime: boolean; mcp_apps: boolean; client_functions: boolean; approvals: boolean; artifacts: boolean; replay: boolean; terminal: boolean; experimental_operations: string[]; disabled_notifications: string[] }",
    ]
    for operation in OPERATIONS:
        class_name = _class_name(operation.name)
        lines.append(f"export interface {class_name}Params {{")
        if operation.name == "initialize":
            lines.extend(
                [
                    "  protocol_version: string;",
                    "  identity: Identity;",
                    "  platform: Platform;",
                    "  capabilities: Capabilities;",
                ]
            )
        else:
            lines.extend(
                f"  {field}: {_typescript_field_type(field)};"
                for field in REQUIRED_FIELDS.get(operation.name, ())
            )
        lines.extend(
            f"  {field}?: {_typescript_field_type(field)};"
            for field in OPTIONAL_FIELDS.get(operation.name, ())
        )
        lines.append("}")
        if operation.name == "initialize":
            lines.append(
                "export interface InitializeResult { protocol_version: string; identity: Identity; platform: Platform; capabilities: Capabilities }"
            )
        elif operation.name == "start_run":
            lines.append(
                "export interface StartRunResult { run_id: string; session_id: string }"
            )
    lines.append("export interface RpcMethodMap {")
    for name in requests:
        class_name = _class_name(name)
        _, result_type = RESULT_TYPES[name]
        if name == "start_run":
            result_type = "StartRunResult"
        lines.append(
            f"  {json.dumps(name)}: {{ params: {class_name}Params; result: {result_type} }};"
        )
    lines.extend(["}", "export interface RpcNotificationMap {"])
    lines.extend(
        f"  {json.dumps(name)}: {_class_name(name)}Params;"
        for name in server_notifications
    )
    lines.extend(
        [
            "}",
            "export interface RpcClientNotificationMap {",
        ]
    )
    lines.extend(
        f"  {json.dumps(name)}: {_class_name(name)}Params;"
        for name in client_notifications
    )
    lines.extend(
        [
            "}",
            "export type RpcRequest<M extends keyof RpcMethodMap = keyof RpcMethodMap> = {",
            "  [K in M]: { jsonrpc: '2.0'; id: JsonRpcId; method: K; params: RpcMethodMap[K]['params'] }",
            "}[M];",
            "export type RpcNotification<E extends keyof RpcNotificationMap = keyof RpcNotificationMap> = {",
            "  [K in E]: { jsonrpc: '2.0'; method: K; params: RpcNotificationMap[K] }",
            "}[E];",
            "export type RpcClientNotification<E extends keyof RpcClientNotificationMap = keyof RpcClientNotificationMap> = {",
            "  [K in E]: { jsonrpc: '2.0'; method: K; params: RpcClientNotificationMap[K] }",
            "}[E];",
            "",
        ]
    )
    return "\n".join(lines).encode()


def build_pinned_tool_outputs(schema: dict) -> tuple[bytes, bytes]:
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        schema_path = temp / "schema.json"
        python_path = temp / "schema_models.py"
        typescript_path = temp / "schema-types.ts"
        schema_path.write_bytes(_json_bytes(schema))
        subprocess.run(
            [
                "datamodel-codegen",
                "--input",
                str(schema_path),
                "--input-file-type",
                "jsonschema",
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--disable-timestamp",
                "--output",
                str(python_path),
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run(
            ["black", "--quiet", str(python_path)],
            check=True,
            cwd=ROOT,
        )
        subprocess.run(
            [
                "npx",
                "--prefix",
                "omniagents/backends/web/ui",
                "json2ts",
                "-i",
                str(schema_path),
                "-o",
                str(typescript_path),
                "--bannerComment",
                "",
            ],
            check=True,
            cwd=ROOT,
        )
        return python_path.read_bytes(), typescript_path.read_bytes()


def validate_external_references(document: dict, schema: dict) -> None:
    prefix = "./schemas/gui-v1.schema.json#/$defs/"
    definitions = schema["$defs"]
    for method in document["methods"]:
        references = [method["x-omni-params-schema"]["$ref"]]
        references.extend(param["schema"]["$ref"] for param in method["params"])
        for reference in references:
            if not reference.startswith(prefix):
                raise ValueError(f"Unsupported external reference: {reference}")
            path = reference.removeprefix(prefix).split("/")
            if path[0] not in definitions:
                raise ValueError(f"Missing schema definition: {reference}")
            if len(path) == 3 and (
                path[1] != "properties"
                or path[2] not in definitions[path[0]].get("properties", {})
            ):
                raise ValueError(f"Missing schema property: {reference}")


def render() -> dict[Path, bytes]:
    openrpc = build_openrpc()
    schema = build_schema()
    validate_external_references(openrpc, schema)
    generated_python_schema, generated_typescript_schema = build_pinned_tool_outputs(
        schema
    )
    canonical_digest = hashlib.sha256(
        _json_bytes(openrpc) + _json_bytes(schema)
    ).hexdigest()
    manifest = {
        "protocol_version": openrpc["info"]["version"],
        "generator": "scripts.protocol.generate_gui_protocol",
        "generator_version": GENERATOR_VERSION,
        "toolchain": {
            "python": {
                "name": "datamodel-code-generator",
                "version": PYTHON_GENERATOR_VERSION,
                "options": [
                    "--output-model-type=pydantic_v2.BaseModel",
                    "--disable-timestamp",
                ],
            },
            "typescript": {
                "name": "json-schema-to-typescript",
                "version": TYPESCRIPT_GENERATOR_VERSION,
                "options": ["--bannerComment="],
            },
            "openrpc_meta_schema": "@open-rpc/meta-schema@1.14.9",
        },
        "canonical_sha256": canonical_digest,
        "artifacts": [
            str(PYTHON_PATH.relative_to(ROOT)),
            str(PYTHON_SCHEMA_PATH.relative_to(ROOT)),
            str(TYPESCRIPT_PATH.relative_to(ROOT)),
            str(INK_TYPESCRIPT_PATH.relative_to(ROOT)),
            str(TYPESCRIPT_SCHEMA_PATH.relative_to(ROOT)),
        ],
    }
    inventory = {
        "source": "omniagents.rpc.protocol_catalog.OPERATIONS",
        "router_mode": "generated_request_allowlist",
        "requests": {
            "stable": [
                operation.name
                for operation in OPERATIONS
                if operation.kind == "request" and operation.stability == "stable"
            ],
            "experimental": [
                operation.name
                for operation in OPERATIONS
                if operation.kind == "request" and operation.stability == "experimental"
            ],
        },
        "notifications": {
            "stable": [
                operation.name
                for operation in OPERATIONS
                if operation.kind != "request" and operation.stability == "stable"
            ],
            "experimental": [
                operation.name
                for operation in OPERATIONS
                if operation.kind != "request" and operation.stability == "experimental"
            ],
        },
        "excluded_surfaces": ["/ws/realtime", "/ws/terminal"],
    }
    return {
        OPENRPC_PATH: _json_bytes(openrpc),
        SCHEMA_PATH: _json_bytes(schema),
        PYTHON_PATH: build_python(),
        PYTHON_SCHEMA_PATH: generated_python_schema,
        PYTHON_INIT_PATH: b"from .models import *\n",
        TYPESCRIPT_PATH: build_typescript(),
        INK_TYPESCRIPT_PATH: build_typescript(),
        TYPESCRIPT_SCHEMA_PATH: generated_typescript_schema,
        MANIFEST_PATH: _json_bytes(manifest),
        INVENTORY_PATH: _json_bytes(inventory),
    }


def write_outputs(outputs: dict[Path, bytes]) -> None:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def check_outputs(outputs: dict[Path, bytes]) -> None:
    with tempfile.TemporaryDirectory():
        mismatches = [
            str(path.relative_to(ROOT))
            for path, content in outputs.items()
            if not path.exists() or path.read_bytes() != content
        ]
    if mismatches:
        raise SystemExit(
            "Generated protocol artifacts are stale: " + ", ".join(mismatches)
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = render()
    if args.check:
        check_outputs(outputs)
    else:
        write_outputs(outputs)


if __name__ == "__main__":
    main()
