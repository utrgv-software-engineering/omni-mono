import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from omniagents.core.debug import Debug


def runtime_hook(event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "_is_omniagents_runtime_hook", True)
        setattr(func, "_runtime_hook_event", str(event))
        return func

    return decorator


def discover_runtime_hooks_in_dir(
    base_dir: str = "runtime_hooks",
) -> Dict[str, List[Callable[..., Any]]]:
    discovered: Dict[str, List[Callable[..., Any]]] = {}
    base_path = Path(base_dir)
    if not base_path.exists() or not base_path.is_dir():
        return discovered

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
                module_name = str(rel.with_suffix(""))
                module_name = module_name.replace(os.sep, ".")
            except Exception:
                continue
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue

            for _, obj in inspect.getmembers(module):
                if not callable(obj):
                    continue
                if not getattr(obj, "_is_omniagents_runtime_hook", False):
                    continue
                event = getattr(obj, "_runtime_hook_event", None)
                if not event:
                    continue
                discovered.setdefault(str(event), []).append(obj)
    finally:
        sys.path = original_sys_path

    return discovered


def build_runtime_hooks_registrar(
    hooks: Dict[str, List[Callable[..., Any]]] | List[Callable[..., Any]],
) -> Callable[[Any], None]:
    def registrar(service: Any) -> None:
        if isinstance(hooks, dict):
            items = hooks.items()
        else:
            grouped: Dict[str, List[Callable[..., Any]]] = {}
            for fn in hooks:
                event = getattr(fn, "_runtime_hook_event", None)
                if event:
                    grouped.setdefault(str(event), []).append(fn)
            items = grouped.items()

        for event, funcs in items:
            for fn in funcs:
                try:
                    service.register_runtime_hook(str(event), fn)
                except Exception as e:
                    Debug.log(f"Warning: Failed to register runtime hook '{event}': {e}")

    return registrar


def register_runtime_hooks_from_dir(
    service: Any, base_dir: str = "runtime_hooks", names: Optional[List[str]] = None
) -> None:
    discovered = discover_runtime_hooks_in_dir(base_dir)
    if names:
        selected: Dict[str, List[Callable[..., Any]]] = {
            k: v for k, v in discovered.items() if k in set(names)
        }
    else:
        selected = discovered
    registrar = build_runtime_hooks_registrar(selected)
    registrar(service)
