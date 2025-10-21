from __future__ import annotations

from typing import Callable, Dict, Any, List, Optional

_MEASURES: Dict[str, Callable[[Any], Dict[str, Any]]] = {}


def evaluation_measure(func: Callable | None = None, *, name: Optional[str] = None):
    def decorator(f: Callable[[Any], Dict[str, Any]]):
        key = name or f.__name__
        _MEASURES[key] = f
        setattr(f, "_is_omni_evaluation_measure", True)
        setattr(f, "_measure_name", key)
        return f

    if func is None:
        return decorator
    return decorator(func)


def get_measure(name: str) -> Optional[Callable[[Any], Dict[str, Any]]]:
    return _MEASURES.get(name)


def run_measures(
    ctx: Any, measures: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    selected = (
        _MEASURES
        if measures is None
        else {k: _MEASURES[k] for k in measures if k in _MEASURES}
    )
    results: List[Dict[str, Any]] = []
    for key, fn in selected.items():
        res = fn(ctx)
        if "name" not in res:
            res["name"] = key
        results.append(res)
    return results
