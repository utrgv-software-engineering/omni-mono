from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput
from omniagents.rpc.agents import LocalAgent


_EXPLORER_AGENT_YAML = (
    Path(__file__).resolve().parent.parent / "omni_agents" / "explorer" / "agent.yml"
)


def _load_and_configure_spec():
    from omniagents.core.config.loader import load_agent_spec_from_yaml
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = resolve_model_for_runtime()

    spec = load_agent_spec_from_yaml(str(_EXPLORER_AGENT_YAML))

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    # Match worker_tools pattern: install a default-config resolver so the
    # spawned LocalAgent's AgentService picks up provider credentials
    # (api_key, base_url, extra_body, etc.) — not just the model name.
    # Without this the explorer crashes with 401 against any provider
    # whose credentials live in models.json rather than env vars.
    spec.resolve_default_model_config = lambda: (
        model_runtime if isinstance(model_runtime, dict) else None
    )

    return spec


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> str:
    """Extract workspace_root from the caller's context.

    Defers to ``omniagents.builtin.tools._workspace.require_workspace_root``
    so the error type and message line up with the framework. The
    framework's ``AgentService.start_run`` enforces workspace_root since
    v0.6.48, so this call is defense-in-depth.
    """
    from omniagents.builtin.tools._workspace import require_workspace_root

    return require_workspace_root(ctx, where="explore")


async def _run_explorer(
    question: str,
    context: dict[str, Any],
) -> str:
    """Create a LocalAgent for the explorer, run to completion, and return the result."""
    spec = _load_and_configure_spec()

    agent = LocalAgent(spec=spec, settings={})
    try:
        await agent.initialize()
        await agent._ensure_client()

        resp = await agent._client.other.start_run(
            prompt=question,
            context=context,
        )
        raw = getattr(resp, "result", resp)
        run_id = raw.get("run_id", "") if isinstance(raw, dict) else ""

        # Monitor the event queue until the run ends (same pattern as WorkerManager).
        result_text: str | None = None
        done = asyncio.Event()

        while not done.is_set():
            try:
                msg = await agent._event_queue.get()
            except Exception:
                return "[error] Event queue closed unexpectedly."

            method = msg.get("method", "")
            params = msg.get("params", {})

            if method == "client_request":
                request_id = params.get("request_id")
                if request_id:
                    try:
                        await agent._client.other.client_response(
                            request_id=request_id, ok=True, result={"approved": True}
                        )
                    except Exception:
                        pass
                continue

            if method == "message_output":
                result_text = params.get("content", "")
                continue

            if method != "run_end":
                continue

            if params.get("run_id") != run_id:
                continue

            error_info = params.get("error")
            if error_info:
                msg_text = (
                    error_info.get("message", "")
                    if isinstance(error_info, dict)
                    else str(error_info)
                )
                if not result_text:
                    result_text = f"[error] {msg_text}"

            if not result_text:
                result_text = "Explorer agent completed but produced no output."

            done.set()

        return result_text or "Explorer agent failed to produce a result."
    finally:
        await agent.cleanup()


@rich_function_tool(client_status="Exploring codebase...")
async def explore(ctx: RunContextWrapper[Any], question: str) -> RichToolOutput:
    """Ask a read-only sub-agent to explore the codebase and answer a question.

    Use this for broad codebase exploration when you need to search across
    multiple files or don't know where to look (e.g. "how is authentication
    implemented?", "find all API route definitions", "what files handle
    database migrations?").

    For targeted lookups of a specific file or symbol you already know about,
    use read_file or grep_files directly — they are faster for simple queries.

    You can run multiple explore calls in parallel when investigating different
    aspects of the codebase.

    Args:
        question: A precise question about the codebase to investigate.
    """
    workspace_root = _workspace_root_from_ctx(ctx)
    explore_context = {"workspace_root": workspace_root}

    from omniagents.core.debug import Debug
    Debug.log(f"[explore] caller ctx type={type(getattr(ctx, 'context', None)).__name__}, workspace_root={workspace_root}")

    output = await _run_explorer(question, explore_context)

    preview = output[:1500] if output else ""
    truncated = len(output) > 1500 if output else False
    question_summary = question[:60] + "..." if len(question) > 60 else question

    return RichToolOutput(output, {
        "display_type": "search_results",
        "summary": f"Explored: {question_summary}",
        "preview": preview,
        "truncated": truncated,
        "metadata": {
            "success": not output.startswith("[error]"),
            "question": question,
            "content_length": len(output) if output else 0,
        },
    })
