from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from typing import Any

from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session import load_history
from omniagents.core.session.query import (
    count_sessions,
    ensure_sessions_schema,
    parse_datetime_arg,
    populate_session_stats,
    query_sessions,
    search_session_messages,
)
from omniagents.core.session.summarize import (
    session_summarizer,
    summarize_session,
)


def dump_json(value: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def coerce_limit(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out <= 0:
        return None
    return out


def coerce_nonnegative_int(value: int | None) -> int:
    if value is None:
        return 0
    try:
        out = int(value)
    except (TypeError, ValueError):
        return 0
    if out < 0:
        return 0
    return out


def add_sessions_subparser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("sessions", help="Inspect OmniAgents sessions")
    parser.set_defaults(command="sessions")
    sessions_sub = parser.add_subparsers(dest="sessions_cmd")

    db_path = sessions_sub.add_parser("db-path", help="Print the sessions DB path")
    db_path.add_argument("--project", default=None, help="Project slug")
    db_path.add_argument("--agent", default=None, help="Agent slug")

    list_parser = sessions_sub.add_parser("list", help="List sessions")
    list_parser.add_argument("--project", default=None, help="Project slug")
    list_parser.add_argument("--agent", default=None, help="Agent slug")
    list_parser.add_argument("--include-archived", action="store_true")
    list_parser.add_argument("--after", default=None)
    list_parser.add_argument("--before", default=None)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--all", action="store_true")
    list_parser.add_argument("--stats", action="store_true")
    list_parser.add_argument("--text", action="store_true")
    list_parser.add_argument("--pretty", action="store_true")

    search_parser = sessions_sub.add_parser("search", help="Search session messages")
    search_parser.add_argument("query")
    search_parser.add_argument("--project", default=None, help="Project slug")
    search_parser.add_argument("--agent", default=None, help="Agent slug")
    search_parser.add_argument(
        "--role", default="any", choices=["any", "user", "assistant"]
    )
    search_parser.add_argument("--include-archived", action="store_true")
    search_parser.add_argument("--after", default=None)
    search_parser.add_argument("--before", default=None)
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--text", action="store_true")
    search_parser.add_argument("--pretty", action="store_true")

    export_parser = sessions_sub.add_parser(
        "export", help="Export a session transcript"
    )
    export_parser.add_argument("session_id")
    export_parser.add_argument("--project", default=None, help="Project slug")
    export_parser.add_argument("--agent", default=None, help="Agent slug")
    export_parser.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    export_parser.add_argument("--pretty", action="store_true")

    summarize_parser = sessions_sub.add_parser(
        "summarize", help="Summarize a session with the installed summarizer"
    )
    summarize_parser.add_argument("session_id")
    summarize_parser.add_argument("--project", default=None, help="Project slug")
    summarize_parser.add_argument("--agent", default=None, help="Agent slug")
    summarize_parser.add_argument(
        "--model",
        default=None,
        help="Model reference handed to the installed summarizer (unresolved)",
    )
    summarize_parser.add_argument(
        "--tail",
        type=int,
        default=200,
        help="Summarize only the last N history items (<=0 for all)",
    )
    summarize_parser.add_argument("--pretty", action="store_true")
    return parser


def handle_sessions_command(args: argparse.Namespace) -> int:
    if not getattr(args, "sessions_cmd", None):
        print("A sessions subcommand is required.")
        return 1

    project = getattr(args, "project", None)
    agent = getattr(args, "agent", None)

    if args.sessions_cmd == "db-path":
        print(str(get_sessions_db_path(project, agent)))
        return 0

    if args.sessions_cmd == "list":
        ensure_sessions_schema(project=project, agent=agent)
        after = parse_datetime_arg(args.after)
        if args.after and after is None:
            print(
                "Invalid --after datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
            )
            return 1
        before = parse_datetime_arg(args.before)
        if args.before and before is None:
            print(
                "Invalid --before datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
            )
            return 1

        limit = None if args.all else coerce_limit(args.limit)
        offset = coerce_nonnegative_int(args.offset)
        sessions = query_sessions(
            project=project,
            agent=agent,
            include_archived=args.include_archived,
            after=after,
            before=before,
            limit=limit,
            offset=offset,
        )
        if args.stats:
            populate_session_stats(project=project, agent=agent, sessions=sessions)
        total = count_sessions(
            project=project,
            agent=agent,
            include_archived=args.include_archived,
            after=after,
            before=before,
        )
        if args.text:
            if not sessions:
                print("No sessions.")
                return 0
            print(f"  {'Session ID':<36} {'Created':<20} {'Msgs':>5} {'Arch'}")
            print(f"  {'-'*36} {'-'*20} {'-'*5} {'-'*4}")
            for session in sessions:
                created = str(session.get("created_at") or "")[:19]
                msg_count = session.get("message_count")
                msg_n = (
                    str(msg_count)
                    if isinstance(msg_count, int)
                    else ("?" if args.stats else "-")
                )
                arch = "yes" if session.get("archived") else "-"
                print(f"  {session.get('id',''):<36} {created:<20} {msg_n:>5} {arch}")
            shown = len(sessions)
            if args.all:
                print(f"Showing {shown} of {total} sessions.", file=sys.stderr)
            else:
                print(
                    f"Showing {shown} of {total} sessions (offset {offset}, limit {limit or 0}).",
                    file=sys.stderr,
                )
            return 0
        print(dump_json(sessions, pretty=args.pretty))
        return 0

    if args.sessions_cmd == "search":
        ensure_sessions_schema(project=project, agent=agent)
        after = parse_datetime_arg(args.after)
        if args.after and after is None:
            print(
                "Invalid --after datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
            )
            return 1
        before = parse_datetime_arg(args.before)
        if args.before and before is None:
            print(
                "Invalid --before datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
            )
            return 1
        results = search_session_messages(
            project=project,
            agent=agent,
            query=(args.query or "").strip(),
            role=args.role,
            include_archived=args.include_archived,
            after=after,
            before=before,
            limit=coerce_limit(args.limit) or 50,
            offset=coerce_nonnegative_int(args.offset),
        )
        if args.text:
            if not results:
                print("No matches.")
                return 0
            for item in results:
                ts = item.get("timestamp") or ""
                sid = item.get("session_id")
                role = item.get("role")
                content = (item.get("content") or "").strip().replace("\n", " ")
                if len(content) > 160:
                    content = content[:157] + "..."
                print(f"{ts} {sid} [{role}] {content}")
            return 0
        print(dump_json(results, pretty=args.pretty))
        return 0

    if args.sessions_cmd == "export":
        ensure_sessions_schema(project=project, agent=agent)
        db_path = get_sessions_db_path(project, agent)
        if not db_path.exists():
            print(f"Session not found: {args.session_id}")
            return 1
        conn = sqlite3.connect(str(db_path))
        try:
            exists = (
                conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id=?", (args.session_id,)
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
        if not exists:
            print(f"Session not found: {args.session_id}")
            return 1
        history = load_history(args.session_id, project_slug=project, agent_slug=agent)
        if args.format == "json":
            print(dump_json(history, pretty=args.pretty))
            return 0
        for item in history:
            print(dump_json(item, pretty=False))
        return 0

    if args.sessions_cmd == "summarize":
        if not (args.session_id or "").strip():
            print("session_id is required")
            return 1
        if session_summarizer() is None:
            print(
                "No session summarizer is installed "
                "(see omniagents.core.session.summarize.set_session_summarizer)."
            )
            return 1
        try:
            out = asyncio.run(
                summarize_session(
                    args.session_id,
                    project=project,
                    agent=agent,
                    model=args.model,
                    tail=coerce_limit(args.tail),
                    # Products with domain-flavored envelope keys pin them on
                    # the namespace before dispatch, like project/agent.
                    envelope=getattr(args, "summary_envelope", None),
                )
            )
        except LookupError as err:
            print(str(err))
            return 1
        print(dump_json(out, pretty=args.pretty))
        return 0

    print(f"Unknown sessions subcommand: {args.sessions_cmd}")
    return 1
