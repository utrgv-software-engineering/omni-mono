import os
import importlib
import inspect
import sys
import asyncio
import functools
from pathlib import Path
from typing import Dict, Callable, Optional, Any, Tuple
import traceback
from omniagents.core.debug import Debug

# Import the original decorator from the agents library
from agents.tool import function_tool as original_function_tool

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


def _wrap_sync_function(func: Callable) -> tuple[Callable, bool]:
    if inspect.iscoroutinefunction(func):
        return func, False
    sig = inspect.signature(func)

    @functools.wraps(func)
    async def async_wrapper(*args, **kw):
        return await asyncio.to_thread(func, *args, **kw)

    async_wrapper.__signature__ = sig
    return async_wrapper, True


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
        wrapped_func, _ = _wrap_sync_function(f)
        client_status = kwargs.pop("client_status", None)
        decorated_func = original_function_tool(wrapped_func, **kwargs)
        # Add our marker attribute
        setattr(decorated_func, "_is_omniagents_core_tool", True)
        # Attach optional client status text for UIs that support it
        if client_status:
            try:
                setattr(decorated_func, "_client_status_text", str(client_status))
            except Exception:
                pass
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
        wrapped_func, _ = _wrap_sync_function(f)
        decorated_func = original_function_tool(wrapped_func, **kwargs)
        # Add our marker attributes
        setattr(decorated_func, "_is_omniagents_core_tool", True)
        setattr(decorated_func, "_is_rich_tool", True)
        return decorated_func

    # Handle both @rich_function_tool and @rich_function_tool(...) usage
    if func is None:
        return decorator
    else:
        return decorator(func)


# --- Approval Metadata Registry --- #

_approval_metadata_registry: Dict[str, Tuple[Callable, inspect.Signature]] = {}


def approval_metadata(tool: str = None):
    """
    Decorator for approval metadata generators with same signature as tool.

    The decorated function should have the same parameter signature as the tool
    it provides metadata for, and return a dictionary with metadata for the
    approval UI.

    Usage:
        @approval_metadata()  # Auto-detects tool name from function name
        def edit_file_approval(file_path: str, old_text: str, new_text: str) -> dict:
            return {
                "display_type": "diff",
                "summary": f"Edit {file_path}",
                ...
            }

        @approval_metadata(tool="my_tool")  # Explicit tool name
        def generate_metadata(param1: str, param2: int) -> dict:
            return {...}

    The metadata dictionary can contain:
        - display_type: UI hint for rendering (e.g., "diff", "file_write", "table", etc.)
        - summary: Brief description of the operation
        - value: The actual data to display (diffs, content, etc.)
        - preview: A preview string for the UI
        - metadata: Additional context (file paths, operations, stats, etc.)
        - truncated: Boolean indicating if content was truncated
    """

    def decorator(func: Callable):
        tool_name = tool
        if not tool_name:
            # Auto-detect tool name from function name
            # Supports patterns: tool_name_approval, tool_name_metadata
            name = func.__name__
            if name.endswith("_approval"):
                tool_name = name[:-9]  # Remove "_approval" suffix
            elif name.endswith("_metadata"):
                tool_name = name[:-9]  # Remove "_metadata" suffix
            else:
                raise ValueError(
                    f"Cannot auto-detect tool name from '{name}'. "
                    f"Function name should end with '_approval' or '_metadata', "
                    f"or use @approval_metadata(tool='name') explicitly."
                )

        # Store the function and its signature for later use
        sig = inspect.signature(func)
        _approval_metadata_registry[tool_name] = (func, sig)

        # Mark the function as a metadata generator for debugging
        setattr(func, "_is_approval_metadata", True)
        setattr(func, "_tool_name", tool_name)

        if Debug.enabled():
            print(
                f"Registered approval metadata for tool '{tool_name}' from function '{func.__name__}'"
            )

        return func  # Return the function unchanged

    return decorator


def get_approval_metadata(tool_name: str, args: dict) -> Optional[Dict[str, Any]]:
    """
    Get approval metadata for a tool by calling the registered generator.

    Args:
        tool_name: The name of the tool
        args: The arguments dictionary from the tool call

    Returns:
        A dictionary with metadata for the approval UI, or None if no
        metadata generator is registered for this tool.
    """
    entry = _approval_metadata_registry.get(tool_name)
    if not entry:
        return None

    func, sig = entry

    # Build kwargs from args dict based on the function's signature
    kwargs = {}
    for param_name, param in sig.parameters.items():
        if param_name in args:
            # Use the value from args
            kwargs[param_name] = args[param_name]
        elif param.default is not param.empty:
            # Use the parameter's default value
            kwargs[param_name] = param.default
        # If the parameter is required but not in args, the function call
        # will fail with an appropriate TypeError

    try:
        # Call the metadata generator with unpacked parameters
        metadata = func(**kwargs)

        if Debug.enabled():
            print(f"Generated approval metadata for tool '{tool_name}'")

        return metadata

    except Exception as e:
        if Debug.enabled():
            print(f"Error generating approval metadata for tool '{tool_name}': {e}")
            import traceback

            traceback.print_exc()
        return None


# --- Updated Discovery Function --- #


def discover_functions_in_dir(
    base_dir: str = "tools", debug: bool = None
) -> Dict[str, Callable]:
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

    preexisting_modules = set(sys.modules.keys())
    loaded_modules: set[str] = set()
    loaded_packages: set[str] = set()
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

                if Debug.enabled():
                    print(f"Attempting to import module: {module_path} from {filepath}")

            try:
                module = importlib.import_module(module_path)
                loaded_modules.add(module.__name__)
                package_root = module.__name__.split(".", 1)[0]
                if package_root not in preexisting_modules:
                    loaded_packages.add(package_root)
                for name, obj in inspect.getmembers(module):
                    # --- Check for the marker attribute set by our wrapper decorator --- #
                    if hasattr(obj, "_is_omniagents_core_tool"):
                        if name in discovered_functions:
                            # always warn on duplicate names
                            print(
                                f"Warning: Duplicate tool name '{name}' found (from {module_path}). Overwriting previous definition."
                            )
                        if Debug.enabled():
                            print(f"  Discovered marked tool: {name}")
                        discovered_functions[name] = obj
                    # Optionally log other functions found but not marked
                    # elif inspect.isfunction(obj) and obj.__module__ == module_path and not name.startswith("_"):
                    #     print(f"  Found unmarked function: {name}")

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
        for module_name in loaded_modules:
            try:
                if (
                    module_name in sys.modules
                    and module_name not in preexisting_modules
                ):
                    del sys.modules[module_name]
            except Exception:
                pass
        for package_name in loaded_packages:
            try:
                if (
                    package_name in sys.modules
                    and package_name not in preexisting_modules
                ):
                    del sys.modules[package_name]
            except Exception:
                pass

    if Debug.enabled():
        print(
            f"Completed tool discovery: Found {len(discovered_functions)} marked tools in '{base_dir}'."
        )
    return discovered_functions
