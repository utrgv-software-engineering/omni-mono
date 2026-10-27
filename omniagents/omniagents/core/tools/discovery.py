import hashlib
import importlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path
from typing import Dict, Callable, Optional, Any, Tuple, Union, overload
import traceback
from omniagents.core.debug import Debug

# Import the original decorator from the agents library
from agents.tool import function_tool as original_function_tool, FunctionTool

# --- Rich Tool Output Class --- #


class RichToolOutput:
    """
    A wrapper for tool outputs that separates LLM-intended text from UI metadata.

    When stringified, only returns the LLM output to avoid polluting the context window.
    The UI metadata is preserved for client-side rendering.
    """

    def __init__(
        self,
        llm_output: str,
        ui_metadata: Dict[str, Any] = None,
        *,
        max_output_chars: int | None = None,
    ):
        """
        Args:
            llm_output: The text output intended for the LLM context
            ui_metadata: Additional data for UI rendering (charts, tables, etc.)
        """
        self.max_output_chars = max_output_chars
        self.llm_output = llm_output
        self.ui_metadata = ui_metadata or {}

    def __str__(self):
        """Return only the LLM-intended output when converted to string."""
        return self.llm_output

    def __repr__(self):
        """Debug representation showing both outputs."""
        return f"RichToolOutput(llm='{self.llm_output[:50]}...', metadata_keys={list(self.ui_metadata.keys())})"


# --- Wrapper Decorators --- #


@overload
def function_tool(func: Callable) -> FunctionTool: ...


@overload
def function_tool(**kwargs: Any) -> Callable[[Callable], FunctionTool]: ...


def function_tool(
    func: Optional[Callable] = None, **kwargs
) -> Union[FunctionTool, Callable[[Callable], FunctionTool]]:
    """
    A wrapper around agents.tool.function_tool that adds a marker
    attribute for easier discovery by the omniagents_core framework.

    Usage: Replace `from agents import function_tool` with
           `from omniagents_core import function_tool` in your tool files.

    Accepts all keyword arguments that agents.tool.function_tool accepts,
    including strict_mode=False for non-strict schemas.
    """

    def decorator(f):
        running_summary = kwargs.pop("running_summary", None)
        tool = original_function_tool(f, **kwargs)

        # Add our marker attribute
        setattr(tool, "_is_omniagents_core_tool", True)
        # Store original function for direct invocation (used by TRL/GRPO training)
        setattr(tool, "_original_func", f)
        # Optional human phrasing for the in-flight tool call — emitted as
        # ``metadata.summary`` on the ``tool_called`` event so UIs can title
        # the running card (the result's own summary supersedes it).
        if running_summary:
            try:
                setattr(tool, "_running_summary", str(running_summary))
            except Exception:
                pass
        return tool

    # Handle both @function_tool and @function_tool(...) usage
    if func is None:
        return decorator
    else:
        return decorator(func)


@overload
def rich_function_tool(func: Callable) -> FunctionTool: ...


@overload
def rich_function_tool(**kwargs: Any) -> Callable[[Callable], FunctionTool]: ...


def rich_function_tool(
    func: Optional[Callable] = None, **kwargs
) -> Union[FunctionTool, Callable[[Callable], FunctionTool]]:
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
        running_summary = kwargs.pop("running_summary", None)
        tool = original_function_tool(f, **kwargs)

        setattr(tool, "_is_omniagents_core_tool", True)
        setattr(tool, "_is_rich_tool", True)
        # Store original function for direct invocation (used by TRL/GRPO training)
        setattr(tool, "_original_func", f)
        if running_summary:
            try:
                setattr(tool, "_running_summary", str(running_summary))
            except Exception:
                pass
        return tool

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

        Debug.log(
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

        Debug.log(f"Generated approval metadata for tool '{tool_name}'")

        return metadata

    except Exception as e:
        Debug.log(f"Error generating approval metadata for tool '{tool_name}': {e}")
        import traceback

        Debug.log(traceback.format_exc())
        return None


# --- Updated Discovery Function --- #


def _resolve_canonical_package_name(base_path: Path) -> Optional[str]:
    """Compute the dotted canonical module name for ``base_path``.

    Walks up while ``__init__.py`` exists in the parent. Returns ``None``
    when ``base_path`` is not a real Python package — the caller falls
    back to a synthetic, collision-proof prefix in that case.
    """
    if not (base_path / "__init__.py").exists():
        return None
    parts = [base_path.name]
    current = base_path.parent
    while (current / "__init__.py").exists():
        parts.insert(0, current.name)
        current = current.parent
    return ".".join(parts)


def discover_functions_in_dir(
    base_dir: str = "tools", debug: bool = None
) -> Dict[str, Callable]:
    """
    Discovers Python functions marked with the omniagents_core @function_tool
    decorator (which adds the '_is_omniagents_core_tool' attribute).

    Modules are loaded under their full canonical dotted name when the
    directory is a real Python package (so two directories named ``tools/``
    in different package roots — e.g. ``omniagents/builtin/tools/`` and a
    project's local ``tools/`` — don't collide on ``sys.modules['tools']``).
    Directories without an ``__init__.py`` chain, or whose canonical name
    is already bound to a different path, fall back to a unique synthetic
    prefix so discovery still works.

    Canonically-named modules stay in ``sys.modules`` after discovery —
    normal import semantics — so module-level state (job registries,
    tick state) remains a process singleton shared with any later
    ``import`` of the same module. Only synthetic-prefix modules are
    removed on the way out; their names are meaningless outside this
    function and holding them would only leak.

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

    Debug.log(f"Starting tool discovery (looking for marker) in directory: {base_path}")

    # Pick a module-name prefix that won't collide with anything else
    # already loaded. Canonical when possible (preserves registry
    # singleton semantics for tools whose state must be a process
    # singleton, e.g. local ``tools/_bash_jobs.py``); synthetic when the
    # canonical name is already pointing somewhere else.
    canonical_name = _resolve_canonical_package_name(base_path)
    canonical_init = (base_path / "__init__.py").resolve() if canonical_name else None
    use_synthetic = canonical_name is None
    if canonical_name and not use_synthetic:
        existing = sys.modules.get(canonical_name)
        if existing is not None:
            existing_file = getattr(existing, "__file__", None)
            if existing_file:
                try:
                    if Path(existing_file).resolve() != canonical_init:
                        use_synthetic = True
                except OSError:
                    use_synthetic = True
            else:
                use_synthetic = True

    synth_pkg: Optional[str] = None
    synth_pkg_registered = False
    if use_synthetic:
        synth_pkg = (
            "_omniagents_disco_" + hashlib.md5(str(base_path).encode()).hexdigest()[:12]
        )
        if synth_pkg not in sys.modules:
            pkg = types.ModuleType(synth_pkg)
            pkg.__path__ = [str(base_path)]
            pkg.__package__ = synth_pkg
            sys.modules[synth_pkg] = pkg
            synth_pkg_registered = True
        module_prefix = synth_pkg
    else:
        module_prefix = canonical_name

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
    try:
        for filepath in base_path.rglob("*.py"):
            if filepath.name == "__init__.py":
                continue

            try:
                relative_path = filepath.relative_to(base_path)
            except ValueError:
                print(
                    f"Warning: Skipping file {filepath} as it seems outside the expected directory {base_path}"
                )
                continue

            sub_name = ".".join(relative_path.with_suffix("").parts)
            module_path = f"{module_prefix}.{sub_name}"
            Debug.log(f"Attempting to import module: {module_path} from {filepath}")

            try:
                if use_synthetic:
                    if module_path in sys.modules:
                        module = sys.modules[module_path]
                        if module_path not in preexisting_modules:
                            loaded_modules.add(module_path)
                    else:
                        spec = importlib.util.spec_from_file_location(
                            module_path, str(filepath)
                        )
                        if spec is None or spec.loader is None:
                            raise ImportError(
                                f"could not build import spec for {filepath}"
                            )
                        module = importlib.util.module_from_spec(spec)
                        module.__package__ = synth_pkg
                        sys.modules[module_path] = module
                        loaded_modules.add(module_path)
                        spec.loader.exec_module(module)
                else:
                    module = importlib.import_module(module_path)
                for name, obj in inspect.getmembers(module):
                    # --- Check for the marker attribute set by our wrapper decorator --- #
                    if hasattr(obj, "_is_omniagents_core_tool"):
                        if name in discovered_functions:
                            # always warn on duplicate names
                            print(
                                f"Warning: Duplicate tool name '{name}' found (from {module_path}). Overwriting previous definition."
                            )
                        Debug.log(f"  Discovered marked tool: {name}")
                        discovered_functions[name] = obj
                    # elif inspect.isfunction(obj) and obj.__module__ == module_path and not name.startswith("_"):
                    #     print(f"  Found unmarked function: {name}")

            except ImportError as e:
                print(
                    f"ERROR: Could not import module '{module_path}' from '{filepath}': {e}"
                )
                if use_synthetic:
                    sys.modules.pop(module_path, None)
                    loaded_modules.discard(module_path)
            except SyntaxError as e:
                print(f"ERROR: Syntax error in file '{filepath}': {e}")
                print(traceback.format_exc())
                if use_synthetic:
                    sys.modules.pop(module_path, None)
                    loaded_modules.discard(module_path)
            except Exception as e:
                print(f"ERROR: Unexpected error processing file '{filepath}': {e}")
                print(traceback.format_exc())
                if use_synthetic:
                    sys.modules.pop(module_path, None)
                    loaded_modules.discard(module_path)

    finally:
        sys.path = original_sys_path
        # Canonical modules persist (see docstring); synthetic ones are
        # discovery-internal names and are always removed.
        if use_synthetic:
            for module_name in loaded_modules:
                try:
                    if (
                        module_name in sys.modules
                        and module_name not in preexisting_modules
                    ):
                        del sys.modules[module_name]
                except Exception:
                    pass
            if synth_pkg_registered and synth_pkg in sys.modules:
                sys.modules.pop(synth_pkg, None)

    Debug.log(
        f"Completed tool discovery: Found {len(discovered_functions)} marked tools in '{base_dir}'."
    )
    return discovered_functions
