"""Run proactive compaction between SDK model calls, after tool saves."""

import asyncio
from contextvars import ContextVar

from omniagents.core.session.compaction import (
    _candidate_token_estimate,
    _has_unresolved_function_call,
)

checkpoint_boundary = ContextVar("checkpoint_boundary", default=False)


def compaction_input_filter(session, service, max_input_tokens, strip_input):
    if getattr(getattr(service, "spec", None), "session_backend", None) is not None:
        # An external SDK backend owns its context; never substitute the
        # Omni transcript for that backend's request history.
        return strip_input
    if (service is None or not max_input_tokens) and getattr(
        session, "_sdk_compaction_session", None
    ) is None:
        return strip_input

    checkpoint_written = False
    sdk_context = getattr(session, "_sdk_history", None)
    task = asyncio.current_task()
    run_id = getattr(session, "active_run_id", None) or (
        task.get_name() if task is not None else None
    )

    async def filter_input(data):
        nonlocal checkpoint_written, sdk_context
        if getattr(session, "_sdk_history", None) is not sdk_context:
            checkpoint_written = True
            sdk_context = session._sdk_history
        if checkpoint_written:
            # The SDK keeps its pre-compaction original input across steps.
            # After a checkpoint, persisted context is authoritative for every
            # subsequent request (including newly completed tool outputs).
            data.model_data.input = await session.get_items()
        policies = getattr(service, "_runtime_hooks", {}).get("token", [])
        if _has_unresolved_function_call(data.model_data.input):
            return strip_input(data)

        native = getattr(session, "_sdk_compaction_session", None)
        if native is not None:
            # SDK persistence defers compacting local tool results. Drain that
            # pressure here, before another model call, using saved outputs.
            if callable(getattr(native, "run_compaction", None)):
                await native.run_compaction({"compaction_mode": "input"})
                if getattr(session, "_sdk_history", None) is not sdk_context:
                    sdk_context = session._sdk_history
                    data.model_data.input = await session.get_items()
                    checkpoint_written = True
            return strip_input(data)

        for hook in policies:
            if getattr(hook, "compaction_policy", None) is None:
                continue
            before = len(session.history)
            before_sdk_context = getattr(session, "_sdk_history", None)
            boundary = checkpoint_boundary.set(True)
            try:
                await hook(
                    service=service,
                    session=session,
                    run_id=run_id,
                    event="token",
                    params={
                        "max_input_tokens": max_input_tokens,
                        "totals": {
                            "total_tokens": _candidate_token_estimate(
                                data.model_data.input
                            )
                            + len(data.model_data.instructions or "") // 4,
                        },
                        "compaction_safe_boundary": True,
                    },
                )
            finally:
                checkpoint_boundary.reset(boundary)
            if (
                len(session.history) > before
                or getattr(session, "_sdk_history", None) is not before_sdk_context
            ):
                # The SDK already prepared this request. Replace it as well as
                # the durable context, or this request still sends old history.
                data.model_data.input = await session.get_items()
                checkpoint_written = True
                break
        return strip_input(data)

    return filter_input
