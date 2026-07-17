"""Deep-merge for layered YAML configuration.

The one merge semantics used across the framework's layered configs
(sandbox profiles, project overlays): dicts merge recursively, lists and
scalars replace.
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = ["deep_merge"]


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dicts; lists replace; scalars replace."""
    if not over:
        return dict(base)
    result: Dict[str, Any] = dict(base)
    for k, v in over.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result
