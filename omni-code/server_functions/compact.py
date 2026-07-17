"""The `/compact` command: checkpoint-compact the session.

The orchestration (window since last checkpoint, shrink-on-context-error
retry, resume-protocol summary item, `context_summary` marker) is the
framework's ``run_checkpoint_compaction``. What lives here is omni-code's
part: resolving which model/provider the compactor agent runs on, and the
compactor agent itself (``omni_agents/compactor``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omniagents.core.runtime import run_agent_once

from omni_code.utility_runs import (
    _build_model_provider,
    ephemeral_workspace_root,
)
from omniagents import server_function
from omniagents.core.runtime.compaction import run_checkpoint_compaction
from omniagents.core.session.manager import Session


def _resolve_compaction_runtime(
    service: Any, session: Session
) -> tuple[str | None, dict[str, Any]]:
    candidates: list[str] = []
    active_model = getattr(session, "active_model", None)
    if isinstance(active_model, str) and active_model.strip():
        candidates.append(active_model.strip())
    spec_model = getattr(getattr(service, "spec", None), "model_name", None)
    if isinstance(spec_model, str) and spec_model.strip():
        candidates.append(spec_model.strip())

    from omni_code.models import resolve_model_for_runtime

    for ref in candidates:
        runtime = resolve_model_for_runtime(ref)
        if runtime:
            return ref, runtime

    model_config = getattr(session, "model_config", None)
    if isinstance(model_config, dict) and model_config.get("model"):
        runtime = {
            "provider": (model_config.get("provider") or "").strip().lower(),
            "model": model_config.get("model"),
            "api_key": model_config.get("api_key"),
            "base_url": model_config.get("base_url"),
            "api_version": model_config.get("api_version"),
            "use_responses": model_config.get("use_responses"),
            "use_responses_websocket": model_config.get("use_responses_websocket"),
        }
        return candidates[0] if candidates else None, runtime

    return candidates[0] if candidates else None, {}


async def _run_compaction_agent(
    *,
    model: str | None,
    runtime: dict[str, Any] | None,
    prompt: str,
    session: Session | None,
) -> str:
    agent_yaml = (
        Path(__file__).resolve().parent.parent
        / "omni_agents"
        / "compactor"
        / "agent.yml"
    )
    model_provider, env_overrides = _build_model_provider(runtime or {})
    return await run_agent_once(
        agent_yaml,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=ephemeral_workspace_root(session),
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
        model_name = runtime.get("model") if isinstance(runtime, dict) else None

        if (not isinstance(model_name, str) or not model_name.strip()) and isinstance(
            model_ref, str
        ):
            model_name = model_ref.strip()

        if (
            isinstance(model_name, str)
            and "/" in model_name
            and runtime.get("provider") != "litellm"
        ):
            prefix = model_name.split("/", 1)[0]
            if prefix not in {"openai", "litellm"}:
                model_name = model_name.split("/", 1)[1]

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
