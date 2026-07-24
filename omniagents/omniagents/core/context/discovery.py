"""
Discovery of context factory functions and model config resolvers from agent directories.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Dict, Callable, Any, Optional

from omniagents.core.debug import Debug


def model_config_resolver(func: Callable) -> Callable:
    """Decorator to mark a function as a model config resolver.

    Model config resolvers are used to provide default model configuration
    (including max_input_tokens, max_output_tokens) for a session.

    Example:
        @model_config_resolver
        def get_default_model():
            return {
                "name": "gpt-4",
                "model": "gpt-4",
                "provider": "openai",
                "max_input_tokens": 128000,
                "max_output_tokens": 16384,
            }
    """
    func._is_model_config_resolver = True
    return func


def realtime_settings_resolver(func: Callable) -> Callable:
    """Decorator to mark a function as a realtime settings resolver.

    Realtime settings resolvers are used to provide settings dicts for
    realtime voice connections (e.g. API keys, base URLs, Azure deployment).

    The resolver should return a dict of settings, or an empty dict to
    intentionally disable realtime connections. Returning None indicates
    fallback to environment-based settings.
    """

    func._is_realtime_settings_resolver = True
    return func


def discover_context_factories(agent_dir: Path) -> Dict[str, Callable]:
    """
    Discover context factory functions in an agent directory.

    Scans all .py files in the agent directory (where agent.yml lives) for
    functions decorated with @context_factory and returns a registry mapping
    function names to callables.

    Args:
        agent_dir: Path to the agent directory containing agent.yml

    Returns:
        Dict mapping function names to context factory callables.
    """
    discovered: Dict[str, Callable] = {}

    if not agent_dir.is_dir():
        return discovered

    # Track modules before import for cleanup
    modules_before = set(sys.modules.keys())

    # Temporarily add agent_dir to sys.path for imports within context files
    agent_dir_str = str(agent_dir)

    # Also add parent directories up to project root for relative imports
    project_root = agent_dir
    current = agent_dir
    while current != current.parent:
        if (current / "project.yml").is_file():
            project_root = current
            break
        current = current.parent

    project_root_str = str(project_root)
    paths_added = []

    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        paths_added.append(project_root_str)

    if agent_dir_str not in sys.path:
        sys.path.insert(0, agent_dir_str)
        paths_added.append(agent_dir_str)

    try:
        # Scan all .py files in agent directory (non-recursive)
        for py_file in agent_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            try:
                # Load module from file without adding to sys.modules
                spec = importlib.util.spec_from_file_location(
                    f"_context_discovery_{py_file.stem}", str(py_file)
                )
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find all context factory decorated functions
                for name in dir(module):
                    if name.startswith("_"):
                        continue
                    obj = getattr(module, name)
                    if callable(obj) and getattr(obj, "_is_context_factory", False):
                        if name in discovered:
                            Debug.log(
                                f"Warning: Duplicate context factory '{name}' "
                                f"found in {py_file}. Overwriting."
                            )
                        discovered[name] = obj
                        Debug.log(f"Discovered context factory: {name} from {py_file}")

            except Exception as e:
                Debug.log(
                    f"Warning: Error loading {py_file} for context discovery: {e}"
                )
                continue

    finally:
        # Clean up sys.path
        for path in paths_added:
            if path in sys.path:
                sys.path.remove(path)

        # Clean up any 'tools' module to prevent conflicts with builtin tools
        if "tools" in sys.modules and "tools" not in modules_before:
            tools_module = sys.modules.get("tools")
            if tools_module:
                tools_file = getattr(tools_module, "__file__", "") or ""
                if project_root_str in tools_file or agent_dir_str in tools_file:
                    del sys.modules["tools"]
                    for mod_name in list(sys.modules.keys()):
                        if mod_name.startswith("tools."):
                            mod = sys.modules.get(mod_name)
                            if mod:
                                mod_file = getattr(mod, "__file__", "") or ""
                                if (
                                    project_root_str in mod_file
                                    or agent_dir_str in mod_file
                                ):
                                    del sys.modules[mod_name]

    return discovered


def discover_model_config_resolvers(agent_dir: Path) -> Dict[str, Callable]:
    """
    Discover model config resolver functions in an agent directory.

    Scans all .py files in the agent directory for functions decorated with
    @model_config_resolver and returns a registry mapping function names to callables.

    Args:
        agent_dir: Path to the agent directory containing agent.yml

    Returns:
        Dict mapping function names to model config resolver callables.
    """
    discovered: Dict[str, Callable] = {}

    if not agent_dir.is_dir():
        return discovered

    # Track modules before import for cleanup
    modules_before = set(sys.modules.keys())

    # Temporarily add agent_dir to sys.path for imports within files
    agent_dir_str = str(agent_dir)

    # Also add parent directories up to project root for relative imports
    project_root = agent_dir
    current = agent_dir
    while current != current.parent:
        if (current / "project.yml").is_file():
            project_root = current
            break
        current = current.parent

    project_root_str = str(project_root)
    paths_added = []

    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        paths_added.append(project_root_str)

    if agent_dir_str not in sys.path:
        sys.path.insert(0, agent_dir_str)
        paths_added.append(agent_dir_str)

    try:
        # Scan all .py files in agent directory (non-recursive)
        for py_file in agent_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            try:
                # Load module from file without adding to sys.modules
                spec = importlib.util.spec_from_file_location(
                    f"_model_config_discovery_{py_file.stem}", str(py_file)
                )
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find all model config resolver decorated functions
                for name in dir(module):
                    if name.startswith("_"):
                        continue
                    obj = getattr(module, name)
                    if callable(obj) and getattr(
                        obj, "_is_model_config_resolver", False
                    ):
                        if name in discovered:
                            Debug.log(
                                f"Warning: Duplicate model config resolver '{name}' "
                                f"found in {py_file}. Overwriting."
                            )
                        discovered[name] = obj
                        Debug.log(
                            f"Discovered model config resolver: {name} from {py_file}"
                        )

            except Exception as e:
                Debug.log(
                    f"Warning: Error loading {py_file} for model config resolver discovery: {e}"
                )
                continue

    finally:
        # Clean up sys.path
        for path in paths_added:
            if path in sys.path:
                sys.path.remove(path)

        # Clean up any 'tools' module to prevent conflicts with builtin tools
        if "tools" in sys.modules and "tools" not in modules_before:
            tools_module = sys.modules.get("tools")
            if tools_module:
                tools_file = getattr(tools_module, "__file__", "") or ""
                if project_root_str in tools_file or agent_dir_str in tools_file:
                    del sys.modules["tools"]
                    for mod_name in list(sys.modules.keys()):
                        if mod_name.startswith("tools."):
                            mod = sys.modules.get(mod_name)
                            if mod:
                                mod_file = getattr(mod, "__file__", "") or ""
                                if (
                                    project_root_str in mod_file
                                    or agent_dir_str in mod_file
                                ):
                                    del sys.modules[mod_name]

    return discovered


def discover_realtime_settings_resolvers(agent_dir: Path) -> Dict[str, Callable]:
    """Discover realtime settings resolver functions in an agent directory."""

    discovered: Dict[str, Callable] = {}

    if not agent_dir.is_dir():
        return discovered

    modules_before = set(sys.modules.keys())
    agent_dir_str = str(agent_dir)

    project_root = agent_dir
    current = agent_dir
    while current != current.parent:
        if (current / "project.yml").is_file():
            project_root = current
            break
        current = current.parent

    project_root_str = str(project_root)
    paths_added = []

    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        paths_added.append(project_root_str)

    if agent_dir_str not in sys.path:
        sys.path.insert(0, agent_dir_str)
        paths_added.append(agent_dir_str)

    try:
        for py_file in agent_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"_realtime_settings_discovery_{py_file.stem}", str(py_file)
                )
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for name in dir(module):
                    if name.startswith("_"):
                        continue
                    obj = getattr(module, name)
                    if callable(obj) and getattr(
                        obj, "_is_realtime_settings_resolver", False
                    ):
                        discovered[name] = obj
                        Debug.log(
                            f"Discovered realtime settings resolver: {name} from {py_file}"
                        )

            except Exception as e:
                Debug.log(
                    f"Warning: Error loading {py_file} for realtime settings resolver discovery: {e}"
                )
                continue

    finally:
        for path in paths_added:
            if path in sys.path:
                sys.path.remove(path)

        if "tools" in sys.modules and "tools" not in modules_before:
            tools_module = sys.modules.get("tools")
            if tools_module:
                tools_file = getattr(tools_module, "__file__", "") or ""
                if project_root_str in tools_file or agent_dir_str in tools_file:
                    del sys.modules["tools"]
                    for mod_name in list(sys.modules.keys()):
                        if mod_name.startswith("tools."):
                            mod = sys.modules.get(mod_name)
                            if mod:
                                mod_file = getattr(mod, "__file__", "") or ""
                                if (
                                    project_root_str in mod_file
                                    or agent_dir_str in mod_file
                                ):
                                    del sys.modules[mod_name]

    return discovered
