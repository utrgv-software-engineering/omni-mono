from __future__ import annotations

import inspect
from typing import Callable, Dict, Any, List, Optional


class EvaluationRegistryError(Exception):
    pass


class DuplicateMeasureError(EvaluationRegistryError):
    pass


class UnknownMeasureError(EvaluationRegistryError):
    pass


class InvalidMeasureError(EvaluationRegistryError):
    pass


_MEASURES: Dict[str, Callable[[Any], Dict[str, Any]]] = {}


def evaluation_measure(
    func: Callable | None = None,
    *,
    name: Optional[str] = None,
    overwrite: bool = False,
):
    """Register a function as an evaluation measure.

    Args:
        func: The measure function (takes EvalContext, returns dict with 'passed' key)
        name: Optional name override (defaults to function name)
        overwrite: If True, silently replace existing measure with same name.
                   Useful in notebooks where cells may be re-run.

    Example:
        @evaluation_measure
        def my_measure(ctx):
            return {"passed": True, "reason": "All good"}

        # In notebooks, use overwrite=True to allow re-running cells:
        @evaluation_measure(overwrite=True)
        def my_measure(ctx):
            return {"passed": True}
    """

    def decorator(f: Callable[[Any], Dict[str, Any]]):
        if not callable(f):
            raise TypeError("evaluation_measure expects a callable")
        key = name or getattr(f, "__name__", None)
        if not isinstance(key, str) or not key.strip():
            raise ValueError("evaluation_measure requires a non-empty measure name")
        key = key.strip()
        existing = _MEASURES.get(key)
        if existing and existing is not f and not overwrite:
            raise DuplicateMeasureError(
                f"evaluation measure '{key}' already registered"
            )
        _MEASURES[key] = f
        setattr(f, "_is_omni_evaluation_measure", True)
        setattr(f, "_measure_name", key)
        return f

    if func is None:
        return decorator
    return decorator(func)


def get_measure(name: str) -> Callable[[Any], Dict[str, Any]]:
    if name not in _MEASURES:
        raise UnknownMeasureError(f"evaluation measure '{name}' is not registered")
    return _MEASURES[name]


def _select(measures: Optional[List[str]]) -> List[tuple]:
    if measures is None:
        return list(_MEASURES.items())
    missing = [m for m in measures if m not in _MEASURES]
    if missing:
        missing_str = ", ".join(sorted(missing))
        raise UnknownMeasureError(
            f"unknown evaluation measures requested: {missing_str}"
        )
    return [(k, _MEASURES[k]) for k in measures]


def _named(key: str, res: Any) -> Dict[str, Any]:
    if not isinstance(res, dict):
        raise InvalidMeasureError(f"evaluation measure '{key}' must return a dict")
    if "name" not in res:
        res["name"] = key
    return res


def run_measures(
    ctx: Any, measures: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Run synchronous measures. An ``async def`` measure needs :func:`run_measures_async`."""
    results: List[Dict[str, Any]] = []
    for key, fn in _select(measures):
        res = fn(ctx)
        if inspect.isawaitable(res):
            close = getattr(res, "close", None)
            if callable(close):
                close()
            raise InvalidMeasureError(
                f"evaluation measure '{key}' is async; use run_measures_async"
            )
        results.append(_named(key, res))
    return results


async def run_measures_async(
    ctx: Any, measures: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Run measures, awaiting any that are ``async def`` (the eval engine's path)."""
    results: List[Dict[str, Any]] = []
    for key, fn in _select(measures):
        res = fn(ctx)
        if inspect.isawaitable(res):
            res = await res
        results.append(_named(key, res))
    return results
