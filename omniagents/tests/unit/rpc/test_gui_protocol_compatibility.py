import copy
import hashlib
import json
from pathlib import Path

from scripts.protocol.check_gui_protocol_compatibility import compare_protocols
from scripts.protocol.generate_gui_protocol import ROOT


def _documents() -> tuple[dict, dict]:
    protocol_root = ROOT / "protocol/openrpc"
    return (
        json.loads((protocol_root / "omniagents-gui-v1.json").read_text()),
        json.loads((protocol_root / "schemas/gui-v1.schema.json").read_text()),
    )


def _method(document: dict, name: str) -> dict:
    return next(method for method in document["methods"] if method["name"] == name)


def test_identical_protocol_is_compatible():
    document, schema = _documents()
    report = compare_protocols(
        document, schema, copy.deepcopy(document), copy.deepcopy(schema)
    )
    assert report.classification == "identical"
    assert report.additions == ()
    assert report.violations == ()


def test_additive_method_and_optional_request_field_are_compatible():
    baseline_document, baseline_schema = _documents()
    candidate_document = copy.deepcopy(baseline_document)
    candidate_schema = copy.deepcopy(baseline_schema)
    candidate_document["methods"].append(
        {
            "name": "future_method",
            "paramStructure": "by-name",
            "params": [],
            "result": {"name": "future_method_result", "schema": {"type": "null"}},
            "x-omni-direction": "client_to_server",
            "x-omni-kind": "request",
            "x-omni-stability": "stable",
            "x-omni-params-schema": {
                "$ref": "./schemas/gui-v1.schema.json#/$defs/future_method"
            },
        }
    )
    candidate_schema["$defs"]["future_method"] = {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
        "x-omni-stability": "stable",
    }
    candidate_schema["$defs"]["start_run"]["properties"]["future_option"] = {
        "type": "string"
    }
    _method(candidate_document, "start_run")["params"].append(
        {
            "name": "future_option",
            "required": False,
            "schema": {
                "$ref": "./schemas/gui-v1.schema.json#/$defs/start_run/properties/future_option"
            },
        }
    )

    report = compare_protocols(
        baseline_document, baseline_schema, candidate_document, candidate_schema
    )
    assert report.classification == "additive"
    assert report.violations == ()
    assert report.additions == (
        "method future_method added (stable)",
        "start_run.params.future_option added (optional)",
    )


def test_enum_widening_is_additive():
    baseline_document, baseline_schema = _documents()
    baseline_schema["$defs"]["start_run"]["properties"]["workflow_name"]["enum"] = [
        "default"
    ]
    candidate_schema = copy.deepcopy(baseline_schema)
    candidate_schema["$defs"]["start_run"]["properties"]["workflow_name"][
        "enum"
    ].append("review")

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        copy.deepcopy(baseline_document),
        candidate_schema,
    )
    assert report.classification == "additive"
    assert report.additions == (
        "start_run.params.workflow_name enum value 'review' added",
    )


def test_request_type_and_union_widening_are_additive():
    baseline_document, baseline_schema = _documents()
    baseline_schema["$defs"]["start_run"]["properties"]["prompt"] = {"type": "string"}
    baseline_schema["$defs"]["start_run"]["properties"]["content"] = {
        "anyOf": [{"type": "string"}]
    }
    candidate_schema = copy.deepcopy(baseline_schema)
    candidate_schema["$defs"]["start_run"]["properties"]["prompt"]["type"] = [
        "string",
        "number",
    ]
    candidate_schema["$defs"]["start_run"]["properties"]["content"]["anyOf"].append(
        {"type": "number"}
    )

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        copy.deepcopy(baseline_document),
        candidate_schema,
    )
    assert report.classification == "additive"
    assert report.violations == ()
    assert report.additions == (
        "start_run.params.content widened anyOf variants",
        "start_run.params.prompt widened type with ['number']",
    )


def test_request_unconstrained_and_typed_variance():
    baseline_document, baseline_schema = _documents()
    unconstrained_schema = copy.deepcopy(baseline_schema)
    unconstrained_schema["$defs"]["start_run"]["properties"]["prompt"] = {}
    typed_schema = copy.deepcopy(unconstrained_schema)
    typed_schema["$defs"]["start_run"]["properties"]["prompt"] = {"type": "string"}

    narrowed = compare_protocols(
        baseline_document,
        unconstrained_schema,
        copy.deepcopy(baseline_document),
        typed_schema,
    )
    assert narrowed.classification == "breaking"
    assert narrowed.violations == (
        "start_run.params.prompt added type constraint to unconstrained input",
    )

    relaxed = compare_protocols(
        baseline_document,
        typed_schema,
        copy.deepcopy(baseline_document),
        unconstrained_schema,
    )
    assert relaxed.classification == "additive"
    assert relaxed.additions == ("start_run.params.prompt removed type constraint",)


def test_request_unconstrained_and_union_variance():
    baseline_document, baseline_schema = _documents()
    unconstrained_schema = copy.deepcopy(baseline_schema)
    unconstrained_schema["$defs"]["start_run"]["properties"]["content"] = {}
    union_schema = copy.deepcopy(unconstrained_schema)
    union_schema["$defs"]["start_run"]["properties"]["content"] = {
        "anyOf": [{"type": "string"}, {"type": "number"}]
    }

    narrowed = compare_protocols(
        baseline_document,
        unconstrained_schema,
        copy.deepcopy(baseline_document),
        union_schema,
    )
    assert narrowed.classification == "breaking"
    assert narrowed.violations == (
        "start_run.params.content added anyOf constraint to unconstrained input",
    )

    relaxed = compare_protocols(
        baseline_document,
        union_schema,
        copy.deepcopy(baseline_document),
        unconstrained_schema,
    )
    assert relaxed.classification == "additive"
    assert relaxed.additions == ("start_run.params.content removed anyOf constraint",)


def test_notification_type_and_union_widening_are_breaking():
    baseline_document, baseline_schema = _documents()
    baseline_schema["$defs"]["message_output"]["properties"]["content"] = {
        "type": "string"
    }
    baseline_schema["$defs"]["message_output"]["properties"]["metadata"] = {
        "oneOf": [{"type": "string"}]
    }
    candidate_schema = copy.deepcopy(baseline_schema)
    candidate_schema["$defs"]["message_output"]["properties"]["content"]["type"] = [
        "string",
        "number",
    ]
    candidate_schema["$defs"]["message_output"]["properties"]["metadata"][
        "oneOf"
    ].append({"type": "number"})

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        copy.deepcopy(baseline_document),
        candidate_schema,
    )
    assert report.classification == "breaking"
    assert report.additions == ()
    assert report.violations == (
        "message_output.params.content changed output type from ['string'] to ['number', 'string']",
        "message_output.params.metadata changed output oneOf variants",
    )


def test_output_constraint_addition_and_removal_are_breaking():
    baseline_document, baseline_schema = _documents()
    unconstrained_schema = copy.deepcopy(baseline_schema)
    unconstrained_schema["$defs"]["message_output"]["properties"]["content"] = {}
    constrained_schema = copy.deepcopy(unconstrained_schema)
    constrained_schema["$defs"]["message_output"]["properties"]["content"] = {
        "oneOf": [{"type": "string"}]
    }

    added = compare_protocols(
        baseline_document,
        unconstrained_schema,
        copy.deepcopy(baseline_document),
        constrained_schema,
    )
    assert added.classification == "breaking"
    assert added.violations == (
        "message_output.params.content changed output oneOf constraint",
    )

    removed = compare_protocols(
        baseline_document,
        constrained_schema,
        copy.deepcopy(baseline_document),
        unconstrained_schema,
    )
    assert removed.classification == "breaking"
    assert removed.violations == (
        "message_output.params.content changed output oneOf constraint",
    )


def test_result_union_widening_is_breaking():
    baseline_document, baseline_schema = _documents()
    candidate_document = copy.deepcopy(baseline_document)
    baseline_result = _method(baseline_document, "start_run")["result"]["schema"]
    candidate_result = _method(candidate_document, "start_run")["result"]["schema"]
    baseline_result.clear()
    baseline_result.update({"anyOf": [{"type": "string"}]})
    candidate_result.clear()
    candidate_result.update({"anyOf": [{"type": "string"}, {"type": "number"}]})

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        candidate_document,
        copy.deepcopy(baseline_schema),
    )
    assert report.classification == "breaking"
    assert report.additions == ()
    assert report.violations == ("start_run.result changed output anyOf variants",)


def test_removed_or_reclassified_stable_method_is_breaking():
    baseline_document, baseline_schema = _documents()
    removed_document = copy.deepcopy(baseline_document)
    removed_document["methods"] = [
        method
        for method in removed_document["methods"]
        if method["name"] != "start_run"
    ]
    removed = compare_protocols(
        baseline_document,
        baseline_schema,
        removed_document,
        copy.deepcopy(baseline_schema),
    )
    assert removed.classification == "breaking"
    assert removed.violations == ("stable method start_run removed",)

    reclassified_document = copy.deepcopy(baseline_document)
    _method(reclassified_document, "start_run")["x-omni-stability"] = "experimental"
    reclassified = compare_protocols(
        baseline_document,
        baseline_schema,
        reclassified_document,
        copy.deepcopy(baseline_schema),
    )
    assert reclassified.classification == "breaking"
    assert reclassified.violations == (
        "stable method start_run changed stability to experimental",
    )


def test_required_request_field_and_removed_field_are_breaking():
    baseline_document, baseline_schema = _documents()
    required_document = copy.deepcopy(baseline_document)
    required_schema = copy.deepcopy(baseline_schema)
    required_schema["$defs"]["start_run"]["properties"]["future_required"] = {
        "type": "string"
    }
    required_schema["$defs"]["start_run"]["required"].append("future_required")
    _method(required_document, "start_run")["params"].append(
        {
            "name": "future_required",
            "required": True,
            "schema": {
                "$ref": "./schemas/gui-v1.schema.json#/$defs/start_run/properties/future_required"
            },
        }
    )
    required = compare_protocols(
        baseline_document, baseline_schema, required_document, required_schema
    )
    assert required.classification == "breaking"
    assert required.violations == (
        "start_run.params.future_required added as required",
    )

    removed_document = copy.deepcopy(baseline_document)
    removed_schema = copy.deepcopy(baseline_schema)
    del removed_schema["$defs"]["start_run"]["properties"]["workflow_name"]
    _method(removed_document, "start_run")["params"] = [
        param
        for param in _method(removed_document, "start_run")["params"]
        if param["name"] != "workflow_name"
    ]
    removed = compare_protocols(
        baseline_document, baseline_schema, removed_document, removed_schema
    )
    assert removed.classification == "breaking"
    assert removed.violations == ("start_run.params.workflow_name removed",)


def test_changed_kind_and_correlation_are_breaking():
    baseline_document, baseline_schema = _documents()
    candidate_document = copy.deepcopy(baseline_document)
    _method(candidate_document, "client_request")["x-omni-kind"] = "notification"
    _method(candidate_document, "client_request")["x-omni-correlation"][
        "responseMethod"
    ] = "future_response"

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        candidate_document,
        copy.deepcopy(baseline_schema),
    )
    assert report.classification == "breaking"
    assert report.violations == (
        "stable method client_request changed correlation metadata",
        "stable method client_request changed kind from correlated_notification to notification",
    )


def test_changed_unsupported_keyword_fails_closed():
    baseline_document, baseline_schema = _documents()
    baseline_schema["$defs"]["start_run"]["properties"]["prompt"]["pattern"] = ".+"
    candidate_schema = copy.deepcopy(baseline_schema)
    candidate_schema["$defs"]["start_run"]["properties"]["prompt"][
        "pattern"
    ] = "^[a-z]+$"

    report = compare_protocols(
        baseline_document,
        baseline_schema,
        copy.deepcopy(baseline_document),
        candidate_schema,
    )
    assert report.classification == "breaking"
    assert report.violations == (
        "start_run.params.prompt changed unsupported keyword pattern",
    )


def test_released_baseline_matches_manifest_digest():
    """The frozen 1.0.0 baseline is self-consistent release evidence.

    Its manifest digest must match the byte content of the released
    artifacts. The baseline is immutable — this test failing means the
    baseline directory was touched, which the governance doc forbids.
    It deliberately does NOT require the current candidate to be
    byte-identical to the baseline: v1 evolves additively, and requiring
    byte-equality here forces exactly the baseline rewrites the doc bans
    (compatibility is enforced by ``test_candidate_protocol_is_backward_
    compatible_with_released_baseline`` below).
    """
    baseline_root = ROOT / "protocol/openrpc/baseline/1.0.0"
    baseline_manifest = json.loads((baseline_root / "manifest.json").read_text())
    digest = hashlib.sha256(
        (baseline_root / "omniagents-gui-v1.json").read_bytes()
        + (baseline_root / "schemas/gui-v1.schema.json").read_bytes()
    ).hexdigest()
    assert baseline_manifest["canonical_sha256"] == digest
    assert baseline_manifest["protocol_version"] == "1.0.0"


def test_candidate_protocol_is_backward_compatible_with_released_baseline():
    """The additive-only v1 promise, enforced against the frozen baseline.

    The current candidate under ``protocol/openrpc/`` must classify as
    ``identical`` or ``additive`` against the immutable 1.0.0 release
    snapshot — never ``breaking``. This is the documented compatibility
    gate (docs/development/gui-protocol-governance.md): the baseline
    stays frozen and the candidate may only grow.
    """
    baseline_root = ROOT / "protocol/openrpc/baseline/1.0.0"
    baseline_document = json.loads(
        (baseline_root / "omniagents-gui-v1.json").read_text()
    )
    baseline_schema = json.loads(
        (baseline_root / "schemas/gui-v1.schema.json").read_text()
    )
    candidate_document, candidate_schema = _documents()
    report = compare_protocols(
        baseline_document, baseline_schema, candidate_document, candidate_schema
    )
    assert report.classification in ("identical", "additive"), report.violations
    assert report.violations == ()
