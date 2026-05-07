"""Discover SDK lifecycle-hook instances from a directory.

Looks for module-level instances of ``agents.RunHooks`` /
``agents.RunHooksBase`` and ``agents.AgentHooks`` / ``agents.AgentHooksBase``
and returns them keyed by attribute name.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


def discover_lifecycle_hooks_in_dir(
    base_dir: str = "lifecycle_hooks",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(run_hooks, agent_hooks)`` mappings of name → instance.

    *base_dir* is scanned recursively for ``.py`` modules, each of which
    is imported and inspected for module-level instances of the SDK hook
    classes.
    """
    run_hooks: Dict[str, Any] = {}
    agent_hooks: Dict[str, Any] = {}

    base_path = Path(base_dir)
    if not base_path.exists() or not base_path.is_dir():
        return run_hooks, agent_hooks

    try:
        from agents.lifecycle import AgentHooksBase, RunHooksBase
    except Exception:  # pragma: no cover — SDK pin guarantees this
        return run_hooks, agent_hooks

    parent_path = str(base_path.parent)
    base_path_str = str(base_path)
    original_sys_path = sys.path[:]
    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)
    if base_path_str not in sys.path:
        sys.path.insert(0, base_path_str)
    try:
        for py_file in base_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                rel = py_file.relative_to(base_path.parent)
                module_name = str(rel.with_suffix("")).replace(os.sep, ".")
            except Exception:
                continue
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue

            for name, obj in inspect.getmembers(module):
                if name.startswith("_"):
                    continue
                if isinstance(obj, RunHooksBase):
                    run_hooks[name] = obj
                elif isinstance(obj, AgentHooksBase):
                    agent_hooks[name] = obj
    finally:
        sys.path = original_sys_path

    return run_hooks, agent_hooks
