"""Guardrail discovery functionality for YAML-based agent configuration."""

import os
import importlib
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import traceback

from agents.guardrail import InputGuardrail, OutputGuardrail
from agents.tool_guardrails import ToolInputGuardrail, ToolOutputGuardrail
from omniagents.core.debug import Debug


def discover_guardrails_in_dir(
    base_dir: str = "guardrails", debug: Optional[bool] = None
) -> Tuple[
    Dict[str, InputGuardrail[Any]],
    Dict[str, OutputGuardrail[Any]],
    Dict[str, ToolInputGuardrail[Any]],
    Dict[str, ToolOutputGuardrail[Any]],
]:
    """
    Discovers Input/Output and ToolInput/ToolOutput Guardrail instances in
    Python files.

    Similar to tool discovery, this function scans a directory for Python files
    and identifies guardrails by checking if objects are instances of the four
    SDK guardrail classes.

    Args:
        base_dir: The directory path (relative or absolute) to scan for .py files.
        debug: Deprecated parameter, uses Debug.enabled() instead.

    Returns:
        A tuple of four dictionaries:
        - input_guardrails: agent-input guardrails
        - output_guardrails: agent-output guardrails
        - tool_input_guardrails: per-tool input guardrails (SDK 0.7+)
        - tool_output_guardrails: per-tool output guardrails (SDK 0.7+)
    """
    input_guardrails: Dict[str, InputGuardrail[Any]] = {}
    output_guardrails: Dict[str, OutputGuardrail[Any]] = {}
    tool_input_guardrails: Dict[str, ToolInputGuardrail[Any]] = {}
    tool_output_guardrails: Dict[str, ToolOutputGuardrail[Any]] = {}

    base_path = Path(base_dir).resolve()

    if not base_path.is_dir():
        Debug.log(
            f"Warning: Guardrail discovery directory '{base_dir}' not found or is not a directory."
        )
        return (
            input_guardrails,
            output_guardrails,
            tool_input_guardrails,
            tool_output_guardrails,
        )

    Debug.log(f"Starting guardrail discovery in directory: {base_path}")

    parent_path = str(base_path.parent)
    base_path_str = str(base_path)
    original_sys_path = sys.path[:]
    paths_added = []

    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)
        paths_added.append(parent_path)

    if base_path_str not in sys.path:
        sys.path.insert(0, base_path_str)
        paths_added.append(base_path_str)

    try:
        for filepath in base_path.rglob("*.py"):
            if filepath.name == "__init__.py":
                continue

            try:
                relative_path = filepath.relative_to(base_path.parent)
                module_path = str(relative_path.with_suffix("")).replace(os.sep, ".")
            except ValueError:
                print(
                    f"Warning: Skipping file {filepath} as it seems outside the expected parent directory {base_path.parent}"
                )
                continue

            Debug.log(f"Attempting to import module: {module_path} from {filepath}")

            try:
                module = importlib.import_module(module_path)

                for name, obj in inspect.getmembers(module):
                    # Skip private attributes
                    if name.startswith("_"):
                        continue

                    # Check if it's an InputGuardrail instance
                    if isinstance(obj, InputGuardrail):
                        guardrail_name = (
                            obj.get_name() if hasattr(obj, "get_name") else name
                        )
                        if guardrail_name in input_guardrails:
                            print(
                                f"Warning: Duplicate input guardrail name '{guardrail_name}' found (from {module_path}). Overwriting previous definition."
                            )
                        Debug.log(f"  Discovered input guardrail: {guardrail_name}")
                        input_guardrails[guardrail_name] = obj

                    # Check if it's an OutputGuardrail instance
                    elif isinstance(obj, OutputGuardrail):
                        guardrail_name = (
                            obj.get_name() if hasattr(obj, "get_name") else name
                        )
                        if guardrail_name in output_guardrails:
                            print(
                                f"Warning: Duplicate output guardrail name '{guardrail_name}' found (from {module_path}). Overwriting previous definition."
                            )
                        Debug.log(f"  Discovered output guardrail: {guardrail_name}")
                        output_guardrails[guardrail_name] = obj

                    # Check if it's a ToolInputGuardrail instance (SDK 0.7+)
                    elif isinstance(obj, ToolInputGuardrail):
                        guardrail_name = (
                            obj.get_name() if hasattr(obj, "get_name") else name
                        )
                        if guardrail_name in tool_input_guardrails:
                            print(
                                f"Warning: Duplicate tool input guardrail name '{guardrail_name}' found (from {module_path}). Overwriting previous definition."
                            )
                        Debug.log(
                            f"  Discovered tool input guardrail: {guardrail_name}"
                        )
                        tool_input_guardrails[guardrail_name] = obj

                    # Check if it's a ToolOutputGuardrail instance (SDK 0.7+)
                    elif isinstance(obj, ToolOutputGuardrail):
                        guardrail_name = (
                            obj.get_name() if hasattr(obj, "get_name") else name
                        )
                        if guardrail_name in tool_output_guardrails:
                            print(
                                f"Warning: Duplicate tool output guardrail name '{guardrail_name}' found (from {module_path}). Overwriting previous definition."
                            )
                        Debug.log(
                            f"  Discovered tool output guardrail: {guardrail_name}"
                        )
                        tool_output_guardrails[guardrail_name] = obj

            except ImportError as e:
                print(
                    f"ERROR: Could not import module '{module_path}' from '{filepath}': {e}"
                )
            except SyntaxError as e:
                print(f"ERROR: Syntax error in file '{filepath}': {e}")
                print(traceback.format_exc())
            except Exception as e:
                print(f"ERROR: Unexpected error processing file '{filepath}': {e}")
                print(traceback.format_exc())

    finally:
        sys.path = original_sys_path

    Debug.log(
        f"Completed guardrail discovery: Found {len(input_guardrails)} input, "
        f"{len(output_guardrails)} output, {len(tool_input_guardrails)} tool-input, "
        f"and {len(tool_output_guardrails)} tool-output guardrails in '{base_dir}'."
    )

    return (
        input_guardrails,
        output_guardrails,
        tool_input_guardrails,
        tool_output_guardrails,
    )
