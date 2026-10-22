"""FastAPI routes for the React UI"""

import inspect
import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal, Set

try:
    from typing_extensions import NotRequired, TypedDict  # type: ignore
except ImportError:  # pragma: no cover - Python 3.12+
    from typing import NotRequired, TypedDict  # type: ignore

from dataclasses import replace, asdict, dataclass

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope
from starlette.datastructures import URL

import yaml

from pydantic import BaseModel, Field
from omniagents.execution import AgentHostResources

from omniagents.core.paths import (
    get_sessions_db_path,
    get_sessions_dir,
    get_traces_dir,
)
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.providers.auth.base import AuthProvider
from omniagents.core.providers.auth.none import NoneAuthProvider
from omniagents.core.providers.auth.token import TokenAuthProvider
from omniagents.rpc.auth import (
    WS_CLOSE_UNAUTHENTICATED,
    AuthenticationFailed,
    ConnectionAuthenticator,
    legacy_query_auth_enabled,
)
from omniagents.rpc.server import JsonRpcChannel, JsonRpcEndpoint
from omniagents.rpc.transport.websocket import ServerWebSocketTransport
from omniagents.rpc.transport import TransportClosedError
from omniagents.core.project.runtime import (
    load_project,
    build_spec_from_project,
    resolve_agent,
)
from omniagents.core.eval.loader import load_eval_config, filter_scenarios
from omniagents.core.eval.scenarios import generate_scenarios
from omniagents.core.eval.multi_turn import run_scenarios_against_agent
from omniagents.core.eval.split import create_splits
from omniagents.core.eval.annotate import (
    annotate_scenarios,
    write_annotation_output,
    _slugify,
)
from omniagents.core.eval.optimize import (
    optimize_judge_from_project,
    optimize_agent_from_project,
)
from omniagents.core.eval.pipeline import run_evaluation, _load_local_measures
from omniagents.core.eval.results import (
    generate_run_id,
    get_results_directory,
    save_run_results,
    update_runs_index,
)
from omniagents.core.evaluation.registry import _MEASURES as _REGISTERED_MEASURES
from omniagents.core.project.apply import apply_agent_instructions
from omniagents.core.project.scaffold import (
    scaffold_judge,
    scaffold_agent,
    delete_agent,
)
from omniagents.core.utils.fs import remove_tree

from omniagents.core.tracing.storage import SQLiteStorage

try:
    from agents.agent_output import AgentOutputSchema  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AgentOutputSchema = None  # type: ignore

logger = logging.getLogger(__name__)


def _expand_env_template(value: str) -> str:
    """Expand ${VAR:-default} style templates using environment variables.

    Supports:
    - ${VAR} - replaced with env var value or empty string
    - ${VAR:-default} - replaced with env var value or default if not set

    Args:
        value: String that may contain ${VAR:-default} templates.

    Returns:
        String with templates expanded.
    """
    import os
    import re

    if not isinstance(value, str) or "${" not in value:
        return value

    # Pattern for ${VAR:-default} or ${VAR}
    pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2) or ""
        return os.environ.get(var_name, default)

    return re.sub(pattern, replacer, value)


def _fetch_first_user_input(conn, trace_id):
    """Pull the first user message from a trace's earliest response/generation span."""
    if not trace_id:
        return None
    cursor = conn.execute(
        """
        SELECT data FROM spans
        WHERE trace_id = ? AND span_type IN ('response', 'generation')
        ORDER BY sequence_number
        LIMIT 1
        """,
        (trace_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    try:
        raw = row["data"]
        full_span = json.loads(raw) if isinstance(raw, str) else (raw or {})
        input_msgs = full_span.get("span_data", {}).get("input") or []
        for msg in input_msgs:
            if isinstance(msg, dict) and msg.get("role") == "user":
                return msg.get("content", "") or None
    except Exception:
        return None
    return None


def _filter_groups_by_dim(conn, group_ids, dim_map):
    """Return the subset of group_ids whose any trace's metadata satisfies the dim filter."""
    if not group_ids or not dim_map:
        return set(group_ids)
    placeholders = ",".join("?" * len(group_ids))
    cursor = conn.execute(
        f"""
        SELECT COALESCE(group_id, trace_id) AS gkey, metadata
        FROM traces
        WHERE COALESCE(group_id, trace_id) IN ({placeholders})
        """,
        group_ids,
    )
    matching: set = set()
    for row in cursor:
        gkey = row["gkey"]
        if gkey in matching:
            continue
        md = row["metadata"]
        try:
            meta = json.loads(md) if isinstance(md, str) else (md or {})
        except Exception:
            meta = {}
        mapping = (
            meta.get("omniagents", {})
            .get("synthetic", {})
            .get("selection", {})
            .get("mapping", {})
        )
        if all(mapping.get(k) in allowed for k, allowed in dim_map.items()):
            matching.add(gkey)
    return matching


_SESSION_LOC_CACHE: Dict[str, Tuple[float, List[Tuple[Path, str]]]] = {}
_SESSION_LOC_CACHE_LOCK = __import__("threading").Lock()
_SESSION_LOC_CACHE_TTL_SECONDS = 30.0


def _compute_session_db_locations(
    trace_project: str, *, sample_size: int = 500
) -> List[Tuple[Path, str]]:
    """Return ``(sessions.db path, agent_slug)`` for every agent that has
    sessions referenced by this trace project.

    Fast path: if a directly-named ``~/.omniagents/sessions/{trace_project}/``
    dir exists with agent sub-dirs, use that (covers the common case where
    trace project name equals the runtime's session project slug).

    Fallback: pull the most recent ``sample_size`` ``session_*`` group_ids
    from the trace DB and probe every ``sessions.db`` (across all session
    project dirs) for a hit. Matching at the **agent dir** level, not the
    project dir level, so unrelated agents that happen to share a session
    project root aren't pulled in.
    """
    sessions_root = get_sessions_dir()
    if not sessions_root.exists():
        return []

    direct = get_sessions_db_path(trace_project, "default").parent.parent
    if direct.exists():
        out: List[Tuple[Path, str]] = []
        for agent_dir in sorted(direct.iterdir(), key=lambda p: p.name.lower()):
            if not agent_dir.is_dir():
                continue
            db_path = agent_dir / "sessions.db"
            if db_path.exists():
                out.append((db_path, agent_dir.name))
        if out:
            return out

    trace_db_path = get_traces_dir() / f"{trace_project}.db"
    if not trace_db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{trace_db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT group_id FROM traces "
            "WHERE group_id LIKE 'session_%' "
            "ORDER BY created_at DESC LIMIT ?",
            (sample_size,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    sample_ids = [r[0][len("session_") :] for r in rows if r and r[0]]
    if not sample_ids:
        return []

    probe_ids = sample_ids[:200]
    placeholders = ",".join("?" for _ in probe_ids)
    probe_sql = f"SELECT 1 FROM sessions WHERE session_id IN ({placeholders}) LIMIT 1"

    matches: List[Tuple[Path, str]] = []
    seen_keys: Set[Tuple[str, str]] = set()
    for proj_dir in sorted(sessions_root.iterdir(), key=lambda p: p.name.lower()):
        if not proj_dir.is_dir():
            continue
        for agent_dir in sorted(proj_dir.iterdir(), key=lambda p: p.name.lower()):
            if not agent_dir.is_dir():
                continue
            db_path = agent_dir / "sessions.db"
            if not db_path.exists():
                continue
            try:
                c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            except sqlite3.OperationalError:
                continue
            try:
                hit = c.execute(probe_sql, probe_ids).fetchone()
            except sqlite3.Error:
                hit = None
            finally:
                c.close()
            if hit:
                key = (proj_dir.name, agent_dir.name)
                if key not in seen_keys:
                    seen_keys.add(key)
                    matches.append((db_path, agent_dir.name))
    return matches


def _list_project_agent_dirs(trace_project: str) -> List[Tuple[Path, str]]:
    """Return ``(sessions.db path, agent_slug)`` for every agent associated
    with this trace project. Cached in-process for
    ``_SESSION_LOC_CACHE_TTL_SECONDS`` since discovery probes many
    sessions.db files when names diverge.

    Honors the ``OMNIAGENTS_HISTORY_DB`` single-file override.
    """
    override = os.getenv("OMNIAGENTS_HISTORY_DB")
    if override:
        path = Path(os.path.expanduser(override)).resolve()
        return [(path, "default")] if path.exists() else []

    import time

    now = time.monotonic()
    with _SESSION_LOC_CACHE_LOCK:
        cached = _SESSION_LOC_CACHE.get(trace_project)
        if cached and now - cached[0] < _SESSION_LOC_CACHE_TTL_SECONDS:
            return cached[1]

    result = _compute_session_db_locations(trace_project)

    with _SESSION_LOC_CACHE_LOCK:
        _SESSION_LOC_CACHE[trace_project] = (now, result)
    return result


def _resolve_agent_group_ids(
    trace_project: str, agent: str, *, max_sessions: int = 10000
) -> List[str]:
    """Resolve session ids for ``agent`` under ``trace_project`` into trace
    group_ids (``session_{sid}``).

    Aggregates across every sessions.db whose ``agent_slug`` matches ``agent``
    (the same slug can appear in multiple session-project dirs).
    """
    if not agent:
        return []

    candidates = [
        db for db, slug in _list_project_agent_dirs(trace_project) if slug == agent
    ]

    out: List[str] = []
    for db_path in candidates:
        if not db_path.exists():
            continue
        remaining = max_sessions - len(out)
        if remaining <= 0:
            break
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            continue
        try:
            rows = conn.execute(
                "SELECT session_id FROM sessions ORDER BY created_at DESC LIMIT ?",
                (remaining,),
            ).fetchall()
        except sqlite3.Error:
            continue
        finally:
            conn.close()
        out.extend(f"session_{r[0]}" for r in rows if r and r[0])
    return out


def _load_session_metadata_for_project(
    trace_project: str, session_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Best-effort lookup of session rows for a list of session_ids.

    Sessions live in per-(session-project, agent) SQLite DBs and the
    session-project slug doesn't have to match the trace project name, so
    we resolve candidates via :func:`_list_project_agent_dirs` (which falls
    back to discovery by trace content) and query each.
    """
    ids = [s for s in session_ids if isinstance(s, str) and s]
    if not ids:
        return {}

    override = os.getenv("OMNIAGENTS_HISTORY_DB")
    if override:
        db_paths: List[Tuple[Path, Optional[str]]] = [
            (Path(os.path.expanduser(override)).resolve(), None)
        ]
    else:
        db_paths = [(p, slug) for p, slug in _list_project_agent_dirs(trace_project)]

    out: Dict[str, Dict[str, Any]] = {}
    seen: Set[str] = set()
    for db_path, agent_slug in db_paths:
        remaining = [sid for sid in ids if sid not in seen]
        if not remaining:
            break
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            continue
        try:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "session_id" not in cols:
                continue
            select_parts = ["session_id", "archived", "created_at"]
            for opt in ("user_id", "hold"):
                if opt in cols:
                    select_parts.append(opt)
            placeholders = ",".join("?" for _ in remaining)
            sql = (
                f"SELECT {', '.join(select_parts)} FROM sessions "
                f"WHERE session_id IN ({placeholders})"
            )
            rows = conn.execute(sql, remaining).fetchall()
            for row in rows:
                rec = dict(zip(select_parts, row))
                sid = rec["session_id"]
                out[sid] = {
                    "session_id": sid,
                    "agent_slug": agent_slug,
                    "archived": bool(rec.get("archived")),
                    "hold": bool(rec.get("hold")),
                    "user_id": rec.get("user_id"),
                    "created_at": rec.get("created_at"),
                }
                seen.add(sid)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return out


def _extract_trace_io_preview(spans):
    """Pull a short input/output text preview from response/generation spans.

    Returns ``(input_text, output_text)``; either may be None.
    """
    trace_input = None
    trace_outputs: list = []
    for span in spans:
        try:
            raw = span["data"]
            full_span = json.loads(raw) if isinstance(raw, str) else (raw or {})
            span_data = full_span.get("span_data", {})
            span_type = span["span_type"]

            if (
                span_type in ("response", "generation")
                and span_data.get("input")
                and not trace_input
            ):
                user_msgs = [
                    m.get("content", "")
                    for m in span_data["input"]
                    if isinstance(m, dict) and m.get("role") == "user"
                ]
                if user_msgs:
                    trace_input = user_msgs[-1] if len(user_msgs) > 1 else user_msgs[0]

            if span_type == "response":
                response = span_data.get("response") or {}
                for output_item in response.get("output") or []:
                    if output_item.get("type") == "message":
                        for content_item in output_item.get("content") or []:
                            if content_item.get("type") == "output_text":
                                text = content_item.get("text", "")
                                if text:
                                    trace_outputs.append(text)
                    elif output_item.get("type") == "function_call":
                        trace_outputs.append(
                            f"→ {output_item.get('name', 'function')}()"
                        )

            elif span_type == "generation":
                output_list = span_data.get("output")
                if isinstance(output_list, list) and output_list:
                    first_output = output_list[0]
                    if isinstance(first_output, dict):
                        inner_outputs = first_output.get("output")
                        if isinstance(inner_outputs, list):
                            for item in inner_outputs:
                                if not isinstance(item, dict):
                                    continue
                                if (
                                    item.get("type") == "message"
                                    and item.get("role") == "assistant"
                                ):
                                    for content in item.get("content") or []:
                                        if isinstance(content, dict) and content.get(
                                            "text"
                                        ):
                                            trace_outputs.append(content["text"])
                                elif item.get("type") == "function_call":
                                    trace_outputs.append(
                                        f"→ {item.get('name', 'function')}()"
                                    )
        except Exception:
            continue

    return trace_input, (trace_outputs[-1] if trace_outputs else None)


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            method = scope.get("method")
            if method not in ("GET", "HEAD"):
                raise
            request_path = scope.get("path") or ""
            if request_path.startswith("/api"):
                raise
            if "." in path:
                raise
            return await super().get_response("index.html", scope)


MODEL_SETTING_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "temperature",
        "label": "Temperature",
        "type": "number",
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    {
        "key": "top_p",
        "label": "Top P",
        "type": "number",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    {
        "key": "frequency_penalty",
        "label": "Frequency Penalty",
        "type": "number",
        "min": -2,
        "max": 2,
        "step": 0.1,
    },
    {
        "key": "presence_penalty",
        "label": "Presence Penalty",
        "type": "number",
        "min": -2,
        "max": 2,
        "step": 0.1,
    },
    {"key": "max_tokens", "label": "Max Output Tokens", "type": "integer", "min": 1},
    {
        "key": "tool_choice",
        "label": "Tool Choice",
        "type": "enum",
        "options": ["auto", "required", "none"],
    },
    {"key": "parallel_tool_calls", "label": "Parallel Tool Calls", "type": "boolean"},
    {
        "key": "truncation",
        "label": "Truncation",
        "type": "enum",
        "options": ["auto", "disabled"],
    },
    {"key": "store", "label": "Store Responses", "type": "boolean"},
    {"key": "include_usage", "label": "Include Usage", "type": "boolean"},
    {"key": "prompt_cache_key", "label": "Prompt Cache Key", "type": "string"},
    {
        "key": "prompt_cache_retention",
        "label": "Prompt Cache Retention",
        "type": "enum",
        "options": ["in-memory", "24h"],
    },
]

STRUCTURED_OUTPUT_TYPE_MAP: Dict[str, Any] = {
    "none": None,
    "plain_text": None,
    "string": str,
    "str": str,
    "dict": dict,
    "dictionary": dict,
    "object": dict,
    "list": list,
    "array": list,
    "boolean": bool,
    "bool": bool,
    "integer": int,
    "int": int,
    "float": float,
    "number": float,
}

STRUCTURED_OUTPUT_LABELS: Dict[str, str] = {
    "none": "Plain text",
    "dict": "JSON object (customizable)",
    "list": "JSON array",
    "boolean": "Boolean",
    "integer": "Integer",
    "float": "Float",
}


def _isoformat_utc(value: datetime) -> str:
    target = (
        value.astimezone(timezone.utc)
        if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )
    text = target.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _utc_now() -> str:
    return _isoformat_utc(datetime.now(timezone.utc))


def _canonical_output_type_key(output_type: Any) -> str:
    if output_type is None:
        return "none"
    if output_type is str:
        return "string"
    if output_type is dict:
        return "dict"
    if output_type is list:
        return "list"
    if output_type is bool:
        return "boolean"
    if output_type is int:
        return "integer"
    if output_type is float:
        return "float"
    return "custom"


def _output_type_label(key: str, fallback: Any = None) -> str:
    if key in STRUCTURED_OUTPUT_LABELS:
        return STRUCTURED_OUTPUT_LABELS[key]
    if key == "string":
        return "String"
    if key == "custom" and fallback is not None:
        try:
            return getattr(fallback, "__name__", "Custom schema") or "Custom schema"
        except Exception:
            return "Custom schema"
    return key.replace("_", " ").title()


def _resolve_output_type_override(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "default":
        return None
    return normalized


def _map_output_type_key_to_python(key: str, *, fallback: Any) -> Any:
    if not key:
        return fallback
    mapped = STRUCTURED_OUTPUT_TYPE_MAP.get(key)
    if key in ("none", "plain_text"):
        return None
    if mapped is not None:
        return mapped
    return fallback


def _structured_output_options(default_label: str) -> List[Dict[str, str]]:
    base_options = [
        {"value": "default", "label": f"Project default ({default_label})"},
        {"value": "none", "label": STRUCTURED_OUTPUT_LABELS["none"]},
        {"value": "dict", "label": STRUCTURED_OUTPUT_LABELS["dict"]},
        {"value": "list", "label": STRUCTURED_OUTPUT_LABELS["list"]},
        {"value": "boolean", "label": STRUCTURED_OUTPUT_LABELS["boolean"]},
        {"value": "integer", "label": STRUCTURED_OUTPUT_LABELS["integer"]},
        {"value": "float", "label": STRUCTURED_OUTPUT_LABELS["float"]},
    ]
    return base_options


def _guardrail_keys(
    active_guardrails: Optional[List[Any]], registry: Dict[str, Any]
) -> List[str]:
    if not active_guardrails:
        return []
    keys: List[str] = []
    for guardrail in active_guardrails:
        matched: Optional[str] = None
        for candidate_key, candidate in registry.items():
            if candidate is guardrail:
                matched = str(candidate_key)
                break
            candidate_name = None
            guardrail_name = None
            if hasattr(candidate, "get_name"):
                try:
                    candidate_name = candidate.get_name()
                except Exception:
                    candidate_name = None
            if hasattr(guardrail, "get_name"):
                try:
                    guardrail_name = guardrail.get_name()
                except Exception:
                    guardrail_name = None
            if candidate_name and guardrail_name and candidate_name == guardrail_name:
                matched = str(candidate_key)
                break
        if matched:
            keys.append(matched)
        else:
            if hasattr(guardrail, "get_name"):
                try:
                    fallback = guardrail.get_name()
                    if fallback:
                        keys.append(str(fallback))
                except Exception:
                    pass
    return keys


def _default_tool_names(spec: AgentSpec) -> List[str]:
    names: List[str] = []
    for tool in getattr(spec, "available_tools", []) or []:
        name = getattr(tool, "name", None)
        if name:
            names.append(str(name))
    return names


def _safe_options_dict(spec: AgentSpec) -> Dict[str, Any]:
    if spec.safe_agent_options:
        return asdict(spec.safe_agent_options)
    return {}


def _resolve_agent_spec(
    resources: Dict[str, Any], agent_key: Optional[str]
) -> Optional[AgentSpec]:
    if agent_key:
        specs = resources.get("agent_specs") or {}
        return specs.get(agent_key)
    return resources.get("base_spec")


def _compute_playground_ws_url(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_port = request.headers.get("x-forwarded-port")

    http_url = URL(str(request.url_for("playground_ws")))

    scheme = (
        forwarded_proto.split(",")[0].strip() if forwarded_proto else http_url.scheme
    )

    host = http_url.hostname or http_url.host
    port = http_url.port

    if forwarded_host:
        host_value = forwarded_host.split(",")[0].strip()
        if host_value:
            if ":" in host_value:
                host_part, _, port_part = host_value.partition(":")
                host = host_part or host
                try:
                    port = int(port_part)
                except ValueError:
                    pass
            else:
                host = host_value or host

    if forwarded_port:
        port_value = forwarded_port.split(",")[0].strip()
        try:
            port = int(port_value)
        except ValueError:
            pass

    ws_scheme = "wss" if scheme == "https" else "ws"
    ws_url = http_url.replace(scheme=ws_scheme)

    if host:
        ws_url = ws_url.replace(hostname=host)
    ws_url = ws_url.replace(port=port)

    return str(ws_url)


def _build_metadata_response(
    spec: AgentSpec,
    resources: Dict[str, Any],
    request: Request,
    *,
    agent_key: Optional[str],
) -> Dict[str, Any]:
    tool_registry: Dict[str, Any] = resources.get("tool_registry", {})
    guardrails_registry: Dict[str, Any] = resources.get(
        "guardrails_registry", {"input": {}, "output": {}}
    )
    project_name = (
        resources.get("project_name") or request.app.state.default_project or "default"
    )

    tools_payload: List[Dict[str, Any]] = []
    for key, tool in tool_registry.items():
        tool_name = getattr(tool, "name", None) or key
        schema = getattr(tool, "params_json_schema", None)
        if schema is not None:
            try:
                json.dumps(schema)
            except TypeError:
                schema = json.loads(json.dumps(schema, default=str))
        tools_payload.append(
            {
                "name": tool_name,
                "description": getattr(tool, "description", "") or "",
                "schema": schema,
                "strict": getattr(tool, "strict_json_schema", None),
            }
        )
    tools_payload.sort(key=lambda item: item["name"].lower())

    instructions_text = ""
    supplier = getattr(spec, "get_agent_instructions", None)
    if callable(supplier):
        try:
            instructions_text = supplier() or ""
        except Exception:
            instructions_text = ""

    default_output_key = _canonical_output_type_key(getattr(spec, "output_type", None))
    default_output_label = _output_type_label(
        default_output_key, getattr(spec, "output_type", None)
    )
    default_output_schema = _structured_output_schema(
        getattr(spec, "output_type", None), getattr(spec, "output_type_strict", True)
    )

    ws_url = _compute_playground_ws_url(request)

    input_guardrails = guardrails_registry.get("input", {})
    output_guardrails = guardrails_registry.get("output", {})

    input_guardrails_payload: List[Dict[str, Any]] = []
    for key, guardrail in input_guardrails.items():
        label = _guardrail_name(guardrail) or key
        input_guardrails_payload.append({"name": key, "label": label})
    input_guardrails_payload.sort(key=lambda item: item["label"].lower())

    output_guardrails_payload: List[Dict[str, Any]] = []
    for key, guardrail in output_guardrails.items():
        label = _guardrail_name(guardrail) or key
        output_guardrails_payload.append({"name": key, "label": label})
    output_guardrails_payload.sort(key=lambda item: item["label"].lower())

    default_guardrails = {
        "input": _guardrail_keys(
            getattr(spec, "input_guardrails", None), input_guardrails
        ),
        "output": _guardrail_keys(
            getattr(spec, "output_guardrails", None), output_guardrails
        ),
    }

    safe_defaults = {
        "use_safe_agent": bool(getattr(spec, "use_safe_agent", False)),
        "options": _safe_options_dict(spec),
    }

    return {
        "available": True,
        "project_name": project_name,
        "entry_agent": spec.name,
        "agent_key": agent_key,
        "agent_label": getattr(spec, "label", None) or spec.name or agent_key,
        "default_model": spec.model_name or "",
        "default_instructions": instructions_text,
        "default_tools": _default_tool_names(spec),
        "tools": tools_payload,
        "requires_model_input": True,
        "playground_ws_url": ws_url,
        "model_settings_schema": MODEL_SETTING_SCHEMA,
        "default_model_settings": spec.model_settings or {},
        "structured_output": {
            "default_type": default_output_key,
            "default_label": default_output_label,
            "default_strict": bool(getattr(spec, "output_type_strict", True)),
            "default_schema": default_output_schema,
            "options": _structured_output_options(default_output_label),
        },
        "guardrails": {
            "input": input_guardrails_payload,
            "output": output_guardrails_payload,
            "default_input": default_guardrails["input"],
            "default_output": default_guardrails["output"],
        },
        "safe_defaults": safe_defaults,
    }


def _structured_output_schema(
    output_type: Any, strict: bool
) -> Optional[Dict[str, Any]]:
    if AgentOutputSchema is None:
        return None
    if output_type is None:
        return None
    try:
        wrapper = AgentOutputSchema(output_type, strict_json_schema=strict)
        if wrapper.is_plain_text():
            return None
        return wrapper.json_schema()
    except Exception:
        return None


def _build_type_from_schema(schema: Dict[str, Any], agent_name: str) -> Optional[type]:
    if not isinstance(schema, dict):
        return None
    schema_type = schema.get("type")
    if schema_type and str(schema_type).lower() not in ("object", "dict", "dictionary"):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None
    required_raw = schema.get("required")
    required_names = (
        {str(item) for item in required_raw if isinstance(item, str)}
        if isinstance(required_raw, list)
        else set()
    )

    annotations: Dict[str, Any] = {}
    for key, value in properties.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(value, dict):
            continue
        json_type = str(value.get("type") or "string").lower()
        mapped = STRUCTURED_OUTPUT_TYPE_MAP.get(json_type)
        if mapped is None:
            if json_type in ("object", "dict", "dictionary"):
                mapped = dict
            elif json_type in ("array", "list"):
                mapped = list
            else:
                mapped = str
        if key in required_names:
            annotations[key] = mapped
        else:
            try:
                annotations[key] = NotRequired[mapped]  # type: ignore[index]
            except Exception:
                annotations[key] = mapped

    if not annotations:
        return dict

    safe_name = (
        "".join(ch for ch in (agent_name or "Playground") if ch.isalnum())
        or "Playground"
    )
    type_name = f"{safe_name}StructuredOutput"
    try:
        return TypedDict(type_name, annotations)  # type: ignore[arg-type]
    except Exception:
        return dict


def _guardrail_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "get_name"):
        try:
            extracted = value.get_name()
            if extracted:
                return str(extracted)
        except Exception:
            return None
    name = getattr(value, "name", None)
    return str(name) if name else None


class StorageManager:
    """Manages multiple storage instances for different projects"""

    def __init__(self):
        self.storages: Dict[str, SQLiteStorage] = {}
        self.storage_dir = get_traces_dir()

    def get_storage(self, project: str) -> SQLiteStorage:
        """Get or create storage instance for a project"""
        if project not in self.storages:
            db_path = self.storage_dir / f"{project}.db"
            created = not db_path.exists()
            self.storages[project] = SQLiteStorage(str(db_path))
            if created:
                logger.info("Created Studio database for project '%s'", project)
        return self.storages[project]


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


class PlaygroundSessionStore:
    """In-memory store of playground session configs.

    Each session also owns a sandbox tempdir, allocated on first
    ``upsert`` and surfaced as ``config["__playground_workspace_root"]``.
    The websocket handler passes that path as ``default_host_workspace_root``
    on the per-session ``AgentService``, so playground agents that have
    builtin filesystem tools (file_tools, bash, etc.) resolve relative
    paths against the sandbox instead of:

      * raising ``WorkspaceRootRequiredError`` under strict mode (the
        framework refuses to silently use the studio server's cwd), or
      * silently writing to wherever the studio server happened to be
        launched from (the bug strict mode was added to catch).

    Studio operators are the only ones interacting with the playground;
    qualifying for a default like ink/web/launcher do, but isolated
    per-session so two playground tabs don't trample each other.
    """

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, session_id: Optional[str], config: Dict[str, Any]) -> str:
        async with self._lock:
            key = session_id or uuid.uuid4().hex
            existing = self._sessions.get(key)
            payload = dict(config)
            # Every update moves updated_at forward, even within one tick
            # of a coarse clock (Windows).
            now = datetime.now(timezone.utc)
            if existing and existing.get("updated_at"):
                previous = datetime.fromisoformat(
                    str(existing["updated_at"]).replace("Z", "+00:00")
                )
                now = max(now, previous + timedelta(microseconds=1))
            payload["updated_at"] = _isoformat_utc(now)
            # Preserve the per-session sandbox tempdir across config
            # updates (the client may upsert the same session_id multiple
            # times to change instructions, tools, etc. — the workspace
            # stays put).
            if existing and existing.get("__playground_workspace_root"):
                payload["__playground_workspace_root"] = existing[
                    "__playground_workspace_root"
                ]
            else:
                payload["__playground_workspace_root"] = tempfile.mkdtemp(
                    prefix=f"omniagents_studio_playground_{key[:8]}_"
                )
            self._sessions[key] = payload
            return key

    async def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            entry = self._sessions.get(session_id)
            return dict(entry) if entry else None

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.pop(session_id, None)
        # Best-effort cleanup outside the lock — slow filesystem ops
        # shouldn't block other session operations.
        if entry:
            ws_root = entry.get("__playground_workspace_root")
            if isinstance(ws_root, str) and ws_root:
                remove_tree(ws_root, ignore_errors=True)


class PlaygroundSessionRequest(BaseModel):
    model: str
    instructions: Optional[str] = None
    tools: Optional[List[str]] = None
    model_settings: Optional[Dict[str, Any]] = None
    use_safe_agent: Optional[bool] = None
    safe_agent_options: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, List[str]]] = None
    output_type: Optional[str] = None
    output_type_strict: Optional[bool] = None
    output_schema: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class AgentUpdatePayload(BaseModel):
    model: str
    instructions: Optional[str] = None
    tools: List[str] = Field(default_factory=list)
    model_settings: Optional[Dict[str, Any]] = None
    use_safe_agent: bool = False
    safe_agent_options: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, List[str]]] = None
    output_type: Optional[str] = None
    output_type_strict: Optional[bool] = None
    output_schema: Optional[Dict[str, Any]] = None


class AgentCreatePayload(BaseModel):
    name: str
    model: Optional[str] = None


class DimensionValueModel(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    examples: List[str] = Field(default_factory=list)


class EvaluationDimensionModel(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    values: List[DimensionValueModel] = Field(default_factory=list)


class EvaluationDimensionsPayload(BaseModel):
    dimensions: List[EvaluationDimensionModel] = Field(default_factory=list)


class FailureCategoryModel(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    measures: List[str] = Field(default_factory=list)
    usageCount: int = 0


class EvaluationMeasureModel(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    module: Optional[str] = None
    sourcePath: Optional[str] = None
    docstring: Optional[str] = None
    hasFunction: bool = False
    usageCount: int = 0


class EvaluationMeasuresResponse(BaseModel):
    project: Optional[str] = None
    measures: List[EvaluationMeasureModel] = Field(default_factory=list)


class EvaluationMeasureCreatePayload(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    createStub: bool = True


class EvaluationMeasureUpdatePayload(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None


class EvaluationMeasureDeleteResponse(BaseModel):
    project: Optional[str] = None
    deleted: str
    measures: List[EvaluationMeasureModel] = Field(default_factory=list)


class ScenarioEntryModel(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    type: Optional[str] = None  # 'synthetic' or 'scripted' (default)
    prompt: Optional[str] = None  # Single prompt for scripted single-turn
    prompts: Optional[List[str]] = None  # Multiple prompts for scripted multi-turn
    goal: Optional[str] = None  # Goal for synthetic scenarios
    fixture: Optional[str] = None  # Fixture reference
    persona: Optional[Dict[str, Any]] = None  # Simulated user persona
    initial_message: Optional[str] = None  # First message for synthetic
    agent_description: Optional[str] = None  # Agent description for synthetic
    max_exchanges: Optional[int] = None  # Max conversation turns
    dimensions: Optional[Dict[str, Any]] = None
    measures: Optional[List[Any]] = None
    failure_categories: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    label: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None
    analysis: Optional[Dict[str, Any]] = None
    expect: Optional[Dict[str, Any]] = (
        None  # Reference data (e.g. doctor_answer, source) — surfaced in dataset-split view
    )


class ScenarioSetModel(BaseModel):
    kind: str
    label: str
    path: str
    scenario_count: int
    updated_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    scenarios: List[ScenarioEntryModel] = Field(default_factory=list)


class EvaluationDatasetSplitModel(BaseModel):
    split: Literal["train", "dev", "test"]
    path: str
    scenario_count: int
    positives: int
    negatives: int
    updated_at: Optional[str] = None


class EvaluationDatasetModel(BaseModel):
    set_name: str
    category: str
    artifact_dir: str
    scenario_set_path: Optional[str] = None
    manifest_path: Optional[str] = None
    splits: List[EvaluationDatasetSplitModel] = Field(default_factory=list)


class EvaluationDatasetsResponse(BaseModel):
    project: Optional[str] = None
    datasets: List[EvaluationDatasetModel] = Field(default_factory=list)


class EvaluationDatasetSplitBrowseResponse(BaseModel):
    project: Optional[str] = None
    set_name: str
    category: str
    split: EvaluationDatasetSplitModel
    scenarios: List[ScenarioEntryModel] = Field(default_factory=list)


class ScenarioSplitPayload(BaseModel):
    set_path: str
    kind: Literal["golden", "generated", "annotated"]
    category: str
    train: float = Field(default=0.15, ge=0.0)
    dev: float = Field(default=0.425, ge=0.0)
    test: float = Field(default=0.425, ge=0.0)
    seed: int = 42
    negatives_ratio: Optional[float] = Field(default=None, ge=0.0)
    output_dir: Optional[str] = None


class ScenarioSplitResponse(BaseModel):
    project: Optional[str] = None
    dataset: EvaluationDatasetModel


class EvaluationDatasetDeletePayload(BaseModel):
    set_name: str
    category: str


class EvaluationDatasetDeleteResponse(BaseModel):
    project: Optional[str] = None
    datasets: List[EvaluationDatasetModel] = Field(default_factory=list)


class ScenarioPromotionPayload(BaseModel):
    source_kind: Literal["generated", "annotated"]
    set_path: str
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None
    index: Optional[int] = None


class ScenarioWriteModel(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    dimensions: Optional[Dict[str, Any]] = None


class ScenarioCreatePayload(BaseModel):
    kind: Literal["golden", "generated", "annotated"]
    set_path: str
    scenario: ScenarioWriteModel


class ScenarioUpdatePayload(ScenarioCreatePayload):
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None
    index: Optional[int] = None


class ScenarioDeletePayload(BaseModel):
    kind: Literal["golden", "generated", "annotated"]
    set_path: str
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None
    index: Optional[int] = None


class ScenarioAnnotatePayload(BaseModel):
    kind: Literal["golden", "generated", "annotated"]
    set_path: str
    agent: Optional[str] = None


class ScenarioAnnotateResponseModel(BaseModel):
    project: Optional[str] = None
    annotated_path: str
    set: ScenarioSetModel


class ScenarioSetDeletePayload(BaseModel):
    kind: Literal["generated", "annotated"]
    set_path: str


class ScenarioSetDeleteResponseModel(BaseModel):
    project: Optional[str] = None
    deleted_path: str
    generated: List[ScenarioSetModel] = Field(default_factory=list)
    annotated: List[ScenarioSetModel] = Field(default_factory=list)


class EvaluationJudgeModel(BaseModel):
    key: str
    agent_name: Optional[str] = None
    agent_label: Optional[str] = None
    model: Optional[str] = None
    source: str
    failure_category: Optional[str] = None
    instructions: Optional[str] = None
    template: Optional[str] = None
    instructions_file: Optional[str] = None
    template_file: Optional[str] = None
    instructions_path: Optional[str] = None
    template_path: Optional[str] = None
    judge_path: str
    updated_at: Optional[str] = None


class EvaluationJudgesResponse(BaseModel):
    project: Optional[str] = None
    judges: List[EvaluationJudgeModel] = Field(default_factory=list)


class EvaluationJudgeCreatePayload(BaseModel):
    name: Optional[str] = None
    key: Optional[str] = None
    model: Optional[str] = None
    agent_label: Optional[str] = None
    agent_name: Optional[str] = None
    source: Optional[str] = None
    failure_category: Optional[str] = None
    instructions: Optional[str] = None
    template: Optional[str] = None
    add_measure: bool = True


class EvaluationJudgeUpdatePayload(BaseModel):
    key: str
    model: Optional[str] = None
    agent_label: Optional[str] = None
    agent_name: Optional[str] = None
    source: Optional[str] = None
    failure_category: Optional[str] = None
    instructions: Optional[str] = None
    template: Optional[str] = None


class EvaluationJudgeDeleteResponse(BaseModel):
    project: Optional[str] = None
    deleted: str
    judges: List[EvaluationJudgeModel] = Field(default_factory=list)


class JudgeOptimizationDatasetPayload(BaseModel):
    set_name: str
    category: str
    artifact_dir: Optional[str] = None
    train_path: str
    dev_path: str
    test_path: str
    runs_path: Optional[str] = None
    source: Optional[str] = None


class JudgeOptimizationRequest(BaseModel):
    judge_key: str
    dataset: JudgeOptimizationDatasetPayload
    max_metric_calls: int = Field(default=100, ge=1)
    seed: Optional[int] = 0


class JudgeOptimizationStatusModel(BaseModel):
    job_id: str
    project: Optional[str] = None
    judge_key: str
    dataset: JudgeOptimizationDatasetPayload
    max_metric_calls: int
    seed: Optional[int] = None
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_dir: Optional[str] = None
    result_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None


class JudgeOptimizationResponse(BaseModel):
    job_id: str
    status: Optional[JudgeOptimizationStatusModel] = None


class AgentOptimizationDatasetPayload(BaseModel):
    set_name: str
    category: str
    artifact_dir: Optional[str] = None
    train_path: str
    dev_path: str
    test_path: str


class AgentOptimizationRequest(BaseModel):
    agent_key: str
    dataset: AgentOptimizationDatasetPayload
    positives_only: bool = False
    max_metric_calls: int = Field(default=100, ge=6)
    seed: Optional[int] = 0


class AgentOptimizationStatusModel(BaseModel):
    job_id: str
    project: Optional[str] = None
    agent_key: str
    dataset: AgentOptimizationDatasetPayload
    positives_only: bool
    max_metric_calls: int
    seed: Optional[int] = None
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_dir: Optional[str] = None
    result_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None
    log_path: Optional[str] = None
    eta_seconds: Optional[float] = None


class AgentOptimizationResponse(BaseModel):
    job_id: str
    status: Optional[AgentOptimizationStatusModel] = None


@dataclass
class ScenarioRunJob:
    job_id: str
    project: Optional[str]
    kind: str
    set_path: str
    include_history: bool
    max_turns: Optional[int]
    output_path: Optional[str]
    select: Optional[str]
    exclude: Optional[str]
    state: str
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runs_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project": self.project,
            "kind": self.kind,
            "set_path": self.set_path,
            "include_history": self.include_history,
            "max_turns": self.max_turns,
            "output_path": self.output_path,
            "select": self.select,
            "exclude": self.exclude,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "runs_path": self.runs_path,
            "workflow_name": self.workflow_name,
            "error": self.error,
        }


class ScenarioRunManager:
    def __init__(self):
        self.jobs: Dict[str, ScenarioRunJob] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def create_job(
        self, project: Optional[str], payload: "ScenarioRunRequest", total: int
    ) -> ScenarioRunJob:
        async with self.lock:
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = ScenarioRunJob(
                job_id=job_id,
                project=project,
                kind=payload.kind,
                set_path=str(payload.set_path),
                include_history=payload.include_history,
                max_turns=payload.max_turns,
                output_path=payload.output_path,
                select=payload.select,
                exclude=payload.exclude,
                state="pending",
                total=total,
                completed=0,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = job
        await self.broadcast(job_id)
        return job

    async def register_task(self, job_id: str, task: asyncio.Task) -> None:
        async with self.lock:
            self.tasks[job_id] = task

        def _on_done(_task: asyncio.Task) -> None:
            asyncio.create_task(self.clear_task(job_id))

        task.add_done_callback(_on_done)

    async def clear_task(self, job_id: str) -> None:
        async with self.lock:
            self.tasks.pop(job_id, None)

    async def update_job(self, job_id: str, **fields: Any) -> Optional[ScenarioRunJob]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            if job.completed > job.total:
                job.total = job.completed
            job.updated_at = _utc_now()
            snapshot = job.to_dict()
        await self.broadcast(job_id, snapshot)
        return job

    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return job.to_dict()

    async def add_connection(self, job_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            await websocket.accept()
            self.connections[job_id].append(websocket)
            snapshot = job.to_dict()
        try:
            await websocket.send_json({"job_id": job_id, "status": snapshot})
        except Exception:
            await self.remove_connection(job_id, websocket)
            return False
        return True

    async def remove_connection(self, job_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            connections = self.connections.get(job_id)
            if connections and websocket in connections:
                connections.remove(websocket)
            if not connections and job_id in self.connections:
                self.connections.pop(job_id, None)

    async def broadcast(
        self, job_id: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job and snapshot is None:
                return
            payload = snapshot or job.to_dict() if job else snapshot
            sockets = list(self.connections.get(job_id, []))
        if not payload:
            return
        message = {"job_id": job_id, "status": payload}
        stale: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self.lock:
                connections = self.connections.get(job_id)
                if connections:
                    for ws in stale:
                        if ws in connections:
                            connections.remove(ws)
                if connections == [] and job_id in self.connections:
                    self.connections.pop(job_id, None)


@dataclass
class SuiteRunJob:
    job_id: str
    project: Optional[str]
    agent: Optional[str]
    name: Optional[str]
    select: Optional[str]
    exclude: Optional[str]
    include_unmeasured: bool
    output_path: Optional[str]
    reporter: Optional[str]
    color: Optional[str]
    state: str
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_path: Optional[str] = None
    report_path: Optional[str] = None
    workflow_name: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    warnings: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project": self.project,
            "agent": self.agent,
            "name": self.name,
            "select": self.select,
            "exclude": self.exclude,
            "include_unmeasured": self.include_unmeasured,
            "output_path": self.output_path,
            "reporter": self.reporter,
            "color": self.color,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_path": self.result_path,
            "report_path": self.report_path,
            "workflow_name": self.workflow_name,
            "summary": self.summary,
            "error": self.error,
            "warnings": self.warnings,
        }


class SuiteRunManager:
    def __init__(self):
        self.jobs: Dict[str, SuiteRunJob] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def create_job(
        self, project: Optional[str], payload: "EvaluationSuiteRunRequest"
    ) -> SuiteRunJob:
        async with self.lock:
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = SuiteRunJob(
                job_id=job_id,
                project=project,
                agent=payload.agent,
                name=payload.name,
                select=payload.select,
                exclude=payload.exclude,
                include_unmeasured=bool(payload.include_unmeasured or False),
                output_path=payload.output_path,
                reporter=payload.reporter,
                color=payload.color,
                state="pending",
                total=0,
                completed=0,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = job
        await self.broadcast(job_id)
        return job

    async def register_task(self, job_id: str, task: asyncio.Task) -> None:
        async with self.lock:
            self.tasks[job_id] = task

        def _on_done(_task: asyncio.Task) -> None:
            asyncio.create_task(self.clear_task(job_id))

        task.add_done_callback(_on_done)

    async def clear_task(self, job_id: str) -> None:
        async with self.lock:
            self.tasks.pop(job_id, None)

    async def update_job(self, job_id: str, **fields: Any) -> Optional[SuiteRunJob]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _utc_now()
            snapshot = job.to_dict()
        await self.broadcast(job_id, snapshot)
        return job

    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return job.to_dict()

    async def add_connection(self, job_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            await websocket.accept()
            self.connections[job_id].append(websocket)
            snapshot = job.to_dict()
        try:
            await websocket.send_json({"job_id": job_id, "status": snapshot})
        except Exception:
            await self.remove_connection(job_id, websocket)
            return False
        return True

    async def remove_connection(self, job_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            connections = self.connections.get(job_id)
            if connections and websocket in connections:
                connections.remove(websocket)
            if not connections and job_id in self.connections:
                self.connections.pop(job_id, None)

    async def broadcast(
        self, job_id: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job and snapshot is None:
                return
            payload = snapshot or job.to_dict() if job else snapshot
            sockets = list(self.connections.get(job_id, []))
        if not payload:
            return
        message = {"job_id": job_id, "status": payload}
        stale: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self.lock:
                connections = self.connections.get(job_id)
                if connections:
                    for ws in stale:
                        if ws in connections:
                            connections.remove(ws)
                if connections == [] and job_id in self.connections:
                    self.connections.pop(job_id, None)


@dataclass
class ScenarioGenerationJob:
    job_id: str
    project: Optional[str]
    name: Optional[str]
    output_path: Optional[str]
    select: Optional[str]
    exclude: Optional[str]
    count: Optional[int]
    seed: Optional[int]
    dimensions: Optional[Dict[str, List[str]]]
    state: str
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scenarios_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project": self.project,
            "name": self.name,
            "output_path": self.output_path,
            "select": self.select,
            "exclude": self.exclude,
            "count": self.count,
            "seed": self.seed,
            "dimensions": self.dimensions,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scenarios_path": self.scenarios_path,
            "workflow_name": self.workflow_name,
            "error": self.error,
        }


class ScenarioGenerationManager:
    def __init__(self):
        self.jobs: Dict[str, ScenarioGenerationJob] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def create_job(
        self, project: Optional[str], payload: "ScenarioGenerationRequest"
    ) -> ScenarioGenerationJob:
        async with self.lock:
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = ScenarioGenerationJob(
                job_id=job_id,
                project=project,
                name=payload.name,
                output_path=payload.output_path,
                select=payload.select,
                exclude=payload.exclude,
                count=payload.count,
                seed=payload.seed,
                dimensions=payload.dimensions,
                state="pending",
                total=int(payload.count or 0),
                completed=0,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = job
        await self.broadcast(job_id)
        return job

    async def register_task(self, job_id: str, task: asyncio.Task) -> None:
        async with self.lock:
            self.tasks[job_id] = task

        def _on_done(_task: asyncio.Task) -> None:
            asyncio.create_task(self.clear_task(job_id))

        task.add_done_callback(_on_done)

    async def clear_task(self, job_id: str) -> None:
        async with self.lock:
            self.tasks.pop(job_id, None)

    async def update_job(
        self, job_id: str, **fields: Any
    ) -> Optional[ScenarioGenerationJob]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _utc_now()
            snapshot = job.to_dict()
        await self.broadcast(job_id, snapshot)
        return job

    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return job.to_dict()

    async def add_connection(self, job_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            await websocket.accept()
            self.connections[job_id].append(websocket)
            snapshot = job.to_dict()
        try:
            await websocket.send_json({"job_id": job_id, "status": snapshot})
        except Exception:
            await self.remove_connection(job_id, websocket)
            return False
        return True

    async def remove_connection(self, job_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            connections = self.connections.get(job_id)
            if connections and websocket in connections:
                connections.remove(websocket)
            if not connections and job_id in self.connections:
                self.connections.pop(job_id, None)

    async def broadcast(
        self, job_id: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job and snapshot is None:
                return
            payload = snapshot or job.to_dict() if job else snapshot
            sockets = list(self.connections.get(job_id, []))
        if not payload:
            return
        message = {"job_id": job_id, "status": payload}
        stale: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self.lock:
                connections = self.connections.get(job_id)
                if connections:
                    for ws in stale:
                        if ws in connections:
                            connections.remove(ws)
                if connections == [] and job_id in self.connections:
                    self.connections.pop(job_id, None)


@dataclass
class JudgeOptimizationJob:
    job_id: str
    project: Optional[str]
    judge_key: str
    dataset: Dict[str, Any]
    max_metric_calls: int
    seed: Optional[int]
    state: str
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_dir: Optional[str] = None
    result_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project": self.project,
            "judge_key": self.judge_key,
            "dataset": self.dataset,
            "max_metric_calls": self.max_metric_calls,
            "seed": self.seed,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_dir": self.run_dir,
            "result_path": self.result_path,
            "workflow_name": self.workflow_name,
            "error": self.error,
        }


class JudgeOptimizationManager:
    def __init__(self):
        self.jobs: Dict[str, JudgeOptimizationJob] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def create_job(
        self, project: Optional[str], payload: JudgeOptimizationRequest
    ) -> JudgeOptimizationJob:
        async with self.lock:
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = JudgeOptimizationJob(
                job_id=job_id,
                project=project,
                judge_key=payload.judge_key,
                dataset=payload.dataset.model_dump(),
                max_metric_calls=int(payload.max_metric_calls),
                seed=payload.seed,
                state="pending",
                total=1,
                completed=0,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = job
        await self.broadcast(job_id)
        return job

    async def register_task(self, job_id: str, task: asyncio.Task) -> None:
        async with self.lock:
            self.tasks[job_id] = task

        def _on_done(_task: asyncio.Task) -> None:
            asyncio.create_task(self.clear_task(job_id))

        task.add_done_callback(_on_done)

    async def clear_task(self, job_id: str) -> None:
        async with self.lock:
            self.tasks.pop(job_id, None)

    async def update_job(
        self, job_id: str, **fields: Any
    ) -> Optional[JudgeOptimizationJob]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _utc_now()
            snapshot = job.to_dict()
        await self.broadcast(job_id, snapshot)
        return job

    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return job.to_dict()

    async def add_connection(self, job_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            await websocket.accept()
            self.connections[job_id].append(websocket)
            snapshot = job.to_dict()
        try:
            await websocket.send_json({"job_id": job_id, "status": snapshot})
        except Exception:
            await self.remove_connection(job_id, websocket)
            return False
        return True

    async def remove_connection(self, job_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            connections = self.connections.get(job_id)
            if connections and websocket in connections:
                connections.remove(websocket)
            if not connections and job_id in self.connections:
                self.connections.pop(job_id, None)

    async def broadcast(
        self, job_id: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job and snapshot is None:
                return
            payload = snapshot or job.to_dict() if job else snapshot
            sockets = list(self.connections.get(job_id, []))
        if not payload:
            return
        message = {"job_id": job_id, "status": payload}
        stale: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self.lock:
                connections = self.connections.get(job_id)
                if connections:
                    for ws in stale:
                        if ws in connections:
                            connections.remove(ws)
                if connections == [] and job_id in self.connections:
                    self.connections.pop(job_id, None)


@dataclass
class AgentOptimizationJob:
    job_id: str
    project: Optional[str]
    agent_key: str
    dataset: Dict[str, Any]
    positives_only: bool
    max_metric_calls: int
    seed: Optional[int]
    state: str
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_dir: Optional[str] = None
    result_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None
    log_path: Optional[str] = None
    eta_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project": self.project,
            "agent_key": self.agent_key,
            "dataset": self.dataset,
            "positives_only": self.positives_only,
            "max_metric_calls": self.max_metric_calls,
            "seed": self.seed,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_dir": self.run_dir,
            "result_path": self.result_path,
            "workflow_name": self.workflow_name,
            "error": self.error,
            "log_path": self.log_path,
            "eta_seconds": self.eta_seconds,
        }


class AgentOptimizationManager:
    def __init__(self):
        self.jobs: Dict[str, AgentOptimizationJob] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def create_job(
        self, project: Optional[str], payload: AgentOptimizationRequest
    ) -> AgentOptimizationJob:
        async with self.lock:
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = AgentOptimizationJob(
                job_id=job_id,
                project=project,
                agent_key=payload.agent_key,
                dataset=payload.dataset.model_dump(),
                positives_only=bool(payload.positives_only),
                max_metric_calls=int(payload.max_metric_calls),
                seed=payload.seed,
                state="pending",
                total=int(payload.max_metric_calls),
                completed=0,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = job
        await self.broadcast(job_id)
        return job

    async def register_task(self, job_id: str, task: asyncio.Task) -> None:
        async with self.lock:
            self.tasks[job_id] = task

        def _on_done(_task: asyncio.Task) -> None:
            asyncio.create_task(self.clear_task(job_id))

        task.add_done_callback(_on_done)

    async def clear_task(self, job_id: str) -> None:
        async with self.lock:
            self.tasks.pop(job_id, None)

    async def update_job(
        self, job_id: str, **fields: Any
    ) -> Optional[AgentOptimizationJob]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _utc_now()
            snapshot = job.to_dict()
        await self.broadcast(job_id, snapshot)
        return job

    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return job.to_dict()

    async def add_connection(self, job_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            await websocket.accept()
            self.connections[job_id].append(websocket)
            snapshot = job.to_dict()
        try:
            await websocket.send_json({"job_id": job_id, "status": snapshot})
        except Exception:
            await self.remove_connection(job_id, websocket)
            return False
        return True

    async def remove_connection(self, job_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            connections = self.connections.get(job_id)
            if connections and websocket in connections:
                connections.remove(websocket)
            if not connections and job_id in self.connections:
                self.connections.pop(job_id, None)

    async def broadcast(
        self, job_id: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job and snapshot is None:
                return
            payload = snapshot or job.to_dict() if job else snapshot
            sockets = list(self.connections.get(job_id, []))
        if not payload:
            return
        message = {"job_id": job_id, "status": payload}
        stale: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self.lock:
                connections = self.connections.get(job_id)
                if connections:
                    for ws in stale:
                        if ws in connections:
                            connections.remove(ws)
                if connections == [] and job_id in self.connections:
                    self.connections.pop(job_id, None)


class ScenarioRunRequest(BaseModel):
    kind: Literal["golden", "generated", "annotated"]
    set_path: str
    agent: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    max_turns: Optional[int] = Field(default=None, ge=1)
    include_history: bool = False
    output_path: Optional[str] = None


class ScenarioRunStatusModel(BaseModel):
    job_id: str
    project: Optional[str] = None
    kind: Literal["golden", "generated", "annotated"]
    set_path: str
    include_history: bool
    max_turns: Optional[int] = None
    output_path: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runs_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None


class ScenarioGenerationRequest(BaseModel):
    name: Optional[str] = None
    output_path: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    count: Optional[int] = Field(default=None, ge=1)
    seed: Optional[int] = None
    agent: Optional[str] = None
    dimensions: Optional[Dict[str, List[str]]] = None


class ScenarioGenerationStatusModel(BaseModel):
    job_id: str
    project: Optional[str] = None
    name: Optional[str] = None
    output_path: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    count: Optional[int] = None
    seed: Optional[int] = None
    dimensions: Optional[Dict[str, List[str]]] = None
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scenarios_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None


SuiteRunStateLiteral = Literal["pending", "running", "completed", "failed", "cancelled"]


class EvaluationSuiteRunSummaryModel(BaseModel):
    total_checks: int
    passed_checks: int
    failed_checks: int
    skipped_checks: int
    measures_total: int
    measures_failed: int
    measures_skipped: int
    metrics_total: int
    metrics_failed: int
    metrics_skipped: int
    scenarios_total: int
    scenarios_included: int
    scenarios_skipped: int


class EvaluationSuiteRunRequest(BaseModel):
    name: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    include_unmeasured: Optional[bool] = False
    output_path: Optional[str] = None
    agent: Optional[str] = None
    reporter: Optional[Literal["auto", "pretty", "tap"]] = None
    color: Optional[Literal["auto", "always", "never"]] = None


class EvaluationSuiteRunStatusModel(BaseModel):
    job_id: str
    project: Optional[str] = None
    agent: Optional[str] = None
    name: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    include_unmeasured: bool
    output_path: Optional[str] = None
    reporter: Optional[str] = None
    color: Optional[str] = None
    state: SuiteRunStateLiteral
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_path: Optional[str] = None
    report_path: Optional[str] = None
    workflow_name: Optional[str] = None
    summary: Optional[EvaluationSuiteRunSummaryModel] = None
    error: Optional[str] = None
    warnings: Optional[List[str]] = None


class EvaluationSuiteRunResponse(BaseModel):
    job_id: str
    status: Optional[EvaluationSuiteRunStatusModel] = None


# Evaluation Results Models
class EvaluationMeasureResultModel(BaseModel):
    name: str
    passed: bool
    reason: Optional[str] = None


class EvaluationScenarioResultModel(BaseModel):
    scenario_id: Optional[str] = None
    scenario_name: str
    type: Literal["scripted", "synthetic"] = "scripted"
    passed: bool
    measures: List[EvaluationMeasureResultModel] = []
    error: Optional[Dict[str, str]] = None
    group_id: Optional[str] = None


class EvaluationMeasureAggregateModel(BaseModel):
    passed: int = 0
    failed: int = 0
    total: int = 0


class EvaluationRunSummaryModel(BaseModel):
    path: str
    run_at: str
    total_scenarios: int
    passed: int
    failed: int
    workflow_name: Optional[str] = None
    agent: Optional[str] = None
    per_measure: Dict[str, EvaluationMeasureAggregateModel] = {}


class EvaluationResultsListResponse(BaseModel):
    project: Optional[str] = None
    results: List[EvaluationRunSummaryModel] = []


class EvaluationResultsDetailResponse(BaseModel):
    project: Optional[str] = None
    path: str
    run_at: str
    summary: Dict[str, int]
    scenarios: List[EvaluationScenarioResultModel] = []
    workflow_name: Optional[str] = None


def build_playground_spec(
    base_spec: AgentSpec,
    tool_registry: Dict[str, Any],
    guardrails_registry: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    session_label: Optional[str] = None,
) -> AgentSpec:
    instructions_override = (
        config.get("instructions") if "instructions" in config else None
    )
    base_instructions_supplier = getattr(base_spec, "get_agent_instructions", None)

    def _instructions_supplier() -> str:
        if instructions_override is not None:
            return instructions_override
        if callable(base_instructions_supplier):
            try:
                return base_instructions_supplier()
            except Exception:
                return ""
        return ""

    if "tools" in config:
        selected_tool_names = config.get("tools") or []
        selected_tools = [
            tool_registry[name] for name in selected_tool_names if name in tool_registry
        ]
    else:
        selected_tools = getattr(base_spec, "available_tools", None)

    base_model_settings = dict(base_spec.model_settings or {})
    model_settings = dict(base_model_settings)
    incoming_model_settings = config.get("model_settings")
    if isinstance(incoming_model_settings, dict):
        overrides = {
            k: v for k, v in incoming_model_settings.items() if v not in (None, "")
        }
        if overrides:
            model_settings = {**base_model_settings, **overrides}

    use_safe_agent = (
        base_spec.use_safe_agent
        if config.get("use_safe_agent") is None
        else bool(config.get("use_safe_agent"))
    )

    base_output_type = getattr(base_spec, "output_type", None)
    base_output_type_strict = getattr(base_spec, "output_type_strict", True)
    output_type_override = config.get("output_type")
    normalized_output_key = (
        _resolve_output_type_override(output_type_override)
        if isinstance(output_type_override, str)
        else None
    )
    schema_override = config.get("output_schema")
    custom_output_type = (
        _build_type_from_schema(schema_override, base_spec.name or "Playground")
        if isinstance(schema_override, dict)
        else None
    )
    resolved_output_type = base_output_type
    if normalized_output_key is not None:
        resolved_output_type = _map_output_type_key_to_python(
            normalized_output_key, fallback=base_output_type
        )
        if normalized_output_key in ("string", "str"):
            resolved_output_type = str
    if custom_output_type is not None:
        resolved_output_type = custom_output_type
    output_type_strict = base_output_type_strict
    if "output_type_strict" in config:
        try:
            output_type_strict = bool(config["output_type_strict"])
        except Exception:
            output_type_strict = base_output_type_strict

    safe_options_dict = {}
    if base_spec.safe_agent_options:
        safe_options_dict = asdict(base_spec.safe_agent_options)
    else:
        safe_options_dict = asdict(SafeAgentOptions())
    incoming_safe_options = config.get("safe_agent_options") or {}
    if isinstance(incoming_safe_options, dict):
        for key in ("skip_approvals", "halt_on_rejection"):
            if key in incoming_safe_options:
                safe_options_dict[key] = bool(incoming_safe_options[key])
        if "safe_tool_names" in incoming_safe_options and isinstance(
            incoming_safe_options["safe_tool_names"], list
        ):
            safe_options_dict["safe_tool_names"] = [
                str(item)
                for item in incoming_safe_options["safe_tool_names"]
                if isinstance(item, str)
            ]
        if "safe_tool_patterns" in incoming_safe_options and isinstance(
            incoming_safe_options["safe_tool_patterns"], list
        ):
            safe_options_dict["safe_tool_patterns"] = [
                str(item)
                for item in incoming_safe_options["safe_tool_patterns"]
                if isinstance(item, str)
            ]
    safe_agent_options = SafeAgentOptions(**safe_options_dict)

    guardrails_config = config.get("guardrails") or {}
    guardrail_registry_input = (guardrails_registry or {}).get("input", {})
    guardrail_registry_output = (guardrails_registry or {}).get("output", {})

    def _resolve_guardrails(
        names: Optional[List[str]],
        registry: Dict[str, Any],
        fallback: Optional[List[Any]],
    ) -> List[Any]:
        if not isinstance(names, list):
            return list(fallback or [])
        resolved = [registry.get(name) for name in names if name in registry]
        return [item for item in resolved if item]

    selected_input_guardrails = _resolve_guardrails(
        guardrails_config.get("input"),
        guardrail_registry_input,
        getattr(base_spec, "input_guardrails", None),
    )
    selected_output_guardrails = _resolve_guardrails(
        guardrails_config.get("output"),
        guardrail_registry_output,
        getattr(base_spec, "output_guardrails", None),
    )

    tracing_cfg = dict(base_spec.tracing_config or {})
    tracing_cfg["workflow_name"] = session_label or "playground"

    spec = replace(
        base_spec,
        name=f"{base_spec.name}_playground" if base_spec.name else "playground_agent",
        welcome_text=base_spec.welcome_text,
        get_agent_instructions=_instructions_supplier,
        agent_slug="playground",
        available_tools=selected_tools,
        model_name=config.get("model") or base_spec.model_name,
        model_settings=model_settings or None,
        use_safe_agent=use_safe_agent,
        safe_agent_options=safe_agent_options,
        output_type=resolved_output_type,
        output_type_strict=output_type_strict,
        input_guardrails=selected_input_guardrails,
        output_guardrails=selected_output_guardrails,
        tracing_config=tracing_cfg,
    )

    spec.build_agent = None
    return spec


def create_api_app(
    storage: Optional[SQLiteStorage] = None,
    default_project: Optional[str] = None,
    *,
    dev_server_url: Optional[str] = None,
    playground_resources: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
):
    app = FastAPI()

    expected_auth_token = (
        auth_token.strip()
        if isinstance(auth_token, str) and auth_token.strip()
        else None
    )

    def _extract_bearer_token(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        scheme, _, credentials = value.partition(" ")
        if scheme.lower() == "bearer" and credentials.strip():
            return credentials.strip()
        return None

    # One ConnectionAuthenticator guards every Studio WebSocket surface,
    # mirroring the unified model on /ws, /ws/realtime and /ws/terminal
    # (omniagents/rpc/auth.py): Authorization headers for clients that can
    # set them, one-time /api/auth/ws-ticket connect tickets for browsers,
    # and the deprecated ?token= transport only behind
    # OMNIAGENTS_LEGACY_QUERY_AUTH=1.
    auth_provider: AuthProvider
    if expected_auth_token:
        auth_provider = TokenAuthProvider({"token": expected_auth_token})
    else:
        auth_provider = NoneAuthProvider()
    connection_authenticator = ConnectionAuthenticator(
        auth_provider,
        allow_query_token=legacy_query_auth_enabled(None),
    )
    app.state.connection_authenticator = connection_authenticator

    if expected_auth_token:

        @app.middleware("http")
        async def _enforce_auth(request: Request, call_next):
            if request.method == "OPTIONS":
                return await call_next(request)
            path = request.url.path or ""
            if path.startswith("/api"):
                supplied = _extract_bearer_token(request.headers.get("authorization"))
                if supplied is None and connection_authenticator.allow_query_token:
                    supplied = request.query_params.get("token")
                if supplied != expected_auth_token:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing token"},
                    )
            return await call_next(request)

    async def _require_ws_auth(websocket: WebSocket) -> None:
        """Authenticate a Studio WebSocket upgrade.

        Resolves the connection principal through the shared
        :class:`ConnectionAuthenticator` (header, one-time ticket, or —
        legacy-mode only — query token) and stores it on
        ``websocket.state.principal``. Failures close with 4401, a
        terminal code: clients must obtain new credentials, not retry.
        """
        try:
            identity = await connection_authenticator.authenticate_ws(websocket)
        except AuthenticationFailed as exc:
            await websocket.close(code=WS_CLOSE_UNAUTHENTICATED, reason=str(exc)[:120])
            raise WebSocketDisconnect(code=WS_CLOSE_UNAUTHENTICATED) from exc
        websocket.state.principal = identity

    @app.post("/api/auth/ws-ticket")
    async def issue_ws_ticket(request: Request) -> Dict[str, Any]:
        """Mint a one-time WebSocket connect ticket.

        Browser clients cannot set WebSocket headers, so they authenticate
        this HTTP call with the ``Authorization`` header and dial the
        Studio WebSocket routes with ``?ticket=<one-time>``. Same contract
        as ``POST /auth/ws-ticket`` on the agent server, scoped under
        ``/api`` so the Studio web client reaches it through its normal
        API base URL. The response never echoes the caller's token.
        """
        try:
            identity = await connection_authenticator.authenticate_http(request)
        except AuthenticationFailed as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        ticket, expires_in = connection_authenticator.ticket_broker.mint(identity)
        return {"ticket": ticket, "expires_in": expires_in}

    storage_manager = StorageManager()

    initial_project = default_project
    if storage:
        db_path = Path(storage.db_path)
        project_name = initial_project or db_path.stem
        storage_manager.storages[project_name] = storage
        initial_project = project_name
    app.state.default_project = initial_project
    resources = dict(playground_resources or {})
    if default_project and not resources.get("project_name"):
        resources["project_name"] = default_project
    if "tool_registry" not in resources:
        resources["tool_registry"] = {}
    if "guardrails_registry" not in resources:
        resources["guardrails_registry"] = {"input": {}, "output": {}}
    if "default_guardrails" not in resources:
        resources["default_guardrails"] = {"input": [], "output": []}
    if "default_safe_agent_options" not in resources:
        resources["default_safe_agent_options"] = {}
    if "default_model_settings" not in resources:
        resources["default_model_settings"] = {}
    if "agent_specs" not in resources:
        resources["agent_specs"] = {}
    if "entry_agent_key" not in resources:
        resources["entry_agent_key"] = None
    app.state.playground_resources = resources
    app.state.playground_session_store = PlaygroundSessionStore()
    app.state.scenario_run_manager = ScenarioRunManager()
    app.state.suite_run_manager = SuiteRunManager()
    app.state.scenario_generation_manager = ScenarioGenerationManager()
    app.state.judge_optimization_manager = JudgeOptimizationManager()
    app.state.agent_optimization_manager = AgentOptimizationManager()

    # Add CORS middleware for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "https://localhost:5173",
            "https://localhost:3000",
            "https://127.0.0.1:5173",
        ],
        allow_origin_regex=r"https://.*\\.app\.github\.dev",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/projects")
    async def get_projects():
        """Get list of all available project databases"""
        try:
            projects = SQLiteStorage.get_projects()
            return {"projects": projects}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/projects/{project}")
    async def delete_project(project: str):
        """Delete a project database"""
        try:
            # Validate project exists
            projects = SQLiteStorage.get_projects()
            if project not in projects:
                raise HTTPException(
                    status_code=404, detail=f"Project '{project}' not found"
                )

            # Close any existing storage connections
            if project in storage_manager.storages:
                storage_manager.storages[project].conn.close()
                del storage_manager.storages[project]

            # Delete the database file
            db_path = storage_manager.storage_dir / f"{project}.db"
            if db_path.exists():
                db_path.unlink()

            # Also delete any journal files
            journal_path = storage_manager.storage_dir / f"{project}.db-journal"
            if journal_path.exists():
                journal_path.unlink()

            return {
                "success": True,
                "message": f"Project '{project}' deleted successfully",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/studio/project")
    async def get_studio_project():
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        manifest_hint = resources.get("manifest_path")
        project_name = (
            resources.get("project_name") or app.state.default_project or None
        )
        manifest_path = None
        available = False
        if isinstance(manifest_hint, str):
            candidate = Path(manifest_hint).expanduser()
            if candidate.is_file():
                manifest_path = str(candidate)
                available = True
        return {
            "available": available,
            "project": project_name,
            "manifest_path": manifest_path,
        }

    def _get_manifest_path() -> Path:
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        manifest_hint = resources.get("manifest_path")
        if manifest_hint:
            manifest_path = Path(manifest_hint).expanduser().resolve()
            if manifest_path.is_file():
                return manifest_path
        raise HTTPException(status_code=404, detail="Project manifest not available")

    def _reload_playground_resources(
        ctx, manifest_path: Path
    ) -> Tuple[Dict[str, Any], Dict[str, AgentSpec]]:
        agent_specs: Dict[str, AgentSpec] = {}
        base_spec = build_spec_from_project(ctx)
        for key in sorted(ctx.agents_map.keys()):
            if key == ctx.entrypoint:
                agent_specs[key] = base_spec
            else:
                try:
                    agent_specs[key] = build_spec_from_project(ctx, key)
                except Exception:
                    continue
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        updated_resources = dict(resources or {})
        project_name = (
            ctx.product.slug
            or resources.get("project_name")
            or app.state.default_project
        )
        updated_resources["project_name"] = project_name
        updated_resources["tool_registry"] = ctx.tool_registry
        updated_resources["guardrails_registry"] = ctx.guardrails_registry
        updated_resources["agent_specs"] = agent_specs
        updated_resources["entry_agent_key"] = ctx.entrypoint
        updated_resources["manifest_path"] = str(manifest_path)
        base_reference = agent_specs.get(ctx.entrypoint)
        if not base_reference and agent_specs:
            base_reference = next(iter(agent_specs.values()))
        if base_reference:
            updated_resources["base_spec"] = base_reference
            updated_resources["default_model_settings"] = (
                base_reference.model_settings or {}
            )
            updated_resources["default_safe_agent"] = bool(
                getattr(base_reference, "use_safe_agent", False)
            )
            if base_reference.safe_agent_options:
                updated_resources["default_safe_agent_options"] = asdict(
                    base_reference.safe_agent_options
                )
            else:
                updated_resources["default_safe_agent_options"] = {}
            updated_resources["default_guardrails"] = {
                "input": _guardrail_keys(
                    getattr(base_reference, "input_guardrails", None),
                    ctx.guardrails_registry.get("input", {}),
                ),
                "output": _guardrail_keys(
                    getattr(base_reference, "output_guardrails", None),
                    ctx.guardrails_registry.get("output", {}),
                ),
            }
        else:
            updated_resources["base_spec"] = None
            updated_resources["default_model_settings"] = {}
            updated_resources["default_safe_agent"] = False
            updated_resources["default_safe_agent_options"] = {}
            updated_resources["default_guardrails"] = {"input": [], "output": []}
        app.state.playground_resources = updated_resources
        return updated_resources, agent_specs

    def _serialize_dimensions_payload(
        dimensions: List[EvaluationDimensionModel],
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for dim in dimensions:
            serialized_values: List[Dict[str, Any]] = []
            for val in dim.values:
                entry: Dict[str, Any] = {"name": val.name}
                if val.label:
                    entry["label"] = val.label
                if val.description:
                    entry["description"] = val.description
                if val.examples:
                    entry["examples"] = [str(x) for x in val.examples]
                serialized_values.append(entry)
            serialized_dim: Dict[str, Any] = {
                "name": dim.name,
                "values": serialized_values,
            }
            if dim.label:
                serialized_dim["label"] = dim.label
            if dim.description:
                serialized_dim["description"] = dim.description
            serialized.append(serialized_dim)
        return serialized

    def _resolve_eval_file(ctx, manifest_path: Path) -> Tuple[Path, bool]:
        eval_paths = ctx.paths.get("evaluations", []) or []
        for entry in eval_paths:
            candidate = entry / "evaluation.yml" if entry.is_dir() else entry
            if candidate.is_file():
                return candidate, True
        default_dir = ctx.root / "evaluations"
        if default_dir.is_dir():
            candidate = default_dir / "evaluation.yml"
            if candidate.is_file():
                return candidate, True
        if eval_paths:
            first = eval_paths[0]
            candidate = first / "evaluation.yml" if first.is_dir() else first
        else:
            candidate = (ctx.root / "evaluations" / "evaluation.yml").resolve()
        return candidate, candidate.is_file()

    def _normalize_dimensions(raw_dims: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for dim in raw_dims or []:
            if not isinstance(dim, dict):
                continue
            values = dim.get("values") if isinstance(dim.get("values"), list) else []
            normalized_values: List[Dict[str, Any]] = []
            for val in values:
                if not isinstance(val, dict):
                    continue
                examples = (
                    val.get("examples") if isinstance(val.get("examples"), list) else []
                )
                normalized_values.append(
                    {
                        "name": str(val.get("name", "")),
                        "label": val.get("label"),
                        "description": val.get("description"),
                        "examples": [str(x) for x in examples],
                    }
                )
            normalized.append(
                {
                    "name": str(dim.get("name", "")),
                    "label": dim.get("label"),
                    "description": dim.get("description"),
                    "values": normalized_values,
                }
            )
        return normalized

    def _load_dimensions(manifest_path: Path) -> List[Dict[str, Any]]:
        ctx = load_project(str(manifest_path))
        eval_file, exists = _resolve_eval_file(ctx, manifest_path)
        if not exists:
            return []
        try:
            data = yaml.safe_load(eval_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        if not isinstance(data, dict):
            data = {}
        if isinstance(data.get("evaluation"), dict):
            evaluation_cfg = data.get("evaluation") or {}
        else:
            evaluation_cfg = data
        synthetic_cfg = (
            evaluation_cfg.get("synthetic_data_generation")
            if isinstance(evaluation_cfg.get("synthetic_data_generation"), dict)
            else {}
        )
        raw_dims = (
            synthetic_cfg.get("dimensions") if isinstance(synthetic_cfg, dict) else []
        )
        return _normalize_dimensions(raw_dims if isinstance(raw_dims, list) else [])

    def _write_yaml_file(path: Path, content: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(content, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _persist_dimensions(
        manifest_path: Path, dimensions: List[EvaluationDimensionModel]
    ) -> List[Dict[str, Any]]:
        ctx = load_project(str(manifest_path))
        serialized_dims = _serialize_dimensions_payload(dimensions)
        target_file, exists = _resolve_eval_file(ctx, manifest_path)
        if exists:
            try:
                data = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        else:
            data = {}

        if not isinstance(data, dict):
            data = {}

        has_eval_section = isinstance(data.get("evaluation"), dict)
        evaluation_cfg = (
            dict(data.get("evaluation") or {}) if has_eval_section else dict(data)
        )
        synthetic_cfg = (
            evaluation_cfg.get("synthetic_data_generation")
            if isinstance(evaluation_cfg.get("synthetic_data_generation"), dict)
            else {}
        )
        synthetic_cfg = dict(synthetic_cfg)
        synthetic_cfg["dimensions"] = serialized_dims
        evaluation_cfg["synthetic_data_generation"] = synthetic_cfg

        if has_eval_section or data:
            updated = dict(data)
            updated["evaluation"] = evaluation_cfg
        else:
            updated = {"evaluation": evaluation_cfg}

        _write_yaml_file(target_file, updated)
        return serialized_dims

    def _load_failure_categories(
        ctx,
        manifest_path: Path,
    ) -> Tuple[List[Dict[str, Any]], Path, Dict[str, Any], Dict[str, Any], bool]:
        target_file, exists = _resolve_eval_file(ctx, manifest_path)
        if exists:
            try:
                data = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        else:
            data = {}
        if not isinstance(data, dict):
            data = {}
        has_eval_section = isinstance(data.get("evaluation"), dict)
        evaluation_cfg = (
            dict(data.get("evaluation") or {}) if has_eval_section else dict(data)
        )
        raw_categories = (
            evaluation_cfg.get("failure_categories")
            if isinstance(evaluation_cfg.get("failure_categories"), list)
            else []
        )
        normalized: List[Dict[str, Any]] = []
        for entry in raw_categories or []:
            if isinstance(entry, dict):
                raw_name = entry.get("name") or entry.get("key") or entry.get("id")
                description = (
                    entry.get("description")
                    if isinstance(entry.get("description"), str)
                    else None
                )
                measures = (
                    entry.get("measures")
                    if isinstance(entry.get("measures"), list)
                    else []
                )
            elif isinstance(entry, str):
                raw_name = entry
                description = None
                measures = []
            else:
                continue
            name = str(raw_name or "").strip()
            if not name:
                continue
            slug = _slugify(name)
            normalized.append(
                {
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "measures": [
                        str(m).strip()
                        for m in measures
                        if isinstance(m, str) and str(m).strip()
                    ],
                }
            )
        return normalized, target_file, data, evaluation_cfg, has_eval_section

    def _persist_failure_categories(
        target_file: Path,
        data: Dict[str, Any],
        evaluation_cfg: Dict[str, Any],
        has_eval_section: bool,
        categories: List[Dict[str, Any]],
    ) -> None:
        serialized: List[Dict[str, Any]] = []
        for category in categories:
            entry: Dict[str, Any] = {"name": category["name"]}
            if category.get("description"):
                entry["description"] = category["description"]
            measures = [
                m
                for m in category.get("measures", [])
                if isinstance(m, str) and m.strip()
            ]
            if measures:
                entry["measures"] = measures
            serialized.append(entry)
        updated_eval = dict(evaluation_cfg)
        updated_eval["failure_categories"] = serialized
        if has_eval_section or data:
            updated = dict(data)
            updated["evaluation"] = updated_eval
        else:
            updated = {"evaluation": updated_eval}
        _write_yaml_with_backup(target_file, updated)

    def _collect_scenario_category_usage(ctx) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for file in _scenarios_files(ctx):
            entries = _read_scenarios_file(file)
            for entry in entries:
                raw = (
                    entry.get("failure_categories") if isinstance(entry, dict) else None
                )
                if not isinstance(raw, list):
                    continue
                seen: Set[str] = set()
                for value in raw:
                    if not isinstance(value, str):
                        continue
                    slug = _slugify(value)
                    if not slug or slug in seen:
                        continue
                    counts[slug] = counts.get(slug, 0) + 1
                    seen.add(slug)
        return counts

    def _remove_category_from_scenarios(ctx, slug: str) -> None:
        normalized = _slugify(slug)
        if not normalized:
            return
        for file in _scenarios_files(ctx):
            entries = _read_scenarios_file(file)
            if not entries:
                continue
            changed = False
            updated_entries: List[Dict[str, Any]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    updated_entries.append(entry)
                    continue
                scenario = dict(entry)
                raw = scenario.get("failure_categories")
                if isinstance(raw, list):
                    filtered = [
                        value
                        for value in raw
                        if isinstance(value, str) and _slugify(value) != normalized
                    ]
                    if filtered and filtered != raw:
                        scenario["failure_categories"] = filtered
                        changed = True
                    elif not filtered and raw:
                        scenario.pop("failure_categories", None)
                        changed = True
                updated_entries.append(scenario)
            if changed:
                _write_scenarios_file(file, updated_entries)

    def _measures_files(ctx) -> List[Path]:
        files: List[Path] = []
        for directory in _evaluation_dirs(ctx):
            candidate = directory / "measures.py"
            if candidate.is_file():
                files.append(candidate)
        eval_paths = ctx.paths.get("evaluations") or []
        for entry in eval_paths:
            if entry.is_file() and entry.name == "measures.py":
                files.append(entry)
        return _unique_paths(files)

    def _load_measures_metadata(
        ctx,
        manifest_path: Path,
    ) -> Tuple[List[Dict[str, Any]], Path, Dict[str, Any], Dict[str, Any], bool]:
        target_file, exists = _resolve_eval_file(ctx, manifest_path)
        if exists:
            try:
                data = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        else:
            data = {}
        if not isinstance(data, dict):
            data = {}
        has_eval_section = isinstance(data.get("evaluation"), dict)
        evaluation_cfg = (
            dict(data.get("evaluation") or {}) if has_eval_section else dict(data)
        )
        raw_measures = (
            evaluation_cfg.get("measures")
            if isinstance(evaluation_cfg.get("measures"), list)
            else []
        )
        normalized: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for entry in raw_measures or []:
            if isinstance(entry, dict):
                raw_name = entry.get("name") or entry.get("key") or entry.get("id")
                label = (
                    entry.get("label") if isinstance(entry.get("label"), str) else None
                )
                description = (
                    entry.get("description")
                    if isinstance(entry.get("description"), str)
                    else None
                )
                module = (
                    entry.get("module")
                    if isinstance(entry.get("module"), str)
                    else None
                )
                path_value = (
                    entry.get("path")
                    if isinstance(entry.get("path"), str)
                    else (
                        entry.get("sourcePath")
                        if isinstance(entry.get("sourcePath"), str)
                        else None
                    )
                )
            elif isinstance(entry, str):
                raw_name = entry
                label = None
                description = None
                module = None
                path_value = None
            else:
                continue
            name = str(raw_name or "").strip()
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            normalized.append(
                {
                    "name": name,
                    "label": label,
                    "description": description,
                    "module": module,
                    "sourcePath": path_value,
                }
            )
        return normalized, target_file, data, evaluation_cfg, has_eval_section

    def _persist_measures(
        target_file: Path,
        data: Dict[str, Any],
        evaluation_cfg: Dict[str, Any],
        has_eval_section: bool,
        measures: List[Dict[str, Any]],
    ) -> None:
        serialized: List[Dict[str, Any]] = []
        for measure in measures:
            entry: Dict[str, Any] = {"name": measure["name"]}
            if measure.get("label"):
                entry["label"] = measure.get("label")
            if measure.get("description"):
                entry["description"] = measure.get("description")
            if measure.get("module"):
                entry["module"] = measure.get("module")
            if measure.get("sourcePath"):
                entry["path"] = measure.get("sourcePath")
            serialized.append(entry)
        updated_eval = dict(evaluation_cfg)
        updated_eval["measures"] = serialized
        if has_eval_section or data:
            updated = dict(data)
            updated["evaluation"] = updated_eval
        else:
            updated = {"evaluation": updated_eval}
        _write_yaml_with_backup(target_file, updated)

    def _collect_measure_usage(ctx, manifest_path: Path) -> Dict[str, int]:
        usage: Dict[str, int] = {}
        try:
            categories, _target, _data, _cfg, _flag = _load_failure_categories(
                ctx, manifest_path
            )
        except Exception:
            categories = []
        for category in categories:
            measures = (
                category.get("measures")
                if isinstance(category.get("measures"), list)
                else []
            )
            for name in measures:
                key = str(name)
                if key:
                    usage[key] = usage.get(key, 0) + 1
        for file in _scenarios_files(ctx):
            entries = _read_scenarios_file(file)
            for item in entries:
                ms = (
                    item.get("measures")
                    if isinstance(item.get("measures"), list)
                    else []
                )
                for name in ms:
                    key = str(name)
                    if key:
                        usage[key] = usage.get(key, 0) + 1
        eval_file, exists = _resolve_eval_file(ctx, manifest_path)
        if exists:
            try:
                data = yaml.safe_load(eval_file.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
            if isinstance(data, dict):
                evaluation_cfg = (
                    data.get("evaluation")
                    if isinstance(data.get("evaluation"), dict)
                    else data
                )
                if isinstance(evaluation_cfg, dict):
                    metrics = (
                        evaluation_cfg.get("metrics")
                        if isinstance(evaluation_cfg.get("metrics"), list)
                        else []
                    )
                    for metric in metrics:
                        if not isinstance(metric, dict):
                            continue
                        fm = metric.get("from_measure")
                        if isinstance(fm, str) and fm.strip():
                            key = fm.strip()
                            usage[key] = usage.get(key, 0) + 1
                        single = metric.get("measure")
                        if isinstance(single, str) and single.strip():
                            key = single.strip()
                            usage[key] = usage.get(key, 0) + 1
                        multi = metric.get("measures")
                        if isinstance(multi, list):
                            for name in multi:
                                key = str(name)
                                if key:
                                    usage[key] = usage.get(key, 0) + 1
        return usage

    def _derive_measure_label(name: str, label: Optional[str]) -> Optional[str]:
        if label:
            return label
        parts = [segment for segment in name.replace("-", "_").split("_") if segment]
        if not parts:
            return None
        return " ".join(part.capitalize() for part in parts)

    def _ensure_measure_stub(ctx, measure_name: str) -> None:
        files = _measures_files(ctx)
        if files:
            target = files[0]
        else:
            eval_dir = ctx.root / "evaluations"
            eval_dir.mkdir(parents=True, exist_ok=True)
            target = eval_dir / "measures.py"
        if not target.exists():
            target.write_text(
                "from omniagents.core.evaluation import evaluation_measure, pass_reason\n\n",
                encoding="utf-8",
            )
        existing = target.read_text(encoding="utf-8")
        if f"def {measure_name}(" in existing:
            return
        header_import = (
            "from omniagents.core.evaluation import evaluation_measure, pass_reason"
        )
        if header_import not in existing:
            if "evaluation_measure" in existing and "pass_reason" not in existing:
                existing = existing.replace(
                    "evaluation_measure", "evaluation_measure, pass_reason", 1
                )
            else:
                existing = header_import + "\n\n" + existing.lstrip()
        block = (
            f"\n\n@evaluation_measure\n"
            f"def {measure_name}(ctx):\n"
            f"    return pass_reason('not_implemented')\n"
        )
        updated = existing.rstrip() + block
        updated = updated.rstrip() + "\n"
        target.write_text(updated, encoding="utf-8")

    def _assemble_measures(ctx, manifest_path: Path) -> List[EvaluationMeasureModel]:
        measures_meta, _target_file, _data, _cfg, _flag = _load_measures_metadata(
            ctx, manifest_path
        )
        usage_map = _collect_measure_usage(ctx, manifest_path)
        saved_registry = dict(_REGISTERED_MEASURES)
        runtime_info: Dict[str, Dict[str, Any]] = {}
        try:
            _REGISTERED_MEASURES.clear()
            eval_file, exists = _resolve_eval_file(ctx, manifest_path)
            if exists and eval_file.is_file():
                _load_local_measures(eval_file)
            for key, fn in _REGISTERED_MEASURES.items():
                module = getattr(fn, "__module__", None)
                try:
                    source_path = inspect.getsourcefile(fn)
                except Exception:
                    source_path = None
                docstring = inspect.getdoc(fn)
                runtime_info[key] = {
                    "module": module,
                    "path": source_path,
                    "doc": docstring,
                }
        finally:
            _REGISTERED_MEASURES.clear()
            _REGISTERED_MEASURES.update(saved_registry)

        meta_map = {item["name"]: item for item in measures_meta}
        all_names = sorted(meta_map.keys() | runtime_info.keys())
        measures_payload: List[EvaluationMeasureModel] = []
        for name in all_names:
            meta = meta_map.get(name, {})
            runtime = runtime_info.get(name, {})
            label_value = _derive_measure_label(name, meta.get("label"))
            description_value = (
                meta.get("description") if meta.get("description") else None
            )
            module_value = meta.get("module") or runtime.get("module")
            source_value = meta.get("sourcePath") or runtime.get("path")
            doc_value = runtime.get("doc") if runtime.get("doc") else None
            measures_payload.append(
                EvaluationMeasureModel(
                    name=name,
                    label=label_value,
                    description=description_value,
                    module=module_value,
                    sourcePath=str(source_value) if source_value else None,
                    docstring=doc_value,
                    hasFunction=name in runtime_info,
                    usageCount=usage_map.get(name, 0),
                )
            )
        return measures_payload

    def _resolve_relative_file(base: Path, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        if candidate.exists():
            return candidate
        return None

    def _read_text_optional(path: Optional[Path]) -> Optional[str]:
        if not path:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def _latest_timestamp(paths: List[Optional[Path]]) -> Optional[str]:
        stamps: List[float] = []
        for item in paths:
            if not item:
                continue
            try:
                stamps.append(item.stat().st_mtime)
            except Exception:
                continue
        if not stamps:
            return None
        return _isoformat_utc(datetime.fromtimestamp(max(stamps), timezone.utc))

    def _write_text_with_backup(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_suffix(
                path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(path, backup)
        path.write_text(content, encoding="utf-8")

    def _write_yaml_with_backup(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_suffix(
                path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(path, backup)
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _judge_directories(ctx) -> List[Path]:
        dirs: List[Path] = []
        for directory in _evaluation_dirs(ctx):
            candidate = directory / "judges"
            if candidate.is_dir():
                dirs.append(candidate)
        return _unique_paths(dirs)

    def _resolve_first_existing(
        bases: List[Path], value: Optional[str]
    ) -> Optional[Path]:
        """Try resolving ``value`` against each base; return the first that exists.

        Absolute paths short-circuit. Used for judge ``instructions_file`` /
        ``template_file`` which can be relative to either the YAML they're
        defined in (inline-in-evaluation.yml judges) or the per-folder judge
        scaffold (which historically used a buggy ``../evaluations/...``
        prefix).
        """
        if not value:
            return None
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None
        for base in bases:
            resolved = (base / candidate).resolve()
            if resolved.exists():
                return resolved
        return None

    def _load_judge_models(ctx) -> List[EvaluationJudgeModel]:
        """Project the evaluation loader's merged judges dict into UI models.

        Source of truth is :func:`load_eval_config`, which already merges
        inline ``evaluation.judges`` and per-folder ``judges/*.yml`` configs
        (see ``omniagents/core/eval/loader.py``). We then resolve each
        judge's ``instructions_file`` and ``template_file`` against the
        YAML the merge picked the judge up from, with sane fallbacks for
        the per-folder scaffold convention.
        """
        try:
            eval_config = load_eval_config(str(ctx.root))
        except Exception:
            return []

        judges_cfg = eval_config.config.get("judges") or {}
        if not isinstance(judges_cfg, dict):
            return []

        eval_dir = eval_config.eval_dir
        project_root = eval_config.project_root

        result: List[EvaluationJudgeModel] = []
        for key, cfg in sorted(judges_cfg.items()):
            if not isinstance(cfg, dict):
                continue
            agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
            input_cfg = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}

            instructions_file = (
                agent_cfg.get("instructions_file")
                if isinstance(agent_cfg.get("instructions_file"), str)
                else None
            )
            template_file = (
                input_cfg.get("template_file")
                if isinstance(input_cfg.get("template_file"), str)
                else None
            )

            source_path = eval_config.judge_sources.get(str(key))
            if source_path is None:
                # Provenance unavailable; the loader merge always tracks
                # judges seen via the supported source paths, so this falls
                # back to evaluation.yml as a safe default rather than
                # silently dropping a real judge.
                source_path = eval_dir / "evaluation.yml"

            bases = [source_path.parent, eval_dir, project_root]
            resolved_instr = _resolve_first_existing(bases, instructions_file)
            resolved_template = _resolve_first_existing(bases, template_file)

            # Per-folder scaffold convention: instructions.md / template.md
            # sit next to judge.yml. Only fall back when the source clearly
            # follows that layout.
            if (
                resolved_instr is None
                and source_path.name == "judge.yml"
                and (source_path.parent / "instructions.md").exists()
            ):
                resolved_instr = source_path.parent / "instructions.md"
            if (
                resolved_template is None
                and source_path.name == "judge.yml"
                and (source_path.parent / "template.md").exists()
            ):
                resolved_template = source_path.parent / "template.md"

            instructions_text = _read_text_optional(resolved_instr)
            template_text = _read_text_optional(resolved_template)
            updated_at = _latest_timestamp(
                [source_path, resolved_instr, resolved_template]
            )

            source_value = str(
                input_cfg.get("source", "final_assistant_text")
                or "final_assistant_text"
            )
            failure_value = cfg.get("failure_category") or cfg.get("category")
            model_value = (
                agent_cfg.get("model")
                if isinstance(agent_cfg.get("model"), str)
                else None
            )
            agent_name = (
                agent_cfg.get("name")
                if isinstance(agent_cfg.get("name"), str)
                else None
            )
            agent_label = (
                agent_cfg.get("label")
                if isinstance(agent_cfg.get("label"), str)
                else None
            )

            result.append(
                EvaluationJudgeModel(
                    key=str(key),
                    agent_name=agent_name,
                    agent_label=agent_label,
                    model=model_value,
                    source=source_value,
                    failure_category=str(failure_value) if failure_value else None,
                    instructions=instructions_text,
                    template=template_text,
                    instructions_file=instructions_file,
                    template_file=template_file,
                    instructions_path=(str(resolved_instr) if resolved_instr else None),
                    template_path=(
                        str(resolved_template) if resolved_template else None
                    ),
                    judge_path=str(source_path),
                    updated_at=updated_at,
                )
            )
        return result

    def _find_judge_entry(
        ctx, key: str
    ) -> Optional[Tuple[Path, Path, Dict[str, Any], Dict[str, Any]]]:
        for root in _judge_directories(ctx):
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                cfg_path = entry / "judge.yml"
                if not cfg_path.is_file():
                    continue
                try:
                    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    data = {}
                judges_cfg = data.get("judges") if isinstance(data, dict) else None
                if not isinstance(judges_cfg, dict):
                    continue
                cfg = judges_cfg.get(key)
                if isinstance(cfg, dict):
                    return entry, cfg_path, data, cfg
        return None

    def _ensure_failure_category_mapping(
        ctx, judge_key: str, failure_category: Optional[str]
    ) -> None:
        if not failure_category:
            return
        eval_dir = ctx.root / "evaluations"
        eval_dir.mkdir(parents=True, exist_ok=True)
        evaluation_yml = eval_dir / "evaluation.yml"
        base = {}
        if evaluation_yml.exists():
            try:
                base = yaml.safe_load(evaluation_yml.read_text(encoding="utf-8")) or {}
            except Exception:
                base = {}
        if not isinstance(base, dict):
            base = {}
        evaluation_cfg = base.get("evaluation") or {}
        if not isinstance(evaluation_cfg, dict):
            evaluation_cfg = {}
        categories = evaluation_cfg.get("failure_categories") or []
        if not isinstance(categories, list):
            categories = []
        found = None
        for item in categories:
            if isinstance(item, dict) and str(item.get("name")) == str(
                failure_category
            ):
                found = item
                break
        measure_name = f"{judge_key}_agent"
        if found is None:
            found = {"name": str(failure_category), "measures": [measure_name]}
            categories.append(found)
        else:
            measures = found.get("measures") or []
            if measure_name not in measures:
                measures.append(measure_name)
                found["measures"] = measures
        evaluation_cfg["failure_categories"] = categories
        base["evaluation"] = evaluation_cfg
        _write_yaml_with_backup(evaluation_yml, base)

    def _unique_paths(items: List[Path]) -> List[Path]:
        seen: set[str] = set()
        result: List[Path] = []
        for item in items:
            try:
                key = str(item.resolve())
            except Exception:
                key = str(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _evaluation_dirs(ctx) -> List[Path]:
        dirs: List[Path] = []
        eval_paths = ctx.paths.get("evaluations") or []
        for entry in eval_paths:
            if entry.is_dir():
                dirs.append(entry)
            elif entry.is_file():
                dirs.append(entry.parent)
        default_dir = ctx.root / "evaluations"
        if default_dir not in dirs:
            dirs.append(default_dir)
        return _unique_paths(dirs)

    def _scenarios_files(ctx) -> List[Path]:
        """Discover scenario YAML files. Mirrors the eval CLI's loader, which
        accepts ``<eval_dir>/scenarios.yml`` *and* any ``*.yml`` under
        ``<eval_dir>/scenarios/`` (recursive). Without this Studio's
        Scenarios tab silently misses anything not named ``scenarios.yml``,
        even though the eval CLI happily loads it."""
        files: List[Path] = []
        for directory in _evaluation_dirs(ctx):
            candidate = directory / "scenarios.yml"
            if candidate.is_file():
                files.append(candidate)
            scenarios_subdir = directory / "scenarios"
            if scenarios_subdir.is_dir():
                files.extend(sorted(scenarios_subdir.rglob("*.yml")))
        eval_paths = ctx.paths.get("evaluations") or []
        for entry in eval_paths:
            if (
                entry.is_file()
                and entry.name.endswith(".yml")
                and entry.name == "scenarios.yml"
            ):
                files.append(entry)
        return _unique_paths(files)

    def _read_scenarios_file(path: Path) -> List[Dict[str, Any]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        if isinstance(data, dict):
            scenarios = data.get("scenarios")
            if isinstance(scenarios, list):
                return [s for s in scenarios if isinstance(s, dict)]
        return []

    def _build_scenario_entries(
        raw_items: List[Dict[str, Any]], source_path: Path
    ) -> List[ScenarioEntryModel]:
        scenarios: List[ScenarioEntryModel] = []
        for idx, item in enumerate(raw_items):
            name = str(item.get("name") or f"scenario_{idx + 1}")
            scenario_id = item.get("id")

            # Description
            description = (
                str(item.get("description"))
                if item.get("description") is not None
                else None
            )

            # Scenario type (synthetic or scripted)
            scenario_type = (
                str(item.get("type")) if item.get("type") is not None else None
            )

            # Prompt (single-turn scripted)
            prompt = str(item.get("prompt")) if item.get("prompt") is not None else None

            # Prompts (multi-turn scripted)
            raw_prompts = item.get("prompts")
            prompts: Optional[List[str]] = None
            if isinstance(raw_prompts, list):
                prompts = [str(p) for p in raw_prompts if p is not None]

            # Goal (synthetic scenarios)
            goal = str(item.get("goal")) if item.get("goal") is not None else None

            # Fixture reference
            fixture = (
                str(item.get("fixture")) if item.get("fixture") is not None else None
            )

            # Persona (synthetic scenarios)
            persona = (
                item.get("persona") if isinstance(item.get("persona"), dict) else None
            )

            # Initial message (synthetic scenarios)
            initial_message = (
                str(item.get("initial_message"))
                if item.get("initial_message") is not None
                else None
            )

            # Agent description (synthetic scenarios)
            agent_description = (
                str(item.get("agent_description"))
                if item.get("agent_description") is not None
                else None
            )

            # Max exchanges (synthetic scenarios)
            raw_max_exchanges = item.get("max_exchanges")
            max_exchanges: Optional[int] = None
            if isinstance(raw_max_exchanges, int):
                max_exchanges = raw_max_exchanges
            elif isinstance(raw_max_exchanges, str) and raw_max_exchanges.isdigit():
                max_exchanges = int(raw_max_exchanges)

            dimensions = (
                item.get("dimensions")
                if isinstance(item.get("dimensions"), dict)
                else None
            )
            measures = (
                item.get("measures") if isinstance(item.get("measures"), list) else None
            )
            raw_failure_categories = (
                item.get("failure_categories")
                if isinstance(item.get("failure_categories"), list)
                else None
            )
            failure_categories: Optional[List[str]] = None
            if raw_failure_categories:
                filtered = [
                    str(value).strip()
                    for value in raw_failure_categories
                    if isinstance(value, str) and str(value).strip()
                ]
                if filtered:
                    failure_categories = filtered
            raw_tags = item.get("tags") if isinstance(item.get("tags"), list) else None
            tags: Optional[List[str]] = None
            if raw_tags:
                filtered_tags = [
                    str(value).strip()
                    for value in raw_tags
                    if isinstance(value, str) and str(value).strip()
                ]
                if filtered_tags:
                    tags = filtered_tags
            analysis_data = (
                item.get("analysis") if isinstance(item.get("analysis"), dict) else None
            )
            expect_data = (
                item.get("expect") if isinstance(item.get("expect"), dict) else None
            )
            label_value = (
                item.get("label") if isinstance(item.get("label"), bool) else None
            )
            metadata = {
                "index": idx,
                "source_path": str(source_path),
            }
            target_category = item.get("target_category")
            if target_category is not None:
                metadata["target_category"] = target_category
            if analysis_data:
                metadata["analysis"] = analysis_data
            scenarios.append(
                ScenarioEntryModel(
                    id=str(scenario_id) if scenario_id is not None else None,
                    name=name,
                    description=description,
                    type=scenario_type,
                    prompt=prompt,
                    prompts=prompts,
                    goal=goal,
                    fixture=fixture,
                    persona=persona,
                    initial_message=initial_message,
                    agent_description=agent_description,
                    max_exchanges=max_exchanges,
                    dimensions=dimensions,
                    measures=measures,
                    failure_categories=failure_categories,
                    tags=tags,
                    label=label_value,
                    metadata=metadata,
                    analysis=analysis_data,
                    expect=expect_data,
                )
            )
        return scenarios

    def _load_golden_sets(ctx) -> List[ScenarioSetModel]:
        files = _scenarios_files(ctx)
        sets: List[ScenarioSetModel] = []
        for file in files:
            raw = _read_scenarios_file(file)
            scenarios = _build_scenario_entries(raw, file)
            try:
                updated_at = _isoformat_utc(
                    datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
                )
            except Exception:
                updated_at = None
            label = "Golden Scenarios" if len(files) == 1 else f"Golden ({file.name})"
            sets.append(
                ScenarioSetModel(
                    kind="golden",
                    label=label,
                    path=str(file),
                    scenario_count=len(scenarios),
                    updated_at=updated_at,
                    metadata={"directory": str(file.parent)},
                    scenarios=scenarios,
                )
            )
        return sets

    def _artifact_root(ctx) -> Path:
        return (ctx.root / "artifacts" / "eval").resolve()

    def _build_artifact_set(
        kind: str, file_path: Path, base_dir: Path
    ) -> ScenarioSetModel:
        raw = _read_scenarios_file(file_path)
        scenarios = _build_scenario_entries(raw, file_path)
        try:
            updated_at = _isoformat_utc(
                datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc)
            )
        except Exception:
            updated_at = None
        metadata: Dict[str, Any] = {"artifact_dir": str(base_dir)}
        manifest_path = base_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                metadata["manifest"] = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except Exception:
                metadata["manifest_error"] = "unreadable"
        labeled_count = sum(1 for item in scenarios if item.failure_categories)
        if labeled_count:
            metadata["labeled_count"] = labeled_count
        analysis_count = sum(1 for item in scenarios if item.analysis)
        if analysis_count:
            metadata["analysis_count"] = analysis_count
        label = (
            metadata.get("manifest", {}).get("name")
            if isinstance(metadata.get("manifest"), dict)
            else None
        )
        if not label:
            label = f"{base_dir.name} ({kind.title()})"
        return ScenarioSetModel(
            kind=kind,
            label=str(label),
            path=str(file_path),
            scenario_count=len(scenarios),
            updated_at=updated_at,
            metadata=metadata,
            scenarios=scenarios,
        )

    def _load_artifact_sets(ctx, kind: str) -> List[ScenarioSetModel]:
        root = _artifact_root(ctx)
        if not root.is_dir():
            return []
        sets: List[ScenarioSetModel] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if kind != "generated":
                continue
            file_path = entry / "generated_scenarios.yml"
            if file_path.is_file():
                sets.append(_build_artifact_set(kind, file_path, entry))
        return sets

    def _scenario_id_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text

    def _write_scenarios_file(path: Path, entries: List[Dict[str, Any]]) -> None:
        if path.exists():
            try:
                existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}
        else:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        updated = dict(existing)
        updated["scenarios"] = entries
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup_path = path.with_suffix(
                path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(path, backup_path)
        path.write_text(
            yaml.safe_dump(updated, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _ensure_scenario_ids(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scenario = dict(entry)
            scenario_id = scenario.get("id")
            if not scenario_id:
                dimensions = (
                    scenario.get("dimensions")
                    if isinstance(scenario.get("dimensions"), dict)
                    else {}
                )
                blob = json.dumps(
                    {
                        "prompt": str(scenario.get("prompt") or ""),
                        "dimensions": dimensions,
                    },
                    sort_keys=True,
                )
                scenario["id"] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
            normalized.append(scenario)
        return normalized

    def _get_scenario_set_by_path(
        ctx, kind: str, target_path: Path
    ) -> ScenarioSetModel:
        normalized = target_path.expanduser().resolve(strict=False)
        if kind == "golden":
            candidates = _load_golden_sets(ctx)
        elif kind == "generated":
            candidates = _load_artifact_sets(ctx, "generated")
        elif kind == "annotated":
            candidates = _load_artifact_sets(ctx, "annotated")
        else:
            raise HTTPException(status_code=400, detail="Invalid scenario set kind")
        for candidate in candidates:
            candidate_path = Path(candidate.path).expanduser().resolve(strict=False)
            if candidate_path == normalized:
                return candidate
        raise HTTPException(status_code=404, detail="Scenario set not found")

    def _locate_scenario_entry(
        entries: List[Dict[str, Any]],
        scenario_id: Optional[str],
        scenario_name: Optional[str],
        index: Optional[int],
    ) -> Optional[Tuple[int, Dict[str, Any]]]:
        candidate_id = _scenario_id_value(scenario_id)
        if candidate_id:
            for idx, entry in enumerate(entries):
                entry_id = _scenario_id_value(entry.get("id"))
                if entry_id == candidate_id:
                    return idx, entry
        if scenario_name:
            for idx, entry in enumerate(entries):
                if entry.get("name") == scenario_name:
                    return idx, entry
        if index is not None and 0 <= index < len(entries):
            return index, entries[index]
        return None

    def _resolve_golden_target(ctx) -> Path:
        files = _scenarios_files(ctx)
        if files:
            return files[0]
        default_dir = (ctx.root / "evaluations").resolve()
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_dir / "scenarios.yml"

    def _discover_dataset_entries(ctx) -> List[EvaluationDatasetModel]:
        root = _artifact_root(ctx)
        if not root.is_dir():
            return []
        datasets: List[EvaluationDatasetModel] = []
        for set_dir in sorted(root.iterdir()):
            if not set_dir.is_dir():
                continue
            splits_root = set_dir / "splits"
            if not splits_root.is_dir():
                continue
            manifest_path = set_dir / "manifest.json"
            scenario_set_path = set_dir / "generated_scenarios.yml"
            for category_dir in sorted(splits_root.iterdir()):
                if not category_dir.is_dir():
                    continue
                splits: List[EvaluationDatasetSplitModel] = []
                for split_file in sorted(category_dir.glob("*.yml")):
                    split_name = split_file.stem.lower()
                    if split_name not in {"train", "dev", "test"}:
                        continue
                    raw_entries = _read_scenarios_file(split_file)
                    scenario_count = len(raw_entries)
                    positives = sum(
                        1 for item in raw_entries if item.get("label") is True
                    )
                    negatives = sum(
                        1 for item in raw_entries if item.get("label") is False
                    )
                    try:
                        updated_at = _isoformat_utc(
                            datetime.fromtimestamp(
                                split_file.stat().st_mtime, timezone.utc
                            )
                        )
                    except Exception:
                        updated_at = None
                    splits.append(
                        EvaluationDatasetSplitModel(
                            split=split_name,
                            path=str(split_file),
                            scenario_count=scenario_count,
                            positives=positives,
                            negatives=negatives,
                            updated_at=updated_at,
                        )
                    )
                if splits:
                    order = {"train": 0, "dev": 1, "test": 2}
                    splits.sort(
                        key=lambda item: (order.get(item.split, 99), item.split)
                    )
                    dataset = EvaluationDatasetModel(
                        set_name=set_dir.name,
                        category=category_dir.name,
                        artifact_dir=str(set_dir),
                        scenario_set_path=(
                            str(scenario_set_path)
                            if scenario_set_path.is_file()
                            else None
                        ),
                        manifest_path=(
                            str(manifest_path) if manifest_path.is_file() else None
                        ),
                        splits=splits,
                    )
                    datasets.append(dataset)
        datasets.sort(key=lambda item: (item.set_name.lower(), item.category.lower()))
        return datasets

    @app.get("/api/evaluation/dimensions")
    async def get_evaluation_dimensions():
        try:
            manifest_path = _get_manifest_path()
            dimensions = _load_dimensions(manifest_path)
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            return {"project": project_name, "dimensions": dimensions}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    async def _run_scenario_generation_job(
        job_id: str, payload: ScenarioGenerationRequest, manifest_path: Path
    ) -> None:
        manager: ScenarioGenerationManager = app.state.scenario_generation_manager
        await manager.update_job(job_id, state="running", started_at=_utc_now())
        try:
            eval_cfg = load_eval_config(
                str(manifest_path),
                select=str(payload.select or ""),
                exclude=str(payload.exclude or ""),
                agent_key=(payload.agent or None),
            )
            compiled_path = eval_cfg.agent_path
            tracing = eval_cfg.tracing_config
            workflow_name: Optional[str] = None
            provider = str(tracing.get("provider") or "").lower()
            name_label = None
            if payload.name:
                name_label = payload.name
            elif payload.output_path:
                try:
                    name_label = Path(payload.output_path).resolve().parent.name
                except Exception:
                    name_label = None
            if provider == "studio":
                from omniagents.core.tracing import local as tracing_module

                base_name = tracing.get("name") or compiled_path.stem
                label = name_label or "scenarios"
                ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                workflow_name = f"{base_name}_scenarios_{label}_{ts}_generate"
                resume_mode = (tracing.get("settings") or {}).get("resume", "never")
                tracing_module.init(
                    project=str(tracing.get("project") or "scenario_generation"),
                    name=workflow_name,
                    resume=str(resume_mode),
                    replace_default=True,
                )

            base_dir = manifest_path.parent.resolve()
            output_path_obj: Optional[Path]
            if payload.output_path:
                candidate = Path(payload.output_path)
                if not candidate.is_absolute():
                    candidate = (base_dir / candidate).resolve()
                output_path_obj = candidate
            elif payload.name:
                output_path_obj = (
                    base_dir
                    / "artifacts"
                    / "eval"
                    / payload.name
                    / "generated_scenarios.yml"
                ).resolve()
            else:
                output_path_obj = None

            progress = {"target": int(payload.count or 0), "completed": 0}

            def _progress_cb(event: Dict[str, Any]) -> None:
                evt = event.get("event")
                if evt == "start":
                    target = event.get("target")
                    if isinstance(target, int) and target >= 0:
                        progress["target"] = target
                        asyncio.create_task(manager.update_job(job_id, total=target))
                elif evt == "progress":
                    progress["completed"] += 1
                    asyncio.create_task(
                        manager.update_job(job_id, completed=progress["completed"])
                    )
                elif evt == "done":
                    count = event.get("count")
                    if isinstance(count, int) and count >= 0:
                        progress["completed"] = count
                        if progress["target"] == 0:
                            progress["target"] = count
                    asyncio.create_task(
                        manager.update_job(
                            job_id,
                            completed=progress["completed"],
                            total=progress["target"],
                        )
                    )

            scenarios, out_path, _ = await generate_scenarios(
                compiled_path,
                count=payload.count,
                seed=payload.seed,
                set_name=payload.name,
                output=output_path_obj,
                workflow_name=workflow_name,
                progress_cb=_progress_cb,
                dimension_filters=payload.dimensions,
                eval_config=eval_cfg,
            )

            final_total = progress["target"] or len(scenarios)
            await manager.update_job(
                job_id,
                state="completed",
                completed=len(scenarios),
                total=final_total,
                finished_at=_utc_now(),
                scenarios_path=str(out_path),
                workflow_name=workflow_name,
            )
        except Exception as exc:
            logger.exception("Scenario generation %s failed", job_id)
            await manager.update_job(
                job_id, state="failed", error=str(exc), finished_at=_utc_now()
            )

    async def _run_judge_optimization_job(
        job_id: str, payload: JudgeOptimizationRequest, manifest_path: Path
    ) -> None:
        manager: JudgeOptimizationManager = app.state.judge_optimization_manager
        await manager.update_job(
            job_id,
            state="running",
            started_at=_utc_now(),
            total=int(payload.max_metric_calls),
        )
        loop = asyncio.get_running_loop()
        dataset = payload.dataset
        dataset_runs_path = dataset.runs_path
        if (not dataset_runs_path) and dataset.artifact_dir:
            candidate = Path(dataset.artifact_dir) / "generated_runs.json"
            if candidate.is_file():
                dataset_runs_path = str(candidate)
        source_value = dataset.source
        try:
            ctx = load_project(str(manifest_path))
            found = _find_judge_entry(ctx, payload.judge_key)
            if found:
                _dir, _cfg_path, _data, cfg = found
                input_cfg = (
                    cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
                )
                inferred = input_cfg.get("source")
                if isinstance(inferred, str) and inferred.strip():
                    source_value = inferred.strip()
        except Exception:
            pass
        if not source_value:
            source_value = "final_assistant_text"
        dataset.runs_path = dataset_runs_path
        dataset.source = source_value
        await manager.update_job(job_id, dataset=dataset.model_dump())

        def _execute() -> Dict[str, Any]:
            return optimize_judge_from_project(
                proj_path=str(manifest_path),
                agent_key=None,
                category=dataset.category,
                train_file=dataset.train_path,
                dev_file=dataset.dev_path,
                test_file=dataset.test_path,
                name=dataset.set_name,
                judge_key=payload.judge_key,
                runs_file=dataset_runs_path,
                source=source_value,
                max_turns=1,
                max_metric_calls=int(payload.max_metric_calls),
                reflection_model=None,
                optimize_components="instructions,template",
                optimizer_key=None,
                seed=int(payload.seed or 0),
                output_dir=None,
                resume=False,
                apply=True,
            )

        try:
            result = await loop.run_in_executor(None, _execute)
            try:
                ctx_refresh = load_project(str(manifest_path))
                found = _find_judge_entry(ctx_refresh, payload.judge_key)
                if found:
                    judge_dir, cfg_path, data, cfg = found
                    agent_cfg = (
                        cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
                    )
                    input_cfg = (
                        cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
                    )
                    instructions_value = (
                        result.get("instructions") if isinstance(result, dict) else None
                    )
                    template_value = (
                        result.get("template") if isinstance(result, dict) else None
                    )
                    if instructions_value:
                        resolved_instr = _resolve_relative_file(
                            cfg_path.parent, agent_cfg.get("instructions_file")
                        ) or (judge_dir / "instructions.md")
                        if resolved_instr:
                            _write_text_with_backup(resolved_instr, instructions_value)
                            logger.info(
                                "Applied optimized judge instructions: %s",
                                resolved_instr,
                            )
                    if template_value:
                        resolved_template = _resolve_relative_file(
                            cfg_path.parent, input_cfg.get("template_file")
                        ) or (judge_dir / "template.md")
                        if resolved_template:
                            _write_text_with_backup(resolved_template, template_value)
                            logger.info(
                                "Applied optimized judge template: %s",
                                resolved_template,
                            )
            except Exception as apply_exc:
                logger.exception(
                    "Failed to apply judge optimization updates: %s", apply_exc
                )
            await manager.update_job(
                job_id,
                state="completed",
                completed=1,
                finished_at=_utc_now(),
                run_dir=result.get("run_dir"),
                result_path=result.get("out_json"),
            )
        except (Exception, SystemExit) as exc:
            logger.exception("Judge optimization %s failed", job_id)
            await manager.update_job(
                job_id,
                state="failed",
                error=str(exc),
                finished_at=_utc_now(),
                completed=1,
            )

    async def _run_agent_optimization_job(
        job_id: str, payload: AgentOptimizationRequest, manifest_path: Path
    ) -> None:
        manager: AgentOptimizationManager = app.state.agent_optimization_manager
        await manager.update_job(
            job_id, state="running", started_at=_utc_now(), total=1
        )
        loop = asyncio.get_running_loop()
        dataset = payload.dataset
        log_file_holder: Dict[str, Optional[str]] = {"value": None}

        def _log_hook(message: str) -> None:
            text = str(message)
            if text.startswith("LOG_PATH:"):
                path_value = text.split("LOG_PATH:", 1)[1].strip()
                log_file_holder["value"] = path_value
                asyncio.run_coroutine_threadsafe(
                    manager.update_job(job_id, log_path=path_value), loop
                )
                return
            if "Exception during reflection/proposal" in text:
                asyncio.run_coroutine_threadsafe(
                    manager.update_job(job_id, error=text), loop
                )
                return
            if text.startswith("PROGRESS:"):
                try:
                    payload_text = text.split("PROGRESS:", 1)[1].strip()
                    parts = payload_text.split(":")
                    value = int(parts[0]) if parts and parts[0] else 0
                    eta_value = None
                    if len(parts) > 1 and parts[1]:
                        try:
                            eta_value = float(parts[1])
                        except Exception:
                            eta_value = None
                except Exception:
                    return
                asyncio.run_coroutine_threadsafe(
                    manager.update_job(job_id, completed=value, eta_seconds=eta_value),
                    loop,
                )

        def _execute() -> Dict[str, Any]:
            return optimize_agent_from_project(
                proj_path=str(manifest_path),
                agent_key=payload.agent_key,
                category=dataset.category,
                train_file=dataset.train_path,
                dev_file=dataset.dev_path,
                test_file=dataset.test_path,
                name=dataset.set_name,
                positives_only=bool(payload.positives_only),
                max_turns=None,
                max_metric_calls=int(payload.max_metric_calls),
                reflection_model="gpt-4.1",
                score_mode="fraction",
                optimizer_key=None,
                seed=int(payload.seed or 0),
                output_dir=None,
                resume=False,
                apply=True,
                log_hook=_log_hook,
            )

        try:
            result = await loop.run_in_executor(None, _execute)
            try:
                instructions_value = (
                    result.get("instructions") if isinstance(result, dict) else None
                )
                if instructions_value:
                    apply_agent_instructions(
                        str(manifest_path), payload.agent_key, instructions_value
                    )
            except Exception as apply_exc:
                logger.exception(
                    "Failed to apply agent optimization updates: %s", apply_exc
                )
            log_path_value = None
            if isinstance(result, dict):
                log_path_value = result.get("log_file") or log_file_holder.get("value")
            else:
                log_path_value = log_file_holder.get("value")
            await manager.update_job(
                job_id,
                state="completed",
                completed=int(payload.max_metric_calls),
                finished_at=_utc_now(),
                run_dir=result.get("run_dir") if isinstance(result, dict) else None,
                result_path=(
                    result.get("out_json") if isinstance(result, dict) else None
                ),
                log_path=log_path_value,
                error=None,
                eta_seconds=0.0,
            )
        except (Exception, SystemExit) as exc:
            logger.exception("Agent optimization %s failed", job_id)
            log_path_value = log_file_holder.get("value")
            await manager.update_job(
                job_id,
                state="failed",
                error=str(exc),
                log_path=log_path_value,
                finished_at=_utc_now(),
                eta_seconds=None,
            )

    @app.post("/api/evaluation/scenarios/generate")
    async def generate_evaluation_scenarios(payload: ScenarioGenerationRequest):
        try:
            name_value = (payload.name or "").strip()
            payload.name = name_value or None
            output_value = (payload.output_path or "").strip()
            payload.output_path = output_value or None
            select_value = (payload.select or "").strip()
            payload.select = select_value or None
            exclude_value = (payload.exclude or "").strip()
            payload.exclude = exclude_value or None
            if payload.dimensions:
                normalized_dims: Dict[str, List[str]] = {}
                for key, values in payload.dimensions.items():
                    if not values:
                        continue
                    filtered_values = [str(v).strip() for v in values if str(v).strip()]
                    if filtered_values:
                        normalized_dims[str(key)] = filtered_values
                payload.dimensions = normalized_dims or None
            if not payload.name and not payload.output_path:
                raise HTTPException(
                    status_code=422, detail="Provide a scenario set name or output_path"
                )
            manifest_path = _get_manifest_path()
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            manager: ScenarioGenerationManager = app.state.scenario_generation_manager
            job = await manager.create_job(project_name, payload)
            task = asyncio.create_task(
                _run_scenario_generation_job(job.job_id, payload, manifest_path)
            )
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = ScenarioGenerationStatusModel(**status) if status else None
            return {
                "job_id": job.job_id,
                "status": status_model.model_dump() if status_model else None,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/scenarios/generate/{job_id}")
    async def get_evaluation_scenario_generation(job_id: str):
        manager: ScenarioGenerationManager = app.state.scenario_generation_manager
        status = await manager.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Scenario generation not found")
        status_model = ScenarioGenerationStatusModel(**status)
        return {"job_id": job_id, "status": status_model.model_dump()}

    @app.put("/api/evaluation/dimensions")
    async def update_evaluation_dimensions(payload: EvaluationDimensionsPayload):
        try:
            manifest_path = _get_manifest_path()
            serialized = _persist_dimensions(manifest_path, payload.dimensions)
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            return {"project": project_name, "dimensions": serialized}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/scenarios")
    async def get_evaluation_scenarios():
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            golden_sets = _load_golden_sets(ctx)
            generated_sets = _load_artifact_sets(ctx, "generated")
            annotated_sets = _load_artifact_sets(ctx, "annotated")
            return {
                "project": project_name,
                "golden": [s.model_dump() for s in golden_sets],
                "generated": [s.model_dump() for s in generated_sets],
                "annotated": [s.model_dump() for s in annotated_sets],
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/datasets")
    async def get_evaluation_datasets():
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            datasets = _discover_dataset_entries(ctx)
            payload = EvaluationDatasetsResponse(
                project=project_name, datasets=datasets
            )
            return payload.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/judges")
    async def get_evaluation_judges():
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            judges = _load_judge_models(ctx)
            payload = EvaluationJudgesResponse(project=project_name, judges=judges)
            return payload.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/judges")
    async def create_evaluation_judge(payload: EvaluationJudgeCreatePayload):
        try:
            raw_name = (
                payload.name or payload.agent_label or payload.key or ""
            ).strip()
            if not raw_name:
                raise HTTPException(status_code=422, detail="Judge name is required")
            key_value = (payload.key or _slugify(raw_name)).strip()
            if not key_value:
                key_value = _slugify(raw_name)
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            default_model = ""
            try:
                base_spec = build_spec_from_project(ctx)
                default_model = str(getattr(base_spec, "model_name", "") or "")
            except Exception:
                default_model = ""
            model_candidate = (payload.model or default_model or "").strip()
            model_value = model_candidate or "gpt-4.1"
            agent_label_value = (payload.agent_label or raw_name).strip()
            agent_name_value = (payload.agent_name or "").strip()
            source_value = (
                payload.source or "final_assistant_text"
            ).strip() or "final_assistant_text"
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            failure_category_value = (payload.failure_category or "").strip() or None
            try:
                created_files, _root = scaffold_judge(
                    str(manifest_path),
                    key_value,
                    model=model_value,
                    source=source_value,
                    add_measure=bool(payload.add_measure),
                    failure_category=failure_category_value,
                    force=False,
                )
            except FileExistsError:
                raise HTTPException(status_code=409, detail="Judge already exists")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            judge_path: Optional[Path] = None
            for item in created_files:
                candidate = Path(item)
                if candidate.name == "judge.yml" and candidate.is_file():
                    judge_path = candidate
                    break
            if judge_path is None:
                for directory in _judge_directories(ctx):
                    candidate = directory / key_value / "judge.yml"
                    if candidate.is_file():
                        judge_path = candidate
                        break
            if judge_path is None or not judge_path.is_file():
                raise HTTPException(
                    status_code=500, detail="Judge manifest not found after creation"
                )
            try:
                data = yaml.safe_load(judge_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            judges_cfg = data.get("judges") if isinstance(data, dict) else {}
            if not isinstance(judges_cfg, dict) or not judges_cfg:
                raise HTTPException(status_code=500, detail="Judge manifest is empty")
            actual_key = next(iter(judges_cfg.keys()))
            cfg = judges_cfg.get(actual_key) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
            input_cfg = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
            agent_cfg["label"] = agent_label_value
            if payload.agent_name is not None:
                agent_cfg["name"] = agent_name_value or actual_key
            agent_cfg["model"] = model_value
            cfg["agent"] = agent_cfg
            input_cfg["source"] = source_value
            cfg["input"] = input_cfg
            if failure_category_value is not None:
                cfg["failure_category"] = failure_category_value
            judges_cfg[actual_key] = cfg
            data["judges"] = judges_cfg
            _write_yaml_with_backup(judge_path, data)
            resolved_instr = _resolve_relative_file(
                judge_path.parent, agent_cfg.get("instructions_file")
            ) or (judge_path.parent / "instructions.md")
            resolved_template = _resolve_relative_file(
                judge_path.parent, input_cfg.get("template_file")
            ) or (judge_path.parent / "template.md")
            if payload.instructions is not None and resolved_instr is not None:
                _write_text_with_backup(resolved_instr, payload.instructions)
            if payload.template is not None and resolved_template is not None:
                _write_text_with_backup(resolved_template, payload.template)
            if failure_category_value:
                _ensure_failure_category_mapping(
                    ctx, actual_key, failure_category_value
                )
            ctx = load_project(str(manifest_path))
            judges = _load_judge_models(ctx)
            response = EvaluationJudgesResponse(project=project_name, judges=judges)
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.put("/api/evaluation/judges")
    async def update_evaluation_judge(payload: EvaluationJudgeUpdatePayload):
        try:
            key_value = (payload.key or "").strip()
            if not key_value:
                raise HTTPException(status_code=422, detail="Judge key is required")
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            found = _find_judge_entry(ctx, key_value)
            if not found:
                raise HTTPException(status_code=404, detail="Judge not found")
            judge_dir, cfg_path, data, cfg = found
            if not isinstance(cfg, dict):
                cfg = {}
            agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
            input_cfg = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
            if payload.model is not None:
                agent_cfg["model"] = str(payload.model)
            if payload.agent_label is not None:
                agent_cfg["label"] = payload.agent_label
            if payload.agent_name is not None:
                agent_cfg["name"] = payload.agent_name
            cfg["agent"] = agent_cfg
            if payload.source is not None:
                input_cfg["source"] = str(payload.source or "final_assistant_text")
            cfg["input"] = input_cfg
            if payload.failure_category is not None:
                fc_value = str(payload.failure_category).strip()
                if fc_value:
                    cfg["failure_category"] = fc_value
                else:
                    cfg.pop("failure_category", None)
            judges_block = data.get("judges") if isinstance(data, dict) else {}
            if not isinstance(judges_block, dict):
                judges_block = {}
            judges_block[key_value] = cfg
            data["judges"] = judges_block
            _write_yaml_with_backup(cfg_path, data)
            resolved_instr = _resolve_relative_file(
                cfg_path.parent, agent_cfg.get("instructions_file")
            ) or (judge_dir / "instructions.md")
            resolved_template = _resolve_relative_file(
                cfg_path.parent, input_cfg.get("template_file")
            ) or (judge_dir / "template.md")
            if payload.instructions is not None and resolved_instr is not None:
                _write_text_with_backup(resolved_instr, payload.instructions)
            if payload.template is not None and resolved_template is not None:
                _write_text_with_backup(resolved_template, payload.template)
            failure_value = (
                payload.failure_category.strip()
                if isinstance(payload.failure_category, str)
                else None
            )
            if failure_value:
                _ensure_failure_category_mapping(ctx, key_value, failure_value)
            ctx = load_project(str(manifest_path))
            judges = _load_judge_models(ctx)
            response = EvaluationJudgesResponse(project=project_name, judges=judges)
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/evaluation/judges/{key}")
    async def delete_evaluation_judge(key: str):
        try:
            key_value = (key or "").strip()
            if not key_value:
                raise HTTPException(status_code=422, detail="Judge key is required")
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            found = _find_judge_entry(ctx, key_value)
            if not found:
                raise HTTPException(status_code=404, detail="Judge not found")
            judge_dir, _cfg_path, _data, _cfg = found
            try:
                shutil.rmtree(judge_dir)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            ctx = load_project(str(manifest_path))
            judges = _load_judge_models(ctx)
            response = EvaluationJudgeDeleteResponse(
                project=project_name, deleted=key_value, judges=judges
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/judges/optimize")
    async def start_judge_optimization(payload: JudgeOptimizationRequest):
        try:
            judge_key = (payload.judge_key or "").strip()
            if not judge_key:
                raise HTTPException(status_code=422, detail="Judge key is required")
            dataset = payload.dataset
            if not dataset:
                raise HTTPException(
                    status_code=422, detail="Dataset selection is required"
                )
            set_name_value = (dataset.set_name or "").strip()
            category_value = (dataset.category or "").strip()
            if not set_name_value or not category_value:
                raise HTTPException(
                    status_code=422, detail="Dataset set_name and category are required"
                )
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            found = _find_judge_entry(ctx, judge_key)
            if not found:
                raise HTTPException(status_code=404, detail="Judge not found")
            _judge_dir, _cfg_path, data, cfg = found
            input_cfg = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
            judge_source = (
                input_cfg.get("source")
                if isinstance(input_cfg.get("source"), str)
                else None
            )
            split_paths = {
                "train": dataset.train_path,
                "dev": dataset.dev_path,
                "test": dataset.test_path,
            }
            resolved_splits: Dict[str, str] = {}
            for split_name, raw_path in split_paths.items():
                candidate = Path(str(raw_path)).expanduser().resolve(strict=False)
                if not candidate.is_file():
                    raise HTTPException(
                        status_code=422,
                        detail=f"{split_name.title()} split not found at {candidate}",
                    )
                resolved_splits[split_name] = str(candidate)
            runs_path = dataset.runs_path
            if (not runs_path) and dataset.artifact_dir:
                candidate = (
                    Path(dataset.artifact_dir).expanduser().resolve(strict=False)
                    / "generated_runs.json"
                )
                if candidate.is_file():
                    runs_path = str(candidate)
            if not runs_path:
                raise HTTPException(
                    status_code=422,
                    detail="Runs file not found. Run the scenarios before optimizing.",
                )
            dataset.set_name = set_name_value
            dataset.category = category_value
            dataset.train_path = resolved_splits["train"]
            dataset.dev_path = resolved_splits["dev"]
            dataset.test_path = resolved_splits["test"]
            dataset.runs_path = runs_path
            if judge_source and isinstance(judge_source, str) and judge_source.strip():
                dataset.source = judge_source.strip()
            else:
                dataset.source = dataset.source or "final_assistant_text"
            payload.judge_key = judge_key
            job_payload = JudgeOptimizationRequest(**payload.model_dump())
            manager: JudgeOptimizationManager = app.state.judge_optimization_manager
            job = await manager.create_job(project_name, job_payload)
            await manager.update_job(
                job.job_id, dataset=job_payload.dataset.model_dump()
            )
            task = asyncio.create_task(
                _run_judge_optimization_job(job.job_id, job_payload, manifest_path)
            )
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = JudgeOptimizationStatusModel(**status) if status else None
            response = JudgeOptimizationResponse(job_id=job.job_id, status=status_model)
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/judges/optimize/{job_id}")
    async def get_judge_optimization(job_id: str):
        manager: JudgeOptimizationManager = app.state.judge_optimization_manager
        status = await manager.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Judge optimization not found")
        status_model = JudgeOptimizationStatusModel(**status)
        response = JudgeOptimizationResponse(job_id=job_id, status=status_model)
        return response.model_dump()

    @app.post("/api/evaluation/agents/optimize")
    async def start_agent_optimization(payload: AgentOptimizationRequest):
        try:
            agent_key = (payload.agent_key or "").strip()
            if not agent_key:
                raise HTTPException(status_code=422, detail="Agent key is required")
            dataset_payload = payload.dataset
            if not dataset_payload:
                raise HTTPException(
                    status_code=422, detail="Dataset selection is required"
                )
            set_name_value = (dataset_payload.set_name or "").strip()
            category_value = (dataset_payload.category or "").strip()
            if not set_name_value or not category_value:
                raise HTTPException(
                    status_code=422, detail="Dataset set_name and category are required"
                )
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            try:
                resolve_agent(ctx, agent_key)
            except Exception:
                raise HTTPException(status_code=404, detail="Agent not found")
            split_paths = {
                "train": dataset_payload.train_path,
                "dev": dataset_payload.dev_path,
                "test": dataset_payload.test_path,
            }
            resolved_splits: Dict[str, str] = {}
            for split_name, raw_path in split_paths.items():
                candidate = Path(str(raw_path)).expanduser().resolve(strict=False)
                if not candidate.is_file():
                    raise HTTPException(
                        status_code=422,
                        detail=f"{split_name.title()} split not found at {candidate}",
                    )
                resolved_splits[split_name] = str(candidate)
            normalized_dataset = AgentOptimizationDatasetPayload(
                set_name=set_name_value,
                category=category_value,
                artifact_dir=dataset_payload.artifact_dir,
                train_path=resolved_splits["train"],
                dev_path=resolved_splits["dev"],
                test_path=resolved_splits["test"],
            )
            positives_only_value = bool(payload.positives_only)
            max_metric_calls_value = int(payload.max_metric_calls or 100)
            if max_metric_calls_value < 6:
                raise HTTPException(
                    status_code=422,
                    detail="Budget must be at least 6 to run optimization",
                )
            seed_value = int(payload.seed or 0)
            normalized_request = AgentOptimizationRequest(
                agent_key=agent_key,
                dataset=normalized_dataset,
                positives_only=positives_only_value,
                max_metric_calls=max_metric_calls_value,
                seed=seed_value,
            )
            manager: AgentOptimizationManager = app.state.agent_optimization_manager
            job = await manager.create_job(project_name, normalized_request)
            await manager.update_job(
                job.job_id, dataset=normalized_dataset.model_dump()
            )
            task = asyncio.create_task(
                _run_agent_optimization_job(
                    job.job_id, normalized_request, manifest_path
                )
            )
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = AgentOptimizationStatusModel(**status) if status else None
            response = AgentOptimizationResponse(job_id=job.job_id, status=status_model)
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/agents/optimize/{job_id}")
    async def get_agent_optimization(job_id: str):
        manager: AgentOptimizationManager = app.state.agent_optimization_manager
        status = await manager.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Agent optimization not found")
        status_model = AgentOptimizationStatusModel(**status)
        response = AgentOptimizationResponse(job_id=job_id, status=status_model)
        return response.model_dump()

    @app.get("/api/evaluation/datasets/split")
    async def get_evaluation_dataset_split(
        path: str = Query(..., description="Absolute path to the split file")
    ):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            target_path = Path(path).expanduser().resolve(strict=False)
            datasets = _discover_dataset_entries(ctx)
            matched_dataset: Optional[EvaluationDatasetModel] = None
            matched_split: Optional[EvaluationDatasetSplitModel] = None
            for dataset in datasets:
                for split in dataset.splits:
                    split_path = Path(split.path).expanduser().resolve(strict=False)
                    if split_path == target_path:
                        matched_dataset = dataset
                        matched_split = split
                        break
                if matched_split is not None:
                    break
            if matched_dataset is None or matched_split is None:
                raise HTTPException(status_code=404, detail="Dataset split not found")
            if not target_path.is_file():
                raise HTTPException(status_code=404, detail="Split file not found")
            raw_entries = _read_scenarios_file(target_path)
            scenarios = _build_scenario_entries(raw_entries, target_path)
            artifact_dir = Path(matched_dataset.artifact_dir)
            runs_index: Dict[str, Dict[str, Any]] = {}
            if artifact_dir.is_dir():
                run_files = sorted(artifact_dir.glob("*runs*.json"))
                for run_file in run_files:
                    try:
                        runs_payload = json.loads(run_file.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    runs_list = (
                        runs_payload.get("runs")
                        if isinstance(runs_payload, dict)
                        else None
                    )
                    if not isinstance(runs_list, list):
                        continue
                    for run in runs_list:
                        if not isinstance(run, dict):
                            continue
                        scenario_id = run.get("scenario_id")
                        if scenario_id is None and isinstance(
                            run.get("scenario"), dict
                        ):
                            scenario_id = run["scenario"].get("id")
                        if scenario_id is not None:
                            runs_index[str(scenario_id)] = run
                        scenario_name = run.get("scenario_name")
                        if isinstance(scenario_name, str) and scenario_name:
                            runs_index[f"name::{scenario_name}"] = run

            def _extract_assistant_message(run_entry: Dict[str, Any]) -> Optional[str]:
                if not run_entry:
                    return None
                message = run_entry.get("final_assistant_text")
                if isinstance(message, str) and message.strip():
                    return message
                history = run_entry.get("history")
                if isinstance(history, list):
                    for item in reversed(history):
                        if not isinstance(item, dict):
                            continue
                        if item.get("role") != "assistant":
                            continue
                        content = item.get("content")
                        if isinstance(content, str) and content.strip():
                            return content
                        if isinstance(content, list):
                            texts: List[str] = []
                            for part in content:
                                if isinstance(part, dict):
                                    text_value = part.get("text")
                                    if isinstance(text_value, str) and text_value:
                                        texts.append(text_value)
                                elif isinstance(part, str):
                                    texts.append(part)
                            combined = "\n".join(t for t in texts if t.strip())
                            if combined.strip():
                                return combined
                return None

            for scenario in scenarios:
                run_entry: Optional[Dict[str, Any]] = None
                if scenario.id is not None:
                    run_entry = runs_index.get(str(scenario.id))
                if run_entry is None and scenario.name:
                    run_entry = runs_index.get(f"name::{scenario.name}")
                assistant_message = (
                    _extract_assistant_message(run_entry or {}) if run_entry else None
                )
                if assistant_message:
                    metadata = dict(scenario.metadata or {})
                    metadata["assistant_last_message"] = assistant_message
                    scenario.metadata = metadata
            positives = sum(
                1
                for item in raw_entries
                if isinstance(item.get("label"), bool) and item.get("label") is True
            )
            negatives = sum(
                1
                for item in raw_entries
                if isinstance(item.get("label"), bool) and item.get("label") is False
            )
            try:
                updated_at = _isoformat_utc(
                    datetime.fromtimestamp(target_path.stat().st_mtime, timezone.utc)
                )
            except Exception:
                updated_at = matched_split.updated_at
            split_payload = EvaluationDatasetSplitModel(
                split=matched_split.split,
                path=str(target_path),
                scenario_count=len(raw_entries),
                positives=positives,
                negatives=negatives,
                updated_at=updated_at,
            )
            response = EvaluationDatasetSplitBrowseResponse(
                project=project_name,
                set_name=matched_dataset.set_name,
                category=matched_dataset.category,
                split=split_payload,
                scenarios=scenarios,
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/scenarios/promote")
    async def promote_evaluation_scenario(payload: ScenarioPromotionPayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            source_path = Path(payload.set_path).expanduser().resolve()
            if not source_path.is_file():
                raise HTTPException(
                    status_code=404, detail="Scenario set path not found"
                )

            raw_scenarios = _read_scenarios_file(source_path)
            target_entry: Optional[Dict[str, Any]] = None
            for idx, entry in enumerate(raw_scenarios):
                if payload.scenario_id is not None and entry.get("id") is not None:
                    if str(entry.get("id")) == payload.scenario_id:
                        target_entry = entry
                        break
                elif (
                    payload.scenario_name is not None
                    and entry.get("name") == payload.scenario_name
                ):
                    target_entry = entry
                    break
                elif payload.index is not None and payload.index == idx:
                    target_entry = entry
                    break
            if target_entry is None:
                raise HTTPException(
                    status_code=404, detail="Scenario not found in source set"
                )

            golden_file = _resolve_golden_target(ctx)
            if golden_file.is_file():
                try:
                    golden_data = (
                        yaml.safe_load(golden_file.read_text(encoding="utf-8")) or {}
                    )
                except Exception:
                    golden_data = {}
            else:
                golden_data = {}

            if not isinstance(golden_data, dict):
                golden_data = {}

            existing = (
                golden_data.get("scenarios")
                if isinstance(golden_data.get("scenarios"), list)
                else []
            )
            existing_list: List[Dict[str, Any]] = [
                e for e in existing if isinstance(e, dict)
            ]

            new_entry = dict(target_entry)

            scenario_id = new_entry.get("id")
            scenario_name = new_entry.get("name")

            for entry in existing_list:
                if scenario_id is not None and str(entry.get("id")) == str(scenario_id):
                    raise HTTPException(
                        status_code=409, detail="Scenario already present in golden set"
                    )
                if (
                    scenario_id is None
                    and scenario_name
                    and entry.get("name") == scenario_name
                ):
                    raise HTTPException(
                        status_code=409, detail="Scenario already present in golden set"
                    )

            existing_list.append(new_entry)
            golden_data = dict(golden_data)
            golden_data["scenarios"] = existing_list

            golden_file.parent.mkdir(parents=True, exist_ok=True)
            if golden_file.exists():
                backup_path = golden_file.with_suffix(
                    golden_file.suffix
                    + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                shutil.copy2(golden_file, backup_path)
            golden_file.write_text(
                yaml.safe_dump(golden_data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            golden_sets = _load_golden_sets(ctx)
            promoted_entry = ScenarioEntryModel(
                id=(
                    str(new_entry.get("id"))
                    if new_entry.get("id") is not None
                    else None
                ),
                name=str(new_entry.get("name") or ""),
                prompt=str(new_entry.get("prompt") or ""),
                dimensions=(
                    new_entry.get("dimensions")
                    if isinstance(new_entry.get("dimensions"), dict)
                    else None
                ),
                metadata={
                    "source_kind": payload.source_kind,
                    "source_path": str(source_path),
                },
            )
            return {
                "project": project_name,
                "promoted": promoted_entry.model_dump(),
                "golden": [s.model_dump() for s in golden_sets],
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/scenarios/annotate")
    async def annotate_evaluation_scenario(payload: ScenarioAnnotatePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            if payload.kind == "golden":
                raise HTTPException(
                    status_code=422,
                    detail="Annotation is only supported for generated or annotated sets",
                )
            scenario_set = _get_scenario_set_by_path(ctx, "generated", target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            if isinstance(project_name, str):
                project_name = project_name.strip() or None
            else:
                project_name = None
            if not project_name:
                defaults = ctx.tracing_defaults or {}
                if isinstance(defaults, dict):
                    candidate = defaults.get("project")
                    if isinstance(candidate, str) and candidate.strip():
                        project_name = candidate.strip()
            if not project_name:
                raise HTTPException(
                    status_code=422,
                    detail="Tracing project not configured; set tracing.project in project.yml",
                )
            try:
                _, agent_yaml_path = resolve_agent(ctx, payload.agent)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            annotated, _original, source_path = annotate_scenarios(
                Path(agent_yaml_path), file_path, str(project_name), None
            )
            output_path = write_annotation_output(ctx.root, source_path, annotated)
            annotated_set = _get_scenario_set_by_path(ctx, "generated", output_path)
            response = ScenarioAnnotateResponseModel(
                project=str(project_name),
                annotated_path=str(output_path),
                set=annotated_set,
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/scenarios/split")
    async def split_evaluation_scenarios(payload: ScenarioSplitPayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            category = payload.category.strip()
            if not category:
                raise HTTPException(status_code=422, detail="Category is required")
            if payload.kind == "golden":
                raise HTTPException(
                    status_code=422, detail="Cannot split golden scenarios"
                )
            total_fraction = (
                float(payload.train) + float(payload.dev) + float(payload.test)
            )
            if total_fraction <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="Train/dev/test fractions must sum to more than zero",
                )
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            base_dir = file_path.parent
            if payload.output_dir:
                output_dir = Path(payload.output_dir)
                if not output_dir.is_absolute():
                    output_dir = (base_dir / output_dir).resolve()
            else:
                output_dir = None
            try:
                create_splits(
                    file_path,
                    category,
                    output_dir=output_dir,
                    f_train=float(payload.train),
                    f_dev=float(payload.dev),
                    f_test=float(payload.test),
                    seed=int(payload.seed),
                    negatives_ratio=(
                        float(payload.negatives_ratio)
                        if payload.negatives_ratio is not None
                        else None
                    ),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            refreshed_ctx = load_project(str(manifest_path))
            datasets = _discover_dataset_entries(refreshed_ctx)
            set_name = base_dir.name
            matched_dataset: Optional[EvaluationDatasetModel] = None
            for dataset in datasets:
                if dataset.set_name == set_name and dataset.category == category:
                    matched_dataset = dataset
                    break
            if matched_dataset is None:
                raise HTTPException(
                    status_code=500, detail="Failed to locate created dataset"
                )
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            response = ScenarioSplitResponse(
                project=str(project_name) if project_name else None,
                dataset=matched_dataset,
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/evaluation/datasets")
    async def delete_evaluation_dataset(payload: EvaluationDatasetDeletePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            base_dir = (_artifact_root(ctx) / payload.set_name).resolve()
            if not base_dir.is_dir():
                raise HTTPException(
                    status_code=404, detail="Dataset set directory not found"
                )
            splits_root = base_dir / "splits" / payload.category
            if splits_root.is_dir():
                shutil.rmtree(splits_root)
            runs_files = list(base_dir.glob("*runs*.json"))
            for runs_file in runs_files:
                try:
                    data = json.loads(runs_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                runs = data.get("runs") if isinstance(data, dict) else None
                if not isinstance(runs, list) or not runs:
                    try:
                        runs_file.unlink()
                    except Exception:
                        pass
            refreshed_ctx = load_project(str(manifest_path))
            datasets = _discover_dataset_entries(refreshed_ctx)
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            response = EvaluationDatasetDeleteResponse(
                project=str(project_name) if project_name else None, datasets=datasets
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/scenarios/delete")
    async def delete_evaluation_scenario_set(payload: ScenarioSetDeletePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            parent_dir = file_path.parent
            if payload.kind == "generated":
                if parent_dir.is_dir():
                    shutil.rmtree(parent_dir)
            else:
                if file_path.exists():
                    if file_path.is_file():
                        file_path.unlink()
                    else:
                        shutil.rmtree(file_path)
                if parent_dir.is_dir():
                    try:
                        remaining = [
                            child for child in parent_dir.iterdir() if child.exists()
                        ]
                        if not remaining:
                            parent_dir.rmdir()
                    except Exception:
                        pass
            ctx_updated = load_project(str(manifest_path))
            resources = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            response = ScenarioSetDeleteResponseModel(
                project=str(project_name) if project_name else None,
                deleted_path=str(file_path),
                generated=_load_artifact_sets(ctx_updated, "generated"),
                annotated=_load_artifact_sets(ctx_updated, "annotated"),
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    async def _run_suite_job(
        job_id: str, payload: EvaluationSuiteRunRequest, manifest_path: Path
    ) -> None:
        manager: SuiteRunManager = app.state.suite_run_manager
        await manager.update_job(
            job_id, state="running", started_at=_utc_now(), completed=0
        )
        loop = asyncio.get_running_loop()
        progress_state = {"total": 0, "completed": 0}
        saved_env_vars: Dict[str, Optional[str]] = {}
        try:
            eval_cfg = load_eval_config(
                str(manifest_path),
                select=str(payload.select or ""),
                exclude=str(payload.exclude or ""),
                agent_key=(payload.agent or None),
            )
            compiled_path = eval_cfg.agent_path
            tracing = eval_cfg.tracing_config
            workflow_name: Optional[str] = None
            provider = str(tracing.get("provider") or "").lower()
            label = None
            if payload.name:
                label = payload.name.strip() or None
            if provider == "studio":
                from omniagents.core.tracing import local as tracing_module

                base_name = tracing.get("name") or compiled_path.stem
                label_segment = label or "suite"
                ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                workflow_name = f"{base_name}_suite_{label_segment}_{ts}"
                resume_mode = (tracing.get("settings") or {}).get("resume", "never")
                tracing_module.init(
                    project=str(tracing.get("project") or "evaluation_suite"),
                    name=workflow_name,
                    resume=str(resume_mode),
                    replace_default=True,
                )

            base_dir = manifest_path.parent.resolve()
            output_arg = (payload.output_path or "").strip()
            if output_arg:
                final_out = Path(output_arg)
                if not final_out.is_absolute():
                    final_out = (base_dir / output_arg).resolve()
            else:
                name_value = label or "suite"
                final_out = (
                    base_dir / "artifacts" / "eval" / name_value / "run_results.json"
                ).resolve()
            final_out.parent.mkdir(parents=True, exist_ok=True)

            # Set environment variables from evaluation environment config
            # This ensures tools have access to required env vars (e.g., API keys)
            env_config = (
                (eval_cfg.config or {}).get("environment", {}).get("config", {})
            )
            for key, value in env_config.items():
                if isinstance(value, str):
                    resolved = _expand_env_template(value)
                    # Use uppercase key as env var name (e.g., api_base -> API_BASE)
                    env_key = key.upper()
                    # Also try common naming patterns
                    # e.g., api_base from hotel config -> HOTEL_API_BASE_URL
                    saved_env_vars[env_key] = os.environ.get(env_key)
                    os.environ[env_key] = resolved

            def _schedule_update(**updates: Any) -> None:
                asyncio.run_coroutine_threadsafe(
                    manager.update_job(job_id, **updates), loop
                )

            def _progress_cb(event: Dict[str, Any]) -> None:
                evt = event.get("event")
                if evt == "start":
                    total = int(event.get("total") or 0)
                    progress_state["total"] = total
                    _schedule_update(total=total)
                elif evt == "progress":
                    idx = event.get("index")
                    if isinstance(idx, int) and idx >= 0:
                        progress_state["completed"] = idx
                    else:
                        progress_state["completed"] = max(
                            progress_state["completed"] + 1, 1
                        )
                    _schedule_update(completed=progress_state["completed"])

            include_unmeasured = bool(payload.include_unmeasured or False)

            def _execute() -> Dict[str, Any]:
                return run_evaluation(
                    compiled_path,
                    tracing_cfg=tracing,
                    workflow_base_name=label or compiled_path.stem,
                    include_unmeasured=include_unmeasured,
                    progress_cb=_progress_cb,
                    eval_config=eval_cfg,
                )

            result = await loop.run_in_executor(None, _execute)
            if progress_state["total"] <= 0:
                runs_count = len(result.get("runs") or [])
                progress_state["total"] = runs_count
            if progress_state["completed"] <= 0:
                progress_state["completed"] = len(result.get("runs") or [])
            final_out.write_text(json.dumps(result, indent=2), encoding="utf-8")

            runs = result.get("runs") or []
            metrics = result.get("metrics") or []
            measure_total = 0
            measure_failed = 0
            measure_skipped = 0
            for run in runs:
                for measure in run.get("measures") or []:
                    measure_total += 1
                    passed_value = measure.get("passed")
                    if passed_value is False:
                        measure_failed += 1
                    elif passed_value is None:
                        measure_skipped += 1
            metrics_total = 0
            metrics_failed = 0
            metrics_skipped = 0
            for metric in metrics:
                metrics_total += 1
                target_block = metric.get("target")
                has_target = target_block is not None
                if has_target:
                    meets = bool(metric.get("meets_target"))
                    if not meets:
                        metrics_failed += 1
                else:
                    metrics_skipped += 1
            stats_block = result.get("stats") or {}
            total_checks = measure_total + metrics_total
            failed_checks = measure_failed + metrics_failed
            skipped_checks = measure_skipped + metrics_skipped
            passed_checks = max(total_checks - failed_checks - skipped_checks, 0)
            summary_model = EvaluationSuiteRunSummaryModel(
                total_checks=total_checks,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                skipped_checks=skipped_checks,
                measures_total=measure_total,
                measures_failed=measure_failed,
                measures_skipped=measure_skipped,
                metrics_total=metrics_total,
                metrics_failed=metrics_failed,
                metrics_skipped=metrics_skipped,
                scenarios_total=int(stats_block.get("total_scenarios") or 0),
                scenarios_included=int(stats_block.get("included_scenarios") or 0),
                scenarios_skipped=int(stats_block.get("skipped_unmeasured") or 0),
            )

            final_total = progress_state["total"] or progress_state["completed"]
            completed_value = progress_state["completed"] or final_total

            # Extract warnings from result (e.g., environment loading errors)
            result_warnings = result.get("warnings") or []

            await manager.update_job(
                job_id,
                state="completed",
                completed=completed_value,
                total=final_total,
                finished_at=_utc_now(),
                result_path=str(final_out),
                workflow_name=workflow_name,
                summary=summary_model.model_dump(),
                warnings=result_warnings if result_warnings else None,
            )
        except Exception as exc:
            logger.exception("Evaluation suite run %s failed", job_id)
            await manager.update_job(
                job_id, state="failed", error=str(exc), finished_at=_utc_now()
            )
        finally:
            # Restore original environment variables
            for env_key, original_value in saved_env_vars.items():
                if original_value is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = original_value

    async def _run_scenario_job(
        job_id: str,
        payload: ScenarioRunRequest,
        manifest_path: Path,
        scenario_set: ScenarioSetModel,
        scenarios: List[Dict[str, Any]],
        results_dir: Path,
        run_id: str,
    ) -> None:
        manager: ScenarioRunManager = app.state.scenario_run_manager
        await manager.update_job(
            job_id,
            state="running",
            started_at=_utc_now(),
            total=len(scenarios),
            completed=0,
        )
        try:
            eval_cfg = load_eval_config(
                str(manifest_path),
                select=str(payload.select or ""),
                exclude=str(payload.exclude or ""),
                agent_key=(payload.agent or None),
            )
            compiled_path = eval_cfg.agent_path
            tracing = eval_cfg.tracing_config
            workflow_name: Optional[str] = None
            provider = str(tracing.get("provider") or "").lower()
            if provider == "studio":
                from omniagents.core.tracing import local as tracing_module

                base_name = tracing.get("name") or compiled_path.stem
                scenario_dir = Path(scenario_set.path).resolve().parent
                label = scenario_dir.name or "scenarios"
                ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                workflow_name = f"{base_name}_scenarios_{label}_{ts}"
                resume_mode = (tracing.get("settings") or {}).get("resume", "never")
                tracing_module.init(
                    project=str(tracing.get("project") or "scenario_runs"),
                    name=workflow_name,
                    resume=str(resume_mode),
                    replace_default=True,
                )

            progress = {"total": len(scenarios), "completed": 0}

            def _progress_cb(event: Dict[str, Any]) -> None:
                evt = event.get("event")
                if evt == "start":
                    total = int(event.get("total") or progress["total"])
                    progress["total"] = total
                    asyncio.create_task(manager.update_job(job_id, total=total))
                elif evt == "progress":
                    current = event.get("current")
                    if isinstance(current, int) and current >= 0:
                        progress["completed"] = max(progress["completed"], current)
                    else:
                        progress["completed"] += 1
                    asyncio.create_task(
                        manager.update_job(job_id, completed=progress["completed"])
                    )
                elif evt == "done":
                    count = event.get("count")
                    if isinstance(count, int) and count >= 0:
                        progress["completed"] = count
                    asyncio.create_task(
                        manager.update_job(job_id, completed=progress["completed"])
                    )

            for sc in scenarios:
                sc.setdefault("_source_path", str(Path(scenario_set.path)))
            runs = await run_scenarios_against_agent(
                compiled_path,
                scenarios,
                max_turns=payload.max_turns,
                include_history=payload.include_history,
                workflow_name=workflow_name,
                progress_cb=_progress_cb,
                scenario_source_path=Path(scenario_set.path),
                eval_config=eval_cfg,
            )

            # Resolve "use entrypoint" (no agent override) to the actual
            # entrypoint key so the historical record is unambiguous.
            resolved_agent = (payload.agent or "").strip() or None
            if resolved_agent is None:
                resolved_agent = (
                    getattr(app.state, "playground_resources", {}) or {}
                ).get("entry_agent_key")

            # Save results using structured storage
            run_metadata = {
                "id": run_id,
                "created_at": _utc_now(),
                "finished_at": _utc_now(),
                "workflow_name": workflow_name,
                "source_path": str(Path(scenario_set.path).resolve()),
                "agent": resolved_agent,
                "filters": {
                    "select": payload.select,
                    "exclude": payload.exclude,
                },
                "summary": {
                    "total_scenarios": len(runs),
                    "passed": sum(
                        1
                        for r in runs
                        if all(m.get("passed", False) for m in r.get("measures", []))
                    ),
                    "failed": sum(
                        1
                        for r in runs
                        if not all(
                            m.get("passed", False) for m in r.get("measures", [])
                        )
                    ),
                },
            }
            results_path = save_run_results(results_dir, run_id, runs, run_metadata)

            # Update runs index
            update_runs_index(
                results_dir,
                {
                    "id": run_id,
                    "timestamp": run_metadata["created_at"],
                    "path": str(results_path),
                    "workflow_name": workflow_name,
                    "agent": resolved_agent,
                    "summary": run_metadata["summary"],
                    "source": str(Path(scenario_set.path).resolve().parent.name),
                },
            )

            # Update manifest with workflow info
            manifest_file = Path(scenario_set.path).resolve().parent / "manifest.json"
            if workflow_name:
                try:
                    if manifest_file.exists():
                        data = (
                            json.loads(manifest_file.read_text(encoding="utf-8")) or {}
                        )
                    else:
                        data = {}
                    data["workflow_run"] = workflow_name
                    data["workflow_run_at"] = _utc_now()
                    manifest_file.parent.mkdir(parents=True, exist_ok=True)
                    manifest_file.write_text(
                        json.dumps(data, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass

            await manager.update_job(
                job_id,
                state="completed",
                completed=len(runs),
                total=progress["total"],
                finished_at=_utc_now(),
                runs_path=str(results_path),
                workflow_name=workflow_name,
            )
        except Exception as exc:
            logger.exception("Scenario run %s failed", job_id)
            await manager.update_job(
                job_id, state="failed", error=str(exc), finished_at=_utc_now()
            )

    def _resolve_runs_output_path(
        manifest_path: Path, scenario_path: Path, override: Optional[str]
    ) -> Tuple[Path, str]:
        """Returns (results_directory, run_id) for storing run results."""
        run_id = generate_run_id()
        base_dir = manifest_path.parent.resolve()

        if override:
            candidate = Path(override)
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            # If override is a file path, use its parent
            if candidate.suffix == ".json":
                results_dir = candidate.parent / "results"
            else:
                results_dir = candidate / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            return (results_dir, run_id)

        return (get_results_directory(scenario_path), run_id)

    @app.post("/api/evaluation/suite/run")
    async def start_evaluation_suite_run(payload: EvaluationSuiteRunRequest):
        try:
            name_value = (payload.name or "").strip()
            payload.name = name_value or None
            select_value = (payload.select or "").strip()
            payload.select = select_value or None
            exclude_value = (payload.exclude or "").strip()
            payload.exclude = exclude_value or None
            output_value = (payload.output_path or "").strip()
            payload.output_path = output_value or None
            agent_value = (payload.agent or "").strip()
            payload.agent = agent_value or None
            manifest_path = _get_manifest_path()
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            manager: SuiteRunManager = app.state.suite_run_manager
            job = await manager.create_job(project_name, payload)
            task = asyncio.create_task(
                _run_suite_job(job.job_id, payload, manifest_path)
            )
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = EvaluationSuiteRunStatusModel(**status) if status else None
            response = EvaluationSuiteRunResponse(
                job_id=job.job_id, status=status_model
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/suite/run/{job_id}")
    async def get_evaluation_suite_run(job_id: str):
        manager: SuiteRunManager = app.state.suite_run_manager
        status = await manager.get_status(job_id)
        if not status:
            raise HTTPException(
                status_code=404, detail="Evaluation suite run not found"
            )
        status_model = EvaluationSuiteRunStatusModel(**status)
        response = EvaluationSuiteRunResponse(job_id=job_id, status=status_model)
        return response.model_dump()

    @app.post("/api/evaluation/scenarios/run")
    async def run_evaluation_scenarios(payload: ScenarioRunRequest):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            entries = _read_scenarios_file(file_path)
            if not entries:
                raise HTTPException(status_code=422, detail="Scenario set is empty")
            scenarios = _ensure_scenario_ids(entries)
            # Apply select/exclude filter before running
            if payload.select or payload.exclude:
                scenarios = filter_scenarios(scenarios, payload.select, payload.exclude)
            if not scenarios:
                raise HTTPException(
                    status_code=422,
                    detail="No scenarios match the select/exclude filter",
                )
            for sc in scenarios:
                sc["_source_path"] = str(file_path)
            results_dir, run_id = _resolve_runs_output_path(
                manifest_path, file_path, payload.output_path
            )
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            manager: ScenarioRunManager = app.state.scenario_run_manager
            job = await manager.create_job(project_name, payload, len(scenarios))
            task = asyncio.create_task(
                _run_scenario_job(
                    job.job_id,
                    payload,
                    manifest_path,
                    scenario_set,
                    scenarios,
                    results_dir,
                    run_id,
                )
            )
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = ScenarioRunStatusModel(**status) if status else None
            return {
                "job_id": job.job_id,
                "status": status_model.model_dump() if status_model else None,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/scenarios/run/{job_id}")
    async def get_evaluation_scenario_run(job_id: str):
        manager: ScenarioRunManager = app.state.scenario_run_manager
        status = await manager.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Scenario run not found")
        status_model = ScenarioRunStatusModel(**status)
        return {"job_id": job_id, "status": status_model.model_dump()}

    @app.post("/api/evaluation/scenarios/item")
    async def create_evaluation_scenario(payload: ScenarioCreatePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            entries = _read_scenarios_file(file_path)
            scenario_data = payload.scenario.model_dump(exclude_unset=True)
            name_value = str(scenario_data.get("name", "")).strip()
            if not name_value:
                raise HTTPException(status_code=422, detail="Scenario name is required")
            prompt_value = str(scenario_data.get("prompt", "")).strip()
            if not prompt_value:
                raise HTTPException(
                    status_code=422, detail="Scenario prompt is required"
                )
            new_entry: Dict[str, Any] = {"name": name_value, "prompt": prompt_value}
            scenario_id_value = None
            if "id" in scenario_data:
                scenario_id_value = _scenario_id_value(scenario_data.get("id"))
                if scenario_id_value:
                    new_entry["id"] = scenario_id_value
            if "dimensions" in scenario_data:
                dims_value = scenario_data.get("dimensions")
                if dims_value is None:
                    pass
                elif isinstance(dims_value, dict):
                    new_entry["dimensions"] = dims_value
                else:
                    raise HTTPException(
                        status_code=422, detail="Scenario dimensions must be an object"
                    )
            if scenario_id_value:
                for entry in entries:
                    entry_id_value = _scenario_id_value(entry.get("id"))
                    if entry_id_value == scenario_id_value:
                        raise HTTPException(
                            status_code=409,
                            detail="Scenario id already exists in this set",
                        )
            else:
                for entry in entries:
                    entry_id_value = _scenario_id_value(entry.get("id"))
                    if entry_id_value is None and entry.get("name") == name_value:
                        raise HTTPException(
                            status_code=409,
                            detail="Scenario name already exists in this set",
                        )
            entries.append(new_entry)
            _write_scenarios_file(file_path, entries)
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            updated_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            return {"project": project_name, "set": updated_set.model_dump()}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.put("/api/evaluation/scenarios/item")
    async def update_evaluation_scenario(payload: ScenarioUpdatePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            entries = _read_scenarios_file(file_path)
            located = _locate_scenario_entry(
                entries, payload.scenario_id, payload.scenario_name, payload.index
            )
            if not located:
                raise HTTPException(
                    status_code=404, detail="Scenario not found in target set"
                )
            idx, current_entry = located
            scenario_data = payload.scenario.model_dump(exclude_unset=True)
            name_value = str(scenario_data.get("name", "")).strip()
            if not name_value:
                raise HTTPException(status_code=422, detail="Scenario name is required")
            prompt_value = str(scenario_data.get("prompt", "")).strip()
            if not prompt_value:
                raise HTTPException(
                    status_code=422, detail="Scenario prompt is required"
                )
            updated_entry = dict(current_entry)
            updated_entry["name"] = name_value
            updated_entry["prompt"] = prompt_value
            if "id" in scenario_data:
                scenario_id_value = _scenario_id_value(scenario_data.get("id"))
                if scenario_id_value:
                    updated_entry["id"] = scenario_id_value
                else:
                    updated_entry.pop("id", None)
            new_id_value = _scenario_id_value(updated_entry.get("id"))
            if "dimensions" in scenario_data:
                dims_value = scenario_data.get("dimensions")
                if dims_value is None:
                    updated_entry.pop("dimensions", None)
                elif isinstance(dims_value, dict):
                    updated_entry["dimensions"] = dims_value
                else:
                    raise HTTPException(
                        status_code=422, detail="Scenario dimensions must be an object"
                    )
            for entry_index, entry in enumerate(entries):
                if entry_index == idx:
                    continue
                other_id = _scenario_id_value(entry.get("id"))
                if new_id_value and other_id == new_id_value:
                    raise HTTPException(
                        status_code=409, detail="Scenario id already exists in this set"
                    )
                if (
                    not new_id_value
                    and other_id is None
                    and entry.get("name") == name_value
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Scenario name already exists in this set",
                    )
            entries[idx] = updated_entry
            _write_scenarios_file(file_path, entries)
            resources = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            updated_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            return {"project": project_name, "set": updated_set.model_dump()}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/evaluation/scenarios/item")
    async def delete_evaluation_scenario(payload: ScenarioDeletePayload):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            target_path = Path(payload.set_path).expanduser().resolve(strict=False)
            scenario_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            file_path = Path(scenario_set.path)
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Scenario file not found")
            entries = _read_scenarios_file(file_path)
            located = _locate_scenario_entry(
                entries, payload.scenario_id, payload.scenario_name, payload.index
            )
            if not located:
                raise HTTPException(
                    status_code=404, detail="Scenario not found in target set"
                )
            idx, _ = located
            entries.pop(idx)
            _write_scenarios_file(file_path, entries)
            resources = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            updated_set = _get_scenario_set_by_path(ctx, payload.kind, target_path)
            return {"project": project_name, "set": updated_set.model_dump()}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/playground/metadata")
    async def get_playground_metadata(request: Request):
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        base_spec: AgentSpec = resources.get("base_spec") if resources else None
        if not base_spec:
            return {"available": False}
        entry_key = resources.get("entry_agent_key")
        return _build_metadata_response(
            base_spec, resources, request, agent_key=entry_key
        )

    @app.post("/api/agents")
    async def create_agent(payload: AgentCreatePayload, request: Request):
        name_value = payload.name.strip() if isinstance(payload.name, str) else ""
        if not name_value:
            raise HTTPException(status_code=422, detail="Agent name is required")
        model_value = payload.model.strip() if isinstance(payload.model, str) else None
        manifest_path = _get_manifest_path()
        try:
            _, _, sanitized = scaffold_agent(
                str(manifest_path), name_value, model=model_value or "gpt-4.1"
            )
        except FileExistsError:
            raise HTTPException(status_code=409, detail="Agent already exists")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        refreshed_ctx = load_project(str(manifest_path))
        updated_resources, agent_specs = _reload_playground_resources(
            refreshed_ctx, manifest_path
        )
        spec = agent_specs.get(sanitized)
        if not spec:
            raise HTTPException(
                status_code=500, detail="Agent metadata unavailable after creation"
            )
        entry_key = updated_resources.get("entry_agent_key")
        summary = {
            "key": sanitized,
            "name": spec.name,
            "label": spec.label or spec.name or sanitized,
            "description": spec.description or "",
            "model": spec.model_name or "",
            "is_entry": sanitized == entry_key,
        }
        metadata = _build_metadata_response(
            spec, updated_resources, request, agent_key=sanitized
        )
        return {"agent": summary, "metadata": metadata}

    @app.get("/api/agents")
    async def list_agents():
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        specs: Dict[str, AgentSpec] = resources.get("agent_specs") or {}
        if not specs:
            return {"agents": []}
        entry_key = resources.get("entry_agent_key")
        agents_payload: List[Dict[str, Any]] = []
        for key, spec in specs.items():
            agents_payload.append(
                {
                    "key": key,
                    "name": spec.name,
                    "label": spec.label or spec.name or key,
                    "description": spec.description or "",
                    "model": spec.model_name or "",
                    "is_entry": key == entry_key,
                }
            )

        def _sort_key(item: Dict[str, Any]) -> tuple:
            is_entry = 0 if item.get("is_entry") else 1
            label_key = (item.get("label") or item.get("key") or "").lower()
            return (is_entry, label_key)

        agents_payload.sort(key=_sort_key)
        return {"agents": agents_payload}

    @app.get("/api/agents/{agent_key}/metadata")
    async def get_agent_metadata(agent_key: str, request: Request):
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            raise HTTPException(
                status_code=404, detail=f"Agent '{agent_key}' not found"
            )
        return _build_metadata_response(spec, resources, request, agent_key=agent_key)

    @app.put("/api/agents/{agent_key}")
    async def update_agent(
        agent_key: str, payload: AgentUpdatePayload, request: Request
    ):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            agent_path = ctx.agents_map.get(agent_key)
            if not agent_path:
                raise HTTPException(
                    status_code=404, detail=f"Agent '{agent_key}' not found"
                )
            try:
                raw_config = (
                    yaml.safe_load(agent_path.read_text(encoding="utf-8")) or {}
                )
            except Exception:
                raw_config = {}
            if not isinstance(raw_config, dict):
                raw_config = {}
            model_value = str(payload.model or "").strip()
            if not model_value:
                raise HTTPException(status_code=422, detail="Model name is required")
            raw_config["model"] = model_value
            instructions_text = payload.instructions or ""
            instructions_file = raw_config.get("instructions_file")
            if isinstance(instructions_file, str) and instructions_file.strip():
                instructions_path = Path(instructions_file)
                if not instructions_path.is_absolute():
                    instructions_path = (
                        agent_path.parent / instructions_path
                    ).resolve()
                instructions_path.parent.mkdir(parents=True, exist_ok=True)
                instructions_path.write_text(instructions_text, encoding="utf-8")
            else:
                if instructions_text:
                    raw_config["instructions"] = instructions_text
                else:
                    raw_config.pop("instructions", None)
            seen_tools: Set[str] = set()
            tools_list: List[str] = []
            for tool_name in payload.tools or []:
                if not isinstance(tool_name, str):
                    continue
                name = tool_name.strip()
                if not name or name in seen_tools:
                    continue
                seen_tools.add(name)
                tools_list.append(name)
            raw_config["tools"] = tools_list
            if payload.model_settings:
                filtered_settings: Dict[str, Any] = {}
                for key, value in payload.model_settings.items():
                    if value is None:
                        continue
                    key_str = str(key).strip()
                    if not key_str:
                        continue
                    filtered_settings[key_str] = value
                if filtered_settings:
                    raw_config["model_settings"] = filtered_settings
                else:
                    raw_config.pop("model_settings", None)
            else:
                raw_config.pop("model_settings", None)
            raw_config["use_safe_agent"] = bool(payload.use_safe_agent)
            safe_options_input = payload.safe_agent_options or {}
            sanitized_safe_options: Dict[str, Any] = {}
            sanitized_safe_options["skip_approvals"] = bool(
                safe_options_input.get("skip_approvals", False)
            )
            sanitized_safe_options["halt_on_rejection"] = bool(
                safe_options_input.get("halt_on_rejection", True)
            )
            tool_set = set(tools_list)
            names_raw = (
                safe_options_input.get("safe_tool_names")
                if isinstance(safe_options_input.get("safe_tool_names"), list)
                else []
            )
            safe_tool_names: List[str] = []
            seen_safe_names: Set[str] = set()
            for name in names_raw:
                if not isinstance(name, str):
                    continue
                trimmed = name.strip()
                if not trimmed or trimmed not in tool_set or trimmed in seen_safe_names:
                    continue
                seen_safe_names.add(trimmed)
                safe_tool_names.append(trimmed)
            sanitized_safe_options["safe_tool_names"] = safe_tool_names
            patterns_raw = (
                safe_options_input.get("safe_tool_patterns")
                if isinstance(safe_options_input.get("safe_tool_patterns"), list)
                else []
            )
            patterns_clean: List[str] = []
            for pattern in patterns_raw:
                if not isinstance(pattern, str):
                    continue
                trimmed = pattern.strip()
                if trimmed and trimmed not in patterns_clean:
                    patterns_clean.append(trimmed)
            sanitized_safe_options["safe_tool_patterns"] = patterns_clean
            if (
                payload.use_safe_agent
                or sanitized_safe_options["safe_tool_names"]
                or sanitized_safe_options["safe_tool_patterns"]
                or sanitized_safe_options["skip_approvals"]
                or not sanitized_safe_options["halt_on_rejection"]
            ):
                raw_config["safe_agent_options"] = sanitized_safe_options
            else:
                raw_config.pop("safe_agent_options", None)
            guardrails_input = payload.guardrails or {}
            input_guardrails = (
                guardrails_input.get("input")
                if isinstance(guardrails_input.get("input"), list)
                else []
            )
            output_guardrails = (
                guardrails_input.get("output")
                if isinstance(guardrails_input.get("output"), list)
                else []
            )
            input_values: List[str] = []
            seen_input: Set[str] = set()
            for item in input_guardrails:
                if not isinstance(item, str):
                    continue
                trimmed = item.strip()
                if not trimmed or trimmed in seen_input:
                    continue
                seen_input.add(trimmed)
                input_values.append(trimmed)
            output_values: List[str] = []
            seen_output: Set[str] = set()
            for item in output_guardrails:
                if not isinstance(item, str):
                    continue
                trimmed = item.strip()
                if not trimmed or trimmed in seen_output:
                    continue
                seen_output.add(trimmed)
                output_values.append(trimmed)
            raw_config["input_guardrails"] = input_values
            raw_config["output_guardrails"] = output_values
            output_type_value = payload.output_type
            if output_type_value is None or output_type_value == "default":
                raw_config.pop("output_type", None)
                raw_config.pop("output_type_strict", None)
                raw_config.pop("output_schema", None)
            elif output_type_value == "none":
                raw_config["output_type"] = None
                raw_config.pop("output_schema", None)
                raw_config.pop("output_type_strict", None)
            else:
                raw_config["output_type"] = output_type_value
                if payload.output_type_strict is not None:
                    raw_config["output_type_strict"] = bool(payload.output_type_strict)
                else:
                    raw_config.pop("output_type_strict", None)
                if (
                    output_type_value in {"dict", "dictionary"}
                    and payload.output_schema
                ):
                    raw_config["output_schema"] = payload.output_schema
                else:
                    raw_config.pop("output_schema", None)
            _write_yaml_file(agent_path, raw_config)
            refreshed_ctx = load_project(str(manifest_path))
            updated_resources, agent_specs = _reload_playground_resources(
                refreshed_ctx, manifest_path
            )
            spec = agent_specs.get(agent_key)
            if not spec:
                raise HTTPException(
                    status_code=500, detail="Agent metadata unavailable after update"
                )
            return _build_metadata_response(
                spec, updated_resources, request, agent_key=agent_key
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/agents/{agent_key}")
    async def remove_agent(agent_key: str):
        manifest_path = _get_manifest_path()
        try:
            delete_agent(str(manifest_path), agent_key)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Agent '{agent_key}' not found"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        refreshed_ctx = load_project(str(manifest_path))
        _reload_playground_resources(refreshed_ctx, manifest_path)
        return {"deleted": agent_key}

    async def _configure_session(
        agent_key: Optional[str], payload: PlaygroundSessionRequest
    ) -> Dict[str, str]:
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            if agent_key:
                raise HTTPException(
                    status_code=404, detail=f"Agent '{agent_key}' not found"
                )
            raise HTTPException(status_code=400, detail="Playground is not available")

        payload_data = payload.model_dump(exclude_unset=True)

        model_name = (payload.model or "").strip()
        if not model_name:
            raise HTTPException(status_code=422, detail="Model name is required")

        tool_registry: Dict[str, Any] = resources.get("tool_registry", {})
        guardrails_registry: Dict[str, Any] = resources.get(
            "guardrails_registry", {"input": {}, "output": {}}
        )
        tools_value = payload_data.get("tools")
        unknown_tools = [
            name for name in (tools_value or []) if name not in tool_registry
        ]
        if unknown_tools:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown tools: {', '.join(sorted(unknown_tools))}",
            )

        store: PlaygroundSessionStore = app.state.playground_session_store
        config: Dict[str, Any] = {"model": model_name}

        if "instructions" in payload_data:
            config["instructions"] = str(payload_data.get("instructions") or "")

        if tools_value is not None:
            config["tools"] = tools_value or []

        if isinstance(payload.model_settings, dict):
            sanitized_settings = {
                k: v for k, v in payload.model_settings.items() if v not in (None, "")
            }
            if sanitized_settings:
                config["model_settings"] = sanitized_settings

        if payload.use_safe_agent is not None:
            config["use_safe_agent"] = bool(payload.use_safe_agent)

        if (
            isinstance(payload.safe_agent_options, dict)
            and "safe_agent_options" in payload_data
        ):
            safe_options: Dict[str, Any] = {}
            for key in ("skip_approvals", "halt_on_rejection"):
                if key in payload.safe_agent_options:
                    safe_options[key] = bool(payload.safe_agent_options[key])
            if "safe_tool_names" in payload.safe_agent_options and isinstance(
                payload.safe_agent_options["safe_tool_names"], list
            ):
                safe_options["safe_tool_names"] = [
                    name
                    for name in payload.safe_agent_options["safe_tool_names"]
                    if isinstance(name, str) and name in tool_registry
                ]
            if "safe_tool_patterns" in payload.safe_agent_options and isinstance(
                payload.safe_agent_options["safe_tool_patterns"], list
            ):
                safe_options["safe_tool_patterns"] = [
                    pattern
                    for pattern in payload.safe_agent_options["safe_tool_patterns"]
                    if isinstance(pattern, str) and pattern
                ]
            config["safe_agent_options"] = safe_options

        if isinstance(payload.guardrails, dict) and "guardrails" in payload_data:
            guardrails_config: Dict[str, List[str]] = {}
            input_registry = guardrails_registry.get("input", {})
            output_registry = guardrails_registry.get("output", {})
            input_names = payload.guardrails.get("input")
            if isinstance(input_names, list):
                guardrails_config["input"] = [
                    name
                    for name in input_names
                    if isinstance(name, str) and name in input_registry
                ]
            output_names = payload.guardrails.get("output")
            if isinstance(output_names, list):
                guardrails_config["output"] = [
                    name
                    for name in output_names
                    if isinstance(name, str) and name in output_registry
                ]
            if guardrails_config:
                config["guardrails"] = guardrails_config

        normalized_for_schema: Optional[str] = None
        if "output_type" in payload_data:
            raw_output_type = payload_data.get("output_type")
            if raw_output_type is not None:
                normalized = _resolve_output_type_override(str(raw_output_type))
                if normalized is None:
                    config.pop("output_type", None)
                elif normalized in STRUCTURED_OUTPUT_TYPE_MAP:
                    config["output_type"] = normalized
                    normalized_for_schema = normalized
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported output_type '{raw_output_type}'",
                    )
        if "output_type_strict" in payload_data:
            raw_strict = payload_data.get("output_type_strict")
            if raw_strict is not None:
                config["output_type_strict"] = bool(raw_strict)

        if "output_schema" in payload_data:
            raw_schema = payload_data.get("output_schema")
            if raw_schema is not None:
                if not isinstance(raw_schema, dict):
                    raise HTTPException(
                        status_code=400, detail="output_schema must be a JSON object"
                    )
                if normalized_for_schema not in (None, "dict", "dictionary", "object"):
                    raise HTTPException(
                        status_code=400,
                        detail="output_schema requires output_type 'dict'",
                    )
                config["output_schema"] = raw_schema

        session_label = "playground" if not agent_key else f"agent:{agent_key}"
        config["__agent_key"] = agent_key
        config["__session_label"] = session_label

        session_id = await store.upsert(payload.session_id, config)
        return {"session_id": session_id}

    @app.post("/api/playground/session")
    async def configure_playground_session(payload: PlaygroundSessionRequest):
        return await _configure_session(None, payload)

    @app.post("/api/agents/{agent_key}/session")
    async def configure_agent_session(
        agent_key: str, payload: PlaygroundSessionRequest
    ):
        return await _configure_session(agent_key, payload)

    @app.get("/api/conversations")
    async def get_conversations(
        project: str = Query(..., description="Project name"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: Optional[str] = None,
        has_errors: Optional[bool] = None,
        workflow_name: Optional[str] = None,
        agent: Optional[str] = Query(
            None,
            description="Filter to conversations whose session lives under this agent slug",
        ),
        dim: Optional[List[str]] = Query(
            None, description="Synthetic dimension filters as 'dimension:value'"
        ),
    ):
        """Get conversations (grouped traces) with filtering and pagination.

        Aggregates per group_id in a single SQL query rather than scanning
        every trace's spans in Python (which used to load the entire DB
        on every sidebar refresh).
        """
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"conversations": [], "total": 0}

            dim_map: Dict[str, set] = {}
            if dim:
                for entry in dim:
                    try:
                        k, v = entry.split(":", 1)
                        dim_map.setdefault(k, set()).add(v)
                    except ValueError:
                        continue

            base_sql = """
                WITH ranked AS (
                  SELECT t.trace_id, t.workflow_name, t.metadata, t.created_at,
                         COALESCE(t.group_id, t.trace_id) AS gkey,
                         ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(t.group_id, t.trace_id)
                           ORDER BY t.created_at DESC
                         ) AS rn_desc,
                         ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(t.group_id, t.trace_id)
                           ORDER BY t.created_at ASC
                         ) AS rn_asc
                  FROM traces t
                ),
                groups AS (
                  SELECT gkey AS group_id,
                         COUNT(*) AS trace_count,
                         MAX(created_at) AS created_at_raw,
                         MAX(CASE WHEN rn_desc = 1 THEN workflow_name END) AS workflow_name,
                         MAX(CASE WHEN rn_desc = 1 THEN metadata END)      AS metadata,
                         MAX(CASE WHEN rn_asc  = 1 THEN trace_id END)      AS first_trace_id
                  FROM ranked
                  GROUP BY gkey
                ),
                err_groups AS (
                  SELECT DISTINCT COALESCE(t.group_id, t.trace_id) AS gkey
                  FROM traces t
                  JOIN spans s ON s.trace_id = t.trace_id
                  WHERE s.error IS NOT NULL
                ),
                joined AS (
                  SELECT g.group_id, g.trace_count, g.created_at_raw,
                         g.workflow_name, g.metadata, g.first_trace_id,
                         CASE WHEN err.gkey IS NOT NULL THEN 1 ELSE 0 END AS has_errors
                  FROM groups g
                  LEFT JOIN err_groups err ON err.gkey = g.group_id
                )
            """

            filters: List[str] = []
            params: List[Any] = []
            if workflow_name:
                filters.append("LOWER(workflow_name) = LOWER(?)")
                params.append(workflow_name)
            if search:
                filters.append(
                    "(LOWER(workflow_name) LIKE LOWER(?) OR LOWER(group_id) LIKE LOWER(?))"
                )
                like = f"%{search}%"
                params.extend([like, like])
            if has_errors is not None:
                filters.append("has_errors = ?")
                params.append(1 if has_errors else 0)
            if agent:
                # Traces don't carry the agent slug, so resolve session ids
                # from the agent's sessions.db and filter by group_id (which
                # bridge.py sets to "session_{session.id}"). Capped to keep
                # the IN clause within SQLite's host-parameter limit.
                gids = _resolve_agent_group_ids(project, agent, max_sessions=10000)
                if not gids:
                    return {"conversations": [], "total": 0}
                placeholders = ",".join("?" for _ in gids)
                filters.append(f"group_id IN ({placeholders})")
                params.extend(gids)
            where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""

            # Without dim filter we can paginate in SQL and get total via window function.
            # With dim filter we need to inspect per-trace metadata, so we materialize
            # the filtered groups in Python and paginate there.
            if not dim_map:
                page_sql = base_sql + f"""
                    SELECT group_id, trace_count,
                           datetime(created_at_raw) || 'Z' AS created_at,
                           workflow_name, metadata, first_trace_id, has_errors,
                           COUNT(*) OVER () AS total_rows
                    FROM joined
                    {where_sql}
                    ORDER BY created_at_raw DESC
                    LIMIT ? OFFSET ?
                    """
                rows = list(storage.conn.execute(page_sql, [*params, limit, offset]))
                total = rows[0]["total_rows"] if rows else 0
            else:
                all_sql = base_sql + f"""
                    SELECT group_id, trace_count,
                           datetime(created_at_raw) || 'Z' AS created_at,
                           workflow_name, metadata, first_trace_id, has_errors
                    FROM joined
                    {where_sql}
                    ORDER BY created_at_raw DESC
                    """
                all_rows = list(storage.conn.execute(all_sql, params))
                matching_ids = _filter_groups_by_dim(
                    storage.conn, [r["group_id"] for r in all_rows], dim_map
                )
                rows = [r for r in all_rows if r["group_id"] in matching_ids]
                total = len(rows)
                rows = rows[offset : offset + limit]

            conversations = []
            for row in rows:
                md = row["metadata"]
                if isinstance(md, str):
                    try:
                        md = json.loads(md) or {}
                    except Exception:
                        md = {}
                elif md is None:
                    md = {}

                conversations.append(
                    {
                        "group_id": row["group_id"],
                        "trace_count": row["trace_count"],
                        "has_errors": bool(row["has_errors"]),
                        "created_at": row["created_at"],
                        "workflow_name": row["workflow_name"],
                        "metadata": md,
                        "first_input": _fetch_first_user_input(
                            storage.conn, row["first_trace_id"]
                        ),
                    }
                )

            # Attach session metadata for conversations whose group_id was
            # produced by the runtime (group_id="session_{session.id}" — see
            # core/runtime/bridge.py). This is the only join key between the
            # trace store and per-(project,agent) sessions.db.
            session_id_by_gid: Dict[str, str] = {}
            for conv in conversations:
                gid = conv.get("group_id")
                if isinstance(gid, str) and gid.startswith("session_"):
                    session_id_by_gid[gid] = gid[len("session_") :]
            if session_id_by_gid:
                session_meta = _load_session_metadata_for_project(
                    project, list(session_id_by_gid.values())
                )
                for conv in conversations:
                    sid = session_id_by_gid.get(conv.get("group_id"))
                    if sid is None:
                        continue
                    meta = session_meta.get(sid)
                    if meta is not None:
                        conv["session"] = meta

            return {"conversations": conversations, "total": total}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/synthetic_dimensions")
    async def get_synthetic_dimensions(
        project: str = Query(..., description="Project name"),
        workflow_name: Optional[str] = None,
    ):
        """Get distinct synthetic dimension definitions for a project/workflow"""
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"dimensions": []}
            sql = """
                SELECT metadata FROM traces
                WHERE json_extract(metadata, '$.omniagents.synthetic.catalog.dimensions') IS NOT NULL
            """
            params: List[Any] = []
            if workflow_name:
                sql += " AND LOWER(workflow_name) = LOWER(?)"
                params.append(workflow_name)
            traces = list(storage.conn.execute(sql, params))
            dims_map: Dict[str, Dict[str, Any]] = {}

            def display_name(s: str) -> str:
                return s.replace("_", " ").title()

            for t in traces:
                md = t["metadata"]
                try:
                    meta = json.loads(md) if isinstance(md, str) else (md or {})
                except Exception:
                    meta = {}
                dims = (
                    meta.get("omniagents", {})
                    .get("synthetic", {})
                    .get("catalog", {})
                    .get("dimensions", [])
                )
                for d in dims or []:
                    name = d.get("name")
                    if not name:
                        continue
                    label = d.get("label") or display_name(name)
                    if name not in dims_map:
                        dims_map[name] = {"label": label, "values": {}}
                    for v in d.get("values", []) or []:
                        vname = v.get("name")
                        if not vname:
                            continue
                        vlabel = v.get("label") or display_name(vname)
                        dims_map[name]["values"][vname] = vlabel
            dimensions = []
            for name, defn in dims_map.items():
                values = [
                    {"name": vn, "label": vl}
                    for vn, vl in sorted(defn["values"].items())
                ]
                dimensions.append(
                    {"name": name, "label": defn["label"], "values": values}
                )
            dimensions.sort(key=lambda d: d["label"].lower())
            return {"dimensions": dimensions}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/workflows")
    async def get_workflows(project: str = Query(..., description="Project name")):
        """Get distinct workflow names for a project"""
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"workflows": []}
            names = storage.get_workflow_names()
            return {"workflows": names}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/project_agents")
    async def get_project_agents(
        project: str = Query(..., description="Project name"),
    ):
        """List agents that have produced sessions referenced by this trace
        project.

        Source-of-truth for the agent-filter dropdown on Logs. We list what
        actually produced sessions on disk (with discovery falling back to
        scanning when trace and session project names diverge) rather than
        what's currently configured in ``/api/agents``, so historical
        conversations from removed or renamed agents remain reachable.

        Same agent_slug appearing under multiple session-project dirs is
        merged into one row with summed session_count.
        """
        per_slug: Dict[str, Dict[str, Any]] = {}
        for db_path, slug in _list_project_agent_dirs(project):
            count: Optional[int] = None
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            except sqlite3.OperationalError:
                conn = None
            if conn is not None:
                try:
                    row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                    count = int(row[0]) if row and row[0] is not None else 0
                except sqlite3.Error:
                    count = None
                finally:
                    conn.close()
            existing = per_slug.get(slug)
            if existing is None:
                per_slug[slug] = {"agent_slug": slug, "session_count": count}
            else:
                if count is not None:
                    existing["session_count"] = (
                        existing.get("session_count") or 0
                    ) + count
        return {
            "agents": sorted(per_slug.values(), key=lambda a: a["agent_slug"].lower())
        }

    @app.get("/api/conversations/{project}/{group_id}/traces")
    async def get_conversation_traces(
        project: str,
        group_id: str,
        include_preview: bool = False,
    ):
        """Get all traces in a conversation.

        ``include_preview`` is opt-in because extracting the input/output text
        requires reading full ``response``/``generation`` span ``data`` blobs,
        which can be hundreds of KB each. The trace list (debug mode) needs it;
        the auto-select-latest path in review mode does not.
        """
        try:
            storage = storage_manager.get_storage(project)

            rows = list(
                storage.conn.execute(
                    """
                    SELECT t.trace_id, t.workflow_name, t.group_id, t.metadata,
                           datetime(t.created_at) || 'Z' AS created_at,
                           COUNT(s.span_id)                                          AS span_count,
                           MAX(CASE WHEN s.error IS NOT NULL THEN 1 ELSE 0 END)      AS has_errors,
                           MIN(s.started_at)                                         AS min_started,
                           MAX(s.ended_at)                                           AS max_ended
                    FROM traces t
                    LEFT JOIN spans s ON s.trace_id = t.trace_id
                    WHERE t.group_id = ?
                       OR (t.group_id IS NULL AND t.trace_id = ?)
                    GROUP BY t.trace_id
                    ORDER BY t.created_at
                    """,
                    (group_id, group_id),
                )
            )

            traces: list = []
            for row in rows:
                trace = dict(row)

                md = trace.get("metadata")
                if isinstance(md, str):
                    try:
                        md = json.loads(md) or {}
                    except Exception:
                        md = {}
                elif md is None:
                    md = {}
                trace["metadata"] = md

                trace["has_errors"] = bool(trace.get("has_errors"))

                min_s = trace.pop("min_started", None)
                max_e = trace.pop("max_ended", None)
                if min_s and max_e:
                    try:
                        start = datetime.fromisoformat(min_s)
                        end = datetime.fromisoformat(max_e)
                        trace["duration"] = (end - start).total_seconds() * 1000
                    except Exception:
                        trace["duration"] = 0
                else:
                    trace["duration"] = 0

                traces.append(trace)

            if include_preview:
                for trace in traces:
                    spans = list(
                        storage.conn.execute(
                            """
                            SELECT span_type, data, sequence_number
                            FROM spans
                            WHERE trace_id = ?
                              AND span_type IN ('response', 'generation')
                            ORDER BY sequence_number
                            """,
                            (trace["trace_id"],),
                        )
                    )
                    trace_input, trace_output = _extract_trace_io_preview(spans)
                    if trace_input:
                        trace["metadata"]["input"] = trace_input
                    if trace_output:
                        trace["metadata"]["output"] = trace_output

            return {
                "traces": traces,
                "group_id": group_id,
                "total": len(traces),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/traces")
    async def get_traces(
        project: str = Query(..., description="Project name"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: Optional[str] = None,
        has_errors: Optional[bool] = None,
        span_type: Optional[str] = None,
    ):
        """Get traces with filtering and pagination"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                # Project doesn't exist
                return {"traces": [], "total": 0}

            # Get traces from the storage
            traces = storage.get_traces(limit=limit + offset)

            # Apply filters
            filtered_traces = traces

            if search:
                filtered_traces = [
                    t
                    for t in filtered_traces
                    if search.lower() in (t.get("workflow_name") or "").lower()
                ]

            if has_errors is not None:
                # Check if trace has any spans with errors
                traces_with_errors = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace["trace_id"])
                    if any(s.get("error") for s in spans):
                        traces_with_errors.add(trace["trace_id"])

                if has_errors:
                    filtered_traces = [
                        t
                        for t in filtered_traces
                        if t["trace_id"] in traces_with_errors
                    ]
                else:
                    filtered_traces = [
                        t
                        for t in filtered_traces
                        if t["trace_id"] not in traces_with_errors
                    ]

            if span_type:
                # Filter traces that contain specific span type
                traces_with_type = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace["trace_id"])
                    if any(s.get("span_type") == span_type for s in spans):
                        traces_with_type.add(trace["trace_id"])
                filtered_traces = [
                    t for t in filtered_traces if t["trace_id"] in traces_with_type
                ]

            # Apply pagination
            paginated = filtered_traces[offset : offset + limit]

            return {"traces": paginated, "total": len(filtered_traces)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/traces/{project}/{trace_id}")
    async def get_trace(project: str, trace_id: str):
        """Get a single trace with all its spans"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                raise HTTPException(status_code=404, detail="Project not found")

            # Get the specific trace by ID
            trace = storage.get_trace(trace_id)

            if not trace:
                raise HTTPException(status_code=404, detail="Trace not found")

            spans = storage.get_spans_for_trace(trace_id)

            # Parse span data and calculate durations
            processed_spans = []
            for span in spans:
                span_data = json.loads(span.get("data", "{}"))
                processed_span = {**span, "data": span_data}

                # Calculate duration if both timestamps exist
                if span.get("started_at") and span.get("ended_at"):
                    start = datetime.fromisoformat(span["started_at"])
                    end = datetime.fromisoformat(span["ended_at"])
                    processed_span["duration"] = (end - start).total_seconds() * 1000

                processed_spans.append(processed_span)

            # Build span hierarchy
            span_map = {s["span_id"]: s for s in processed_spans}
            for span in processed_spans:
                if span.get("parent_id") and span["parent_id"] in span_map:
                    parent = span_map[span["parent_id"]]
                    if "children" not in parent:
                        parent["children"] = []
                    parent["children"].append(span)

            return {"trace": trace, "spans": processed_spans}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/search")
    async def search(
        project: str = Query(..., description="Project name"),
        q: str = Query(..., min_length=1),
        type: str = Query("traces", pattern="^(traces|spans)$"),
    ):
        """Search traces or spans within a project"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"results": [], "total": 0}

            results = []

            if type == "traces":
                traces = storage.get_traces(limit=1000)
                results = [
                    t
                    for t in traces
                    if q.lower() in (t.get("workflow_name") or "").lower()
                    or q.lower() in t.get("trace_id", "").lower()
                ]
            else:
                # Search in spans
                traces = storage.get_traces(limit=1000)
                for trace in traces:
                    spans = storage.get_spans_for_trace(trace["trace_id"])
                    for span in spans:
                        span_data = json.loads(span.get("data", "{}"))
                        if (
                            q.lower() in span.get("span_name", "").lower()
                            or q.lower() in span.get("span_type", "").lower()
                            or q.lower() in json.dumps(span_data).lower()
                        ):
                            results.append(span)

            return {"results": results[:100], "total": len(results)}  # Limit results
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/stats/overview")
    async def get_stats(project: str = Query(..., description="Project name")):
        """Get statistics overview for a project"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {
                    "total_traces": 0,
                    "total_spans": 0,
                    "error_rate": 0,
                    "avg_duration": 0,
                    "spans_by_type": {},
                }

            traces = storage.get_traces(limit=10000)
            total_spans = 0
            error_count = 0
            durations = []
            spans_by_type = {}

            for trace in traces:
                spans = storage.get_spans_for_trace(trace["trace_id"])
                total_spans += len(spans)

                for span in spans:
                    # Count errors
                    if span.get("error"):
                        error_count += 1

                    # Count by type
                    span_type = span.get("span_type", "unknown")
                    spans_by_type[span_type] = spans_by_type.get(span_type, 0) + 1

                    # Calculate duration
                    if span.get("started_at") and span.get("ended_at"):
                        start = datetime.fromisoformat(span["started_at"])
                        end = datetime.fromisoformat(span["ended_at"])
                        duration = (end - start).total_seconds() * 1000
                        durations.append(duration)

            avg_duration = sum(durations) / len(durations) if durations else 0
            error_rate = (error_count / total_spans * 100) if total_spans > 0 else 0

            return {
                "total_traces": len(traces),
                "total_spans": total_spans,
                "error_rate": error_rate,
                "avg_duration": avg_duration,
                "spans_by_type": spans_by_type,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/analysis/{project}")
    async def get_all_analysis(project: str, workflow_name: Optional[str] = None):
        """Get all analysis data for a project, optionally filtered by workflow name"""
        try:
            storage = storage_manager.get_storage(project)
            analysis_data = storage.get_all_analysis(project)

            if workflow_name:
                rows = storage.conn.execute(
                    """
                    SELECT DISTINCT COALESCE(group_id, trace_id) AS gkey
                    FROM traces
                    WHERE LOWER(workflow_name) = LOWER(?)
                    """,
                    (workflow_name,),
                )
                allowed_group_ids = {r["gkey"] for r in rows}
                analysis_data = [
                    a for a in analysis_data if a.get("group_id") in allowed_group_ids
                ]

            # Convert to frontend format
            result = {}
            for item in analysis_data:
                result[item["group_id"]] = {
                    "markedForAnalysis": bool(item["marked_for_analysis"]),
                    "judgment": item["judgment"],
                    "notes": item["notes"] or "",
                    "analyzedAt": item["analyzed_at"],
                }

            return result
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/analysis/{project}/{group_id}")
    async def get_analysis(project: str, group_id: str):
        """Get analysis data for a specific conversation"""
        try:
            storage = storage_manager.get_storage(project)
            analysis = storage.get_analysis(project, group_id)

            if not analysis:
                return {
                    "markedForAnalysis": False,
                    "judgment": None,
                    "notes": "",
                    "analyzedAt": None,
                }

            return {
                "markedForAnalysis": bool(analysis["marked_for_analysis"]),
                "judgment": analysis["judgment"],
                "notes": analysis["notes"] or "",
                "analyzedAt": analysis["analyzed_at"],
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def _sync_scenario_annotations(
        project: str, group_id: str, analysis_payload: Optional[Dict[str, Any]] = None
    ) -> None:
        try:
            storage = storage_manager.get_storage(project)
        except ValueError:
            return
        traces = storage.get_traces_by_group(group_id)
        scenario_path: Optional[Path] = None
        scenario_id: Optional[str] = None
        scenario_name: Optional[str] = None
        workflow_name: Optional[str] = None
        for trace in traces:
            metadata_raw = trace.get("metadata")
            try:
                metadata = (
                    json.loads(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else metadata_raw or {}
                )
            except Exception:
                metadata = {}
            scenario_meta = metadata.get("omniagents", {}).get("scenario") or {}
            raw_path = scenario_meta.get("path") or scenario_meta.get("source_path")
            if raw_path and not scenario_path:
                candidate = Path(str(raw_path)).expanduser()
                scenario_path = candidate
            if not scenario_id:
                sid = scenario_meta.get("id")
                if sid is not None:
                    scenario_id = str(sid)
            if not scenario_name:
                sname = scenario_meta.get("name")
                if sname is not None:
                    scenario_name = str(sname)
            if scenario_meta:
                workflow_name = trace.get("workflow_name") or workflow_name
            if scenario_path and (scenario_id or scenario_name):
                break
        if not scenario_path or not scenario_path.exists():
            return
        entries = _read_scenarios_file(scenario_path)
        if not entries:
            return
        target_index: Optional[int] = None
        for idx, entry in enumerate(entries):
            if scenario_id and str(entry.get("id")) == scenario_id:
                target_index = idx
                break
            if not scenario_id and scenario_name and entry.get("name") == scenario_name:
                target_index = idx
                break
        if target_index is None:
            return
        scenario_entry = dict(entries[target_index])
        category_slugs = storage.get_analysis_categories(group_id)
        categories: List[str] = []
        if category_slugs:
            slugs = {
                _slugify(cat)
                for cat in category_slugs
                if isinstance(cat, str) and cat.strip()
            }
            categories = sorted(slugs)
        if categories:
            scenario_entry["failure_categories"] = categories
        else:
            scenario_entry.pop("failure_categories", None)
        if analysis_payload is None:
            record = storage.get_analysis(project, group_id)
            if record:
                analysis_payload = {
                    "markedForAnalysis": bool(record.get("marked_for_analysis")),
                    "judgment": record.get("judgment"),
                    "notes": record.get("notes") or "",
                    "analyzedAt": record.get("analyzed_at"),
                }
        if analysis_payload:
            analysis_block = {
                "group_id": group_id,
                "judgment": analysis_payload.get("judgment"),
                "notes": analysis_payload.get("notes"),
                "marked": analysis_payload.get("markedForAnalysis"),
                "analyzed_at": analysis_payload.get("analyzedAt"),
                "updated_at": _utc_now(),
            }
            if workflow_name:
                analysis_block["workflow"] = workflow_name
            scenario_entry["analysis"] = analysis_block
        else:
            scenario_entry.pop("analysis", None)
        entries[target_index] = scenario_entry
        _write_scenarios_file(scenario_path, entries)

    @app.put("/api/analysis/{project}/{group_id}")
    async def update_analysis(
        project: str, group_id: str, analysis_data: Dict[str, Any]
    ):
        """Update or create analysis data for a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            storage.upsert_analysis(project, group_id, analysis_data)
            _sync_scenario_annotations(project, group_id, analysis_data)

            # Return the updated data
            return {
                "markedForAnalysis": analysis_data.get("markedForAnalysis", False),
                "judgment": analysis_data.get("judgment"),
                "notes": analysis_data.get("notes", ""),
                "analyzedAt": analysis_data.get("analyzedAt"),
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/analysis/{project}/{group_id}")
    async def delete_analysis(project: str, group_id: str):
        """Delete analysis data for a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            storage.delete_analysis(group_id)
            return {"message": "Analysis deleted successfully"}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/categories/{project}")
    async def get_failure_categories(project: str):
        try:
            storage = storage_manager.get_storage(project)
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            categories, _target_file, _data, _cfg, _flag = _load_failure_categories(
                ctx, manifest_path
            )
            scenario_usage = _collect_scenario_category_usage(ctx)
            analysis_usage = storage.get_analysis_category_usage(project)
            payload: List[Dict[str, Any]] = []
            for category in categories:
                slug = category["slug"]
                total_usage = scenario_usage.get(slug, 0) + analysis_usage.get(slug, 0)
                model = FailureCategoryModel(
                    id=slug,
                    name=category["name"],
                    description=category.get("description"),
                    measures=category.get("measures", []),
                    usageCount=total_usage,
                )
                payload.append(model.model_dump())
            payload.sort(key=lambda item: item["name"].lower())
            return payload
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/categories/{project}")
    async def create_failure_category(project: str, category_data: Dict[str, Any]):
        try:
            storage_manager.get_storage(project)
            name = str(category_data.get("name", "")).strip()
            if not name:
                raise HTTPException(status_code=400, detail="Category name is required")
            description = category_data.get("description")
            if isinstance(description, str):
                description = description.strip() or None
            else:
                description = None
            raw_measures = category_data.get("measures")
            slug = _slugify(name)
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            categories, target_file, data, evaluation_cfg, has_eval_section = (
                _load_failure_categories(ctx, manifest_path)
            )
            if any(item["slug"] == slug for item in categories):
                raise HTTPException(
                    status_code=409, detail="Category with this name already exists"
                )
            measures_list: List[str] = []
            if raw_measures is not None:
                if not isinstance(raw_measures, list):
                    raise HTTPException(
                        status_code=400, detail="Measures must be a list"
                    )
                available_measures = _assemble_measures(ctx, manifest_path)
                valid_names = {item.name for item in available_measures}
                normalized: List[str] = []
                for value in raw_measures:
                    if not isinstance(value, str):
                        raise HTTPException(
                            status_code=400, detail="Measures must be strings"
                        )
                    trimmed = value.strip()
                    if not trimmed:
                        continue
                    if trimmed not in valid_names:
                        raise HTTPException(
                            status_code=400, detail=f"Unknown measure: {trimmed}"
                        )
                    if trimmed not in normalized:
                        normalized.append(trimmed)
                measures_list = normalized
            categories.append(
                {
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "measures": measures_list,
                }
            )
            _persist_failure_categories(
                target_file, data, evaluation_cfg, has_eval_section, categories
            )
            scenario_usage = _collect_scenario_category_usage(ctx)
            analysis_usage = storage_manager.get_storage(
                project
            ).get_analysis_category_usage(project)
            model = FailureCategoryModel(
                id=slug,
                name=name,
                description=description,
                measures=measures_list,
                usageCount=scenario_usage.get(slug, 0) + analysis_usage.get(slug, 0),
            )
            return model.model_dump()
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.put("/api/categories/{project}/{category_slug}")
    async def update_failure_category(
        project: str, category_slug: str, payload: Dict[str, Any]
    ):
        try:
            storage_manager.get_storage(project)
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            categories, target_file, data, evaluation_cfg, has_eval_section = (
                _load_failure_categories(ctx, manifest_path)
            )
            slug = _slugify(category_slug)
            if not slug:
                raise HTTPException(status_code=404, detail="Category not found")
            entry_index: Optional[int] = None
            for idx, item in enumerate(categories):
                if item.get("slug") == slug:
                    entry_index = idx
                    break
            if entry_index is None:
                raise HTTPException(status_code=404, detail="Category not found")
            entry = dict(categories[entry_index])
            if "description" in payload:
                description_value = payload.get("description")
                if description_value is None:
                    entry["description"] = None
                elif isinstance(description_value, str):
                    entry["description"] = description_value.strip() or None
                else:
                    raise HTTPException(
                        status_code=400, detail="Description must be a string"
                    )
            if "measures" in payload:
                raw_measures = payload.get("measures")
                if raw_measures is None:
                    normalized_measures: List[str] = []
                elif not isinstance(raw_measures, list):
                    raise HTTPException(
                        status_code=400, detail="Measures must be a list"
                    )
                else:
                    available_measures = _assemble_measures(ctx, manifest_path)
                    valid_names = {item.name for item in available_measures}
                    normalized_measures = []
                    for value in raw_measures:
                        if not isinstance(value, str):
                            raise HTTPException(
                                status_code=400, detail="Measures must be strings"
                            )
                        trimmed = value.strip()
                        if not trimmed:
                            continue
                        if trimmed not in valid_names:
                            raise HTTPException(
                                status_code=400, detail=f"Unknown measure: {trimmed}"
                            )
                        if trimmed not in normalized_measures:
                            normalized_measures.append(trimmed)
                entry["measures"] = normalized_measures
            categories[entry_index] = entry
            _persist_failure_categories(
                target_file, data, evaluation_cfg, has_eval_section, categories
            )
            scenario_usage = _collect_scenario_category_usage(ctx)
            analysis_usage = storage_manager.get_storage(
                project
            ).get_analysis_category_usage(project)
            model = FailureCategoryModel(
                id=slug,
                name=entry.get("name", slug),
                description=entry.get("description"),
                measures=entry.get("measures", []),
                usageCount=scenario_usage.get(slug, 0) + analysis_usage.get(slug, 0),
            )
            return model.model_dump()
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/categories/{project}/{category_slug}")
    async def delete_failure_category(project: str, category_slug: str):
        try:
            storage = storage_manager.get_storage(project)
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            categories, target_file, data, evaluation_cfg, has_eval_section = (
                _load_failure_categories(ctx, manifest_path)
            )
            slug = _slugify(category_slug)
            if not slug:
                raise HTTPException(status_code=404, detail="Category not found")
            scenario_usage = _collect_scenario_category_usage(ctx)
            analysis_usage = storage.get_analysis_category_usage(project)
            previous_usage = scenario_usage.get(slug, 0) + analysis_usage.get(slug, 0)
            filtered = [item for item in categories if item["slug"] != slug]
            if len(filtered) == len(categories):
                raise HTTPException(status_code=404, detail="Category not found")
            _persist_failure_categories(
                target_file, data, evaluation_cfg, has_eval_section, filtered
            )
            storage.remove_analysis_category_slug(project, slug)
            _remove_category_from_scenarios(ctx, slug)
            return {
                "message": "Category deleted successfully",
                "previousUsageCount": previous_usage,
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/measures")
    async def get_evaluation_measures():
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            measures_payload = _assemble_measures(ctx, manifest_path)
            response = EvaluationMeasuresResponse(
                project=project_name, measures=measures_payload
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/evaluation/measures")
    async def create_evaluation_measure(payload: EvaluationMeasureCreatePayload):
        try:
            measure_name = (payload.name or "").strip()
            if not measure_name:
                raise HTTPException(status_code=422, detail="Measure name is required")
            if not measure_name.isidentifier():
                raise HTTPException(
                    status_code=422, detail="Measure name must be a valid identifier"
                )
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            measures_meta, target_file, data, evaluation_cfg, has_eval_section = (
                _load_measures_metadata(ctx, manifest_path)
            )
            existing_names = {item["name"] for item in measures_meta}
            if measure_name in existing_names:
                raise HTTPException(status_code=409, detail="Measure already exists")
            label_value = (
                payload.label.strip()
                if isinstance(payload.label, str) and payload.label.strip()
                else None
            )
            description_value = (
                payload.description.strip()
                if isinstance(payload.description, str) and payload.description.strip()
                else None
            )
            measures_meta.append(
                {
                    "name": measure_name,
                    "label": label_value,
                    "description": description_value,
                    "module": None,
                    "sourcePath": None,
                }
            )
            _persist_measures(
                target_file, data, evaluation_cfg, has_eval_section, measures_meta
            )
            if payload.createStub:
                _ensure_measure_stub(ctx, measure_name)
            ctx_updated = load_project(str(manifest_path))
            measures_payload = _assemble_measures(ctx_updated, manifest_path)
            response = EvaluationMeasuresResponse(
                project=project_name, measures=measures_payload
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.put("/api/evaluation/measures")
    async def update_evaluation_measure(payload: EvaluationMeasureUpdatePayload):
        try:
            measure_name = (payload.name or "").strip()
            if not measure_name:
                raise HTTPException(status_code=422, detail="Measure name is required")
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            measures_meta, target_file, data, evaluation_cfg, has_eval_section = (
                _load_measures_metadata(ctx, manifest_path)
            )
            updated = False
            for entry in measures_meta:
                if entry.get("name") == measure_name:
                    if payload.label is not None:
                        entry["label"] = (
                            payload.label.strip()
                            if isinstance(payload.label, str) and payload.label.strip()
                            else None
                        )
                    if payload.description is not None:
                        entry["description"] = (
                            payload.description.strip()
                            if isinstance(payload.description, str)
                            and payload.description.strip()
                            else None
                        )
                    updated = True
                    break
            if not updated:
                raise HTTPException(status_code=404, detail="Measure not found")
            _persist_measures(
                target_file, data, evaluation_cfg, has_eval_section, measures_meta
            )
            ctx_updated = load_project(str(manifest_path))
            measures_payload = _assemble_measures(ctx_updated, manifest_path)
            response = EvaluationMeasuresResponse(
                project=project_name, measures=measures_payload
            )
            return response.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/evaluation/measures/{name}")
    async def delete_evaluation_measure(name: str):
        try:
            measure_name = (name or "").strip()
            if not measure_name:
                raise HTTPException(status_code=422, detail="Measure name is required")
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            measures_meta, target_file, data, evaluation_cfg, has_eval_section = (
                _load_measures_metadata(ctx, manifest_path)
            )
            filtered = [
                entry for entry in measures_meta if entry.get("name") != measure_name
            ]
            if len(filtered) == len(measures_meta):
                raise HTTPException(status_code=404, detail="Measure not found")
            _persist_measures(
                target_file, data, evaluation_cfg, has_eval_section, filtered
            )
            ctx_updated = load_project(str(manifest_path))
            measures_payload = _assemble_measures(ctx_updated, manifest_path)
            response = EvaluationMeasuresResponse(
                project=project_name, measures=measures_payload
            )
            return EvaluationMeasureDeleteResponse(
                project=project_name, deleted=measure_name, measures=response.measures
            ).model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # Evaluation Results Endpoints
    def _find_result_runs(manifest_path: Path) -> List[Dict[str, Any]]:
        """Find all evaluation runs from runs.json indexes."""
        base_dir = manifest_path.parent.resolve()
        runs: List[Dict[str, Any]] = []

        index_patterns = [
            "evaluations/**/results/runs.json",
            "artifacts/eval/**/results/runs.json",
        ]
        for pattern in index_patterns:
            for runs_index in base_dir.glob(pattern):
                try:
                    index_data = json.loads(runs_index.read_text(encoding="utf-8"))
                    for run in index_data.get("runs", []):
                        run["_results_dir"] = str(runs_index.parent)
                        runs.append(run)
                except Exception:
                    pass

        return sorted(runs, key=lambda r: r.get("timestamp", ""), reverse=True)

    def _parse_results_file(
        results_path: Path,
    ) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
        """Parse generated_runs.json into summary and scenario list."""
        data = json.loads(results_path.read_text(encoding="utf-8"))
        runs = data.get("runs", [])

        summary = {"total": 0, "passed": 0, "failed": 0}
        scenarios: List[Dict[str, Any]] = []

        for run in runs:
            measures = run.get("measures", [])
            all_passed = (
                all(m.get("passed", False) for m in measures) if measures else True
            )

            summary["total"] += 1
            if all_passed:
                summary["passed"] += 1
            else:
                summary["failed"] += 1

            # Resolve the trace group_id under which this scenario's spans
            # were persisted. Three sources, in priority order:
            #   1. ``run.trace_group_id`` — the eval pipeline's run_uid
            #      ({scenario_id}-{uuid8}). Authoritative when present.
            #   2. ``run.metadata.group_id`` — older eval result schemas.
            #   3. ``session_{run.session_id}`` — runtime conversation
            #      pattern; only correct for traces produced outside the
            #      eval pipeline (e.g., interactive playground sessions).
            group_id = run.get("trace_group_id")
            if not group_id:
                metadata = (
                    run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
                )
                group_id = metadata.get("group_id")
            if not group_id:
                session_id = run.get("session_id")
                if session_id:
                    group_id = f"session_{session_id}"

            scenarios.append(
                {
                    "scenario_id": run.get("scenario_id") or run.get("id"),
                    "scenario_name": run.get("scenario_name")
                    or run.get("scenario", "unnamed"),
                    "type": run.get("type", "scripted"),
                    "passed": all_passed,
                    "measures": [
                        {
                            "name": m.get("name", "unknown"),
                            "passed": m.get("passed", False),
                            "reason": m.get("reason"),
                        }
                        for m in measures
                    ],
                    "error": run.get("error"),
                    "group_id": group_id,
                }
            )

        return summary, scenarios

    @app.get("/api/evaluation/results")
    async def get_evaluation_results():
        """List all available result runs with summaries."""
        try:
            manifest_path = _get_manifest_path()
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project

            results: List[EvaluationRunSummaryModel] = []
            for run in _find_result_runs(manifest_path):
                try:
                    summary = run.get("summary", {})
                    pm_raw = summary.get("per_measure") or {}
                    per_measure = {
                        name: EvaluationMeasureAggregateModel(
                            passed=int(stats.get("passed", 0)),
                            failed=int(stats.get("failed", 0)),
                            total=int(stats.get("total", 0)),
                        )
                        for name, stats in pm_raw.items()
                        if isinstance(stats, dict)
                    }
                    results.append(
                        EvaluationRunSummaryModel(
                            path=run.get("path", ""),
                            run_at=run.get("timestamp", ""),
                            total_scenarios=summary.get("total_scenarios", 0),
                            passed=summary.get("passed", 0),
                            failed=summary.get("failed", 0),
                            workflow_name=run.get("workflow_name"),
                            agent=run.get("agent"),
                            per_measure=per_measure,
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to process run entry: %s", e)

            return EvaluationResultsListResponse(
                project=project_name, results=results
            ).model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/evaluation/results/detail")
    async def get_evaluation_results_detail(path: str = Query(...)):
        """Get detailed results for a specific run."""
        try:
            results_path = Path(path).resolve()
            if not results_path.is_file():
                raise HTTPException(status_code=404, detail="Results file not found")

            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project

            summary, scenarios = _parse_results_file(results_path)

            # Get workflow_name from metadata.json (in same directory as results.json)
            metadata_file = results_path.parent / "metadata.json"
            workflow_name = None
            run_at = datetime.fromtimestamp(
                results_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            if metadata_file.is_file():
                try:
                    mdata = json.loads(metadata_file.read_text(encoding="utf-8"))
                    workflow_name = mdata.get("workflow_name")
                    if mdata.get("created_at"):
                        run_at = mdata["created_at"]
                except Exception:
                    pass

            return EvaluationResultsDetailResponse(
                project=project_name,
                path=str(results_path),
                run_at=run_at,
                summary=summary,
                scenarios=[EvaluationScenarioResultModel(**s) for s in scenarios],
                workflow_name=workflow_name,
            ).model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/analysis/{project}/{group_id}/categories")
    async def get_analysis_categories(project: str, group_id: str):
        """Get categories for a specific analysis"""
        try:
            storage = storage_manager.get_storage(project)
            category_ids = storage.get_analysis_categories(group_id)
            return {"categoryIds": category_ids}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/analysis/{project}/{group_id}/categories")
    async def update_analysis_categories(
        project: str, group_id: str, data: Dict[str, Any]
    ):
        """Update categories for an analysis"""
        try:
            storage = storage_manager.get_storage(project)
            raw_ids = data.get("categoryIds", [])
            if not isinstance(raw_ids, list):
                raise HTTPException(status_code=400, detail="Invalid category IDs")
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            categories, _target_file, _data, _cfg, _flag = _load_failure_categories(
                ctx, manifest_path
            )
            valid_slugs = {item["slug"] for item in categories}
            normalized: List[str] = []
            for value in raw_ids:
                if not isinstance(value, str):
                    raise HTTPException(status_code=400, detail="Invalid category IDs")
                slug = _slugify(value)
                if not slug or slug not in valid_slugs:
                    raise HTTPException(status_code=400, detail="Unknown category")
                if slug not in normalized:
                    normalized.append(slug)
            storage.set_analysis_categories(project, group_id, normalized)
            _sync_scenario_annotations(project, group_id)
            return {"categoryIds": normalized}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/export/{project}/{trace_id}")
    async def export_trace(
        project: str, trace_id: str, format: str = Query("json", pattern="^(json|csv)$")
    ):
        """Export a trace in different formats"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                raise HTTPException(status_code=404, detail="Project not found")

            # Get the specific trace by ID
            trace = storage.get_trace(trace_id)

            if not trace:
                raise HTTPException(status_code=404, detail="Trace not found")

            spans = storage.get_spans_for_trace(trace_id)

            if format == "json":
                data = {"trace": trace, "spans": spans}
                return StreamingResponse(
                    io.BytesIO(json.dumps(data, indent=2).encode()),
                    media_type="application/json",
                    headers={
                        "Content-Disposition": f"attachment; filename=trace_{trace_id}.json"
                    },
                )
            else:
                # CSV format
                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "span_id",
                        "span_type",
                        "span_name",
                        "started_at",
                        "ended_at",
                        "parent_id",
                        "sequence_number",
                        "error",
                    ],
                )
                writer.writeheader()

                for span in spans:
                    writer.writerow(
                        {
                            "span_id": span.get("span_id"),
                            "span_type": span.get("span_type"),
                            "span_name": span.get("span_name"),
                            "started_at": span.get("started_at"),
                            "ended_at": span.get("ended_at"),
                            "parent_id": span.get("parent_id"),
                            "sequence_number": span.get("sequence_number"),
                            "error": "Yes" if span.get("error") else "No",
                        }
                    )

                return StreamingResponse(
                    io.BytesIO(output.getvalue().encode()),
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": f"attachment; filename=trace_{trace_id}.csv"
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/api/evaluation/suite/run/{job_id}/ws")
    async def suite_run_ws(job_id: str, websocket: WebSocket):
        await _require_ws_auth(websocket)
        manager: SuiteRunManager = app.state.suite_run_manager
        accepted = await manager.add_connection(job_id, websocket)
        if not accepted:
            await websocket.close(code=4404)
            return
        try:
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await manager.remove_connection(job_id, websocket)
        except Exception:
            await manager.remove_connection(job_id, websocket)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/api/evaluation/scenarios/run/{job_id}/ws")
    async def scenario_run_ws(job_id: str, websocket: WebSocket):
        await _require_ws_auth(websocket)
        manager: ScenarioRunManager = app.state.scenario_run_manager
        accepted = await manager.add_connection(job_id, websocket)
        if not accepted:
            await websocket.close(code=4404)
            return
        try:
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await manager.remove_connection(job_id, websocket)
        except Exception:
            await manager.remove_connection(job_id, websocket)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/api/evaluation/scenarios/generate/{job_id}/ws")
    async def scenario_generation_ws(job_id: str, websocket: WebSocket):
        await _require_ws_auth(websocket)
        manager: ScenarioGenerationManager = app.state.scenario_generation_manager
        accepted = await manager.add_connection(job_id, websocket)
        if not accepted:
            await websocket.close(code=4404)
            return
        try:
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await manager.remove_connection(job_id, websocket)
        except Exception:
            await manager.remove_connection(job_id, websocket)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/api/evaluation/judges/optimize/{job_id}/ws")
    async def judge_optimize_ws(job_id: str, websocket: WebSocket):
        await _require_ws_auth(websocket)
        manager: JudgeOptimizationManager = app.state.judge_optimization_manager
        accepted = await manager.add_connection(job_id, websocket)
        if not accepted:
            await websocket.close(code=4404)
            return
        try:
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await manager.remove_connection(job_id, websocket)
        except Exception:
            await manager.remove_connection(job_id, websocket)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/api/evaluation/agents/optimize/{job_id}/ws")
    async def agent_optimize_ws(job_id: str, websocket: WebSocket):
        await _require_ws_auth(websocket)
        manager: AgentOptimizationManager = app.state.agent_optimization_manager
        accepted = await manager.add_connection(job_id, websocket)
        if not accepted:
            await websocket.close(code=4404)
            return
        try:
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await manager.remove_connection(job_id, websocket)
        except Exception:
            await manager.remove_connection(job_id, websocket)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/api/ws/traces")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket for real-time trace updates"""
        await _require_ws_auth(websocket)
        await manager.connect(websocket)
        try:
            while True:
                # Keep connection alive
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    @app.websocket("/api/playground/ws")
    async def playground_ws(websocket: WebSocket):
        logger.debug(
            "[playground] incoming websocket request %s",
            {
                key: value
                for key, value in websocket.query_params.multi_items()
                # Never log credentials (one-time tickets, legacy tokens).
                if key not in ("token", "ticket")
            },
        )
        await _require_ws_auth(websocket)
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        session_id = websocket.query_params.get("session_id")
        if not session_id:
            logger.debug("[playground] closing websocket: session_id missing")
            await websocket.close(code=4400)
            return

        store: PlaygroundSessionStore = app.state.playground_session_store
        config = await store.get(session_id)
        if not config:
            logger.debug(
                "[playground] closing websocket: session config missing for %s",
                session_id,
            )
            await websocket.close(code=4404)
            return

        agent_key = config.get("__agent_key")
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            logger.debug(
                "[playground] closing websocket: spec missing for agent %s", agent_key
            )
            await websocket.close(code=4404)
            return
        session_label = config.get("__session_label") or (
            "playground" if not agent_key else f"agent:{agent_key}"
        )

        try:
            spec = build_playground_spec(
                spec,
                resources.get("tool_registry", {}),
                resources.get("guardrails_registry", {}),
                config,
                session_label=session_label,
            )
        except Exception as exc:
            logger.exception("[playground] failed to build spec")
            await websocket.close(code=1011, reason=str(exc))
            return

        service = AgentService(
            spec,
            AgentHostResources.create(f"playground-{uuid.uuid4().hex}"),
        )
        # The playground authorizes under the same policy the deployment's
        # auth provider implies: enforced (ownership + method
        # classification) when principals are distinguishable, permissive
        # in single-user/no-auth mode.
        service.authorization_policy = connection_authenticator.authorization_policy
        transport = ServerWebSocketTransport(websocket)
        # Dispatch through JsonRpcEndpoint's message processor instead of a
        # hand-rolled getattr() loop so every request runs through
        # ``AgentService.authorize_rpc`` (resource ownership + global-method
        # classification) before it executes — exactly like /ws on the
        # agent server. No ``gui_handshake``/``allowed_methods``: the
        # playground client predates the GUI handshake, and enabling strict
        # request/result model validation is a protocol change beyond this
        # authorization fix.
        endpoint = JsonRpcEndpoint(service)

        try:
            logger.debug("[playground] accepting websocket for session %s", session_id)
            await transport.connect()
            channel = JsonRpcChannel(transport)
            channel.principal = getattr(websocket.state, "principal", None)
            service.channel = channel
            while transport.is_connected():
                try:
                    data = await transport.receive_json()
                except TransportClosedError:
                    break
                except WebSocketDisconnect:
                    break

                try:
                    await endpoint._process_message(channel, data)
                except Exception:
                    logger.exception("[playground] failed processing message")
                    break
        finally:
            logger.debug("[playground] websocket closed for session %s", session_id)
            await transport.disconnect()

    if dev_server_url:
        dev_base = dev_server_url.rstrip("/")

        @app.get("/", include_in_schema=False)
        async def dev_root():
            return RedirectResponse(dev_base, status_code=307)

        @app.get("/{path:path}", include_in_schema=False)
        async def dev_proxy(path: str, request: Request):
            target = f"{dev_base}/{path}" if path else dev_base
            query = request.url.query
            if query:
                target = f"{target}?{query}"
            return RedirectResponse(target, status_code=307)

    else:
        # Serve static files in production
        # Prefer the packaged build; fall back to a local web build in a checkout
        candidate_dirs = [
            Path(__file__).parent / "static",
            Path(__file__).resolve().parents[1] / "web" / "dist",
        ]
        for static_dir in candidate_dirs:
            if static_dir.exists():
                app.mount(
                    "/",
                    SpaStaticFiles(directory=str(static_dir), html=True),
                    name="static",
                )
                break

    return app, manager
