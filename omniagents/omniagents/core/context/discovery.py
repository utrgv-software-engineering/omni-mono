"""
Discovery of context factory functions from agent directories.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Dict, Callable, Any, Optional

from omniagents.core.debug import Debug


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
                            if Debug.enabled():
                                print(
                                    f"Warning: Duplicate context factory '{name}' "
                                    f"found in {py_file}. Overwriting."
                                )
                        discovered[name] = obj
                        if Debug.enabled():
                            print(f"Discovered context factory: {name} from {py_file}")

            except Exception as e:
                if Debug.enabled():
                    print(f"Warning: Error loading {py_file} for context discovery: {e}")
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
                                if project_root_str in mod_file or agent_dir_str in mod_file:
                                    del sys.modules[mod_name]

    return discovered
