"""omni-code's CLI extensions, declared in project.yml (``cli.extensions``).

The framework's product CLI host (:mod:`omniagents.product_cli`) imports
this module and applies, in order: :data:`PREFLIGHT` (argv hooks run
before any dispatch), :data:`COMMANDS` (product subcommands and
overrides of reserved ones), then the framework's reserved commands,
then the fallthrough agent run.

Preflights self-gate with :func:`omniagents.product_cli.dispatches_to_command`
so wizard/embedded handling only fires on agent-run invocations — the
governance preflight runs unconditionally (it owns its own skip list)
and MUST stay first: if platform policy requires a sandbox it re-execs
or exits before anything else happens.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional


# Importing the models module installs omni-code's ModelConfigStore
# (default catalog, platform governance, codex OAuth, credential
# resolver) as the process-wide registry the framework's reserved
# `omni model` / `omni provider` commands operate on.
import omni_code.models  # noqa: F401  — installs the model store


def _get_arg_value(args: List[str], *flags: str) -> Optional[str]:
    for i, arg in enumerate(args):
        if arg in flags and i + 1 < len(args):
            return args[i + 1]
    return None


def _is_agent_run(args: List[str]) -> bool:
    """Whether this invocation falls through to the chat runtime."""
    from omniagents.product_cli import dispatches_to_command

    return not dispatches_to_command(args, extra_commands=COMMANDS)


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


#: Set by the embedded preflight; read by the models preflight so a
#: missing-models condition surfaces as JSON for the Electron launcher
#: instead of an interactive wizard.
_embedded_mode = False


def embedded_preflight(args: List[str]) -> Optional[List[str]]:
    """Embedded (Electron launcher) mode: strip ``--embedded``, suppress
    browser auto-open and the startup update notice, and force JSON
    output for web mode unless --output was given explicitly."""
    global _embedded_mode
    if not _is_agent_run(args) or "--embedded" not in args:
        return None

    _embedded_mode = True
    args = [a for a in args if a != "--embedded"]
    os.environ["OMNI_WEB_AUTO_OPEN"] = "false"
    # The launcher owns update UX; the framework host prints the notice
    # on the fallthrough path unless the product env disables it.
    os.environ["OMNI_CODE_UPDATE_CHECK"] = "0"
    if (
        _get_arg_value(args, "--mode", "-m") == "web"
        and _get_arg_value(args, "--output") is None
    ):
        args = [*args, "--output", "json"]
    return args


def models_preflight(args: List[str]) -> None:
    """Load the model config (+ platform model governance) and export
    MODEL_NAME; when no models are configured, run the setup wizard
    (or fail with a JSON error in embedded mode)."""
    if not _is_agent_run(args):
        return None

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()

    # Apply platform model governance if authenticated
    try:
        from omni_code.platform_models import apply_platform_model_governance

        apply_platform_model_governance()
    except Exception:
        pass

    if "MODEL_NAME" not in os.environ:
        model_runtime = resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]

    from omni_code.models import has_models_configured

    if not has_models_configured():
        if _embedded_mode:
            import json

            print(json.dumps({"error": "No model credentials configured"}))
            sys.exit(1)
        print("No model credentials configured. Starting setup wizard...\n")
        from omni_code.models import _STORE
        from omniagents.core.models.cli import run_model_command

        run_model_command(["setup"], store=_STORE)
        print()
    return None


PREFLIGHT = [governance_preflight, embedded_preflight, models_preflight]


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


def _serve(args: List[str]) -> Optional[int]:
    """Host-side agent server backed by a long-lived sandbox session."""
    from omni_code.serve_cli import main as serve_main

    return serve_main(args)


def _sandbox(args: List[str]) -> Optional[int]:
    """Run Omni Code inside a Docker sandbox."""
    from omni_code.sandbox_cli import main as sandbox_main

    return sandbox_main(args)


def _sandbox_host(args: List[str]) -> Optional[int]:
    """Host-side sandbox supervisor for the desktop launcher."""
    from omni_code.sandbox_host_cli import main as sandbox_host_main

    return sandbox_host_main(args)


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


COMMANDS = {
    "serve": _serve,
    "sandbox": _sandbox,
    "sandbox-host": _sandbox_host,
    "sessions": _sessions,
}
