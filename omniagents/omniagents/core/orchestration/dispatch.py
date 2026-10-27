"""Nested calls share the direct path's predicates, reviewer and durable claims."""

import asyncio
import inspect
import json
import uuid
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from omniagents.core.agents.service import current_service
from omniagents.core.runtime.tool_durability import DurableToolHooks, active_tool_hooks
from omniagents.core.session.helpers import session_from_ctx
from omniagents.core.tools import result_store
from omniagents.core.tools.output_budget import read_output


class Dispatcher:
    def __init__(self, context, agent, session, service, scope, hooks):
        self.context, self.agent, self.session = context, agent, session
        self.service, self.scope, self.hooks = service, scope, hooks
        self.tools = {}

    @classmethod
    async def create(cls, context):
        session, service = session_from_ctx(context), current_service()
        agent = getattr(context, "agent", None)
        if (
            session is None
            or service is None
            or agent is None
            or not session.active_run_id
        ):
            raise RuntimeError("JavaScript requires an active durable agent session")
        manager = service._session_manager
        scope = (manager._project_slug, manager._agent_slug)
        self = cls(
            context,
            agent,
            session,
            service,
            scope,
            active_tool_hooks.get() or DurableToolHooks(session, session.active_run_id),
        )
        for tool in await agent.get_all_tools(context):
            if not isinstance(tool, FunctionTool) or getattr(
                tool, "_orchestrator", False
            ):
                continue
            enabled = (
                tool.is_enabled(context, agent)
                if callable(tool.is_enabled)
                else tool.is_enabled
            )
            if inspect.isawaitable(enabled):
                enabled = await enabled
            if enabled and hasattr(tool, "_invoke_retained"):
                self.tools[tool.name] = tool
        return self

    async def __call__(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("Arguments must be an object")
        self.hooks.assert_owner()
        if method == "cells.list":
            from .cells import list_cells

            return await asyncio.to_thread(
                list_cells, self.scope, self.session.id, **params
            )
        if method in {"subagents.list", "subagents.transcript"}:
            return await self._subagents(method, params)
        if method == "transcript.list":
            if set(params) - {"cursor", "limit"}:
                raise ValueError("Only cursor and limit are accepted")
            from omniagents.core.conversation.model import ItemKind

            from .subagents import active_child

            child = active_child.get()
            return await self.service.list_items(
                child.thread_id if child else self.session.id,
                kinds=[kind.value for kind in ItemKind if kind != ItemKind.REASONING],
                **params,
            )
        if method == "tools.list":
            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.params_json_schema,
                }
                for tool in self.tools.values()
            ]
        if method == "results.list":
            return await asyncio.to_thread(
                result_store.list_results, self.scope, self.session.id, **params
            )
        if method == "results.get":
            return await asyncio.to_thread(
                result_store.get_result, self.scope, self.session.id, **params
            )
        if method == "results.read":
            return await asyncio.to_thread(
                read_output, self.scope, self.session.id, **params
            )
        if method != "tools.call":
            raise ValueError("Unknown JavaScript API")
        name, args = params.get("name"), params.get("args", {})
        if (
            not isinstance(name, str)
            or name not in self.tools
            or not isinstance(args, dict)
        ):
            raise ValueError("Unknown or unavailable tool, or invalid arguments")
        tool = self.tools[name]
        enabled = (
            tool.is_enabled(self.context, self.agent)
            if callable(tool.is_enabled)
            else tool.is_enabled
        )
        if inspect.isawaitable(enabled):
            enabled = await enabled
        if not enabled:
            raise ValueError("Tool is no longer enabled")
        call_id = "js_" + uuid.uuid4().hex
        arguments = json.dumps(args, ensure_ascii=False, allow_nan=False)
        ctx = ToolContext.from_agent_context(
            self.context,
            call_id,
            agent=self.agent,
            tool_name=name,
            tool_arguments=arguments,
        )
        approval = (
            tool.needs_approval(ctx, args, call_id)
            if callable(tool.needs_approval)
            else tool.needs_approval
        )
        if inspect.isawaitable(approval):
            approval = await approval
        if approval:
            response = await self.service.request_tool_approval(
                session=self.session,
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                run_id=self.hooks.run_id,
            )
            if response.get("decision") != "approve":
                raise PermissionError(
                    "Tool approval denied: "
                    + str(response.get("rejection_message") or name)
                )
        from agents.run_internal.tool_execution import _execute_tool_input_guardrails

        rejected = await _execute_tool_input_guardrails(
            func_tool=tool,
            tool_context=ctx,
            agent=self.agent,
            tool_input_guardrail_results=[],
        )
        if rejected is not None:
            return {"id": None, "status": "rejected", "value": rejected}
        # Recheck the lease after waiting for human review, before any side effect.
        self.hooks.assert_owner()
        await self.hooks.on_tool_start(ctx, self.agent, tool)
        if self.agent.hooks:
            await self.agent.hooks.on_tool_start(ctx, self.agent, tool)
        await self._event(
            "tool_called",
            {
                "run_id": self.hooks.run_id,
                "call_id": call_id,
                "tool": name,
                "input": arguments,
            },
        )
        envelope, preview = await tool._invoke_retained(ctx, arguments)
        await self.hooks.on_tool_end(ctx, self.agent, tool, preview)
        if self.agent.hooks:
            await self.agent.hooks.on_tool_end(ctx, self.agent, tool, preview)
        from omniagents.core.runtime.subagents import note_parent_tool_result

        await note_parent_tool_result(call_id, {"output": str(preview)})
        await self._event(
            "tool_result",
            {
                "run_id": self.hooks.run_id,
                "call_id": call_id,
                "tool": name,
                "output": str(preview),
                "metadata": getattr(preview, "ui_metadata", {}),
            },
        )
        if (
            len(json.dumps(envelope, ensure_ascii=True).encode())
            > result_store.MAX_INLINE_BYTES
        ):
            if envelope["id"]:
                return {k: v for k, v in envelope.items() if k != "value"} | {
                    "inline": False,
                    "read": "session.results.read({reference: id, offset: 0, limit: 8000})",
                }
            raise RuntimeError(
                "Tool completed but its result exceeds transport limits and retention failed; do not rerun"
            )
        return envelope

    async def _event(self, method, payload):
        from omniagents.core.runtime.bridge import _broadcast, _mcp_label_fields
        from omniagents.core.runtime.tool_events import emit_tool_audit

        payload.update(_mcp_label_fields(self.session, payload.get("tool")))

        if not await _broadcast(self.session, method, payload):
            self.session.pending_events.append((method, payload))
        await emit_tool_audit(self.service, self.session, method, payload)

    async def _subagents(self, method, params):
        from omniagents.core.conversation.model import ItemKind, ThreadFilter, UNSCOPED
        from .subagents import active_child

        child = active_child.get()
        root = child.thread_id if child else self.session.id
        conversation = self.service._conversation_service()

        def descendant(thread_id):
            seen = set()
            current = thread_id
            for _ in range(128):
                if not isinstance(current, str) or current in seen:
                    break
                seen.add(current)
                row = conversation.store.get_thread(current)
                if row is None or row.user_id != getattr(self.session, "user_id", None):
                    break
                if row.parent_thread_id == root:
                    return conversation.store.get_thread(thread_id)
                current = row.parent_thread_id
            raise ValueError("Subagent is unavailable in this session")

        if method == "subagents.list":
            if set(params) - {"limit", "cursor", "parent_id"}:
                raise ValueError("Only parent_id, limit and cursor are accepted")
            parent_id = params.get("parent_id", root)
            if parent_id != root:
                descendant(parent_id)
            page = conversation.list_threads(
                thread_filter=ThreadFilter(
                    user_id=getattr(self.session, "user_id", None) or UNSCOPED,
                    parent_thread_id=parent_id,
                ),
                **{k: v for k, v in params.items() if k != "parent_id"},
            )
            page["threads"] = [
                row
                for row in page["threads"]
                if row.get("user_id") == getattr(self.session, "user_id", None)
            ]
            return page
        if set(params) - {"id", "limit", "cursor"}:
            raise ValueError("Only id, limit and cursor are accepted")
        thread_id = params.get("id")
        thread = descendant(thread_id)
        target = thread.metadata.get("worker_target")
        if isinstance(target, dict):
            from omniagents.core.conversation.service import ConversationService

            conversation = ConversationService(
                project_slug=target["project_slug"], agent_slug=target["agent_slug"]
            )
            thread_id = target["thread_id"]
            worker = conversation.store.get_thread(thread_id)
            if worker is None or worker.user_id != getattr(
                self.session, "user_id", None
            ):
                raise ValueError("Worker transcript is unavailable")
        return conversation.list_items(
            thread_id,
            kinds=[kind.value for kind in ItemKind if kind != ItemKind.REASONING],
            **{k: v for k, v in params.items() if k != "id"},
        )
