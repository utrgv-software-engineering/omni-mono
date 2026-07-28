import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_KEYWORDS = {
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "enum",
    "items",
    "oneOf",
    "properties",
    "required",
    "type",
    "x-omni-stability",
}


@dataclass(frozen=True)
class ProtocolCompatibilityReport:
    classification: str
    additions: tuple[str, ...]
    violations: tuple[str, ...]
    baseline_version: str
    candidate_version: str
    baseline_sha256: str = ""
    candidate_sha256: str = ""


def _method_map(document: dict) -> dict[str, dict]:
    return {method["name"]: method for method in document["methods"]}


def _resolve(schema: dict, root: dict) -> dict:
    reference = schema.get("$ref")
    if not reference:
        return schema
    marker = "#/$defs/"
    if marker not in reference:
        raise ValueError(f"Unsupported schema reference: {reference}")
    node: Any = root["$defs"]
    for part in reference.split(marker, 1)[1].split("/"):
        node = node[part]
    return node


def _variant_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _compare_schema(
    baseline: dict,
    candidate: dict,
    baseline_root: dict,
    candidate_root: dict,
    path: str,
    additions: list[str],
    violations: list[str],
    request: bool,
) -> None:
    baseline = _resolve(baseline, baseline_root)
    candidate = _resolve(candidate, candidate_root)
    changed_unsupported = sorted(
        keyword
        for keyword in set(baseline) | set(candidate)
        if keyword not in SUPPORTED_SCHEMA_KEYWORDS
        and baseline.get(keyword) != candidate.get(keyword)
    )
    violations.extend(
        f"{path} changed unsupported keyword {keyword}"
        for keyword in changed_unsupported
    )

    baseline_type = baseline.get("type")
    candidate_type = candidate.get("type")
    baseline_types = set(
        baseline_type if isinstance(baseline_type, list) else [baseline_type]
    )
    candidate_types = set(
        candidate_type if isinstance(candidate_type, list) else [candidate_type]
    )
    baseline_types.discard(None)
    candidate_types.discard(None)
    if request:
        if not baseline_types and candidate_types:
            violations.append(f"{path} added type constraint to unconstrained input")
        elif baseline_types and not candidate_types:
            additions.append(f"{path} removed type constraint")
        elif baseline_types and not baseline_types <= candidate_types:
            violations.append(
                f"{path} narrowed type from {sorted(baseline_types)} to {sorted(candidate_types)}"
            )
        elif candidate_types - baseline_types:
            additions.append(
                f"{path} widened type with {sorted(candidate_types - baseline_types)}"
            )
    elif baseline_types != candidate_types:
        violations.append(
            f"{path} changed output type from {sorted(baseline_types)} to {sorted(candidate_types)}"
        )

    if "const" in baseline and baseline.get("const") != candidate.get("const"):
        violations.append(f"{path} changed const value")

    baseline_enum = set(baseline.get("enum", []))
    candidate_enum = set(candidate.get("enum", []))
    if baseline_enum and not baseline_enum <= candidate_enum:
        violations.append(f"{path} narrowed enum values")
    for value in sorted(candidate_enum - baseline_enum, key=repr):
        additions.append(f"{path} enum value {value!r} added")

    for union_keyword in ("anyOf", "oneOf"):
        baseline_has_union = union_keyword in baseline
        candidate_has_union = union_keyword in candidate
        baseline_variants = {
            _variant_key(value) for value in baseline.get(union_keyword, [])
        }
        candidate_variants = {
            _variant_key(value) for value in candidate.get(union_keyword, [])
        }
        if request:
            if not baseline_has_union and candidate_has_union:
                violations.append(
                    f"{path} added {union_keyword} constraint to unconstrained input"
                )
            elif baseline_has_union and not candidate_has_union:
                additions.append(f"{path} removed {union_keyword} constraint")
            elif not baseline_variants <= candidate_variants:
                violations.append(f"{path} narrowed {union_keyword} variants")
            elif candidate_variants - baseline_variants:
                additions.append(f"{path} widened {union_keyword} variants")
        elif baseline_has_union != candidate_has_union:
            violations.append(f"{path} changed output {union_keyword} constraint")
        elif baseline_has_union and baseline_variants != candidate_variants:
            violations.append(f"{path} changed output {union_keyword} variants")

    if "items" in baseline:
        if "items" not in candidate:
            violations.append(f"{path} removed array item schema")
        else:
            _compare_schema(
                baseline["items"],
                candidate["items"],
                baseline_root,
                candidate_root,
                f"{path}.items",
                additions,
                violations,
                request,
            )

    baseline_properties = baseline.get("properties", {})
    candidate_properties = candidate.get("properties", {})
    baseline_required = set(baseline.get("required", []))
    candidate_required = set(candidate.get("required", []))
    for name in sorted(baseline_properties):
        child_path = f"{path}.{name}"
        if name not in candidate_properties:
            violations.append(f"{child_path} removed")
            continue
        _compare_schema(
            baseline_properties[name],
            candidate_properties[name],
            baseline_root,
            candidate_root,
            child_path,
            additions,
            violations,
            request,
        )
    for name in sorted(candidate_properties.keys() - baseline_properties.keys()):
        child_path = f"{path}.{name}"
        if request and name in candidate_required:
            violations.append(f"{child_path} added as required")
        else:
            requiredness = "required" if name in candidate_required else "optional"
            additions.append(f"{child_path} added ({requiredness})")
    if request:
        for name in sorted(candidate_required - baseline_required):
            if name in baseline_properties:
                violations.append(f"{path}.{name} became required")
    else:
        for name in sorted(baseline_required - candidate_required):
            violations.append(f"{path}.{name} became optional")

    baseline_additional = baseline.get("additionalProperties", True)
    candidate_additional = candidate.get("additionalProperties", True)
    if request and baseline_additional is not False and candidate_additional is False:
        violations.append(f"{path} now rejects unknown properties")
    if (
        not request
        and baseline_additional is False
        and candidate_additional is not False
    ):
        additions.append(f"{path} now permits unknown properties")


def compare_protocols(
    baseline_document: dict,
    baseline_schema: dict,
    candidate_document: dict,
    candidate_schema: dict,
    *,
    baseline_sha256: str = "",
    candidate_sha256: str = "",
) -> ProtocolCompatibilityReport:
    additions: list[str] = []
    violations: list[str] = []
    baseline_methods = _method_map(baseline_document)
    candidate_methods = _method_map(candidate_document)
    baseline_stable = {
        name: method
        for name, method in baseline_methods.items()
        if method["x-omni-stability"] == "stable"
    }

    for name in sorted(baseline_stable):
        baseline_method = baseline_stable[name]
        candidate_method = candidate_methods.get(name)
        if candidate_method is None:
            violations.append(f"stable method {name} removed")
            continue
        if candidate_method["x-omni-stability"] != "stable":
            violations.append(
                f"stable method {name} changed stability to {candidate_method['x-omni-stability']}"
            )
            continue
        for metadata in ("x-omni-direction", "x-omni-kind"):
            if baseline_method.get(metadata) != candidate_method.get(metadata):
                label = metadata.removeprefix("x-omni-")
                violations.append(
                    f"stable method {name} changed {label} from "
                    f"{baseline_method.get(metadata)} to {candidate_method.get(metadata)}"
                )
        if baseline_method.get("x-omni-correlation") != candidate_method.get(
            "x-omni-correlation"
        ):
            violations.append(f"stable method {name} changed correlation metadata")
        baseline_errors = {
            _variant_key(error) for error in baseline_method.get("errors", [])
        }
        candidate_errors = {
            _variant_key(error) for error in candidate_method.get("errors", [])
        }
        if not baseline_errors <= candidate_errors:
            violations.append(f"stable method {name} removed structured errors")
        _compare_schema(
            baseline_method["x-omni-params-schema"],
            candidate_method["x-omni-params-schema"],
            baseline_schema,
            candidate_schema,
            f"{name}.params",
            additions,
            violations,
            baseline_method["x-omni-direction"] == "client_to_server",
        )
        _compare_schema(
            baseline_method["result"]["schema"],
            candidate_method["result"]["schema"],
            baseline_schema,
            candidate_schema,
            f"{name}.result",
            additions,
            violations,
            False,
        )

    for name in sorted(candidate_methods.keys() - baseline_methods.keys()):
        additions.append(
            f"method {name} added ({candidate_methods[name]['x-omni-stability']})"
        )

    ordered_additions = tuple(sorted(set(additions)))
    ordered_violations = tuple(sorted(set(violations)))
    if ordered_violations:
        classification = "breaking"
    elif ordered_additions:
        classification = "additive"
    else:
        classification = "identical"
    return ProtocolCompatibilityReport(
        classification=classification,
        additions=ordered_additions,
        violations=ordered_violations,
        baseline_version=baseline_document["info"]["version"],
        candidate_version=candidate_document["info"]["version"],
        baseline_sha256=baseline_sha256,
        candidate_sha256=candidate_sha256,
    )


def _read_protocol(root: Path) -> tuple[dict, dict, dict, str]:
    openrpc_path = root / "omniagents-gui-v1.json"
    schema_path = root / "schemas/gui-v1.schema.json"
    manifest_path = root / "manifest.json"
    openrpc_bytes = openrpc_path.read_bytes()
    schema_bytes = schema_path.read_bytes()
    document = json.loads(openrpc_bytes)
    schema = json.loads(schema_bytes)
    manifest = json.loads(manifest_path.read_text())
    digest = hashlib.sha256(openrpc_bytes + schema_bytes).hexdigest()
    if manifest["canonical_sha256"] != digest:
        raise ValueError(
            f"Canonical digest mismatch in {manifest_path}: expected "
            f"{manifest['canonical_sha256']}, actual {digest}"
        )
    return document, schema, manifest, digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        baseline_document, baseline_schema, _, baseline_digest = _read_protocol(
            args.baseline_root
        )
        candidate_document, candidate_schema, _, candidate_digest = _read_protocol(
            args.candidate_root
        )
        report = compare_protocols(
            baseline_document,
            baseline_schema,
            candidate_document,
            candidate_schema,
            baseline_sha256=baseline_digest,
            candidate_sha256=candidate_digest,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"GUI protocol compatibility check failed: {error}")
        raise SystemExit(1) from error
    payload = asdict(report)
    if args.json_output:
        args.json_output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
    print(
        f"GUI protocol compatibility: {report.classification} "
        f"({report.baseline_version} -> {report.candidate_version})"
    )
    for addition in report.additions:
        print(f"ADD: {addition}")
    for violation in report.violations:
        print(f"BREAK: {violation}")
    raise SystemExit(1 if report.classification == "breaking" else 0)


if __name__ == "__main__":
    main()
