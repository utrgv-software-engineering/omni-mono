"""Canonical transcripts for agent-as-tool runs, keyed before dispatch."""

from __future__ import annotations
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from types import SimpleNamespace
from typing import Any
from agents import RunHooks
from omniagents.core.runtime.tool_durability import DurableToolHooks, active_tool_hooks
from omniagents.core.conversation.recorder import recorder_for_session
from omniagents.core.session.helpers import session_from_ctx

active_child: ContextVar[Any] = ContextVar("orchestration_child", default=None)


@dataclass
class Child:
    thread_id: str
    run_id: str
    recorder: Any
    hooks: Any
    capture_error: Exception | None = None


class ChildHooks(RunHooks):
    async def on_llm_start(self, context, agent, system_prompt, input_items):
        child = active_child.get()
        if child is not None:
            child.hooks.assert_owner()

    async def on_tool_start(self, context, agent, tool):
        child = active_child.get()
        if child is not None:
            await child.hooks.on_tool_start(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        child = active_child.get()
        if child is not None:
            await child.hooks.on_tool_end(context, agent, tool, result)


async def record_stream(payload):
    child = active_child.get()
    if child is None:
        return
    try:
        event = payload.get("event")
        if getattr(event, "type", "") != "run_item_stream_event":
            return
        from omniagents.core.runtime.bridge import stream_event_payload

        method, data = stream_event_payload(
            event.item,
            run_id=child.run_id,
            session_id=child.thread_id,
            session=None,
            include_mcp_ui=False,
        )
        if (
            method in {"message_output", "tool_called", "tool_result"}
            and data is not None
        ):
            child.recorder.record(child.thread_id, method, data)
    except Exception as exc:
        # SDK observers swallow exceptions; surface capture failure from the
        # outer invocation instead of pretending the transcript is complete.
        child.capture_error = exc
        raise


def wrap_agent_tool(tool, name):
    invoke = tool.on_invoke_tool

    async def call(ctx, arguments):
        parent = session_from_ctx(ctx)
        if parent is None:
            return await invoke(ctx, arguments)
        recorder = recorder_for_session(parent)
        if recorder is None:
            raise RuntimeError("Subagent transcript storage unavailable")
        owner_hooks = active_tool_hooks.get()
        if owner_hooks is None:
            return await invoke(ctx, arguments)
        owner_hooks.assert_owner()
        parent_child = active_child.get()
        parent_id = parent_child.thread_id if parent_child else parent.id
        call_id = ctx.tool_call_id
        digest = hashlib.sha256(
            f"{parent_id}:{owner_hooks.run_id}:{call_id}".encode()
        ).hexdigest()
        thread_id = "sub_" + digest[:32]
        recorder.ensure_thread(
            thread_id,
            user_id=getattr(parent, "user_id", None),
            source="agent_tool",
            project_legacy_history=False,
        )

        def link(thread):
            thread.parent_thread_id = parent_id
            thread.metadata.update(
                agent=name, parent_run_id=owner_hooks.run_id, parent_call_id=call_id
            )
            return thread

        recorder.store.mutate_thread(thread_id, link)
        recorder.open_turn(thread_id, call_id, prompt=arguments, prompt_role="user")
        hooks = DurableToolHooks(
            SimpleNamespace(id=thread_id), call_id, owner_hooks.delegate
        )
        setattr(hooks, "recorder", lambda: recorder)
        setattr(hooks, "assert_owner", owner_hooks.assert_owner)
        child = Child(thread_id, call_id, recorder, hooks)
        token = active_child.set(child)
        hooks_token = active_tool_hooks.set(hooks)
        reason = "completed"
        try:
            result = await invoke(ctx, arguments)
            if child.capture_error:
                raise RuntimeError(
                    "Subagent completed but canonical transcript capture failed"
                ) from child.capture_error
            return result
        except BaseException:
            reason = "error"
            raise
        finally:
            try:
                from .cells import close_run

                await close_run(parent.id, call_id)
                recorder.close_turn(
                    thread_id,
                    call_id,
                    end_reason=reason,
                    error=None,
                    usage=None,
                    model=None,
                    model_ref=None,
                )
            finally:
                active_tool_hooks.reset(hooks_token)
                active_child.reset(token)

    tool.on_invoke_tool = call
    return tool


def link_worker(
    parent_service, parent_id, worker_service, worker_session_id, worker_id
):
    """Persist a cross-shard reference; never mirror a worker's transient UI stream."""
    if parent_service is None or not parent_id or not worker_session_id:
        return
    getter = getattr(parent_service, "get_session", None)
    parent = getter(parent_id) if callable(getter) else None
    if parent is None:
        return
    recorder = recorder_for_session(parent)
    if recorder is None:
        raise RuntimeError("Worker relationship storage unavailable")
    manager = worker_service._session_manager
    target = {
        "project_slug": manager._project_slug,
        "agent_slug": manager._agent_slug,
        "thread_id": worker_session_id,
    }
    digest = hashlib.sha256(
        f"{parent_id}:{worker_id}:{worker_session_id}".encode()
    ).hexdigest()
    thread_id = "worker_" + digest[:32]
    recorder.ensure_thread(
        thread_id,
        user_id=getattr(parent, "user_id", None),
        source="worker_reference",
        project_legacy_history=False,
    )

    def link(thread):
        thread.parent_thread_id = parent_id
        thread.metadata.update(worker_id=worker_id, worker_target=target)
        return thread

    recorder.store.mutate_thread(thread_id, link)
