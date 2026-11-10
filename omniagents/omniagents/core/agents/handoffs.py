"""Handing work to another agent's own conversation.

An ``agent_tools`` entry with ``mode: conversation`` gives the agent a tool
that hands a request to that agent as a run of its own, in its own
conversation for where the request came from, instead of calling it as a
helper inside the caller's run. Whatever hosts the agents (a factory's
integrations) installs the handler that does it; elsewhere the tool says it
can't.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

__all__ = ["HandOff", "handler", "set_handler"]

#: ``(caller session id, caller agent, target agent key, request) -> result``.
HandOff = Callable[[Optional[str], str, str, str], Awaitable[Dict[str, Any]]]

_handler: Optional[HandOff] = None


def set_handler(fn: Optional[HandOff]) -> None:
    """Install (or, with ``None``, remove) what hands work over."""
    global _handler
    _handler = fn


def handler() -> Optional[HandOff]:
    return _handler
