"""The `/compact` command: checkpoint-compact the session.

The orchestration (window since last checkpoint, shrink-on-context-error
retry, resume-protocol summary item, `context_summary` marker) is the
framework's ``run_checkpoint_compaction``. What lives here is omni-code's
part: resolving which model/provider the compactor agent runs on, and the
compactor agent itself (``omni_code/agents/compactor``).
"""

from __future__ import annotations

from typing import Any

from omniagents import server_function
from omniagents.core.runtime.compaction import run_checkpoint_compaction
from omniagents.core.session.manager import Session

from omni_code.utility_runs import (
    normalize_model_name,
    resolve_service_model_runtime as _resolve_compaction_runtime,
    run_utility_agent,
)


async def _run_compaction_agent(
    *,
    model: str | None,
    runtime: dict[str, Any] | None,
    prompt: str,
    session: Session | None,
) -> str:
    return await run_utility_agent(
        "compactor", prompt, model=model, runtime=runtime, session=session
    )


@server_function(
    description="Compact context by appending a summary marker",
    strict=True,
    name_override="compact",
)
async def compact(service: Any, session: Session) -> dict:
    await service.set_client_status(
        "Compacting...",
        session=session,
        run_id=getattr(session, "active_run_id", None),
        show_spinner=True,
    )
    try:
        model_ref, runtime = _resolve_compaction_runtime(service, session)
        model_name = normalize_model_name(model_ref, runtime)

        async def _summarize(prompt: str) -> str:
            return await _run_compaction_agent(
                model=model_name,
                runtime=runtime,
                prompt=prompt,
                session=session,
            )

        await run_checkpoint_compaction(session, _summarize)
    finally:
        await service.set_client_status(
            "",
            session=session,
            run_id=getattr(session, "active_run_id", None),
            show_spinner=False,
        )

    return "Context compacted."
