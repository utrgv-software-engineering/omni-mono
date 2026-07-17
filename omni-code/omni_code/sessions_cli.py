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
from pathlib import Path
from typing import Any

from omniagents.core.runtime import run_agent_once

from omni_code.utility_runs import _build_model_provider
from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session import list_sessions, load_history
from omniagents.core.session.cli import (
    coerce_limit as _coerce_limit,
    dump_json as _dump_json,
    handle_sessions_command,
)
from omniagents.core.session.query import (
    populate_session_stats as oa_populate_session_stats,
    ensure_sessions_schema as oa_ensure_sessions_schema,
)
from omniagents.core.runtime.compaction import summarize_with_window_shrink


def _default_project() -> str:
    return "omni_code"


def _default_agent() -> str:
    return "omni"


def _delegate(sessions_cmd: str, args: argparse.Namespace, **fields: Any) -> None:
    """Run a framework sessions subcommand pinned to the omni_code project.

    The db-path/list/search/export command bodies live in
    ``omniagents.core.session.cli.handle_sessions_command``; this shim only
    maps omni-code's arg surface (project always ``omni_code``, ``--agent``
    defaulted) onto the framework namespace.
    """
    ns = argparse.Namespace(
        sessions_cmd=sessions_cmd,
        project=_default_project(),
        agent=args.agent,
        **fields,
    )
    rc = handle_sessions_command(ns)
    if rc:
        raise SystemExit(rc)


def cmd_db_path(args: argparse.Namespace) -> None:
    _delegate("db-path", args)


def cmd_list(args: argparse.Namespace) -> None:
    _delegate(
        "list",
        args,
        include_archived=args.include_archived,
        after=args.after,
        before=args.before,
        offset=args.offset,
        limit=args.limit,
        all=args.all,
        stats=args.stats,
        text=args.text,
        pretty=args.pretty,
    )


def cmd_search(args: argparse.Namespace) -> None:
    query = (args.query or "").strip()
    if not query:
        print("Query is required.")
        raise SystemExit(1)

    role = (args.role or "any").strip().lower()
    if role not in {"any", "user", "assistant"}:
        print("Invalid role. Use: any, user, assistant")
        raise SystemExit(1)

    _delegate(
        "search",
        args,
        query=query,
        role=role,
        include_archived=args.include_archived,
        after=args.after,
        before=args.before,
        offset=args.offset,
        limit=args.limit,
        text=args.text,
        pretty=args.pretty,
    )


def cmd_export(args: argparse.Namespace) -> None:
    session_id = (args.session_id or "").strip()
    if not session_id:
        print("session_id is required")
        raise SystemExit(1)

    fmt = (args.format or "jsonl").strip().lower()
    if fmt not in {"json", "jsonl"}:
        print("Invalid format. Use: json, jsonl")
        raise SystemExit(1)

    _delegate("export", args, session_id=session_id, format=fmt, pretty=args.pretty)


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


def _extract_transcript(history: list[dict]) -> str:
    parts: list[str] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return "\n".join(parts)


async def _run_session_summarizer_agent(
    *, model: str | None, runtime: dict[str, Any], prompt: str
) -> str:
    
    agent_yaml = (
        Path(__file__).resolve().parent.parent
        / "omni_agents"
        / "session_summarizer"
        / "agent.yml"
    )
    model_provider, env_overrides = _build_model_provider(runtime or {})
    # CLI invocation — anchor to the user's current working directory.
    # The session_summarizer agent has tools=[] today so this is unused,
    # but passing it keeps the call forward-compatible and consistent
    # with the framework's strict workspace_root convention.
    return await run_agent_once(
        agent_yaml,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        workspace_root=os.getcwd(),
    )


def cmd_summarize(args: argparse.Namespace) -> None:
    session_id = (args.session_id or "").strip()
    if not session_id:
        print("session_id is required")
        raise SystemExit(1)

    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    runtime = resolve_model_for_runtime(args.model)
    if not runtime:
        print("No model configured. Run: omni model setup")
        raise SystemExit(1)

    provider_obj, _env_overrides = _build_model_provider(runtime)
    if provider_obj is None:
        print("No usable model credentials found. Run: omni model setup")
        raise SystemExit(1)

    model_name = runtime.get("model")
    runtime_provider = runtime.get("provider")
    if (
        isinstance(model_name, str)
        and "/" in model_name
        and runtime_provider != "litellm"
    ):
        prefix = model_name.split("/", 1)[0]
        if prefix not in {"openai", "litellm"}:
            model_name = model_name.split("/", 1)[1]

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
            f"{_extract_transcript(w)}"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omni sessions", add_help=True)
    parser.add_argument("--agent", default=_default_agent())

    sub = parser.add_subparsers(dest="command", required=True)

    p_db = sub.add_parser("db-path", help="Print sessions.db path")
    p_db.set_defaults(func=cmd_db_path)

    p_list = sub.add_parser("list", help="List sessions")
    p_list.add_argument("--include-archived", action="store_true")
    p_list.add_argument(
        "--after", help="Only sessions created at/after this time (ISO-8601)"
    )
    p_list.add_argument(
        "--before", help="Only sessions created before this time (ISO-8601)"
    )
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument(
        "--all", action="store_true", help="Return all sessions (ignores --limit)"
    )
    p_list.add_argument(
        "--stats",
        action="store_true",
        help="Include message_count and first/last message previews (slower)",
    )
    p_list.add_argument("--pretty", action="store_true")
    p_list.add_argument("--text", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_export = sub.add_parser("export", help="Export a session's history")
    p_export.add_argument("session_id")
    p_export.add_argument("--format", default="jsonl")
    p_export.add_argument("--pretty", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_search = sub.add_parser("search", help="Search messages across sessions")
    p_search.add_argument("query")
    p_search.add_argument("--role", default="any")
    p_search.add_argument("--include-archived", action="store_true")
    p_search.add_argument("--after", help="Only messages at/after this time (ISO-8601)")
    p_search.add_argument("--before", help="Only messages before this time (ISO-8601)")
    p_search.add_argument("--offset", type=int, default=0)
    p_search.add_argument("--limit", type=int, default=50)
    p_search.add_argument("--pretty", action="store_true")
    p_search.add_argument("--text", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_sum = sub.add_parser("summarize", help="Summarize a prior session using an LLM")
    p_sum.add_argument("session_id")
    p_sum.add_argument(
        "--model", help="Model reference (e.g., gpt-5.1 or local/gpt-4.1)"
    )
    p_sum.add_argument("--tail", type=int, default=200)
    p_sum.add_argument("--pretty", action="store_true")
    p_sum.set_defaults(func=cmd_summarize)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


def setup_entrypoint() -> None:
    main(sys.argv[1:])
