"""FastAPI routes for the React UI"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal


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


from omniagents.core.paths import (
    get_traces_dir,
)
from omniagents.core.providers.auth.base import AuthProvider
from omniagents.core.providers.auth.none import NoneAuthProvider
from omniagents.core.providers.auth.token import TokenAuthProvider
from omniagents.rpc.auth import (
    WS_CLOSE_UNAUTHENTICATED,
    AuthenticationFailed,
    ConnectionAuthenticator,
    legacy_query_auth_enabled,
)

from omniagents.core.tracing.storage import SQLiteStorage

from omniagents.core.eval.workspace import (
    EvalWorkspace,
    EvalWorkspaceError,
    NotFound,
    slugify,
)

from .agent_runtimes import AgentRuntimes
from .agents import router as agents_router
from .evaluation import router as evaluation_router
from .jobs import Jobs

try:
    from agents.agent_output import AgentOutputSchema  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AgentOutputSchema = None  # type: ignore

logger = logging.getLogger(__name__)


_SESSION_LOC_CACHE: Dict[str, Tuple[float, List[Tuple[Path, str]]]] = {}


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


SuiteRunStateLiteral = Literal["pending", "running", "completed", "failed", "cancelled"]


# Evaluation Results Models


def create_api_app(
    storage: Optional[SQLiteStorage] = None,
    default_project: Optional[str] = None,
    *,
    dev_server_url: Optional[str] = None,
    project_resources: Optional[Dict[str, Any]] = None,
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
    resources = dict(project_resources or {})
    if default_project and not resources.get("project_name"):
        resources["project_name"] = default_project
    app.state.project_resources = resources
    manifest_hint = resources.get("manifest_path")
    agent_runtimes = AgentRuntimes(
        Path(manifest_hint).expanduser().resolve() if manifest_hint else None,
        auth_token=expected_auth_token,
    )
    app.state.agent_runtimes = agent_runtimes
    app.state.storage_manager = storage_manager
    app.state.jobs = Jobs()
    app.state.require_ws_auth = _require_ws_auth
    app.include_router(agents_router)
    app.include_router(evaluation_router)

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
        resources: Dict[str, Any] = getattr(app.state, "project_resources", {})
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
        resources: Dict[str, Any] = getattr(app.state, "project_resources", {})
        manifest_hint = resources.get("manifest_path")
        if manifest_hint:
            manifest_path = Path(manifest_hint).expanduser().resolve()
            if manifest_path.is_file():
                return manifest_path
        raise HTTPException(status_code=404, detail="Project manifest not available")

    # Each agent's real runtime (ws, terminals, tickets) for the Playground.
    app.mount("/api/agents/{agent_key}/runtime", agent_runtimes)

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
        """Copy a review onto the scenario its conversation ran, when it was
        an evaluation run of one of the project's scenario sets: the
        failure categories become the scenario's labels (what a judge
        dataset is built from) and the judgment and notes its ``analysis``."""
        try:
            storage = storage_manager.get_storage(project)
            workspace = EvalWorkspace(_get_manifest_path())
        except (ValueError, HTTPException, NotFound):
            return
        scenario: Dict[str, Any] = {}
        workflow_name: Optional[str] = None
        for trace in storage.get_traces_by_group(group_id):
            metadata = trace.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except ValueError:
                    metadata = {}
            found = ((metadata or {}).get("omniagents") or {}).get("scenario") or {}
            if found:
                scenario = found
                workflow_name = trace.get("workflow_name")
                break
        set_path = scenario.get("path") or scenario.get("source_path")
        if not set_path or not (scenario.get("id") or scenario.get("name")):
            return
        if analysis_payload is None:
            record = storage.get_analysis(project, group_id) or {}
            analysis_payload = {
                "judgment": record.get("judgment"),
                "notes": record.get("notes") or "",
                "analyzedAt": record.get("analyzed_at"),
            }
        review = {
            "group_id": group_id,
            "judgment": analysis_payload.get("judgment"),
            "notes": analysis_payload.get("notes") or None,
            "analyzed_at": analysis_payload.get("analyzedAt"),
            "workflow": workflow_name,
        }
        try:
            workspace.label_scenario(
                set_path,
                failure_categories=storage.get_analysis_categories(group_id) or [],
                review=review if review["judgment"] or review["notes"] else None,
                scenario_id=str(scenario["id"]) if scenario.get("id") else None,
                name=None if scenario.get("id") else scenario.get("name"),
            )
        except EvalWorkspaceError:
            # The set has changed or is gone; the review stays with Studio.
            return

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

    # Evaluation Results Endpoints

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
        """Set the failure categories a reviewer saw in a conversation."""
        raw_ids = data.get("categoryIds", [])
        if not isinstance(raw_ids, list) or not all(
            isinstance(v, str) for v in raw_ids
        ):
            raise HTTPException(status_code=400, detail="Invalid category IDs")
        known = {
            category["id"]
            for category in EvalWorkspace(_get_manifest_path()).failure_categories()
        }
        normalized: List[str] = []
        for value in raw_ids:
            category_id = slugify(value)
            if category_id not in known:
                raise HTTPException(
                    status_code=400, detail=f"Unknown category: {value}"
                )
            if category_id not in normalized:
                normalized.append(category_id)
        try:
            storage = storage_manager.get_storage(project)
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        storage.set_analysis_categories(project, group_id, normalized)
        _sync_scenario_annotations(project, group_id)
        return {"categoryIds": normalized}

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
        # A checkout's own web build, else the bundle a wheel ships.
        candidate_dirs = [
            Path(__file__).resolve().parents[1] / "web" / "dist",
            Path(__file__).parent / "static",
        ]
        for static_dir in candidate_dirs:
            if static_dir.exists():
                app.mount(
                    "/",
                    SpaStaticFiles(directory=str(static_dir), html=True),
                    name="static",
                )
                break

    return app
