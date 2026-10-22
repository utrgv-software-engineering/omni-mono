"""OpenAPI to Python type mapping.

Converts OpenAPI schema types to Python type annotations.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def openapi_type_to_python(schema: Dict[str, Any]) -> Tuple[str, str]:
    """Convert an OpenAPI schema to a Python type annotation.

    Args:
        schema: OpenAPI schema object

    Returns:
        Tuple of (type_annotation, import_needed)
        - type_annotation: Python type as string (e.g., "str", "Optional[int]")
        - import_needed: Additional import needed (e.g., "List", "Dict")
    """
    if not schema:
        return "Any", "Any"

    openapi_type = schema.get("type", "string")
    format_ = schema.get("format")

    # Handle arrays
    if openapi_type == "array":
        items_schema = schema.get("items", {})
        item_type, item_import = openapi_type_to_python(items_schema)
        return f"List[{item_type}]", "List"

    # Handle objects
    if openapi_type == "object":
        return "Dict[str, Any]", "Dict"

    # Handle primitives
    type_map = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }

    python_type = type_map.get(openapi_type, "str")

    # Special formats (still return str but can document)
    # date, date-time, email, uri, etc. are all strings in Python
    if format_ in ("date", "date-time", "email", "uri", "uuid"):
        python_type = "str"

    return python_type, ""


def get_required_imports(operations: list) -> set:
    """Determine which typing imports are needed for a set of operations.

    Args:
        operations: List of Operation objects

    Returns:
        Set of import names needed (e.g., {"List", "Dict", "Optional"})
    """
    imports = {"Any", "Dict", "Optional"}  # Always need these

    for op in operations:
        for param in op.parameters:
            _, import_needed = openapi_type_to_python(param.schema)
            if import_needed:
                imports.add(import_needed)

        if op.request_body:
            schema = op.request_body.get("schema", {})
            for prop_schema in schema.get("properties", {}).values():
                _, import_needed = openapi_type_to_python(prop_schema)
                if import_needed:
                    imports.add(import_needed)

    return imports
