"""Builtin ``/compact`` server function.

Uses native Responses compaction when configured. Other sessions use the
checkpoint summarizer installed by the product. Manual requests and automatic
context-error recovery share this routing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from omniagents import server_function
from omniagents.core.runtime.compaction import (
    compaction_summarizer,
    run_checkpoint_compaction,
)
from omniagents.core.session.manager import Session
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

logger = logging.getLogger(__name__)
COMPACTION_TIMEOUT_SECONDS = 45


@server_function(
    description="Compact context using the session's native compactor when available",
    strict=True,
    name_override="compact",
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.NONE,
)
async def compact(service: Any, session: Session) -> str:
    if getattr(getattr(service, "spec", None), "session_backend", None) is not None:
        raise RuntimeError(
            "Explicit compaction is unavailable for an external SDK session backend."
        )
    if getattr(session, "_compact_command_in_progress", False):
        raise RuntimeError("Context compaction is already in progress.")
    if getattr(session, "_checkpoint_compaction_in_progress", False) or getattr(
        session, "_sdk_compaction_in_progress", False
    ):
        raise RuntimeError("Context compaction is already in progress.")
    from omniagents.core.session.compaction import _has_unresolved_function_call
    from omniagents.core.session.compaction import native_compaction_for_session
    from omniagents.core.session.compaction import has_pending_compaction_tools
    from omniagents.core.runtime.compaction_boundary import checkpoint_boundary

    active_stream = getattr(session, "current_stream_result", None)
    if (
        active_stream is not None
        and not active_stream.is_complete
        and not checkpoint_boundary.get()
    ):
        raise RuntimeError(
            "Wait for the active run to finish before compacting context."
        )

    context_items = (
        await session.get_items()
        if callable(getattr(session, "get_items", None))
        else session.history
    )
    if has_pending_compaction_tools(session) or _has_unresolved_function_call(
        context_items
    ):
        raise RuntimeError("Context compaction must wait for pending tool results.")
    # get_items can yield; claim only after rechecking concurrent entry.
    if (
        getattr(session, "_checkpoint_compaction_in_progress", False)
        or getattr(session, "_sdk_compaction_in_progress", False)
        or getattr(session, "_compact_command_in_progress", False)
    ):
        raise RuntimeError("Context compaction is already in progress.")
    history = list(getattr(session, "history", []) or [])
    if not history:
        return "Nothing to compact yet — this session hasn't started."

    native = native_compaction_for_session(service, session)
    summarizer = compaction_summarizer() if native is None else None
    if native is None and summarizer is None:
        return (
            "Compaction is unavailable: no compaction summarizer is installed "
            "(see omniagents.core.runtime.compaction.set_compaction_summarizer)."
        )

    setattr(session, "_compact_command_in_progress", True)
    setattr(session, "_checkpoint_compaction_in_progress", native is None)
    implementation = "native" if native is not None else "checkpoint"
    started = time.monotonic()
    try:
        await service.set_client_status(
            "Compacting...",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=True,
        )
        logger.info(
            "Compaction started session=%s implementation=%s",
            session.id,
            implementation,
        )

        async def _summarize(prompt: str) -> str:
            assert summarizer is not None
            return await summarizer(prompt, service=service, session=session)

        async def _compact():
            if native is not None:
                from omniagents.core.session.compaction import (
                    explicit_native_compaction,
                )

                # Explicit/recovery calls must use current durable history,
                # not a cached response ID from a prior or failed model turn.
                native._compaction_candidate_items = None
                native._session_items = None
                token = explicit_native_compaction.set(True)
                try:
                    await native.run_compaction(
                        {"force": True, "compaction_mode": "input"}
                    )
                finally:
                    explicit_native_compaction.reset(token)
            else:
                await run_checkpoint_compaction(session, _summarize)

        try:
            await asyncio.wait_for(
                _compact(),
                timeout=COMPACTION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Context compaction timed out after {COMPACTION_TIMEOUT_SECONDS}s; "
                "conversation history was preserved."
            ) from exc
    finally:
        setattr(session, "_checkpoint_compaction_in_progress", False)
        setattr(session, "_compact_command_in_progress", False)
        logger.info(
            "Compaction finished session=%s elapsed=%.1fs",
            session.id,
            time.monotonic() - started,
        )
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )

    return "Context compacted."
