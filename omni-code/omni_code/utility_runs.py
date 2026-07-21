"""Shared wiring for omni-code's ephemeral (utility) agent runs.

The compact, recap, and session-summarize flows all run a one-shot
utility agent via ``omniagents.core.runtime.run_agent_once``. The model
resolution dance (ref → runtime dict → SDK provider) lives framework-side
in ``omniagents.core.models`` now and is re-exported here against
omni-code's installed model store; what stays product-specific is the
codex (openai-oauth) token-store binding and how workspace_root is
anchored for runs that bypass the AgentService resolver.
"""

from __future__ import annotations

import os
from typing import Any, Optional

# Install omni-code's model store as the process-wide registry before any
# re-exported resolver runs (import side effect of omni_code.models).
import omni_code.models  # noqa: F401

# ref → (model_name, runtime) resolution and name normalization are
# framework helpers bound to the installed store; re-exported here for
# omni-code's server functions and CLIs.
from omniagents.core.models import (  # noqa: E402,F401
    normalize_model_name,
    provider_from_runtime,
    resolve_model_runtime,
    resolve_service_model_runtime,
)

# Session-shape workspace-root reading and the ensure+persist dance are
# framework helpers now; re-exported here for omni-code's server functions.
from omniagents.core.server_functions.helpers import (  # noqa: E402,F401
    ensure_session_with_workspace,
    session_workspace_root,
)


def build_model_provider(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    """Turn a resolved model-runtime dict into an SDK model provider.

    Delegates to the framework's
    :func:`omniagents.core.models.provider_from_runtime` (litellm env
    overrides, no-credential → no-provider, dummy-key-for-local
    fallbacks, registered custom builders). What stays here is product
    policy: binding the codex token store to omni-code's config dir
    before the framework's ``openai-oauth`` builder constructs the
    refreshing client.
    """
    provider = (runtime.get("provider") or "").strip().lower()
    if provider == "openai-oauth":
        import omni_code.codex_auth  # noqa: F401

    return provider_from_runtime(runtime)


async def run_utility_agent(
    agent_name: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    runtime: Optional[dict[str, Any]] = None,
    session: Any = None,
    workspace_root: Optional[str] = None,
    model_settings: Optional[dict[str, Any]] = None,
    max_turns: int = 1,
    instructions: Optional[str] = None,
) -> str:
    """Run one of omni-code's headless utility agents (``omni_code/agents/<name>``)
    through the framework's ephemeral-run harness.

    ``instructions`` replaces the spec's system prompt for this run —
    used when the prompt's single source of truth lives in code (e.g.
    the framework's ``DEFAULT_RECAP_INSTRUCTIONS``) rather than in the
    agent dir's ``instructions.md``.
    """
    from pathlib import Path

    from omniagents.core.runtime import run_agent_once

    agent_yaml = (
        Path(__file__).resolve().parent / "agents" / agent_name / "agent.yml"
    )
    agent: Any = agent_yaml
    wants_settings = isinstance(model_settings, dict) and bool(model_settings)
    if wants_settings or instructions is not None:
        # run_agent_once applies the model-name override itself; model_settings
        # is the one spec field it doesn't touch, so stamp it on the loaded
        # spec and hand the spec (not the path) to the harness. Likewise,
        # ``instructions`` replaces the spec's system prompt when its single
        # source of truth lives in code rather than the agent dir.
        from omniagents.core.config.loader import load_agent_spec_from_yaml

        spec = load_agent_spec_from_yaml(str(agent_yaml))
        if wants_settings:
            spec.model_settings = model_settings
        if instructions is not None:
            spec.get_agent_instructions = lambda: instructions
        agent = spec
    model_provider, env_overrides = build_model_provider(runtime or {})
    return await run_agent_once(
        agent,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=workspace_root or ephemeral_workspace_root(session),
        max_turns=max_turns,
    )


def ephemeral_workspace_root(session: Any = None) -> str:
    """workspace_root for a run that bypasses the AgentService resolver.

    Prefer the caller's session workspace, then the sandbox manifest root
    over the host cwd — they differ for container backends, and an
    unclamped host path would be wrong there. Matches
    fs_get_workspace_root's fallback chain.
    """
    if session is not None:
        sess_ctx = getattr(session, "context", None)
        if isinstance(sess_ctx, dict):
            wr = sess_ctx.get("workspace_root")
            if isinstance(wr, str) and wr.strip():
                return wr
    from omni_code.agent_workspace_state import get_manifest_root

    return get_manifest_root() or os.getcwd()
