"""Discovery and resolution helpers for ``is_enabled`` predicates."""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from omniagents.core.debug import Debug


def is_enabled_predicate(
    func: Callable[..., Any] | None = None, *, name: str | None = None
) -> Callable[..., Any]:
    """Mark a callable as a discoverable ``is_enabled`` predicate.

    Decorated callables can be referenced by name from YAML via the
    ``is_enabled:`` key on tools, handoffs, or agent_tools.
    """

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        setattr(f, "_is_omniagents_is_enabled_predicate", True)
        setattr(f, "_predicate_name", name or f.__name__)
        return f

    if func is None:
        return decorator
    return decorator(func)


def discover_predicates_in_dir(base_dir: str | Path) -> Dict[str, Callable[..., Any]]:
    """Walk ``<base_dir>/predicates/`` and return decorated predicates by name."""
    discovered: Dict[str, Callable[..., Any]] = {}
    base_path = Path(base_dir)
    predicates_dir = base_path / "predicates"
    if not predicates_dir.exists() or not predicates_dir.is_dir():
        return discovered

    parent_path = str(predicates_dir.parent)
    base_path_str = str(predicates_dir)
    original_sys_path = sys.path[:]
    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)
    if base_path_str not in sys.path:
        sys.path.insert(0, base_path_str)

    preexisting_modules = set(sys.modules.keys())
    loaded_modules: set[str] = set()
    loaded_packages: set[str] = set()
    try:
        for py_file in predicates_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                rel = py_file.relative_to(predicates_dir.parent)
                module_name = str(rel.with_suffix("")).replace(os.sep, ".")
            except Exception:
                continue
            try:
                module = importlib.import_module(module_name)
                loaded_modules.add(module.__name__)
                package_root = module.__name__.split(".", 1)[0]
                if package_root not in preexisting_modules:
                    loaded_packages.add(package_root)
            except Exception as e:
                Debug.log(f"predicate discovery: failed to import {module_name}: {e}")
                continue
            for _, obj in inspect.getmembers(module):
                if not callable(obj):
                    continue
                if not getattr(obj, "_is_omniagents_is_enabled_predicate", False):
                    continue
                pname = getattr(obj, "_predicate_name", None) or obj.__name__
                discovered[pname] = obj
    finally:
        sys.path = original_sys_path
        # Drop modules we loaded so subsequent calls with different base_dirs
        # don't get cached predicates from another agent.
        for module_name in loaded_modules:
            if module_name in sys.modules and module_name not in preexisting_modules:
                try:
                    del sys.modules[module_name]
                except Exception:
                    pass
        for package_name in loaded_packages:
            if package_name in sys.modules and package_name not in preexisting_modules:
                try:
                    del sys.modules[package_name]
                except Exception:
                    pass

    return discovered


def _import_dotted_path(spec: str) -> Optional[Callable[..., Any]]:
    """Try to import a callable from a ``module:func`` or ``module.func`` string."""
    if ":" in spec:
        mod_name, func_name = spec.rsplit(":", 1)
    elif "." in spec:
        mod_name, func_name = spec.rsplit(".", 1)
    else:
        return None
    try:
        module = importlib.import_module(mod_name)
    except Exception:
        return None
    return getattr(module, func_name, None)


def resolve_predicate(
    spec: Any,
    registry: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Any:
    """Resolve a YAML ``is_enabled`` value into a bool or callable.

    Accepts:
      - bool: returned as-is
      - callable: returned as-is
      - str: looked up in ``registry`` first, then via dotted-path import
      - None: returns None (caller decides default)
    """
    if spec is None:
        return None
    if isinstance(spec, bool):
        return spec
    if callable(spec):
        return spec
    if isinstance(spec, str):
        if registry and spec in registry:
            return registry[spec]
        resolved = _import_dotted_path(spec)
        if resolved is not None:
            return resolved
        Debug.log(f"resolve_predicate: could not resolve '{spec}' to a callable")
        return None
    Debug.log(f"resolve_predicate: unsupported type {type(spec).__name__} for {spec!r}")
    return None
