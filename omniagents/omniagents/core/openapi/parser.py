"""OpenAPI specification parser.

Parses OpenAPI 3.x specs and extracts operation definitions for tool generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    import jsonref
except ImportError:
    jsonref = None  # type: ignore


@dataclass
class Parameter:
    """Represents an OpenAPI parameter (path, query, header, or cookie)."""

    name: str
    location: str  # "path", "query", "header", "cookie"
    required: bool
    schema: Dict[str, Any]
    description: str = ""


@dataclass
class Operation:
    """Represents an OpenAPI operation (endpoint)."""

    operation_id: str
    path: str
    method: str
    summary: str
    description: str
    parameters: List[Parameter] = field(default_factory=list)
    request_body: Optional[Dict[str, Any]] = None
    responses: Optional[Dict[str, Any]] = None
    tags: List[str] = field(default_factory=list)


def _resolve_refs(spec: Dict[str, Any], spec_path: Path) -> Dict[str, Any]:
    """Resolve $ref references in the OpenAPI spec."""
    if jsonref is None:
        # Fallback: return spec without resolution
        # References will need to be resolved manually
        return spec

    # Convert to JSON and back through jsonref for resolution
    base_uri = spec_path.absolute().as_uri()
    resolved = jsonref.replace_refs(spec, base_uri=base_uri, lazy_load=False)
    # Convert JsonRef objects to regular dicts by serializing and deserializing
    # Use a custom encoder that handles JsonRef objects
    return _deep_convert(resolved)


def _deep_convert(obj: Any) -> Any:
    """Recursively convert JsonRef objects to plain Python objects."""
    if isinstance(obj, dict):
        return {k: _deep_convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_convert(item) for item in obj]
    else:
        return obj


def _extract_parameters(
    operation: Dict[str, Any], path_item: Dict[str, Any]
) -> List[Parameter]:
    """Extract parameters from operation and path item."""
    params = []

    # Path-level parameters
    for param in path_item.get("parameters", []):
        params.append(_parse_parameter(param))

    # Operation-level parameters (override path-level)
    seen_names = {p.name for p in params}
    for param in operation.get("parameters", []):
        parsed = _parse_parameter(param)
        if parsed.name in seen_names:
            # Override path-level parameter
            params = [p for p in params if p.name != parsed.name]
        params.append(parsed)

    return params


def _parse_parameter(param: Dict[str, Any]) -> Parameter:
    """Parse a single parameter definition."""
    # Sanitize name to be a valid Python identifier (replace hyphens with underscores)
    name = param.get("name", "").replace("-", "_")
    return Parameter(
        name=name,
        location=param.get("in", "query"),
        required=param.get("required", False),
        schema=param.get("schema", {"type": "string"}),
        description=param.get("description", ""),
    )


def _extract_request_body(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract request body schema from operation."""
    request_body = operation.get("requestBody")
    if not request_body:
        return None

    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    schema = json_content.get("schema", {})

    if not schema:
        return None

    return {
        "required": request_body.get("required", False),
        "schema": schema,
    }


def _extract_responses(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract response schemas from operation."""
    responses = operation.get("responses", {})
    if not responses:
        return None

    result = {}
    for status_code, response in responses.items():
        if not isinstance(response, dict):
            continue

        response_info = {
            "description": response.get("description", ""),
        }

        # Extract schema from content
        content = response.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})

        if schema:
            response_info["schema"] = schema

        result[status_code] = response_info

    return result if result else None


def parse_openapi_spec(
    spec_path: str,
    include_tags: Optional[List[str]] = None,
    exclude_operations: Optional[List[str]] = None,
) -> List[Operation]:
    """Parse an OpenAPI spec and return a list of operations.

    Args:
        spec_path: Path to the OpenAPI spec file (YAML or JSON)
        include_tags: If provided, only include operations with these tags
        exclude_operations: If provided, exclude operations with these IDs

    Returns:
        List of Operation objects representing API endpoints
    """
    path = Path(spec_path)
    if not path.exists():
        raise FileNotFoundError(f"OpenAPI spec not found: {spec_path}")

    # Load the spec
    with open(path, "r", encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            spec = yaml.safe_load(f)
        else:
            spec = json.load(f)

    # Resolve $ref references
    spec = _resolve_refs(spec, path)

    operations = []
    exclude_set = set(exclude_operations or [])

    for path_str, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue

        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            if method not in path_item:
                continue

            operation = path_item[method]
            if not isinstance(operation, dict):
                continue

            operation_id = operation.get("operationId")
            if not operation_id:
                # Generate operationId from method + path
                # e.g., GET /data/obs/{regionCode}/recent -> get_data_obs_regionCode_recent
                path_part = path_str.lstrip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
                operation_id = f"{method}_{path_part}"

            if operation_id in exclude_set:
                continue

            tags = operation.get("tags", [])
            if include_tags and not any(tag in include_tags for tag in tags):
                continue

            operations.append(
                Operation(
                    operation_id=operation_id,
                    path=path_str,
                    method=method.upper(),
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    parameters=_extract_parameters(operation, path_item),
                    request_body=_extract_request_body(operation),
                    responses=_extract_responses(operation),
                    tags=tags,
                )
            )

    return operations


def get_security_scheme(spec_path: str) -> Optional[Dict[str, Any]]:
    """Extract the primary security scheme from an OpenAPI spec.

    Returns the first API key security scheme found, if any.
    """
    path = Path(spec_path)
    with open(path, "r", encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            spec = yaml.safe_load(f)
        else:
            spec = json.load(f)

    components = spec.get("components", {})
    security_schemes = components.get("securitySchemes", {})

    for name, scheme in security_schemes.items():
        if scheme.get("type") == "apiKey":
            return {
                "name": name,
                "header": scheme.get("name", "X-API-Key"),
                "in": scheme.get("in", "header"),
            }

    return None
