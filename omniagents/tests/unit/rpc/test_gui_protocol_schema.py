import hashlib
import importlib
import json
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from omniagents.rpc.protocol_catalog import OPERATIONS
from omniagents.rpc.server import GUI_RPC_METHODS
from scripts.protocol.generate_gui_protocol import (
    ROOT,
    render,
    validate_external_references,
)
from scripts.protocol.inventory import (
    discover_agent_service_methods,
    discover_client_rpc_calls,
    discover_notification_handlers,
    missing_from_catalog,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_openrpc_inventory_matches_explicit_catalog():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    actual = {
        (
            method["name"],
            method["x-omni-direction"],
            method["x-omni-kind"],
            method["x-omni-stability"],
        )
        for method in document["methods"]
    }
    expected = {
        (operation.name, operation.direction, operation.kind, operation.stability)
        for operation in OPERATIONS
    }
    assert actual == expected


def test_json_schema_uses_valid_draft_2020_12():
    schema = _load(ROOT / "protocol/openrpc/schemas/gui-v1.schema.json")
    Draft202012Validator.check_schema(schema)


def _validate_openrpc(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "node",
            str(ROOT / "scripts/protocol/validate_openrpc.mjs"),
            str(path),
        ],
        cwd=ROOT / "omniagents/backends/web/ui",
        text=True,
        capture_output=True,
        check=False,
    )


def test_openrpc_matches_reference_meta_schema():
    result = _validate_openrpc(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    assert result.returncode == 0, result.stderr


def test_malformed_openrpc_fails_reference_meta_schema(tmp_path):
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    del document["openrpc"]
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps(document))
    result = _validate_openrpc(malformed)
    assert result.returncode != 0


def test_all_external_schema_references_resolve():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    schema = _load(ROOT / "protocol/openrpc/schemas/gui-v1.schema.json")
    definitions = schema["$defs"]
    for method in document["methods"]:
        refs = [method["x-omni-params-schema"]["$ref"]]
        refs.extend(param["schema"]["$ref"] for param in method["params"])
        for reference in refs:
            prefix = "./schemas/gui-v1.schema.json#/$defs/"
            assert reference.startswith(prefix)
            path = reference.removeprefix(prefix).split("/")
            assert path[0] in definitions
            if len(path) == 3:
                assert path[1] == "properties"
                assert path[2] in definitions[path[0]]["properties"]


def test_malformed_external_reference_fails_validation():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    schema = _load(ROOT / "protocol/openrpc/schemas/gui-v1.schema.json")
    document["methods"][0]["params"][0]["schema"][
        "$ref"
    ] = "./schemas/gui-v1.schema.json#/$defs/start_run/properties/missing"
    with pytest.raises(ValueError, match="Missing schema property"):
        validate_external_references(document, schema)


def test_every_nonempty_payload_declares_required_fields():
    schema = _load(ROOT / "protocol/openrpc/schemas/gui-v1.schema.json")
    assert schema["$defs"]["start_run"]["required"] == ["prompt"]
    assert schema["$defs"]["run_end"]["required"] == [
        "run_id",
        "session_id",
        "end_reason",
    ]
    assert schema["$defs"]["client_request"]["required"] == [
        "request_id",
        "function",
        "args",
    ]
    assert schema["$defs"]["initialize"]["required"] == [
        "protocol_version",
        "identity",
        "platform",
        "capabilities",
    ]


def test_initialize_handshake_has_strict_negotiated_models():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    params = {
        "protocol_version": "1.0.0",
        "identity": {"name": "omni-desktop", "version": "1.2.3"},
        "platform": {"os": "linux", "arch": "x86_64"},
        "capabilities": {
            "realtime": True,
            "mcp_apps": True,
            "client_functions": True,
            "approvals": True,
            "artifacts": True,
            "replay": True,
            "terminal": False,
            "experimental_operations": [],
            "disabled_notifications": ["token"],
        },
    }
    assert generated.CLIENT_REQUEST_MODELS["initialize"].model_validate(params)
    assert generated.CLIENT_RESULT_MODELS["initialize"].model_validate(params)
    with pytest.raises(ValidationError):
        generated.CLIENT_REQUEST_MODELS["initialize"].model_validate(
            {**params, "identity": {**params["identity"], "extra": True}}
        )
    assert generated.CLIENT_REQUEST_MODELS["initialize"].model_validate(
        {**params, "protocol_version": "0.9.0"}
    )


def test_initialized_is_stable_client_notification():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    operation = next(item for item in OPERATIONS if item.name == "initialized")
    assert operation.direction == "client_to_server"
    assert operation.kind == "notification"
    assert operation.stability == "stable"
    assert generated.CLIENT_NOTIFICATION_MODELS["initialized"].model_validate({})
    with pytest.raises(ValidationError):
        generated.CLIENT_NOTIFICATION_MODELS["initialized"].model_validate(
            {"unexpected": True}
        )


def test_protocol_errors_have_reserved_codes_and_exact_overload_shape():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    errors = document["components"]["errors"]
    assert {
        errors[name]["code"]
        for name in (
            "InitializationRequired",
            "AlreadyInitialized",
            "ProtocolVersionMismatch",
            "CapabilityMismatch",
        )
    } == {-32010, -32011, -32012, -32013}
    assert errors["TransportOverloaded"] == {
        "code": -32020,
        "message": "Transport overloaded",
        "data": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "resource",
                "unit",
                "limit",
                "retryable",
                "retry_after_ms",
            ],
            "properties": {
                "resource": {"type": "string"},
                "unit": {"type": "string", "enum": ["messages", "bytes"]},
                "limit": {"type": "integer", "minimum": 1},
                "retryable": {"type": "boolean"},
                "retry_after_ms": {"type": ["integer", "null"], "minimum": 0},
            },
        },
    }


def test_client_request_is_correlated_notification_without_wire_id():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    method = next(
        item for item in document["methods"] if item["name"] == "client_request"
    )
    assert method["x-omni-kind"] == "correlated_notification"
    assert method["x-omni-correlation"] == {
        "requestField": "request_id",
        "responseMethod": "client_response",
    }
    assert method["result"]["schema"] == {"type": "null"}


def test_experimental_names_are_preserved():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    experimental = {
        method["name"]
        for method in document["methods"]
        if method["x-omni-stability"] == "experimental"
    }
    assert "fork_session" in experimental
    assert "session_forked" in experimental
    assert all(not name.startswith("experimental.") for name in experimental)


def test_generation_is_deterministic_and_checked_in():
    first = render()
    second = render()
    assert first == second
    for path, content in first.items():
        assert path.read_bytes() == content


def test_ink_and_web_consume_identical_generated_protocol_maps():
    web = ROOT / "omniagents/backends/web/ui/src/protocol/generated/gui-v1.ts"
    ink = ROOT / "omniagents/backends/ink/tui/src/protocol/generated/gui-v1.ts"
    assert ink.read_bytes() == web.read_bytes()


def test_manifest_digest_matches_canonical_documents():
    manifest = _load(ROOT / "protocol/openrpc/manifest.json")
    openrpc = (ROOT / "protocol/openrpc/omniagents-gui-v1.json").read_bytes()
    schema = (ROOT / "protocol/openrpc/schemas/gui-v1.schema.json").read_bytes()
    assert manifest["protocol_version"] == "1.0.0"
    assert manifest["toolchain"]["python"]["version"] == "0.71.0"
    assert manifest["toolchain"]["typescript"]["version"] == "15.0.4"
    assert manifest["canonical_sha256"] == hashlib.sha256(openrpc + schema).hexdigest()


def test_inventory_reports_generated_request_allowlist():
    inventory = _load(ROOT / "protocol/openrpc/inventory.json")
    assert inventory["source"] == "omniagents.rpc.protocol_catalog.OPERATIONS"
    assert inventory["router_mode"] == "generated_request_allowlist"
    assert len(inventory["requests"]["stable"]) == 35
    assert len(inventory["requests"]["experimental"]) == 13
    assert len(inventory["notifications"]["stable"]) == 16
    assert len(inventory["notifications"]["experimental"]) == 2


def test_catalog_requests_exist_on_real_agent_service():
    catalog_requests = {
        operation.name for operation in OPERATIONS if operation.kind == "request"
    }
    service_methods = discover_agent_service_methods(
        ROOT / "omniagents/core/agents/service.py"
    )
    assert catalog_requests - {"initialize"} <= service_methods
    assert GUI_RPC_METHODS == catalog_requests


def test_maintained_client_rpc_calls_are_cataloged():
    paths = [
        ROOT / "omniagents/backends/web/ui/src/rpc/client.ts",
        ROOT / "omniagents/backends/ink/tui/src/rpc/client.ts",
    ]
    discovered = discover_client_rpc_calls(paths)
    catalog = {
        operation.name for operation in OPERATIONS if operation.kind == "request"
    }
    assert not missing_from_catalog(discovered, catalog)
    assert {"start_run", "archive_session", "enqueue_message"} <= discovered


def test_maintained_notification_handlers_are_cataloged():
    paths = [
        ROOT / "omniagents/backends/web/ui/src/App.tsx",
        ROOT / "omniagents/backends/ink/tui/src/hooks/useMessagesNew.ts",
    ]
    discovered = discover_notification_handlers(paths)
    catalog = {
        operation.name for operation in OPERATIONS if operation.kind != "request"
    }
    assert not missing_from_catalog(discovered, catalog)
    assert {"run_started", "client_request", "queue_changed"} <= discovered


def test_source_drift_check_is_sensitive_to_new_client_surface(tmp_path):
    source = tmp_path / "client.ts"
    source.write_text(
        "this.call('future_method', {}); rpc.on('future_event', () => {});"
    )
    request_catalog = {
        operation.name for operation in OPERATIONS if operation.kind == "request"
    }
    notification_catalog = {
        operation.name for operation in OPERATIONS if operation.kind != "request"
    }
    assert missing_from_catalog(
        discover_client_rpc_calls([source]), request_catalog
    ) == {"future_method"}
    assert missing_from_catalog(
        discover_notification_handlers([source]), notification_catalog
    ) == {"future_event"}


def test_generated_python_imports():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    assert generated.CLIENT_REQUEST_MODELS["start_run"]
    assert "client_request" in generated.STABLE_NOTIFICATIONS
    assert generated.CLIENT_NOTIFICATION_MODELS["initialized"]


def test_generated_models_reject_missing_required_fields():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    with pytest.raises(ValidationError):
        generated.CLIENT_REQUEST_MODELS["start_run"].model_validate({})
    with pytest.raises(ValidationError):
        generated.SERVER_NOTIFICATION_MODELS["run_end"].model_validate(
            {"run_id": "run", "session_id": "session"}
        )
    assert (
        generated.CLIENT_REQUEST_MODELS["start_run"]
        .model_validate({"prompt": "hello"})
        .prompt
        == "hello"
    )


def test_generated_models_type_optional_wire_fields():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    model = generated.CLIENT_REQUEST_MODELS["server_call"]
    valid = model.model_validate(
        {"function": "session.ensure", "args": {}, "session_id": "session"}
    )
    assert valid.args == {}
    assert valid.session_id == "session"
    with pytest.raises(ValidationError):
        model.model_validate({"function": "session.ensure", "args": "wrong"})
    with pytest.raises(ValidationError):
        model.model_validate({"function": "session.ensure", "unknown": True})


def test_pinned_tool_outputs_include_optional_fields():
    python_output = (
        ROOT / "omniagents/rpc/generated/gui_v1/schema_models.py"
    ).read_text()
    typescript_output = (
        ROOT / "omniagents/backends/web/ui/src/protocol/generated/schema-types.ts"
    ).read_text()
    assert "class ServerCall" in python_output
    assert "args: dict[str, Any] | None = None" in python_output
    assert "session_id: str | None = None" in python_output
    assert "export interface ServerCall" in typescript_output
    assert "args?:" in typescript_output
    assert "session_id?: string" in typescript_output
