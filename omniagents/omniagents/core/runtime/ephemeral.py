"""Ephemeral agent runs: run a project agent once, outside the session
lifecycle.

Utility agents (context compactors, recappers, session summarizers)
deliberately bypass ``AgentService`` — their runs must not append to the
user's session history, emit UI events, or trigger notifications. That
is a legitimate use of the SDK ``Runner``; what products kept
re-implementing around it was the same boilerplate: load the spec,
override the model, build the agent, wire a model provider, stamp a
workspace_root, drain the stream, extract the text.
:func:`run_agent_once` owns that boilerplate.

What this deliberately does NOT do: sessions, persistence, approvals
(there is no channel to answer them — keep utility agents tool-less or
approval-free), notifications, tracing beyond what the SDK does on its
own. If you need those, run through ``AgentService``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional


@contextmanager
def temporary_environ(overrides: Dict[str, Optional[str]]):
    """Apply environment overrides for the duration of the block.

    ``None`` values unset the variable. Prior values (or absence) are
    restored on exit.
    """
    prior: Dict[str, Optional[str]] = {}
    for key, value in (overrides or {}).items():
        prior[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def run_agent_once(
    agent: Any,
    prompt: str,
    *,
    model: Optional[str] = None,
    model_provider: Any = None,
    env_overrides: Optional[Dict[str, Optional[str]]] = None,
    session: Any = None,
    workspace_root: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    max_turns: int = 1,
) -> str:
    """Run *agent* once with *prompt* and return its final text output.

    Args:
        agent: A path to an ``agent.yml`` (str or Path) or a pre-loaded
            ``AgentSpec``.
        prompt: The single user input for the run.
        model: Optional model-name override applied to the spec.
        model_provider: Optional SDK ``ModelProvider`` for the run (e.g.
            a provider carrying OAuth credentials). Products resolve this
            themselves — provider wiring is deployment policy.
        env_overrides: Environment variables to apply for the duration of
            the run (e.g. per-provider API keys). ``None`` values unset.
        session: Optional live Session; used only as a workspace_root
            fallback source and passed to the agent builder (some
            builders read session-scoped settings). The run does NOT
            append to the session.
        workspace_root: Explicit workspace root for the run context.
            Falls back to ``session.context["workspace_root"]``, then the
            process cwd. Runs bypass the AgentService resolver, so pass
            an explicit value when host cwd is not the right anchor
            (e.g. sandboxed deployments).
        context: Extra run-context entries merged over the workspace_root
            stamp.
        max_turns: Turn budget; utility agents default to a single turn.

    Returns:
        The run's final output as a string.
    """
    from agents import RunConfig, Runner

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    if isinstance(agent, (str, Path)):
        spec = load_agent_spec_from_yaml(str(agent))
    else:
        spec = agent
    if model:
        spec.model_name = model

    built = await _default_build_agent(
        settings={}, mcp_servers=None, spec=spec, session=session
    )

    run_config = (
        RunConfig(model_provider=model_provider) if model_provider is not None else None
    )

    if not workspace_root and session is not None:
        sess_ctx = getattr(session, "context", None)
        if isinstance(sess_ctx, dict):
            wr = sess_ctx.get("workspace_root")
            if isinstance(wr, str) and wr.strip():
                workspace_root = wr
    if not workspace_root:
        workspace_root = os.getcwd()

    run_context: Dict[str, Any] = {"workspace_root": workspace_root}
    if context:
        run_context.update(context)

    with temporary_environ(env_overrides or {}):
        # Use the streaming runner: some backends (e.g. Codex) require
        # stream=true, and for every other model run_streamed routes
        # through the same per-turn path via the disable_token_streaming
        # patch.
        result = Runner.run_streamed(
            built,
            prompt,
            context=run_context,
            max_turns=max_turns,
            run_config=run_config,
        )
        async for _ in result.stream_events():
            pass
        return result.final_output_as(str)
