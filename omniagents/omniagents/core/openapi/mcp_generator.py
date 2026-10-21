"""MCP server code generator for OpenAPI specs.

Generates FastMCP server files with @mcp.tool decorated async functions from OpenAPI operations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .parser import Operation, Parameter, get_security_scheme, parse_openapi_spec
from .type_mapper import get_required_imports, openapi_type_to_python


def _camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _flatten_object_properties(
    prop_name: str, prop_schema: Dict[str, Any], prefix: str = ""
) -> List[tuple]:
    """Flatten nested object properties into a list of (name, schema, is_required) tuples.

    For nested objects, we flatten them with underscore-separated names.
    E.g., guest.first_name becomes guest_first_name
    """
    results = []

    if prop_schema.get("type") == "object" and prop_schema.get("properties"):
        nested_props = prop_schema.get("properties", {})
        nested_required = set(prop_schema.get("required", []))

        for nested_name, nested_schema in nested_props.items():
            full_name = (
                f"{prefix}{prop_name}_{nested_name}"
                if prefix == ""
                else f"{prefix}_{prop_name}_{nested_name}"
            )
            if nested_schema.get("type") == "object" and nested_schema.get(
                "properties"
            ):
                results.extend(
                    _flatten_object_properties(
                        nested_name,
                        nested_schema,
                        (
                            f"{prefix}{prop_name}_"
                            if prefix == ""
                            else f"{prefix}_{prop_name}_"
                        ),
                    )
                )
            else:
                results.append(
                    (full_name, nested_schema, nested_name in nested_required)
                )
    else:
        results.append(
            (f"{prefix}{prop_name}" if prefix else prop_name, prop_schema, True)
        )

    return results


def _build_param_description(
    schema: Dict[str, Any],
    default_desc: str,
    is_required: bool = False,
    is_nullable: bool = False,
) -> str:
    """Build a parameter description including enum values, defaults, and format hints."""
    parts = []

    if is_required:
        parts.append("(Required)")

    desc = schema.get("description", default_desc)
    parts.append(desc)

    if is_nullable or schema.get("nullable"):
        parts.append("Can be null")

    enum_values = schema.get("enum")
    if enum_values:
        enum_str = ", ".join(str(v) for v in enum_values)
        parts.append(f"Must be one of: {enum_str}")

    constraints = []
    if schema.get("minimum") is not None:
        constraints.append(f">= {schema['minimum']}")
    if schema.get("exclusiveMinimum") is not None:
        constraints.append(f"> {schema['exclusiveMinimum']}")
    if schema.get("maximum") is not None:
        constraints.append(f"<= {schema['maximum']}")
    if schema.get("exclusiveMaximum") is not None:
        constraints.append(f"< {schema['exclusiveMaximum']}")
    if constraints:
        parts.append(f"Value {', '.join(constraints)}")

    length_constraints = []
    if schema.get("minLength") is not None:
        length_constraints.append(f"min {schema['minLength']}")
    if schema.get("maxLength") is not None:
        length_constraints.append(f"max {schema['maxLength']}")
    if length_constraints:
        parts.append(f"Length: {', '.join(length_constraints)} chars")

    array_constraints = []
    if schema.get("minItems") is not None:
        array_constraints.append(f"min {schema['minItems']}")
    if schema.get("maxItems") is not None:
        array_constraints.append(f"max {schema['maxItems']}")
    if array_constraints:
        parts.append(f"Array size: {', '.join(array_constraints)} items")
    if schema.get("uniqueItems"):
        parts.append("Items must be unique")

    pattern = schema.get("pattern")
    if pattern:
        if pattern == r"^\d{4}-\d{2}-\d{2}$":
            parts.append("Format: YYYY-MM-DD")
        elif ":" in pattern and ("[0-9]" in pattern or "\\d" in pattern):
            parts.append("Format: HH:MM (24-hour)")
        else:
            parts.append(f"Pattern: {pattern}")

    default = schema.get("default")
    if default is not None:
        parts.append(f"Default: {default}")

    format_hint = schema.get("format")
    if format_hint:
        format_map = {
            "date": "YYYY-MM-DD",
            "date-time": "ISO 8601 datetime",
            "email": "email address",
            "uri": "URL",
            "uuid": "UUID",
            "int32": "32-bit integer",
            "int64": "64-bit integer",
            "float": "float",
            "double": "double precision",
            "byte": "base64 encoded",
            "binary": "binary data",
            "password": "password (sensitive)",
        }
        readable = format_map.get(format_hint, format_hint)
        parts.append(f"Format: {readable}")

    return ". ".join(parts)


def _describe_schema_briefly(schema: Dict[str, Any], depth: int = 0) -> str:
    """Generate a brief description of a schema structure for response docs."""
    if depth > 2:
        return "..."

    schema_type = schema.get("type", "object")

    if schema_type == "array":
        items = schema.get("items", {})
        item_desc = _describe_schema_briefly(items, depth + 1)
        return f"[{item_desc}]"

    if schema_type == "object":
        props = schema.get("properties", {})
        if not props:
            return "object"

        key_fields = list(props.keys())[:5]
        fields_str = ", ".join(key_fields)
        if len(props) > 5:
            fields_str += f", ... (+{len(props) - 5} more)"
        return f"{{{fields_str}}}"

    return schema_type


def _build_nested_body_dict(properties: Dict[str, Any], prefix: str = "") -> str:
    """Build a dict literal for request body, reconstructing nested objects from flattened params."""
    parts = []

    for prop_name, prop_schema in properties.items():
        if prop_schema.get("type") == "object" and prop_schema.get("properties"):
            nested_parts = []
            nested_props = prop_schema.get("properties", {})
            for nested_name, nested_schema in nested_props.items():
                param_name = (
                    f"{prefix}{prop_name}_{nested_name}"
                    if prefix == ""
                    else f"{prefix}_{prop_name}_{nested_name}"
                )
                if nested_schema.get("type") == "object" and nested_schema.get(
                    "properties"
                ):
                    nested_dict = _build_nested_body_dict(
                        {nested_name: nested_schema},
                        (
                            f"{prefix}{prop_name}_"
                            if prefix == ""
                            else f"{prefix}_{prop_name}_"
                        ),
                    )
                    nested_parts.append(nested_dict)
                else:
                    nested_parts.append(f'"{nested_name}": {param_name}')
            parts.append(f'"{prop_name}": {{{", ".join(nested_parts)}}}')
        else:
            param_name = f"{prefix}{prop_name}" if prefix else prop_name
            parts.append(f'"{prop_name}": {param_name}')

    return ", ".join(parts)


# --- MCP-specific generation functions ---


def _generate_mcp_function_signature(op: Operation) -> str:
    """Generate the async function signature for an MCP tool operation."""
    func_name = _camel_to_snake(op.operation_id)
    params = []

    # Path parameters first (required)
    for param in op.parameters:
        if param.location == "path":
            type_str, _ = openapi_type_to_python(param.schema)
            params.append(f"{param.name}: {type_str}")

    # Required query/header parameters
    for param in op.parameters:
        if param.location != "path" and param.required:
            type_str, _ = openapi_type_to_python(param.schema)
            params.append(f"{param.name}: {type_str}")

    # Request body parameters (if any) - flatten nested objects
    if op.request_body:
        schema = op.request_body.get("schema", {})
        properties = schema.get("properties", {})
        required_props = set(schema.get("required", []))

        required_params = []
        optional_params = []

        for prop_name, prop_schema in properties.items():
            is_required = prop_name in required_props

            if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                flattened = _flatten_object_properties(prop_name, prop_schema)
                for flat_name, flat_schema, nested_required in flattened:
                    type_str, _ = openapi_type_to_python(flat_schema)
                    if is_required and nested_required:
                        required_params.append(f"{flat_name}: {type_str}")
                    else:
                        optional_params.append(
                            f"{flat_name}: Optional[{type_str}] = None"
                        )
            else:
                type_str, _ = openapi_type_to_python(prop_schema)
                if is_required:
                    required_params.append(f"{prop_name}: {type_str}")
                else:
                    optional_params.append(f"{prop_name}: Optional[{type_str}] = None")

        params.extend(required_params)

    # Optional query/header parameters
    for param in op.parameters:
        if param.location != "path" and not param.required:
            type_str, _ = openapi_type_to_python(param.schema)
            params.append(f"{param.name}: Optional[{type_str}] = None")

    # Add optional body params
    if op.request_body:
        schema = op.request_body.get("schema", {})
        properties = schema.get("properties", {})
        required_props = set(schema.get("required", []))

        for prop_name, prop_schema in properties.items():
            is_required = prop_name in required_props

            if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                flattened = _flatten_object_properties(prop_name, prop_schema)
                for flat_name, flat_schema, nested_required in flattened:
                    type_str, _ = openapi_type_to_python(flat_schema)
                    if not (is_required and nested_required):
                        if f"{flat_name}: {type_str}" not in params:
                            params.append(f"{flat_name}: Optional[{type_str}] = None")
            else:
                if not is_required:
                    type_str, _ = openapi_type_to_python(prop_schema)
                    params.append(f"{prop_name}: Optional[{type_str}] = None")

    params_str = ",\n    ".join(params) if params else ""
    if params_str:
        params_str = "\n    " + params_str + ",\n"

    return f"async def {func_name}({params_str}) -> Dict[str, Any]:"


def _generate_mcp_docstring(op: Operation) -> str:
    """Generate the docstring for an MCP tool operation."""
    lines = ['    """']

    if op.summary:
        lines.append(f"    {op.summary}")
    else:
        lines.append(f"    {op.operation_id}")

    if op.description and op.description != op.summary:
        lines.append("")
        desc = op.description.replace("\n", " ").strip()
        lines.append(f"    {desc}")

    all_params = []

    for param in op.parameters:
        is_required = param.required or param.location == "path"
        desc = _build_param_description(
            param.schema,
            param.description or f"{param.name} parameter",
            is_required=is_required,
        )
        all_params.append((param.name, desc))

    if op.request_body:
        schema = op.request_body.get("schema", {})
        properties = schema.get("properties", {})
        required_props = set(schema.get("required", []))

        for prop_name, prop_schema in properties.items():
            is_required = prop_name in required_props
            is_nullable = prop_schema.get("nullable", False)

            if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                flattened = _flatten_object_properties(prop_name, prop_schema)
                for flat_name, flat_schema, nested_req in flattened:
                    param_required = is_required and nested_req
                    desc = _build_param_description(
                        flat_schema,
                        f"{flat_name.replace('_', ' ')}",
                        is_required=param_required,
                        is_nullable=flat_schema.get("nullable", False),
                    )
                    all_params.append((flat_name, desc))
            else:
                desc = _build_param_description(
                    prop_schema,
                    f"{prop_name} field",
                    is_required=is_required,
                    is_nullable=is_nullable,
                )
                all_params.append((prop_name, desc))

    if all_params:
        lines.append("")
        lines.append("    Args:")
        for name, desc in all_params:
            if len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"        {name}: {desc}")

    if op.responses:
        lines.append("")
        lines.append("    Returns:")
        lines.append("        Dict with 'success' (bool) and either 'data' or 'error'.")

        for status in ["200", "201", "204"]:
            if status in op.responses:
                resp = op.responses[status]
                resp_schema = resp.get("schema", {})
                if resp_schema:
                    schema_desc = _describe_schema_briefly(resp_schema)
                    lines.append(f"        On success ({status}): {schema_desc}")
                elif status == "204":
                    lines.append(f"        On success ({status}): No content")
                break

        error_codes = [c for c in op.responses.keys() if c.startswith(("4", "5"))]
        if error_codes:
            error_list = ", ".join(sorted(error_codes)[:3])
            if len(error_codes) > 3:
                error_list += f" (+{len(error_codes) - 3} more)"
            lines.append(f"        On error ({error_list}): error details in 'detail'")

    lines.append('    """')
    return "\n".join(lines)


def _generate_mcp_function_body(op: Operation) -> str:
    """Generate the async function body for an MCP tool operation."""
    lines = []

    # Build path_params dict
    path_params = [p for p in op.parameters if p.location == "path"]
    if path_params:
        path_dict = ", ".join(f'"{p.name}": {p.name}' for p in path_params)
        lines.append(f"    path_params = {{{path_dict}}}")
    else:
        lines.append("    path_params = {}")

    # Build query_params dict
    query_params = [p for p in op.parameters if p.location == "query"]
    if query_params:
        query_dict = ", ".join(f'"{p.name}": {p.name}' for p in query_params)
        lines.append(f"    query_params = {{{query_dict}}}")
    else:
        lines.append("    query_params = {}")

    # Build body dict from request body, handling nested objects
    if op.request_body:
        schema = op.request_body.get("schema", {})
        properties = schema.get("properties", {})
        if properties:
            body_dict = _build_nested_body_dict(properties)
            lines.append(f"    body = {{{body_dict}}}")
        else:
            lines.append("    body = {}")
    else:
        lines.append("    body = None")

    lines.append("")
    lines.append("    return await _request(")
    lines.append(f'        method="{op.method}",')
    lines.append(f'        path="{op.path}",')
    lines.append("        path_params=path_params,")
    lines.append("        query_params=query_params,")
    lines.append("        body=body,")
    lines.append("    )")

    return "\n".join(lines)


def _generate_mcp_function(op: Operation) -> str:
    """Generate a complete @mcp.tool decorated async function for an operation."""
    parts = [
        "@mcp.tool",
        _generate_mcp_function_signature(op),
        _generate_mcp_docstring(op),
        _generate_mcp_function_body(op),
    ]
    return "\n".join(parts)


def generate_mcp_server_code(
    operations: List[Operation],
    server_name: str,
    api_name: str,
    auth_header: str = "X-API-Key",
    default_base_url: str = "http://localhost:3000",
) -> str:
    """Generate the complete MCP server Python module code.

    Args:
        operations: List of Operation objects to generate tools for
        server_name: Display name for the MCP server
        api_name: API name for environment variable prefix (e.g., "hotel_api")
        auth_header: HTTP header name for API key authentication
        default_base_url: Default base URL for the API

    Returns:
        Complete Python module source code as a string
    """
    env_prefix = api_name.upper()

    imports = get_required_imports(operations)
    imports_str = ", ".join(sorted(imports))

    header = f'''#!/usr/bin/env python3
"""MCP Server: {server_name}

Auto-generated from OpenAPI spec by: omniagents generate mcp
Do not edit manually - regenerate from source spec instead.

Environment Variables:
    {env_prefix}_BASE_URL - API base URL (default: {default_base_url})
    {env_prefix}_KEY - API key for authentication

Run: python <this_file>.py
"""
from __future__ import annotations

import os
import httpx
from typing import {imports_str}

from fastmcp import FastMCP

# --- Configuration ---
_BASE_URL = os.environ.get("{env_prefix}_BASE_URL", "{default_base_url}")
_API_KEY = os.environ.get("{env_prefix}_KEY", "")
_HEADERS = {{
    "{auth_header}": _API_KEY,
    "Content-Type": "application/json",
}}

# --- MCP Server ---
mcp = FastMCP("{server_name}")


# --- HTTP Helper ---
async def _request(
    method: str,
    path: str,
    path_params: Dict[str, Any] | None = None,
    query_params: Dict[str, Any] | None = None,
    body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Make an HTTP request to the API.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)
        path: URL path with optional {{param}} placeholders
        path_params: Values for path placeholders
        query_params: Query string parameters
        body: JSON request body

    Returns:
        Dictionary with success status and data or error
    """
    url = f"{{_BASE_URL}}{{path.format(**(path_params or {{}}))}}"
    query = {{k: v for k, v in (query_params or {{}}).items() if v is not None}}
    json_body = {{k: v for k, v in (body or {{}}).items() if v is not None}} or None

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=_HEADERS,
                params=query or None,
                json=json_body,
                timeout=30.0,
            )
            resp.raise_for_status()
            return {{"success": True, "data": resp.json()}}
        except httpx.HTTPStatusError as e:
            error_data = {{"success": False, "error": str(e)}}
            error_data["status_code"] = e.response.status_code
            try:
                error_data["detail"] = e.response.json()
            except Exception:
                error_data["detail"] = e.response.text[:500]
            return error_data
        except httpx.RequestError as e:
            return {{"success": False, "error": str(e)}}


# --- Generated Tools ---

'''

    footer = """

# --- Entry Point ---
if __name__ == "__main__":
    mcp.run(show_banner=False)
"""

    functions = []
    for op in operations:
        functions.append(_generate_mcp_function(op))

    return header + "\n\n".join(functions) + footer


def generate_mcp_server_file(
    spec_path: str,
    output_path: str,
    server_name: str,
    api_name: str,
    include_tags: Optional[List[str]] = None,
    exclude_operations: Optional[List[str]] = None,
) -> int:
    """Generate an MCP server Python file from an OpenAPI spec.

    Args:
        spec_path: Path to the OpenAPI spec file (YAML or JSON)
        output_path: Path for the output Python file
        server_name: Display name for the MCP server
        api_name: API name for environment variable prefix
        include_tags: Only include operations with these tags
        exclude_operations: Exclude operations with these IDs

    Returns:
        Number of tools generated
    """
    operations = parse_openapi_spec(
        spec_path,
        include_tags=include_tags,
        exclude_operations=exclude_operations,
    )

    if not operations:
        raise ValueError("No operations found in the OpenAPI spec")

    security = get_security_scheme(spec_path)
    auth_header = security["header"] if security else "X-API-Key"

    # Try to extract base URL from spec
    import yaml
    import json

    spec_file = Path(spec_path)
    content = spec_file.read_text(encoding="utf-8")
    if spec_file.suffix in (".yaml", ".yml"):
        spec_data = yaml.safe_load(content)
    else:
        spec_data = json.loads(content)

    servers = spec_data.get("servers", [])
    default_base_url = "http://localhost:3000"
    if servers and isinstance(servers[0], dict):
        default_base_url = servers[0].get("url", default_base_url)

    code = generate_mcp_server_code(
        operations, server_name, api_name, auth_header, default_base_url
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(code, encoding="utf-8")

    return len(operations)
