"""Builtin ``/compact`` server function.

Checkpoint-compacts the session: appends a model-visible summary +
resume-protocol item with the ``context_summary`` marker. The
orchestration lives in ``omniagents.core.runtime.compaction``; products
install their summarizer with ``set_compaction_summarizer``. Activated
by the ``compact`` capability pack; lifted from omni-code.
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.core.runtime.compaction import (
    compaction_summarizer,
    run_checkpoint_compaction,
)
from omniagents.core.session.manager import Session


@server_function(
    description="Compact context by appending a summary marker",
    strict=True,
    name_override="compact",
)
async def compact(service: Any, session: Session) -> str:
    history = list(getattr(session, "history", []) or [])
    if not history:
        return "Nothing to compact yet — this session hasn't started."

    summarizer = compaction_summarizer()
    if summarizer is None:
        return (
            "Compaction is unavailable: no compaction summarizer is installed "
            "(see omniagents.core.runtime.compaction.set_compaction_summarizer)."
        )

    await service.set_client_status(
        "Compacting...",
        session=session,
        run_id=getattr(session, "active_run_id", None),
        show_spinner=True,
    )
    try:

        async def _summarize(prompt: str) -> str:
            return await summarizer(prompt, service=service, session=session)

        await run_checkpoint_compaction(session, _summarize)
    finally:
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )

    return "Context compacted."
