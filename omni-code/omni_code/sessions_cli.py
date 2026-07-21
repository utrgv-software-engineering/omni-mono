"""CLI for inspecting OmniAgents sessions.

Usage:
    omni sessions db-path
    omni sessions list [--include-archived] [--after ISO] [--before ISO] [--offset N] [--limit N] [--all] [--stats] [--text] [--pretty]
    omni sessions export <session_id> [--format json|jsonl] [--pretty]
    omni sessions search <query> [--role user|assistant|any] [--include-archived] [--after ISO] [--before ISO] [--offset N] [--limit N] [--pretty]
    omni sessions summarize <session_id> [--model <ref>] [--tail N] [--pretty]

All commands accept:
    --agent <slug>      (default: omni)

This CLI is always scoped to the `omni_code` project.

JSON is the default output format for machine parsing.

The commands — including ``summarize`` — are the framework's sessions
CLI (``omniagents.core.session.cli``); what stays here is the product
surface (leading ``--agent`` flag, pinned project slug, the coding
summary envelope) and the installed summarizer: a one-shot run of the
``session_summarizer`` agent on omni-code's resolved model.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from omniagents.core.session.cli import (
    add_sessions_subparser,
    handle_sessions_command,
)
from omniagents.core.session.summarize import set_session_summarizer

#: The coding-flavored summary envelope. Keys must agree with the JSON
#: schema in ``omni_code/agents/session_summarizer/instructions.md``.
SUMMARY_ENVELOPE: dict[str, Any] = {
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


def _default_project() -> str:
    return "omni_code"


def _default_agent() -> str:
    return "omni"


async def _summarize_with_utility_agent(
    prompt: str, *, model: str | None, project: str | None, agent: str | None,
    session_id: str,
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


set_session_summarizer(_summarize_with_utility_agent)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Product surface: a leading `--agent` flag (defaulted to the omni
    # agent) ahead of the subcommand; project is always pinned to
    # `omni_code` and the summary envelope carries the coding schema.
    # Everything else is the framework's sessions CLI.
    top = argparse.ArgumentParser(prog="omni sessions", add_help=False)
    top.add_argument("--agent", default=_default_agent())
    ns, rest = top.parse_known_args(argv)

    framework = argparse.ArgumentParser(prog="omni")
    add_sessions_subparser(framework.add_subparsers(dest="command"))
    args = framework.parse_args(["sessions", *rest])
    args.project = _default_project()
    if not getattr(args, "agent", None):
        args.agent = ns.agent
    args.summary_envelope = SUMMARY_ENVELOPE
    try:
        rc = handle_sessions_command(args)
    except RuntimeError as err:
        # Summarizer-installed model errors ("No model configured...")
        # surface as a clean message + exit 1, not a traceback.
        print(str(err))
        raise SystemExit(1)
    if rc:
        raise SystemExit(rc)


def setup_entrypoint() -> None:
    main(sys.argv[1:])
