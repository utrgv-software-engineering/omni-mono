import argparse
import black
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from omniagents.rpc.protocol_catalog import OPERATIONS

ROOT = Path(__file__).resolve().parents[2]
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
    "update_session_variables": ("session_id", "updates"),
    "set_client_status": ("text",),
    "disable_tool": ("tool_name",),
    "disable_mcp_server": ("server_name",),
    "report_incident": ("session_id", "description"),
    "export_session": ("session_id",),
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
    "queue_changed": ("session_id", "depth", "items"),
    "session_forked": ("session_id", "new_session_id"),
    "session_variables_changed": ("session_id", "changed", "variables"),
}

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
    "run_started": ("prompt", "prompt_role"),
    "run_status": ("attempt", "total", "next_delay"),
    "tool_result": ("metadata",),
    "token": (
        "turn",
        "response_id",
        "model",
        "max_input_tokens",
        "max_output_tokens",
        "truncation",
    ),
    "run_end": (
        "usage",
        "model",
        "max_input_tokens",
        "max_output_tokens",
        "truncation",
        "error",
    ),
    "tool_approval_requested": ("metadata", "session_id", "run_id"),
    "tool_approval_resolved": ("session_id",),
    "mcp_approval_requested": ("session_id", "run_id"),
    "mcp_approval_resolved": ("session_id",),
    "client_request": ("session_id", "idempotency_key", "run_id"),
}

ARRAY_FIELDS = {"disable_mcp_servers", "disable_tools", "functions", "items"}
BOOLEAN_FIELDS = {
    "always_approve",
    "hold",
    "include_archived",
    "ok",
    "redact",
    "trigger_run",
}
INTEGER_FIELDS = {
    "attempt",
    "depth",
    "limit",
    "max_input_tokens",
    "max_output_tokens",
    "next_delay",
    "offset",
    "total",
    "turn",
    "version",
}
OBJECT_FIELDS = {
    "args",
    "changed",
    "context",
    "delta",
    "error",
    "metadata",
    "result",
    "safe_tool_overrides",
    "totals",
    "updates",
    "usage",
    "variables",
}

RESULT_TYPES = {
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
}


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
                    {"$ref": "./schemas/gui-v1.schema.json#/$defs/result"}
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
            ]
        if operation.kind == "correlated_notification":
            entry["x-omni-correlation"] = {
                "requestField": "request_id",
                "responseMethod": "client_response",
            }
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
            }
        },
        "x-omni-jsonrpc-version": "2.0",
        "x-omni-schema-dialect": "https://json-schema.org/draft/2020-12/schema",
    }


def build_schema() -> dict:
    definitions = {
        "result": {},
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
            if field in ARRAY_FIELDS:
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
        "from typing import Any",
        "",
        "from pydantic import BaseModel, ConfigDict, RootModel",
        "",
        "",
        "class JsonRpcError(BaseModel):",
        "    code: int",
        "    message: str",
        "    data: Any | None = None",
        "",
    ]
    request_models = {}
    result_models = {}
    notification_models = {}
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
        if fields:
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
            if operation.name == "start_run":
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
            notification_models[operation.name] = params_name
    lines.extend(
        [
            f"CLIENT_REQUEST_MODELS = {_python_model_map(request_models)}",
            f"CLIENT_RESULT_MODELS = {_python_model_map(result_models)}",
            f"SERVER_NOTIFICATION_MODELS = {_python_model_map(notification_models)}",
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
    notifications = [op.name for op in OPERATIONS if op.kind != "request"]
    lines = [
        "export type JsonRpcId = string | number;",
        "export type JsonRpcError = { code: number; message: string; data?: unknown };",
    ]
    for operation in OPERATIONS:
        class_name = _class_name(operation.name)
        lines.append(f"export interface {class_name}Params {{")
        lines.extend(
            f"  {field}: {_typescript_field_type(field)};"
            for field in REQUIRED_FIELDS.get(operation.name, ())
        )
        lines.extend(
            f"  {field}?: {_typescript_field_type(field)};"
            for field in OPTIONAL_FIELDS.get(operation.name, ())
        )
        lines.append("}")
        if operation.name == "start_run":
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
        f"  {json.dumps(name)}: {_class_name(name)}Params;" for name in notifications
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
