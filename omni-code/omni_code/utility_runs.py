"""Shared wiring for omni-code's ephemeral (utility) agent runs.

The compact, recap, init, and session-summarize flows all run a one-shot
utility agent via the framework's project-aware harness
(``omniagents.core.runtime.run_project_agent_once``), which resolves
``omni_code/agents/<name>/agent.yml`` through the packaged project
manifest and applies the model_settings/instructions overrides itself.
What stays product-specific here is model resolution against omni-code's
installed model store, the codex (openai-oauth) token-store binding, and
how workspace_root is anchored for runs that bypass the AgentService
resolver.
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
        import omniagents.core.providers.codex  # noqa: F401

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
    """Run one of omni-code's headless utility agents (``omni_code/agents/<name>``).

    Thin delegate to the framework's
    :func:`omniagents.core.runtime.run_project_agent_once` — the agent
    resolves through the bootstrapped product manifest. omni-code's
    remaining policy: the codex-aware provider build
    (:func:`build_model_provider`) and the manifest-root workspace
    fallback (:func:`ephemeral_workspace_root`).
    """
    from omniagents.core.runtime import run_project_agent_once

    model_provider, env_overrides = build_model_provider(runtime or {})
    return await run_project_agent_once(
        agent_name,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=workspace_root or ephemeral_workspace_root(session),
        model_settings=model_settings,
        max_turns=max_turns,
        instructions=instructions,
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
