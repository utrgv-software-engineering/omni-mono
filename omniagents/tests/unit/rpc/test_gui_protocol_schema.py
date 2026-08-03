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


def test_openrpc_publishes_resource_and_execution_metadata():
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    actual = {
        method["name"]: (
            method["x-omni-resource-scope"],
            method["x-omni-execution-requirements"],
        )
        for method in document["methods"]
    }
    expected = {
        operation.name: (
            operation.resource_scope.value,
            [
                requirement.name.lower()
                for requirement in type(operation.execution_requirements)
                if requirement.value and operation.execution_requirements & requirement
            ],
        )
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
    assert schema["$defs"]["start_run"]["required"] == [
        "prompt",
        "environment_selection",
    ]
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


def test_fs_and_git_operations_are_environment_addressed():
    schema = _load(ROOT / "protocol/openrpc/schemas/gui-v1.schema.json")
    environment_operations = [
        operation
        for operation in OPERATIONS
        if operation.name.startswith(("fs_", "git_"))
    ]

    for operation in environment_operations:
        definition = schema["$defs"][operation.name]
        required = definition["required"]
        properties = definition["properties"]

        assert "environment_id" in required, operation.name
        assert properties["environment_id"] == {"type": "string"}, operation.name
        assert "session_id" not in required, operation.name
        assert "session_id" not in properties, operation.name
        assert "workspace_id" not in required, operation.name
        assert properties["workspace_id"] == {"type": "string"}, operation.name
        assert "environment_generation" not in required, operation.name
        assert properties["environment_generation"] == {
            "type": "integer"
        }, operation.name

        if operation.direction == "server_to_client":
            assert "seq" not in required, operation.name
            assert "stream_id" not in required, operation.name
            assert properties["seq"] == {"type": "integer"}, operation.name
            assert properties["stream_id"] == {"type": "string"}, operation.name


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
    result = {
        **params,
        "agent_host": {
            "agent_host_id": "agent-host-1",
            "default_workspace_id": None,
            "default_environment_id": None,
        },
    }
    assert generated.CLIENT_RESULT_MODELS["initialize"].model_validate(result)
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
    assert len(inventory["requests"]["stable"]) == 49
    assert len(inventory["requests"]["experimental"]) == 57
    assert len(inventory["notifications"]["stable"]) == 19
    assert len(inventory["notifications"]["experimental"]) == 8
    assert len(inventory["operation_metadata"]) == len(OPERATIONS)
    assert inventory["operation_metadata"]["start_run"] == {
        "resource_scope": "thread",
        "execution_requirements": ["run_plan"],
    }
    assert inventory["operation_metadata"]["git_push"] == {
        "resource_scope": "workspace",
        "execution_requirements": [
            "workspace_write",
            "process",
            "network",
        ],
    }


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
        .model_validate({"prompt": "hello", "environment_selection": {"mode": "none"}})
        .prompt
        == "hello"
    )


def test_start_run_environment_selection_is_a_strict_union():
    generated = importlib.import_module("omniagents.rpc.generated.gui_v1")
    model = generated.CLIENT_REQUEST_MODELS["start_run"]

    explicit = model.model_validate(
        {
            "prompt": "hello",
            "environment_selection": {
                "mode": "explicit",
                "environment_id": "environment-1",
                "environment_generation": 3,
            },
        }
    )
    assert explicit.environment_selection.environment_id == "environment-1"
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "prompt": "hello",
                "environment_selection": {"mode": "explicit"},
            }
        )
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "prompt": "hello",
                "environment_selection": {
                    "mode": "none",
                    "environment_id": "environment-1",
                },
            }
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


def _error_component_by_code(document: dict) -> dict:
    return {
        entry["code"]: entry
        for entry in document["components"]["errors"].values()
        if "code" in entry
    }


def test_raised_typed_errors_validate_against_their_published_schema():
    """A typed error's ``data`` must satisfy the component that declares it.

    Nothing else in the suite checks this, and it is exactly the gap that
    let ``fork_point_invalid`` ship emitting ``next_item_id`` while the
    published component declared ``turn_first_item_id``/``turn_last_item_id``
    (which no code path produces) and set ``additionalProperties: false`` —
    so a conformant consumer would have rejected the real payload.
    """
    from omniagents.core.conversation.service import (
        ForkPointInvalid,
        SearchUnsupported,
        ThreadHasDescendants,
        ThreadMetadataInvalid,
        ThreadOnLegalHold,
    )

    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    components = _error_component_by_code(document)

    raised = [
        SearchUnsupported("no FTS5 in this build"),
        ThreadHasDescendants("t1", 3, ["c1", "c2"]),
        ForkPointInvalid(
            "no exact cut",
            thread_id="t1",
            turn_id="r1",
            item_id="itm_1",
            next_item_id="itm_2",
            next_item_kind="agent_message",
        ),
        ForkPointInvalid(
            "still running", thread_id="t1", turn_id="r1", status="running"
        ),
        ThreadOnLegalHold("t1", ["t1", "c1"]),
        ThreadMetadataInvalid("too big", size=99, max_size=10),
    ]
    for error in raised:
        component = components.get(error.code)
        assert component is not None, f"no published component for {error.code}"
        Draft202012Validator(component["data"]).validate(error.data)


#: Where a typed conversation error can be constructed. The classes live
#: in the first file; the second raises two of them.
_ERROR_SOURCES = (
    "omniagents/core/conversation/service.py",
    "omniagents/core/agents/service.py",
)


def _emitted_error_fields() -> dict[int, set[str]]:
    """``error code -> the field names its construction sites can emit``.

    Read from the AST rather than by substring, because ``field in
    source`` passes for any plausible name that appears ANYWHERE in the
    module — ``thread_id`` and ``descendant_count`` occur dozens of times
    apart from any error — so the converse check was only ever able to
    catch an invented word. What it claims to check is that the SERVER
    CONSTRUCTS the field, and that is a property of the call sites.

    Every ``data`` key reaches ``ConversationRpcError.__init__`` as a
    keyword, by exactly two routes: the ``super().__init__(...)`` call in
    a subclass's ``__init__``, or — for the two subclasses that forward
    ``**data`` — the keywords at each ``raise`` site. Both are collected.
    """
    import ast

    from omniagents.core.conversation import service as conversation_service

    trees = {path: ast.parse((ROOT / path).read_text()) for path in _ERROR_SOURCES}

    explicit: dict[str, set[str]] = {}
    forwards: set[str] = set()
    codes: dict[str, int] = {}

    for node in ast.walk(trees[_ERROR_SOURCES[0]]):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            isinstance(base, ast.Name) and base.id == "ConversationRpcError"
            for base in node.bases
        ):
            continue
        for statement in node.body:
            if isinstance(statement, ast.Assign) and isinstance(
                statement.value, ast.Name
            ):
                target = statement.targets[0]
                if isinstance(target, ast.Name) and target.id == "code":
                    codes[node.name] = getattr(conversation_service, statement.value.id)
            if not (
                isinstance(statement, ast.FunctionDef) and statement.name == "__init__"
            ):
                continue
            for call in ast.walk(statement):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "__init__"
                    and isinstance(func.value, ast.Call)
                    and isinstance(func.value.func, ast.Name)
                    and func.value.func.id == "super"
                ):
                    continue
                names = explicit.setdefault(node.name, set())
                for keyword in call.keywords:
                    if keyword.arg is None:
                        # ``**data`` — this subclass's fields are decided
                        # by its raise sites, not by its constructor.
                        forwards.add(node.name)
                    else:
                        names.add(keyword.arg)

    # Construction sites, for the forwarding subclasses.
    for tree in trees.values():
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name not in forwards:
                continue
            assert name is not None
            for keyword in call.keywords:
                if keyword.arg is not None:
                    explicit.setdefault(name, set()).add(keyword.arg)

    assert codes, "no typed conversation errors found — the AST walk is broken"
    return {codes[name]: fields for name, fields in explicit.items() if name in codes}


def test_every_declared_error_property_is_reachable_from_code():
    """The other direction: a declared field nothing can emit is a promise
    the server does not keep."""
    document = _load(ROOT / "protocol/openrpc/omniagents-gui-v1.json")
    emitted = _emitted_error_fields()

    for entry in _error_component_by_code(document).values():
        if entry["code"] not in emitted:
            continue
        for field in entry["data"]["properties"]:
            if field == "kind":
                continue
            assert field in emitted[entry["code"]], (
                f"{entry['message']!r} declares {field!r}, which no error "
                "construction site produces"
            )


def test_the_reachability_check_is_stronger_than_a_substring_scan():
    """The AST check must reject what ``field in source`` waved through.

    ``parent_thread_id`` and ``max_depth`` are real, frequent identifiers
    in ``conversation/service.py`` — a substring scan accepts them as
    "reachable" — but no error construction site passes either one, so a
    component declaring them would ship a field no client ever receives.
    """
    source = (ROOT / "omniagents/core/conversation/service.py").read_text()
    emitted = _emitted_error_fields()

    from omniagents.core.conversation import service as conversation_service

    descendants = emitted[conversation_service.RPC_ERROR_THREAD_HAS_DESCENDANTS]
    for plausible in ("parent_thread_id", "max_depth", "branched_from_item_id"):
        assert plausible in source  # the old check passed on these
        assert plausible not in descendants  # the new one does not

    # ...while everything the constructor really emits still passes.
    assert {"thread_id", "descendant_thread_ids", "descendant_count", "truncated"} <= (
        descendants
    )
    # And the forwarding subclasses are covered through their raise sites.
    fork = emitted[conversation_service.RPC_ERROR_FORK_POINT_INVALID]
    assert {"thread_id", "turn_id", "next_item_id"} <= fork
    assert "parent_thread_id" not in fork
