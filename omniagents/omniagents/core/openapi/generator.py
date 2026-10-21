"""Python code generator for OpenAPI tools.

Generates @function_tool decorated Python functions from OpenAPI operations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .parser import Operation, Parameter, get_security_scheme, parse_openapi_spec
from .type_mapper import get_required_imports, openapi_type_to_python


def _camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    # Insert underscore before uppercase letters
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    # Insert underscore before uppercase letters following lowercase
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _sanitize_param_name(name: str) -> str:
    """Sanitize a parameter name to be a valid Python identifier.

    Replaces hyphens with underscores and removes other invalid chars.
    """
    return name.replace("-", "_")


def _flatten_object_properties(
    prop_name: str, prop_schema: Dict[str, Any], prefix: str = ""
) -> List[tuple]:
    """Flatten nested object properties into a list of (name, schema, is_required) tuples.

    For nested objects, we flatten them with underscore-separated names.
    E.g., guest.first_name becomes guest_first_name
    """
    results = []

    # If it's an object with properties, flatten it
    if prop_schema.get("type") == "object" and prop_schema.get("properties"):
        nested_props = prop_schema.get("properties", {})
        nested_required = set(prop_schema.get("required", []))

        for nested_name, nested_schema in nested_props.items():
            full_name = (
                f"{prefix}{prop_name}_{nested_name}"
                if prefix == ""
                else f"{prefix}_{prop_name}_{nested_name}"
            )
            # Recursively flatten if it's also an object
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
        # Not an object, just return as-is
        results.append(
            (f"{prefix}{prop_name}" if prefix else prop_name, prop_schema, True)
        )

    return results


def _generate_function_signature(
    op: Operation, context_params: Optional[List[str]] = None
) -> str:
    """Generate the function signature for an operation."""
    func_name = _camel_to_snake(op.operation_id)
    params = []
    context_params_set = set(context_params or [])

    # Check if any path params are context params - if so, add ctx as first param
    has_context_param = any(
        p.location == "path" and p.name in context_params_set for p in op.parameters
    )
    if has_context_param:
        params.append("ctx: RunContextWrapper[Any]")

    # Path parameters first (required) - skip context params
    for param in op.parameters:
        if param.location == "path" and param.name not in context_params_set:
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

            # Check if this is a nested object that needs flattening
            if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                flattened = _flatten_object_properties(prop_name, prop_schema)
                for flat_name, flat_schema, nested_required in flattened:
                    type_str, _ = openapi_type_to_python(flat_schema)
                    # Nested fields are only required if parent is required AND field is required
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
                        # Already added above if required
                        if f"{flat_name}: {type_str}" not in params:
                            params.append(f"{flat_name}: Optional[{type_str}] = None")
            else:
                if not is_required:
                    type_str, _ = openapi_type_to_python(prop_schema)
                    params.append(f"{prop_name}: Optional[{type_str}] = None")

    params_str = ",\n    ".join(params) if params else ""
    if params_str:
        params_str = "\n    " + params_str + ",\n"

    return f"def {func_name}({params_str}) -> Dict[str, Any]:"


def _build_param_description(
    schema: Dict[str, Any],
    default_desc: str,
    is_required: bool = False,
    is_nullable: bool = False,
) -> str:
    """Build a parameter description including enum values, defaults, and format hints.

    Args:
        schema: OpenAPI schema for the parameter
        default_desc: Default description if none provided
        is_required: Whether the parameter is required
        is_nullable: Whether the parameter can be null

    Returns:
        Enhanced description string
    """
    parts = []

    # Required indicator at the start
    if is_required:
        parts.append("(Required)")

    # Base description
    desc = schema.get("description", default_desc)
    parts.append(desc)

    # Add nullable indicator
    if is_nullable or schema.get("nullable"):
        parts.append("Can be null")

    # Add enum values if present
    enum_values = schema.get("enum")
    if enum_values:
        enum_str = ", ".join(str(v) for v in enum_values)
        parts.append(f"Must be one of: {enum_str}")

    # Add numeric constraints
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

    # Add string length constraints
    length_constraints = []
    if schema.get("minLength") is not None:
        length_constraints.append(f"min {schema['minLength']}")
    if schema.get("maxLength") is not None:
        length_constraints.append(f"max {schema['maxLength']}")
    if length_constraints:
        parts.append(f"Length: {', '.join(length_constraints)} chars")

    # Add array constraints
    array_constraints = []
    if schema.get("minItems") is not None:
        array_constraints.append(f"min {schema['minItems']}")
    if schema.get("maxItems") is not None:
        array_constraints.append(f"max {schema['maxItems']}")
    if array_constraints:
        parts.append(f"Array size: {', '.join(array_constraints)} items")
    if schema.get("uniqueItems"):
        parts.append("Items must be unique")

    # Add pattern constraint
    pattern = schema.get("pattern")
    if pattern:
        # Provide human-readable hints for common patterns
        if pattern == r"^\d{4}-\d{2}-\d{2}$":
            parts.append("Format: YYYY-MM-DD")
        elif ":" in pattern and ("[0-9]" in pattern or "\\d" in pattern):
            parts.append("Format: HH:MM (24-hour)")
        else:
            parts.append(f"Pattern: {pattern}")

    # Add default value if present
    default = schema.get("default")
    if default is not None:
        parts.append(f"Default: {default}")

    # Add format hint if present (date, date-time, email, uri, uuid, etc.)
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
    """Generate a brief description of a schema structure for response docs.

    Args:
        schema: OpenAPI schema
        depth: Current nesting depth (to limit recursion)

    Returns:
        Brief description of the schema structure
    """
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

        # List key fields
        key_fields = list(props.keys())[:5]  # Limit to 5 fields
        fields_str = ", ".join(key_fields)
        if len(props) > 5:
            fields_str += f", ... (+{len(props) - 5} more)"
        return f"{{{fields_str}}}"

    # Primitive types
    return schema_type


def _generate_docstring(
    op: Operation, context_params: Optional[List[str]] = None
) -> str:
    """Generate the docstring for an operation."""
    lines = ['    """']
    context_params_set = set(context_params or [])

    # Summary
    if op.summary:
        lines.append(f"    {op.summary}")
    else:
        lines.append(f"    {op.operation_id}")

    # Description
    if op.description and op.description != op.summary:
        lines.append("")
        # Wrap long descriptions
        desc = op.description.replace("\n", " ").strip()
        lines.append(f"    {desc}")

    # Args section
    all_params = []

    # Track required params for path parameters - skip context params
    for param in op.parameters:
        # Skip context params - they're injected, not passed
        if param.name in context_params_set:
            continue
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

            # Check if this is a nested object that gets flattened
            if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                # Document each flattened property with its constraints
                flattened = _flatten_object_properties(prop_name, prop_schema)
                nested_required = set(prop_schema.get("required", []))
                for flat_name, flat_schema, nested_req in flattened:
                    # Extract the leaf name to check if it's in nested required
                    leaf_name = (
                        flat_name.split("_")[-1] if "_" in flat_name else flat_name
                    )
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
            # Truncate long descriptions
            if len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"        {name}: {desc}")

    # Returns section - document response structure
    if op.responses:
        lines.append("")
        lines.append("    Returns:")
        lines.append("        Dict with 'success' (bool) and either 'data' or 'error'.")

        # Document success response (usually 200 or 201)
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

        # Document common error responses
        error_codes = [c for c in op.responses.keys() if c.startswith(("4", "5"))]
        if error_codes:
            error_list = ", ".join(sorted(error_codes)[:3])
            if len(error_codes) > 3:
                error_list += f" (+{len(error_codes) - 3} more)"
            lines.append(f"        On error ({error_list}): error details in 'detail'")

    lines.append('    """')
    return "\n".join(lines)


def _build_nested_body_dict(properties: Dict[str, Any], prefix: str = "") -> str:
    """Build a dict literal for request body, reconstructing nested objects from flattened params."""
    parts = []

    for prop_name, prop_schema in properties.items():
        if prop_schema.get("type") == "object" and prop_schema.get("properties"):
            # Recursively build nested dict
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
                    # Further nesting
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


def _generate_function_body(
    op: Operation, context_params: Optional[List[str]] = None
) -> str:
    """Generate the function body for an operation."""
    lines = []
    context_params_set = set(context_params or [])

    # Build path_params dict
    path_params = [p for p in op.parameters if p.location == "path"]
    if path_params:
        path_parts = []
        for p in path_params:
            if p.name in context_params_set:
                # Use context getter function
                path_parts.append(f'"{p.name}": _get_{p.name}(ctx)')
            else:
                path_parts.append(f'"{p.name}": {p.name}')
        path_dict = ", ".join(path_parts)
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
    lines.append("    return _request(")
    lines.append(f'        method="{op.method}",')
    lines.append(f'        path="{op.path}",')
    lines.append("        path_params=path_params,")
    lines.append("        query_params=query_params,")
    lines.append("        body=body,")
    lines.append("    )")

    return "\n".join(lines)


def _generate_function(
    op: Operation, context_params: Optional[List[str]] = None
) -> str:
    """Generate a complete function for an operation."""
    parts = [
        "@function_tool",
        _generate_function_signature(op, context_params),
        _generate_docstring(op, context_params),
        _generate_function_body(op, context_params),
    ]
    return "\n".join(parts)


def _generate_context_getter(
    param_name: str,
    env_prefix: str,
    param_schema: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a context getter function for a context parameter.

    Args:
        param_name: Name of the parameter (e.g., "hotel_id")
        env_prefix: Environment variable prefix (e.g., "HOTEL_API")
        param_schema: OpenAPI schema for the parameter (to infer type)

    Returns:
        Python function code as a string
    """
    env_var = f"{env_prefix}_{param_name.upper()}"

    # Infer return type from schema
    py_type = "str"
    convert_value = "str(value)"
    convert_env = "env_value"

    if param_schema:
        inferred_type, _ = openapi_type_to_python(param_schema)
        if inferred_type == "int":
            py_type = "int"
            convert_value = "int(value)"
            convert_env = "int(env_value)"
        elif inferred_type == "float":
            py_type = "float"
            convert_value = "float(value)"
            convert_env = "float(env_value)"
        elif inferred_type == "bool":
            py_type = "bool"
            convert_value = "value.lower() in ('true', '1', 'yes') if isinstance(value, str) else bool(value)"
            convert_env = "env_value.lower() in ('true', '1', 'yes')"
        else:
            py_type = "str"
            convert_value = "str(value)"
            convert_env = "env_value"

    return f'''
def _get_{param_name}(ctx: RunContextWrapper[Any]) -> {py_type}:
    """Get {param_name} from runtime context or environment variable.

    Args:
        ctx: Runtime context wrapper

    Returns:
        {param_name} value

    Raises:
        ValueError: If {param_name} is not found in context or environment
    """
    # Try to get from runtime context first
    context = getattr(ctx, "context", None)
    if isinstance(context, dict):
        value = context.get("{param_name}")
        if value is not None:
            return {convert_value}

    # Fall back to environment variable
    env_value = os.environ.get("{env_var}")
    if env_value is not None:
        return {convert_env}

    raise ValueError(
        "{param_name} not found. Set {env_var} environment variable "
        "or provide {param_name} in runtime context."
    )
'''


def generate_tools_code(
    operations: List[Operation],
    api_name: str,
    auth_header: str = "X-API-Key",
    context_params: Optional[List[str]] = None,
) -> str:
    """Generate the complete Python module code for API tools.

    Args:
        operations: List of Operation objects to generate tools for
        api_name: API name for environment variable prefix (e.g., "hotel_api")
        auth_header: HTTP header name for API key authentication
        context_params: List of path parameter names to inject from runtime context

    Returns:
        Complete Python module source code as a string
    """
    # Convert api_name to uppercase for env vars
    env_prefix = api_name.upper()
    context_params = context_params or []

    # Determine required imports
    imports = get_required_imports(operations)
    imports_str = ", ".join(sorted(imports))

    # Build context param env var comments
    context_env_comments = ""
    if context_params:
        for param in context_params:
            context_env_comments += f"\n#   {env_prefix}_{param.upper()} - Default {param} (used if not in runtime context)"

    # Build RunContextWrapper import if needed
    run_context_import = ""
    if context_params:
        run_context_import = "\nfrom agents.run_context import RunContextWrapper"

    # Generate header
    header = f'''# Auto-generated from OpenAPI spec - DO NOT EDIT
# Generated by: omniagents generate openapi
#
# API Name: {api_name}
# Environment Variables:
#   {env_prefix}_BASE_URL - Base URL for the API
#   {env_prefix}_KEY - API key for authentication{context_env_comments}

from __future__ import annotations

import os
import requests
from typing import {imports_str}
{run_context_import}
from omniagents.core.tools import function_tool

_BASE_URL = os.environ.get("{env_prefix}_BASE_URL", "http://localhost:3000")
_API_KEY = os.environ.get("{env_prefix}_KEY", "")
_HEADERS = {{
    "{auth_header}": _API_KEY,
    "Content-Type": "application/json",
}}


def _request(
    method: str,
    path: str,
    path_params: Dict[str, Any] = None,
    query_params: Dict[str, Any] = None,
    body: Dict[str, Any] = None,
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

    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=_HEADERS,
            params=query or None,
            json=json_body,
            timeout=30,
        )
        resp.raise_for_status()
        return {{"success": True, "data": resp.json()}}
    except requests.HTTPError as e:
        error_data = {{"success": False, "error": str(e)}}
        if e.response is not None:
            error_data["status_code"] = e.response.status_code
            try:
                error_data["detail"] = e.response.json()
            except Exception:
                error_data["detail"] = e.response.text[:500]
        return error_data
    except requests.RequestException as e:
        return {{"success": False, "error": str(e)}}


# --- Generated API Tools ---

'''

    # Generate context getter functions if needed
    # First, build a map of context param names to their schemas
    context_param_schemas: Dict[str, Dict[str, Any]] = {}
    if context_params:
        for op in operations:
            for op_param in op.parameters:
                if (
                    op_param.name in context_params
                    and op_param.name not in context_param_schemas
                ):
                    context_param_schemas[op_param.name] = op_param.schema

    context_getters = ""
    if context_params:
        for param in context_params:
            schema = context_param_schemas.get(param)
            context_getters += _generate_context_getter(param, env_prefix, schema)
        context_getters += "\n"

    # Generate functions
    functions = []
    for op in operations:
        functions.append(_generate_function(op, context_params))

    return header + context_getters + "\n\n".join(functions) + "\n"


def generate_tools_file(
    spec_path: str,
    output_path: str,
    api_name: str,
    include_tags: Optional[List[str]] = None,
    exclude_operations: Optional[List[str]] = None,
    context_params: Optional[List[str]] = None,
) -> int:
    """Generate a Python tools file from an OpenAPI spec.

    Args:
        spec_path: Path to the OpenAPI spec file
        output_path: Path for the output Python file
        api_name: API name for environment variable prefix
        include_tags: Only include operations with these tags
        exclude_operations: Exclude operations with these IDs
        context_params: Path parameter names to inject from runtime context
            instead of requiring as function arguments. These will use
            ctx.context['param_name'] or fall back to ENV_PREFIX_PARAM_NAME.

    Returns:
        Number of tools generated
    """
    # Parse the spec
    operations = parse_openapi_spec(
        spec_path,
        include_tags=include_tags,
        exclude_operations=exclude_operations,
    )

    if not operations:
        raise ValueError("No operations found in the OpenAPI spec")

    # Get auth header from spec
    security = get_security_scheme(spec_path)
    auth_header = security["header"] if security else "X-API-Key"

    # Generate code
    code = generate_tools_code(operations, api_name, auth_header, context_params)

    # Write output file
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(code, encoding="utf-8")

    return len(operations)
