"""Runtime tool loader for OpenAPI specs.

Creates function tools directly from OpenAPI operations without code generation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import requests

from agents.tool import FunctionTool

from .parser import Operation, parse_openapi_spec, get_security_scheme


def _camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _make_request(
    base_url: str,
    headers: Dict[str, str],
    method: str,
    path: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    body: Optional[Dict[str, Any]],
    timeout: int = 30,
) -> Dict[str, Any]:
    """Make an HTTP request and return standardized result."""
    url = f"{base_url}{path.format(**path_params)}"
    query = {k: v for k, v in query_params.items() if v is not None}
    json_body = {k: v for k, v in (body or {}).items() if v is not None} or None

    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query or None,
            json=json_body,
            timeout=timeout,
        )
        resp.raise_for_status()
        return {"success": True, "data": resp.json()}
    except requests.HTTPError as e:
        error_data = {"success": False, "error": str(e)}
        if e.response is not None:
            error_data["status_code"] = e.response.status_code
            try:
                error_data["detail"] = e.response.json()
            except Exception:
                error_data["detail"] = e.response.text[:500]
        return error_data
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


def _build_json_schema(op: Operation) -> Dict[str, Any]:
    """Build JSON schema for tool parameters from operation."""
    properties = {}
    required = []

    # Path parameters (always required)
    for param in op.parameters:
        if param.location == "path":
            prop = {
                "type": param.schema.get("type", "string"),
                "description": param.description or f"{param.name} path parameter",
            }
            # For array types, must include items schema
            if param.schema.get("type") == "array":
                prop["items"] = param.schema.get("items", {"type": "string"})
            properties[param.name] = prop
            required.append(param.name)

    # Query parameters
    for param in op.parameters:
        if param.location == "query":
            prop = {
                "type": param.schema.get("type", "string"),
                "description": param.description or f"{param.name} query parameter",
            }
            if param.schema.get("enum"):
                prop["enum"] = param.schema["enum"]
            # For array types, must include items schema
            if param.schema.get("type") == "array":
                prop["items"] = param.schema.get("items", {"type": "string"})
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

    # Request body properties
    if op.request_body:
        schema = op.request_body.get("schema", {})
        body_props = schema.get("properties", {})
        body_required = set(schema.get("required", []))

        for prop_name, prop_schema in body_props.items():
            properties[prop_name] = {
                "type": prop_schema.get("type", "string"),
                "description": prop_schema.get("description", f"{prop_name} field"),
            }
            if prop_name in body_required:
                required.append(prop_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _create_tool(
    op: Operation,
    base_url: str,
    headers: Dict[str, str],
) -> FunctionTool:
    """Create a FunctionTool from an Operation."""
    func_name = _camel_to_snake(op.operation_id)
    description = op.summary or op.description or op.operation_id

    # Build the JSON schema for parameters
    params_schema = _build_json_schema(op)

    # Create the invoke handler
    async def on_invoke(ctx, input_json: str) -> Any:
        import json

        args = json.loads(input_json) if input_json else {}

        # Separate path, query, and body params
        path_params = {}
        query_params = {}
        body = {}

        path_param_names = {p.name for p in op.parameters if p.location == "path"}
        query_param_names = {p.name for p in op.parameters if p.location == "query"}

        for key, value in args.items():
            if key in path_param_names:
                path_params[key] = value
            elif key in query_param_names:
                query_params[key] = value
            else:
                body[key] = value

        result = _make_request(
            base_url=base_url,
            headers=headers,
            method=op.method,
            path=op.path,
            path_params=path_params,
            query_params=query_params,
            body=body if body else None,
        )
        return str(result)

    return FunctionTool(
        name=func_name,
        description=description,
        params_json_schema=params_schema,
        on_invoke_tool=on_invoke,
        strict_json_schema=True,
    )


def load_tools(
    spec_path: str,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    auth_header: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    include_tags: Optional[List[str]] = None,
    exclude_operations: Optional[List[str]] = None,
) -> List[FunctionTool]:
    """Load tools directly from an OpenAPI spec.

    This creates FunctionTool instances at runtime without code generation.

    Args:
        spec_path: Path to the OpenAPI spec file (JSON or YAML)
        base_url: Base URL for API requests
        api_key: API key for authentication
        auth_header: Header name for API key (auto-detected from spec if not provided)
        headers: Additional headers to include in requests
        include_tags: Only include operations with these tags
        exclude_operations: Exclude operations with these IDs

    Returns:
        List of FunctionTool instances ready to use with an Agent

    Example:
        tools = load_tools(
            "ebird_spec.json",
            base_url="https://api.ebird.org/v2",
            api_key=os.environ["EBIRD_API_KEY"],
        )
        agent = Agent(tools=tools)
    """
    # Parse the spec
    operations = parse_openapi_spec(
        spec_path,
        include_tags=include_tags,
        exclude_operations=exclude_operations,
    )

    if not operations:
        raise ValueError("No operations found in the OpenAPI spec")

    # Get auth header from spec if not provided
    if auth_header is None:
        security = get_security_scheme(spec_path)
        auth_header = security["header"] if security else "X-API-Key"

    # Build headers
    request_headers = {"Content-Type": "application/json"}
    if api_key:
        request_headers[auth_header] = api_key
    if headers:
        request_headers.update(headers)

    # Use base_url from spec if not provided
    if base_url is None:
        raise ValueError("base_url is required")

    # Create tools
    tools = []
    for op in operations:
        tool = _create_tool(op, base_url, request_headers)
        tools.append(tool)

    return tools
