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
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from agents import Runner
from omniagents.core.paths import get_sessions_db_path
from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.session import list_sessions, load_history
from omniagents.core.session.query import (
    count_sessions as oa_count_sessions,
    ensure_sessions_schema as oa_ensure_sessions_schema,
    parse_datetime_arg as oa_parse_datetime_arg,
    populate_session_stats as oa_populate_session_stats,
    query_sessions as oa_query_sessions,
    search_session_messages as oa_search_session_messages,
)
from openai import APIStatusError, BadRequestError


def _dump_json(value: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _default_project() -> str:
    return "omni_code"


def _default_agent() -> str:
    return "omni"


def _coerce_limit(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out <= 0:
        return None
    return out


def _coerce_nonnegative_int(value: int | None) -> int:
    if value is None:
        return 0
    try:
        out = int(value)
    except (TypeError, ValueError):
        return 0
    if out < 0:
        return 0
    return out


def cmd_db_path(args: argparse.Namespace) -> None:
    db_path = get_sessions_db_path(_default_project(), args.agent)
    print(str(db_path))


def cmd_list(args: argparse.Namespace) -> None:
    project = _default_project()
    oa_ensure_sessions_schema(project=project, agent=args.agent)

    after = oa_parse_datetime_arg(args.after)
    if args.after and after is None:
        print(
            "Invalid --after datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
        )
        raise SystemExit(1)
    before = oa_parse_datetime_arg(args.before)
    if args.before and before is None:
        print(
            "Invalid --before datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
        )
        raise SystemExit(1)

    limit = None if args.all else _coerce_limit(args.limit)
    offset = _coerce_nonnegative_int(args.offset)

    sessions = oa_query_sessions(
        project=project,
        agent=args.agent,
        include_archived=args.include_archived,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
    )

    if args.stats:
        oa_populate_session_stats(project=project, agent=args.agent, sessions=sessions)

    total = oa_count_sessions(
        project=project,
        agent=args.agent,
        include_archived=args.include_archived,
        after=after,
        before=before,
    )

    if args.text:
        if not sessions:
            print("No sessions.")
            return
        print(f"  {'Session ID':<36} {'Created':<20} {'Msgs':>5} {'Arch'}")
        print(f"  {'-'*36} {'-'*20} {'-'*5} {'-'*4}")
        for s in sessions:
            created = str(s.get("created_at") or "")[:19]
            msg_count = s.get("message_count")
            msg_n = (
                str(msg_count)
                if isinstance(msg_count, int)
                else ("?" if args.stats else "-")
            )
            arch = "yes" if s.get("archived") else "-"
            print(f"  {s.get('id',''):<36} {created:<20} {msg_n:>5} {arch}")

        shown = len(sessions)
        if args.all:
            print(f"Showing {shown} of {total} sessions.", file=sys.stderr)
        else:
            print(
                f"Showing {shown} of {total} sessions (offset {offset}, limit {limit or 0}).",
                file=sys.stderr,
            )
        return

    print(_dump_json(sessions, pretty=args.pretty))

    shown = len(sessions)
    if args.all:
        print(f"Showing {shown} of {total} sessions.", file=sys.stderr)
    else:
        print(
            f"Showing {shown} of {total} sessions (offset {offset}, limit {limit or 0}).",
            file=sys.stderr,
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

    project = _default_project()
    oa_ensure_sessions_schema(project=project, agent=args.agent)
    db_path = get_sessions_db_path(project, args.agent)
    if not db_path.exists():
        if args.text:
            print("No matches.")
        else:
            print("[]")
        return

    after = oa_parse_datetime_arg(args.after)
    if args.after and after is None:
        print(
            "Invalid --after datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
        )
        raise SystemExit(1)
    before = oa_parse_datetime_arg(args.before)
    if args.before and before is None:
        print(
            "Invalid --before datetime. Use ISO-8601 like 2026-02-18 or 2026-02-18T15:04:05Z"
        )
        raise SystemExit(1)

    max_results = _coerce_limit(args.limit) or 50
    desired_offset = _coerce_nonnegative_int(args.offset)
    results = oa_search_session_messages(
        project=project,
        agent=args.agent,
        query=query,
        role=role,
        include_archived=args.include_archived,
        after=after,
        before=before,
        limit=max_results,
        offset=desired_offset,
    )

    if args.text:
        if not results:
            print("No matches.")
            return
        for r in results:
            ts = r.get("timestamp") or ""
            sid = r.get("session_id")
            rrole = r.get("role")
            content = (r.get("content") or "").strip().replace("\n", " ")
            if len(content) > 160:
                content = content[:157] + "..."
            print(f"{ts} {sid} [{rrole}] {content}")
        return

    print(_dump_json(results, pretty=args.pretty))


def cmd_export(args: argparse.Namespace) -> None:
    session_id = (args.session_id or "").strip()
    if not session_id:
        print("session_id is required")
        raise SystemExit(1)

    project = _default_project()
    oa_ensure_sessions_schema(project=project, agent=args.agent)
    db_path = get_sessions_db_path(project, args.agent)
    if not db_path.exists():
        print(f"Session not found: {session_id}")
        raise SystemExit(1)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,))
        exists = cur.fetchone() is not None
    finally:
        conn.close()
    if not exists:
        print(f"Session not found: {session_id}")
        raise SystemExit(1)

    history = load_history(session_id, project_slug=project, agent_slug=args.agent)
    fmt = (args.format or "jsonl").strip().lower()
    if fmt not in {"json", "jsonl"}:
        print("Invalid format. Use: json, jsonl")
        raise SystemExit(1)

    if fmt == "json":
        print(_dump_json(history, pretty=args.pretty))
        return

    for item in history:
        print(_dump_json(item, pretty=False))


def _is_context_length_exceeded(err: Exception) -> bool:
    if not isinstance(err, (BadRequestError, APIStatusError)):
        return False
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            return error_obj.get("code") == "context_length_exceeded"
    return False


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


def _litellm_env_overrides(
    *, model: str | None, api_key: str | None
) -> dict[str, str | None]:
    if not api_key:
        return {}

    model_name = (model or "").lower()
    if model_name.startswith("anthropic/") or model_name.startswith("claude"):
        return {"ANTHROPIC_API_KEY": api_key}
    if model_name.startswith("gemini/") or model_name.startswith("google/"):
        return {"GOOGLE_API_KEY": api_key}
    return {"OPENAI_API_KEY": api_key}


def _temporary_environ(overrides: dict[str, str | None]):
    @contextlib.contextmanager
    def _impl():
        prior: dict[str, str | None] = {}
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

    return _impl()


def _build_model_provider(
    runtime: dict[str, Any],
) -> tuple[Any | None, dict[str, str | None]]:
    provider = (runtime.get("provider") or "").strip().lower()
    model = runtime.get("model")
    api_key = runtime.get("api_key")
    base_url = runtime.get("base_url")

    if provider == "litellm":
        from agents.extensions.models.litellm_provider import LitellmProvider

        return LitellmProvider(), _litellm_env_overrides(model=model, api_key=api_key)

    if provider in {"openai", "openai-compatible", "azure"}:
        from openai import AsyncOpenAI
        from agents.models.openai_provider import OpenAIProvider

        resolved_base_url = base_url or "https://api.openai.com/v1"
        resolved_api_key = api_key
        if not resolved_api_key:
            if provider == "azure":
                resolved_api_key = os.getenv("AZURE_OPENAI_API_KEY")
            else:
                resolved_api_key = os.getenv("OPENAI_API_KEY")

        is_azure = resolved_base_url and (
            "openai.azure.com" in resolved_base_url
            or "services.ai.azure.com" in resolved_base_url
        )

        if is_azure:
            if not resolved_api_key:
                return None, {}
            client = AsyncOpenAI(
                api_key="dummy",
                base_url=resolved_base_url,
                default_headers={"api-key": resolved_api_key},
            )
            return OpenAIProvider(openai_client=client), {}

        if not resolved_api_key and resolved_base_url != "https://api.openai.com/v1":
            resolved_api_key = "dummy"

        if not resolved_api_key:
            return None, {}

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        return OpenAIProvider(openai_client=client), {}

    return None, {}


async def _run_session_summarizer_agent(
    *, model: str | None, runtime: dict[str, Any], prompt: str
) -> str:
    from agents import RunConfig

    agent_yaml = (
        Path(__file__).resolve().parent.parent
        / "omni_agents"
        / "session_summarizer"
        / "agent.yml"
    )
    spec = load_agent_spec_from_yaml(str(agent_yaml))
    if model:
        spec.model_name = model
    agent = await _default_build_agent(
        settings={}, mcp_servers=None, spec=spec, session=None
    )

    model_provider, env_overrides = _build_model_provider(runtime or {})
    run_config = (
        RunConfig(model_provider=model_provider) if model_provider is not None else None
    )
    with _temporary_environ(env_overrides):
        result = await Runner.run(agent, prompt, max_turns=1, run_config=run_config)
        return result.final_output_as(str)


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

    prompt = (
        "Session metadata:\n"
        f"{json.dumps(session_meta, ensure_ascii=False, default=str)}\n\n"
        "Transcript (chronological raw JSON items):\n"
        f"{_extract_transcript(window)}"
    )

    while True:
        try:
            raw = asyncio.run(
                _run_session_summarizer_agent(
                    model=model_name, runtime=runtime, prompt=prompt
                )
            )
            break
        except Exception as e:
            if _is_context_length_exceeded(e) and window:
                window.pop(0)
                prompt = (
                    "Session metadata:\n"
                    f"{json.dumps(session_meta, ensure_ascii=False, default=str)}\n\n"
                    "Transcript (chronological raw JSON items):\n"
                    f"{_extract_transcript(window)}"
                )
                continue
            raise

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
