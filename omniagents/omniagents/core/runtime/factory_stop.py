"""The factory's kill switch, as its runtime holds it.

A stopped factory does no AI work: no turn starts (``release_gate.admission``),
no model or tool call runs (``tool_durability.DurableToolHooks``), and events
it receives are recorded as skipped (``integrations.intake``). The state is the
process's own, set by the integrations server from its durable store and by
whoever runs the factory (``/integrations/stop``).

Sandboxes are tracked here as they come up, so stopping can end every command
still running in them (``terminate_sandbox_commands``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "FactoryStopped",
    "current",
    "message",
    "raise_if_stopped",
    "set_state",
    "terminate_sandbox_commands",
    "track",
    "untrack",
]


class FactoryStopped(RuntimeError):
    """Raised where AI work would start or continue while the factory is stopped."""


_state: Optional[dict] = None
#: Live sandbox sessions by identity (sessions need not be hashable).
_live: dict = {}


def current() -> Optional[dict]:
    """The stop in force (``stopped_by``, ``stopped_at``, ``reason``), or ``None``."""
    return _state


def set_state(state: Optional[dict]) -> None:
    global _state
    _state = dict(state) if state else None


def message(state: Optional[dict] = None) -> str:
    state = state if state is not None else _state
    reason = (state or {}).get("reason")
    return "This factory is stopped" + (f": {reason}" if reason else "")


def raise_if_stopped() -> None:
    if _state is not None:
        raise FactoryStopped(message(_state))


def track(session: Any) -> None:
    _live[id(session)] = session


def untrack(session: Any) -> None:
    _live.pop(id(session), None)


async def terminate_sandbox_commands(timeout: float = 8) -> int:
    """End every command running in a live sandbox; how many sandboxes were reached."""

    async def terminate(session):
        try:
            await asyncio.wait_for(session.pty_terminate_all(), timeout)
            return True
        except Exception:  # noqa: BLE001 - a sandbox already gone has nothing running
            logger.warning(
                "factory stop: could not end a sandbox's commands", exc_info=True
            )
            return False

    sessions = [s for s in list(_live.values()) if hasattr(s, "pty_terminate_all")]
    results = await asyncio.gather(*(terminate(s) for s in sessions))
    return sum(results)
