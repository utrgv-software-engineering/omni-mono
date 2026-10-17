"""Bound model-visible text and retain omitted content outside the workspace.

References are scoped to the product, agent, and session, never filesystem paths.
Media blocks remain SDK-native; only text is budgeted. Provider-executed hosted
tools are outside the local FunctionTool boundary.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path
from types import MethodType
from typing import Any

from agents import RunContextWrapper, function_tool
from agents.tool import FunctionTool

from omniagents.core.paths import get_sessions_db_path
from omniagents.core.tools import RichToolOutput, session_id_from_ctx

MAX_OUTPUT_BYTES = 40_000  # 10,000 approximate tokens, shared across text blocks.
PAGE_CHARS = 8_000


def output_dir(scope: tuple[str | None, str | None], session_id: str) -> Path:
    if not session_id:
        raise ValueError("A session is required to retain or read tool output")
    owner = hashlib.sha256(json.dumps([*scope, session_id]).encode()).hexdigest()
    return get_sessions_db_path(*scope).parent / "tool_outputs" / owner


def save_output(scope, session_id: str, text: str) -> str:
    directory = output_dir(scope, session_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ref = uuid.uuid4().hex
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{ref}.txt")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return ref


def read_output(
    scope, session_id: str, reference: str, offset=0, limit=PAGE_CHARS, query=None
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", reference):
        raise ValueError("Tool output is unavailable in this session")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative character offset")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= PAGE_CHARS
    ):
        raise ValueError(f"limit must be between 1 and {PAGE_CHARS} characters")
    if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 500):
        raise ValueError("query must contain 1 to 500 characters")
    path = output_dir(scope, session_id) / f"{reference}.txt"
    try:
        with path.open(encoding="utf-8") as stream:
            remaining = offset
            while remaining:
                skipped = stream.read(min(remaining, 65536))
                if not skipped:
                    return {
                        "reference": reference,
                        "offset": offset,
                        "text": "",
                        "next_offset": None,
                    }
                remaining -= len(skipped)
            if query is not None:
                # Literal streaming search: retain overlap and context so a match
                # deep inside a minified line remains retrievable without regex risks.
                position = offset
                overlap = ""
                while True:
                    chunk = stream.read(65536)
                    window = overlap + chunk
                    found = window.find(query)
                    if found >= 0:
                        match_offset = position - len(overlap) + found
                        start = max(
                            offset,
                            match_offset - min(200, max(0, (limit - len(query)) // 2)),
                        )
                        page = read_output(scope, session_id, reference, start, limit)
                        page.update(
                            match_offset=match_offset,
                            next_search_offset=match_offset + len(query),
                        )
                        return page
                    if not chunk:
                        return {
                            "reference": reference,
                            "offset": offset,
                            "text": "",
                            "next_offset": None,
                            "match_offset": None,
                        }
                    position += len(chunk)
                    overlap = window[-(len(query) + 200) :]
            text = stream.read(limit)
            more = bool(stream.read(1))
            return {
                "reference": reference,
                "offset": offset,
                "text": text,
                "next_offset": offset + len(text) if more else None,
            }
    except FileNotFoundError:
        raise ValueError("Tool output is unavailable in this session") from None


class RichOutputList(list):
    """Keep structured SDK media output and UI retention metadata separate."""

    def __init__(self, items, metadata):
        super().__init__(items)
        self.ui_metadata = metadata


def _text_part(item):
    if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
        return item.get("text")
    if getattr(item, "type", None) == "text":
        return getattr(item, "text", None)
    return None


def _replace_text(item, text):
    if isinstance(item, dict):
        return {**item, "text": text}
    return item.model_copy(update={"text": text})


def _preview(text: str, budget: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    marker = "\n[… output omitted …]\n"
    available = max(0, budget - len(marker.encode()))
    left = available // 2
    right = available - left
    return (
        encoded[:left].decode("utf-8", errors="ignore")
        + marker
        + (encoded[-right:].decode("utf-8", errors="ignore") if right else "")
    )


async def bound_output(
    result: Any, context: Any, scope=(None, None), *, reference=None
) -> Any:
    metadata = dict(getattr(result, "ui_metadata", {}) or {})
    # Structured image/file blocks must not be converted into strings or clipped.
    original_result = result
    if (
        isinstance(result, dict)
        and result.get("type")
        in {"text", "image", "file", "input_text", "input_image", "input_file"}
        or getattr(result, "type", None) in {"text", "image", "file"}
    ):
        result = [result]
    structured = isinstance(result, (list, tuple)) and all(
        isinstance(item, dict)
        and item.get("type")
        in {"text", "image", "file", "input_text", "input_image", "input_file"}
        or getattr(item, "type", None) in {"text", "image", "file"}
        for item in result
    )
    texts = (
        [t for item in result if isinstance(t := _text_part(item), str)]
        if structured
        else [str(result)]
    )
    text = "\n\n".join(texts)
    budget = MAX_OUTPUT_BYTES
    requested = getattr(result, "max_output_chars", None)
    if isinstance(requested, int):
        budget = min(budget, max(512, requested))
    if len(text.encode("utf-8")) <= budget:
        return original_result
    if reference is None:
        try:
            reference = await asyncio.to_thread(
                save_output, scope, session_id_from_ctx(context) or "", text
            )
        except (OSError, ValueError):
            pass
    retained = {
        "status": "saved" if reference else "unavailable",
        "reference": reference,
        "session_id": session_id_from_ctx(context),
        "original_bytes": len(text.encode("utf-8")),
        "budget_bytes": budget,
    }
    if reference:
        notice = f'[Output shortened. Full text saved as {reference}. Use read_tool_output(reference="{reference}", offset=0) for character ranges or query="literal text" to search. Do not rerun the tool just to recover output.]\n'
    else:
        notice = "[Output shortened. Saving the full text failed; omitted content is unavailable. The tool already ran; do not assume it is safe to rerun.]\n"
    # Leave room for the notice and marker within the total budget.
    if structured:
        remaining = max(32, budget - len(notice.encode()) - 2 * len(texts))
        total = max(1, sum(len(t.encode()) for t in texts))
        output = []
        first = True
        for item in result:
            part = _text_part(item)
            if isinstance(part, str):
                allocation = max(0, remaining * len(part.encode()) // total)
                excerpt = _preview(part, allocation) if allocation >= 32 else ""
                output.append(_replace_text(item, (notice if first else "") + excerpt))
                first = False
            else:
                output.append(item)
        metadata["output_retention"] = retained
        return RichOutputList(output, metadata)
    shortened = notice + _preview(text, budget - len(notice.encode()))
    # Avoid retaining another unlimited copy in the UI payload. The artifact
    # is canonical; structured tool-specific metadata remains available.
    metadata.update(
        output_retention=retained, value=shortened, preview=shortened, truncated=True
    )
    inner = dict(metadata.get("metadata") or {})
    for key in ("stdout", "stderr"):
        if key in inner:
            inner[key] = (
                "[See retained tool output]"
                if reference
                else "[Full output unavailable]"
            )
    if inner:
        metadata["metadata"] = inner
    return RichToolOutput(shortened, metadata)


def session_scope(context, fallback):
    from omniagents.core.agents.service import current_service
    from omniagents.core.session.helpers import session_from_ctx

    service = current_service()
    if service is not None and session_from_ctx(context) is not None:
        manager = service._session_manager
        return (manager._project_slug, manager._agent_slug)
    return fallback


def install_output_budget(agent, scope):
    """Wrap the final tool list so dynamic MCP and sandbox function tools join it."""
    if getattr(agent, "_output_budget_installed", False):
        return agent
    original = type(agent).get_all_tools

    @function_tool
    async def read_tool_output(
        ctx: RunContextWrapper[Any],
        reference: str,
        offset: int = 0,
        limit: int = PAGE_CHARS,
        query: str | None = None,
    ) -> str:
        """Read saved tool text by character offset, or find literal query with surrounding context.

        References belong to this session. Follow next_offset for pages or
        next_search_offset for subsequent matches. Never rerun a tool to recover saved output.
        """
        try:
            page = await asyncio.to_thread(
                read_output,
                session_scope(ctx, scope),
                session_id_from_ctx(ctx) or "",
                reference,
                offset,
                limit,
                query,
            )
            return json.dumps(page, ensure_ascii=False)
        except (OSError, ValueError) as error:
            return json.dumps(
                {
                    "error": (
                        str(error)
                        if isinstance(error, ValueError)
                        else "Saved output could not be read"
                    )
                }
            )

    # Explicit read-only capability, available even when the agent has no workspace.
    setattr(read_tool_output, "_output_reader", True)
    agent.tools = [*agent.tools, read_tool_output]

    async def get_all_tools(self, context):
        tools = await original(self, context)
        bounded = []
        for tool in tools:
            if not isinstance(tool, FunctionTool):
                bounded.append(tool)
                continue
            wrapped = copy.copy(tool)
            invoke = tool.on_invoke_tool

            def make_invoke(fn, original_tool):
                name = original_tool.name

                async def capture(ctx, args):
                    from .result_store import retain

                    effective_scope = session_scope(ctx, scope)
                    value = await fn(ctx, args)
                    # Apply the SDK's output policy BEFORE durable capture so
                    # result inspection cannot recover rejected sensitive content.
                    if original_tool.tool_output_guardrails:
                        from agents.run_internal.tool_execution import (
                            _execute_tool_output_guardrails,
                        )

                        value = await _execute_tool_output_guardrails(
                            func_tool=original_tool,
                            tool_context=ctx,
                            agent=self,
                            real_result=value,
                            tool_output_guardrail_results=[],
                        )
                    try:
                        envelope = await asyncio.to_thread(
                            retain,
                            effective_scope,
                            session_id_from_ctx(ctx) or "",
                            name,
                            getattr(ctx, "tool_call_id", None),
                            value,
                        )
                    except (
                        OSError,
                        ValueError,
                        TypeError,
                        sqlite3.Error,
                    ):
                        # The side effect already happened. Never retry it to repair storage.
                        from .result_store import json_value

                        envelope = {
                            "id": None,
                            "status": "completed",
                            "retention": "unavailable",
                            "completeness": "tool_return_value",
                            "value": json_value(value),
                        }
                    preview = await bound_output(
                        value, ctx, effective_scope, reference=envelope["id"]
                    )
                    return envelope, preview

                async def call(ctx, args):
                    _, preview = await capture(ctx, args)
                    return preview

                return call, capture

            call, capture = make_invoke(invoke, tool)
            wrapped.on_invoke_tool = call
            wrapped.tool_output_guardrails = None
            setattr(wrapped, "_invoke_retained", capture)
            bounded.append(wrapped)
        return bounded

    agent.get_all_tools = MethodType(get_all_tools, agent)
    agent._output_budget_installed = True
    return agent
