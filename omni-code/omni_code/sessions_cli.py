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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from typing import Any

from omni_code.utility_runs import build_model_provider
from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session import list_sessions, load_history
from omniagents.core.session.cli import (
    add_sessions_subparser,
    coerce_limit as _coerce_limit,
    dump_json as _dump_json,
    handle_sessions_command,
)
from omniagents.core.session.query import (
    populate_session_stats as oa_populate_session_stats,
    ensure_sessions_schema as oa_ensure_sessions_schema,
)
from omniagents.core.runtime.compaction import (
    extract_transcript,
    summarize_with_window_shrink,
)


def _default_project() -> str:
    return "omni_code"


def _default_agent() -> str:
    return "omni"


def _trim_history_tail(items: list[dict], *, tail: int | None) -> list[dict]:
    if not items:
        return []
    if tail is None:
        return list(items)
    if tail <= 0:
        return []
    if len(items) <= tail:
        return list(items)
    return list(items[-tail:])


async def _run_session_summarizer_agent(
    *, model: str | None, runtime: dict[str, Any], prompt: str
) -> str:
    # CLI invocation — anchor to the user's current working directory.
    # The session_summarizer agent has tools=[] today so this is unused,
    # but passing it keeps the call forward-compatible and consistent
    # with the framework's strict workspace_root convention.
    from omni_code.utility_runs import run_utility_agent

    return await run_utility_agent(
        "session_summarizer",
        prompt,
        model=model,
        runtime=runtime,
        workspace_root=os.getcwd(),
    )


def cmd_summarize(args: argparse.Namespace) -> None:
    session_id = (args.session_id or "").strip()
    if not session_id:
        print("session_id is required")
        raise SystemExit(1)

    from omni_code.utility_runs import resolve_model_runtime

    model_name, runtime = resolve_model_runtime(args.model)
    if not runtime:
        print("No model configured. Run: omni model setup")
        raise SystemExit(1)

    provider_obj, _env_overrides = build_model_provider(runtime)
    if provider_obj is None:
        print("No usable model credentials found. Run: omni model setup")
        raise SystemExit(1)

    project = _default_project()
    list_sessions(project_slug=project, agent_slug=args.agent)
    oa_ensure_sessions_schema(project=project, agent=args.agent)
    db_path = get_sessions_db_path(project, args.agent)
    if not db_path.exists():
        print(f"Session not found: {session_id}")
        raise SystemExit(1)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT session_id, archived, created_at FROM sessions WHERE session_id=?",
            (session_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        print(f"Session not found: {session_id}")
        raise SystemExit(1)
    session_meta = {
        "id": row[0],
        "archived": bool(row[1]),
        "created_at": row[2],
        "message_count": None,
    }
    oa_populate_session_stats(project=project, agent=args.agent, sessions=[session_meta])

    history = load_history(session_id, project_slug=project, agent_slug=args.agent)
    window = _trim_history_tail(history, tail=_coerce_limit(args.tail))

    def _build_prompt(w: list[dict]) -> str:
        return (
            "Session metadata:\n"
            f"{json.dumps(session_meta, ensure_ascii=False, default=str)}\n\n"
            "Transcript (chronological raw JSON items):\n"
            f"{extract_transcript(w)}"
        )

    async def _summarize(prompt: str) -> str:
        return await _run_session_summarizer_agent(
            model=model_name, runtime=runtime, prompt=prompt
        )

    raw = asyncio.run(
        summarize_with_window_shrink(
            window, build_prompt=_build_prompt, summarize=_summarize
        )
    )

    out: dict[str, Any]
    try:
        parsed = json.loads(raw)
        out = parsed if isinstance(parsed, dict) else {"raw": raw}
    except Exception:
        out = {"raw": raw}

    out.setdefault("session_id", session_id)
    out.setdefault("project", project)
    out.setdefault("agent", args.agent)
    if isinstance(session_meta, dict):
        out.setdefault("created_at", session_meta.get("created_at"))
        out.setdefault("message_count", session_meta.get("message_count"))

    out.setdefault("summary", "")
    out.setdefault("decisions", [])
    out.setdefault("constraints", [])
    out.setdefault("key_files", [])
    out.setdefault("commands", [])
    out.setdefault("tests", [])
    out.setdefault("open_questions", [])
    out.setdefault("next_steps", [])
    out.setdefault("notable_errors", [])

    print(_dump_json(out, pretty=args.pretty))


def _build_summarize_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omni sessions summarize",
        description="Summarize a prior session using an LLM",
    )
    parser.add_argument("session_id")
    parser.add_argument(
        "--model", help="Model reference (e.g., gpt-5.1 or local/gpt-4.1)"
    )
    parser.add_argument("--tail", type=int, default=200)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Product surface: a leading `--agent` flag (defaulted to the omni
    # agent) ahead of the subcommand; project is always pinned to
    # `omni_code`. Everything else is the framework's sessions CLI.
    top = argparse.ArgumentParser(prog="omni sessions", add_help=False)
    top.add_argument("--agent", default=_default_agent())
    ns, rest = top.parse_known_args(argv)

    if rest and rest[0] == "summarize":
        args = _build_summarize_parser().parse_args(rest[1:])
        args.agent = ns.agent
        cmd_summarize(args)
        return

    framework = argparse.ArgumentParser(prog="omni")
    add_sessions_subparser(framework.add_subparsers(dest="command"))
    args = framework.parse_args(["sessions", *rest])
    args.project = _default_project()
    if not getattr(args, "agent", None):
        args.agent = ns.agent
    rc = handle_sessions_command(args)
    if rc:
        raise SystemExit(rc)


def setup_entrypoint() -> None:
    main(sys.argv[1:])
