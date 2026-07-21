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
# COMMANDS — product subcommands + overrides of reserved ones
# ---------------------------------------------------------------------------

# No overrides: the framework's reserved defaults are complete for
# omni-code.
#
# ``sessions`` is fully conventional — the reserved default pins the
# product slug as project and the manifest's entrypoint agent, the
# summarizer runs the conventionally-named
# ``omni_code/agents/session_summarizer`` agent (its instructions.md is
# the prompt), and the coding-flavored summary envelope is declared in
# project.yml (``sessions.summary_envelope``).
#
# ``serve`` and ``sandbox-host`` are launcher-complete framework
# defaults (omniagents.product_serve) — same argv surface, readiness
# payload, profile chain (packaged sandbox_profile_default.yml via the
# manifest-sibling convention), entrypoint spec, and codex registration
# (conventional on the catalog's openai-oauth provider).
COMMANDS: Dict[str, Any] = {}
