"""omni-code's CLI extensions, declared in project.yml (``cli.extensions``).

The framework's product CLI host (:mod:`omniagents.product_cli`) imports
this module and applies, in order: :data:`PREFLIGHT` (argv hooks run
before any dispatch), :data:`COMMANDS` (product subcommands and
overrides of reserved ones), then the framework's reserved commands,
then the fallthrough agent run (which carries the framework's embedded
interception and models-bootstrap conventions — no product code).

The governance preflight runs unconditionally (it owns its own skip
list) and MUST stay first: if platform policy requires a sandbox it
re-execs or exits before anything else happens.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


# omni-code's ModelConfigStore (catalog from omni_code/models.yml,
# platform governance, codex OAuth, credential resolver) is installed
# process-wide by the framework's product bootstrap — `bootstrap_product`
# reads the manifest's declared model catalog (`paths.model_catalog`)
# before this module is loaded.


# ---------------------------------------------------------------------------
# PREFLIGHT — run in order before any dispatch
# ---------------------------------------------------------------------------


def governance_preflight(args: List[str]) -> None:
    """Governance: if platform policy requires sandbox, re-exec inside
    omni-sandbox.

    Must run before anything else — if it re-execs, this process is
    replaced. Skips for non-agent subcommands (auth, model, etc.)
    automatically. Fail-closed: if governance can't be enforced, this
    exits rather than silently running ungoverned.
    """
    from omni_code.governance import maybe_enforce_sandbox

    maybe_enforce_sandbox()
    return None


PREFLIGHT = [governance_preflight]


# ---------------------------------------------------------------------------
# Sessions: the coding-flavored summary envelope + installed summarizer
# ---------------------------------------------------------------------------

#: The coding-flavored summary envelope. Keys must agree with the JSON
#: schema in ``omni_code/agents/session_summarizer/instructions.md``.
SUMMARY_ENVELOPE: Dict[str, Any] = {
    "summary": "",
    "decisions": [],
    "constraints": [],
    "key_files": [],
    "commands": [],
    "tests": [],
    "open_questions": [],
    "next_steps": [],
    "notable_errors": [],
}


async def _summarize_with_utility_agent(
    prompt: str, *, model: Optional[str], project: Optional[str],
    agent: Optional[str], session_id: str,
) -> str:
    """omni-code's session summarizer: resolve the model, then run the
    ``session_summarizer`` agent one-shot over the transcript prompt."""
    from omni_code.utility_runs import (
        build_model_provider,
        resolve_model_runtime,
        run_utility_agent,
    )

    model_name, runtime = resolve_model_runtime(model)
    if not runtime:
        raise RuntimeError("No model configured. Run: omni model setup")
    provider_obj, _env_overrides = build_model_provider(runtime)
    if provider_obj is None:
        raise RuntimeError("No usable model credentials found. Run: omni model setup")

    # CLI invocation — anchor to the user's current working directory.
    # The session_summarizer agent has tools=[] today so this is unused,
    # but passing it keeps the call forward-compatible and consistent
    # with the framework's strict workspace_root convention.
    return await run_utility_agent(
        "session_summarizer",
        prompt,
        model=model_name,
        runtime=runtime,
        workspace_root=os.getcwd(),
    )


# ---------------------------------------------------------------------------
# COMMANDS — product subcommands + overrides of reserved ones
# ---------------------------------------------------------------------------


def _sessions(args: List[str]) -> int:
    """Inspect OmniAgents sessions."""
    # Wire omni-code's summarizer + coding envelope, then delegate to the
    # framework's reserved-sessions default (project pinned to the
    # product slug from identity).
    from omniagents.core.session.summarize import set_session_summarizer
    from omniagents.product_cli import run_product_sessions_command

    set_session_summarizer(_summarize_with_utility_agent)
    return run_product_sessions_command(
        args, agent="omni", summary_envelope=SUMMARY_ENVELOPE
    )


# ``serve`` and ``sandbox-host`` are NOT overridden: the framework's
# reserved defaults (omniagents.product_serve) are launcher-complete for
# omni-code — same argv surface, readiness payload, profile chain
# (packaged sandbox_profile_default.yml via the manifest-sibling
# convention), entrypoint spec, and codex registration (conventional on
# the catalog's openai-oauth provider).
COMMANDS = {
    "sessions": _sessions,
}
