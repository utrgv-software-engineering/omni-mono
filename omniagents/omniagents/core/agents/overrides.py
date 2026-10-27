"""A model and reasoning effort every agent built in this context uses instead of its own.

A benchmark runs the same tasks under several configurations (``eval
scenarios run --matrix``): inside :func:`overriding`, every agent the builder
makes (the scenario's agent, and the agents it calls as tools) takes the
configuration's model and reasoning effort, whatever its own definition says.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

__all__ = ["current_overrides", "overriding"]

_OVERRIDES: ContextVar[Dict[str, str]] = ContextVar(
    "omniagents_run_overrides", default={}
)


def current_overrides() -> Dict[str, str]:
    """``{"model": ..., "reasoning": ...}``: the ones set here (none by default)."""
    return _OVERRIDES.get()


@contextmanager
def overriding(
    *, model: Optional[str] = None, reasoning: Optional[str] = None
) -> Iterator[None]:
    values = {k: v for k, v in (("model", model), ("reasoning", reasoning)) if v}
    token = _OVERRIDES.set({**_OVERRIDES.get(), **values})
    try:
        yield
    finally:
        _OVERRIDES.reset(token)
