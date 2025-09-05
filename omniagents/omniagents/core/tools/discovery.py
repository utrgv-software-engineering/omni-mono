import os
import importlib
import inspect
import sys
from pathlib import Path
from typing import Dict, Callable
import traceback
from omniagents.core.debug import Debug

# Import the original decorator from the agents library
from agents.tool import function_tool as original_function_tool
from typing import Any, Dict

# --- Rich Tool Output Class --- #

class RichToolOutput:
    """
    A wrapper for tool outputs that separates LLM-intended text from UI metadata.
    
    When stringified, only returns the LLM output to avoid polluting the context window.
    The UI metadata is preserved for client-side rendering.
    """
    
    def __init__(self, llm_output: str, ui_metadata: Dict[str, Any] = None):
        """
        Args:
            llm_output: The text output intended for the LLM context
            ui_metadata: Additional data for UI rendering (charts, tables, etc.)
        """
        self.llm_output = llm_output
        self.ui_metadata = ui_metadata or {}
    
    def __str__(self):
        """Return only the LLM-intended output when converted to string."""
        return self.llm_output
    
    def __repr__(self):
        """Debug representation showing both outputs."""
        return f"RichToolOutput(llm='{self.llm_output[:50]}...', metadata_keys={list(self.ui_metadata.keys())})"

# --- Wrapper Decorators --- #

def function_tool(func: Callable = None, **kwargs) -> Callable:
    """
    A wrapper around agents.tool.function_tool that adds a marker
    attribute for easier discovery by the omniagents_core framework.

    Usage: Replace `from agents import function_tool` with
           `from omniagents_core import function_tool` in your tool files.
    
    Accepts all keyword arguments that agents.tool.function_tool accepts,
    including strict_mode=False for non-strict schemas.
    """
    def decorator(f):
        # Apply the original decorator with all passed kwargs
        decorated_func = original_function_tool(f, **kwargs)
        # Add our marker attribute
        setattr(decorated_func, '_is_omniagents_core_tool', True)
        return decorated_func
    
    # Handle both @function_tool and @function_tool(...) usage
    if func is None:
        return decorator
    else:
        return decorator(func)

def rich_function_tool(func: Callable = None, **kwargs) -> Callable:
    """
    A decorator for tools that return RichToolOutput with separate LLM and UI components.
    
    This is a wrapper around agents.tool.function_tool that adds marker attributes
    for rich output support. Tools must return a RichToolOutput instance.
    
    Usage:
        @rich_function_tool
        async def analyze_data(data: str) -> RichToolOutput:
            analysis = "Found 5 anomalies"
            metadata = {"chart": {...}, "stats": {...}}
            return RichToolOutput(analysis, metadata)
    
    Accepts all keyword arguments that agents.tool.function_tool accepts.
    """
    def decorator(f):
        # Apply the original decorator with all passed kwargs
        decorated_func = original_function_tool(f, **kwargs)
        # Add our marker attributes
        setattr(decorated_func, '_is_omniagents_core_tool', True)
        setattr(decorated_func, '_is_rich_tool', True)
        return decorated_func
    
    # Handle both @rich_function_tool and @rich_function_tool(...) usage
    if func is None:
        return decorator
    else:
        return decorator(func)

# --- Updated Discovery Function --- #

def discover_functions_in_dir(base_dir: str = "tools", debug: bool = None) -> Dict[str, Callable]:
    """
    Discovers Python functions marked with the omniagents_core @function_tool
    decorator (which adds the '_is_omniagents_core_tool' attribute).

    It temporarily adds the directory's parent and the directory itself to sys.path.

    Args:
        base_dir: The directory path (relative or absolute) to scan for .py files.
        debug: Deprecated parameter, uses Debug.enabled() instead.

    Returns:
        A dictionary mapping function names to the callable function objects.
    """
    discovered_functions: Dict[str, Callable] = {}
    base_path = Path(base_dir).resolve()

    if not base_path.is_dir():
        print(f"Warning: Tool discovery directory '{base_dir}' not found or is not a directory.")
        return {}

    if Debug.enabled():
        print(f"Starting tool discovery (looking for marker) in directory: {base_path}")

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
                print(f"Warning: Skipping file {filepath} as it seems outside the expected parent directory {base_path.parent}")
                continue

                if Debug.enabled():
                    print(f"Attempting to import module: {module_path} from {filepath}")

            try:
                module = importlib.import_module(module_path)
                for name, obj in inspect.getmembers(module):
                    # --- Check for the marker attribute set by our wrapper decorator --- #
                    if hasattr(obj, '_is_omniagents_core_tool'):
                        if name in discovered_functions:
                            # always warn on duplicate names
                            print(f"Warning: Duplicate tool name '{name}' found (from {module_path}). Overwriting previous definition.")
                        if Debug.enabled():
                            print(f"  Discovered marked tool: {name}")
                        discovered_functions[name] = obj
                    # Optionally log other functions found but not marked
                    # elif inspect.isfunction(obj) and obj.__module__ == module_path and not name.startswith("_"):
                    #     print(f"  Found unmarked function: {name}")

            except ImportError as e:
                print(f"ERROR: Could not import module '{module_path}' from '{filepath}': {e}")
            except SyntaxError as e:
                 print(f"ERROR: Syntax error in file '{filepath}': {e}")
                 print(traceback.format_exc())
            except Exception as e:
                print(f"ERROR: Unexpected error processing file '{filepath}': {e}")
                print(traceback.format_exc())

    finally:
        sys.path = original_sys_path

    if Debug.enabled():
        print(f"Completed tool discovery: Found {len(discovered_functions)} marked tools in '{base_dir}'.")
    return discovered_functions 