"""Fail-closed invocation boundaries for SDK function tools (including MCP adapters).

Claims are never leases. A missing completion does not authorize another call.
Hosted/provider-executed and non-function SDK tools are outside this hook contract.
"""

import uuid

from agents import RunHooks

from omniagents.core.conversation.recorder import recorder_for_session
from omniagents.core.agents import mcp_ui
from omniagents.core.agents.mcp_identity import lookup_identity


class DurableToolHooks(RunHooks):
    def __init__(self, session, run_id, delegate=None):
        self.session = session
        self.run_id = run_id
        self.delegate = delegate or RunHooks()
        self.claims = {}
        for name in dir(RunHooks):
            if name.startswith("on_") and name not in (
                "on_tool_start",
                "on_tool_end",
                "on_llm_start",
            ):
                setattr(
                    self, name, getattr(self.delegate, name, getattr(RunHooks(), name))
                )

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def recorder(self):
        recorder = recorder_for_session(self.session)
        if recorder is None:
            raise RuntimeError("Tool execution requires available durable storage")
        return recorder

    def assert_owner(self):
        from .ownership import current_ownership

        ownership = current_ownership.get()
        if ownership is not None:
            if (
                ownership.thread_id != self.session.id
                or ownership.run_id != self.run_id
            ):
                raise RuntimeError("Tool invocation belongs to a different run owner")
            ownership.assert_current()

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.assert_owner()
        await self.delegate.on_llm_start(context, agent, system_prompt, input_items)

    async def on_tool_start(self, context, agent, tool):
        self.assert_owner()
        call_id = getattr(context, "tool_call_id", None)
        if isinstance(call_id, str) and call_id:
            payload = {
                "run_id": self.run_id,
                "call_id": call_id,
                "tool": tool.name,
                "input": context.tool_arguments,
            }
            identity = lookup_identity(self.session, tool.name)
            if identity:
                payload.update(server_label=identity[0], tool_label=identity[1])
            if getattr(tool, "_running_summary", None):
                payload["metadata"] = {"summary": str(tool._running_summary)}
            owner = uuid.uuid4().hex
            self.recorder().claim_tool_execution(self.session.id, payload, owner)
            self.claims[call_id] = (payload, owner)
        await self.delegate.on_tool_start(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        call_id = getattr(context, "tool_call_id", None)
        claim = self.claims.get(call_id)
        if call_id and claim is None:
            raise RuntimeError("Tool completion has no owned dispatch claim")
        if claim is not None:
            from omniagents.core.tools import RichToolOutput

            payload, owner = claim
            metadata = (
                dict(result.ui_metadata) if isinstance(result, RichToolOutput) else {}
            )
            ui = mcp_ui.pop(
                payload.get("tool_label", tool.name),
                payload.get("server_label"),
                call_id,
                consume=False,
            )
            if ui is not None:
                metadata.update(display_type="mcp_ui", mcp_ui=ui)
            self.recorder().complete_tool_execution(
                self.session.id,
                {
                    **payload,
                    "output": str(result),
                    "metadata": metadata,
                },
                owner,
            )
        await self.delegate.on_tool_end(context, agent, tool, result)
