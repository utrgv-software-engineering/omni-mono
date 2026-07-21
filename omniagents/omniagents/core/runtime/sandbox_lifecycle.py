"""Embedder seam for the builtin ``sandbox.switch`` server function.

``sandbox.switch`` (``omniagents.builtin.server_functions.
sandbox_lifecycle``) is the operator RPC that swaps a running serve
session's sandbox to a different profile in place. The framework carries
the RPC surface — name, schema, validation, and the observable
transition state on :class:`~omniagents.core.runtime.serve_state.
SwitchContext` — but the orchestration (quiesce, snapshot, teardown,
bring-up, re-wire, rollback) is serve-host policy and stays with the
embedder for now. The serve host installs its orchestrator here, the
same pattern as :func:`omniagents.core.runtime.recap.
set_recap_summarizer` / :func:`omniagents.core.runtime.compaction.
set_compaction_summarizer`:

* :func:`set_switch_executor` — install an ``async (profile: str) ->
  dict`` callable that performs the switch and returns the RPC result
  dict (``{"ok": bool, "profile": ..., "backend": ..., "container_id":
  ..., "services": ..., "reason": ...}``).
* :func:`switch_executor` — the installed executor, or ``None``. With no
  executor installed, ``sandbox.switch`` returns a clear
  ``{"ok": false}`` error instead of raising — a serve host that
  activates the pack without wiring the seam degrades loudly but safely.

Authority model (security property of the pack): switching the sandbox
is a **control-plane operation owned by the human operator** — a
launcher UI or slash command over the server-function channel — never
the LLM. Server functions are a separate registry from the model's tool
schema, so the model cannot invoke ``sandbox.switch`` and thus cannot
move itself to a less-isolated profile (sandbox-escape / egress-bypass
vector). The LLM's only sandbox surface is the read-only ``sandbox``
capability pack (``sandbox_get_state`` / ``sandbox_wait_until_ready``).

Lifted from omni-code (``omni_code/server_functions/sandbox.py``).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

__all__ = [
    "SwitchExecutor",
    "set_switch_executor",
    "switch_executor",
]

SwitchExecutor = Callable[[str], Awaitable[Dict[str, Any]]]

_switch_executor: Optional[SwitchExecutor] = None


def set_switch_executor(executor: Optional[SwitchExecutor]) -> None:
    """Install the serve host's switch orchestrator (``None`` clears).

    The executor receives the target profile path/name string from the
    ``sandbox.switch`` RPC and must return the RPC result dict. It owns
    locking, run-quiescing, snapshot/rehydrate, re-wiring, rollback, and
    UI broadcasts.
    """
    global _switch_executor
    _switch_executor = executor


def switch_executor() -> Optional[SwitchExecutor]:
    """The installed switch executor, or ``None`` (standalone)."""
    return _switch_executor
