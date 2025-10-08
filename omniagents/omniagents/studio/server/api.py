"""FastAPI routes for the React UI"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import shutil
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal, Set

from dataclasses import replace, asdict, dataclass

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import yaml

from pydantic import BaseModel, Field

from omniagents.core.paths import get_traces_dir
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.rpc.server import JsonRpcChannel
from omniagents.rpc.transport.websocket import ServerWebSocketTransport
from omniagents.rpc.transport import TransportClosedError
from omniagents.core.project.runtime import load_project, build_spec_from_project
from omniagents.core.project.compiler import compile_project
from omniagents.core.eval.scenarios import run_scenarios_against_agent, generate_scenarios

from ..tracing.storage import SQLiteStorage

try:
    from agents.agent_output import AgentOutputSchema  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AgentOutputSchema = None  # type: ignore

logger = logging.getLogger(__name__)

MODEL_SETTING_SCHEMA: List[Dict[str, Any]] = [
    {"key": "temperature", "label": "Temperature", "type": "number", "min": 0, "max": 2, "step": 0.1},
    {"key": "top_p", "label": "Top P", "type": "number", "min": 0, "max": 1, "step": 0.01},
    {"key": "frequency_penalty", "label": "Frequency Penalty", "type": "number", "min": -2, "max": 2, "step": 0.1},
    {"key": "presence_penalty", "label": "Presence Penalty", "type": "number", "min": -2, "max": 2, "step": 0.1},
    {"key": "max_tokens", "label": "Max Output Tokens", "type": "integer", "min": 1},
    {"key": "tool_choice", "label": "Tool Choice", "type": "enum", "options": ["auto", "required", "none"]},
    {"key": "parallel_tool_calls", "label": "Parallel Tool Calls", "type": "boolean"},
    {"key": "truncation", "label": "Truncation", "type": "enum", "options": ["auto", "disabled"]},
    {"key": "store", "label": "Store Responses", "type": "boolean"},
    {"key": "include_usage", "label": "Include Usage", "type": "boolean"},
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


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


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


def _guardrail_keys(active_guardrails: Optional[List[Any]], registry: Dict[str, Any]) -> List[str]:
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


def _resolve_agent_spec(resources: Dict[str, Any], agent_key: Optional[str]) -> Optional[AgentSpec]:
    if agent_key:
        specs = resources.get("agent_specs") or {}
        return specs.get(agent_key)
    return resources.get("base_spec")


def _compute_playground_ws_url(request: Request) -> str:
    api_origin = str(request.base_url).rstrip("/")
    if api_origin.endswith("/api"):
        api_origin = api_origin[:-4]
    ws_origin = api_origin
    if ws_origin.startswith("https://"):
        ws_origin = "wss://" + ws_origin[8:]
    elif ws_origin.startswith("http://"):
        ws_origin = "ws://" + ws_origin[7:]
    return f"{ws_origin}/api/playground/ws"


def _build_metadata_response(
    spec: AgentSpec,
    resources: Dict[str, Any],
    request: Request,
    *,
    agent_key: Optional[str],
) -> Dict[str, Any]:
    tool_registry: Dict[str, Any] = resources.get("tool_registry", {})
    guardrails_registry: Dict[str, Any] = resources.get("guardrails_registry", {"input": {}, "output": {}})
    project_name = resources.get("project_name") or request.app.state.default_project or "default"

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
    default_output_label = _output_type_label(default_output_key, getattr(spec, "output_type", None))
    default_output_schema = _structured_output_schema(getattr(spec, "output_type", None), getattr(spec, "output_type_strict", True))

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
        "input": _guardrail_keys(getattr(spec, "input_guardrails", None), input_guardrails),
        "output": _guardrail_keys(getattr(spec, "output_guardrails", None), output_guardrails),
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


def _structured_output_schema(output_type: Any, strict: bool) -> Optional[Dict[str, Any]]:
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
    try:
        try:
            from typing import NotRequired  # type: ignore
        except ImportError:
            from typing_extensions import NotRequired  # type: ignore
        try:
            from typing import TypedDict  # type: ignore
        except ImportError:
            from typing_extensions import TypedDict  # type: ignore
    except Exception:
        return None

    required_raw = schema.get("required")
    required_names = {
        str(item)
        for item in required_raw
        if isinstance(item, str)
    } if isinstance(required_raw, list) else set()

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

    safe_name = "".join(ch for ch in (agent_name or "Playground") if ch.isalnum()) or "Playground"
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
    
    def get_all_storages(self) -> Dict[str, SQLiteStorage]:
        """Load all available project storages"""
        if not self.storage_dir.exists():
            return {}
        
        for db_file in self.storage_dir.glob("*.db"):
            project = db_file.stem
            if project not in self.storages:
                self.storages[project] = SQLiteStorage(str(db_file))
        
        return self.storages

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
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, session_id: Optional[str], config: Dict[str, Any]) -> str:
        async with self._lock:
            key = session_id or uuid.uuid4().hex
            payload = dict(config)
            payload["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self._sessions[key] = payload
            return key

    async def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            entry = self._sessions.get(session_id)
            return dict(entry) if entry else None

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)


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


class ScenarioEntryModel(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    dimensions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class ScenarioSetModel(BaseModel):
    kind: str
    label: str
    path: str
    scenario_count: int
    updated_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    scenarios: List[ScenarioEntryModel] = Field(default_factory=list)


class EvaluationDatasetModel(BaseModel):
    label: str
    path: str
    source_kind: Literal['golden', 'generated', 'annotated', 'unknown'] = 'unknown'
    source_label: Optional[str] = None
    source_path: Optional[str] = None
    run_count: int
    scenario_count: Optional[int] = None
    expected_count: Optional[int] = None
    includes_history: bool = False
    updated_at: Optional[str] = None
    created_at: Optional[str] = None
    workflow_generate: Optional[str] = None
    workflow_run: Optional[str] = None
    workflow_run_at: Optional[str] = None
    manifest_path: Optional[str] = None
    size_bytes: Optional[int] = None


class EvaluationDatasetsResponse(BaseModel):
    project: Optional[str] = None
    datasets: List[EvaluationDatasetModel] = Field(default_factory=list)


class ScenarioPromotionPayload(BaseModel):
    source_kind: Literal['generated', 'annotated']
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
    kind: Literal['golden', 'generated', 'annotated']
    set_path: str
    scenario: ScenarioWriteModel


class ScenarioUpdatePayload(ScenarioCreatePayload):
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None
    index: Optional[int] = None


class ScenarioDeletePayload(BaseModel):
    kind: Literal['golden', 'generated', 'annotated']
    set_path: str
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None
    index: Optional[int] = None


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

    async def create_job(self, project: Optional[str], payload: 'ScenarioRunRequest', total: int) -> ScenarioRunJob:
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

    async def broadcast(self, job_id: str, snapshot: Optional[Dict[str, Any]] = None) -> None:
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

    async def create_job(self, project: Optional[str], payload: 'ScenarioGenerationRequest') -> ScenarioGenerationJob:
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

    async def update_job(self, job_id: str, **fields: Any) -> Optional[ScenarioGenerationJob]:
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

    async def broadcast(self, job_id: str, snapshot: Optional[Dict[str, Any]] = None) -> None:
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
    kind: Literal['golden', 'generated', 'annotated']
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
    kind: Literal['golden', 'generated', 'annotated']
    set_path: str
    include_history: bool
    max_turns: Optional[int] = None
    output_path: Optional[str] = None
    select: Optional[str] = None
    exclude: Optional[str] = None
    state: Literal['pending', 'running', 'completed', 'failed', 'cancelled']
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runs_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None


class ScenarioRunResponseModel(BaseModel):
    job_id: str
    status: ScenarioRunStatusModel


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
    state: Literal['pending', 'running', 'completed', 'failed', 'cancelled']
    total: int
    completed: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scenarios_path: Optional[str] = None
    workflow_name: Optional[str] = None
    error: Optional[str] = None


class ScenarioGenerationResponseModel(BaseModel):
    job_id: str
    status: Optional[ScenarioGenerationStatusModel]


def build_playground_spec(
    base_spec: AgentSpec,
    tool_registry: Dict[str, Any],
    guardrails_registry: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    session_label: Optional[str] = None,
) -> AgentSpec:
    instructions_override = config.get("instructions") if "instructions" in config else None
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
        selected_tools = [tool_registry[name] for name in selected_tool_names if name in tool_registry]
    else:
        selected_tools = getattr(base_spec, "available_tools", None)

    base_model_settings = dict(base_spec.model_settings or {})
    model_settings = dict(base_model_settings)
    incoming_model_settings = config.get("model_settings")
    if isinstance(incoming_model_settings, dict):
        overrides = {k: v for k, v in incoming_model_settings.items() if v not in (None, "")}
        if overrides:
            model_settings = {**base_model_settings, **overrides}

    use_safe_agent = base_spec.use_safe_agent if config.get("use_safe_agent") is None else bool(config.get("use_safe_agent"))

    base_output_type = getattr(base_spec, "output_type", None)
    base_output_type_strict = getattr(base_spec, "output_type_strict", True)
    output_type_override = config.get("output_type")
    normalized_output_key = _resolve_output_type_override(output_type_override) if isinstance(output_type_override, str) else None
    schema_override = config.get("output_schema")
    custom_output_type = _build_type_from_schema(schema_override, base_spec.name or "Playground") if isinstance(schema_override, dict) else None
    resolved_output_type = base_output_type
    if normalized_output_key is not None:
        resolved_output_type = _map_output_type_key_to_python(normalized_output_key, fallback=base_output_type)
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
        if "safe_tool_names" in incoming_safe_options and isinstance(incoming_safe_options["safe_tool_names"], list):
            safe_options_dict["safe_tool_names"] = [str(item) for item in incoming_safe_options["safe_tool_names"] if isinstance(item, str)]
        if "safe_tool_patterns" in incoming_safe_options and isinstance(incoming_safe_options["safe_tool_patterns"], list):
            safe_options_dict["safe_tool_patterns"] = [str(item) for item in incoming_safe_options["safe_tool_patterns"] if isinstance(item, str)]
    safe_agent_options = SafeAgentOptions(**safe_options_dict)

    guardrails_config = config.get("guardrails") or {}
    guardrail_registry_input = (guardrails_registry or {}).get("input", {})
    guardrail_registry_output = (guardrails_registry or {}).get("output", {})

    def _resolve_guardrails(names: Optional[List[str]], registry: Dict[str, Any], fallback: Optional[List[Any]]) -> List[Any]:
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
        header_title="OmniAgents Playground",
        page_title="OmniAgents Playground",
        welcome_text=base_spec.welcome_text,
        client_storage_prefix="playground.",
        get_agent_instructions=_instructions_supplier,
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
):
    app = FastAPI()

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
    app.state.scenario_generation_manager = ScenarioGenerationManager()

    # Add CORS middleware for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
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
                raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
            
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
            
            return {"success": True, "message": f"Project '{project}' deleted successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/studio/project")
    async def get_studio_project():
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        manifest_hint = resources.get("manifest_path")
        project_name = resources.get("project_name") or app.state.default_project or None
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

    def _serialize_dimensions_payload(dimensions: List[EvaluationDimensionModel]) -> List[Dict[str, Any]]:
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
            serialized_dim: Dict[str, Any] = {"name": dim.name, "values": serialized_values}
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
                examples = val.get("examples") if isinstance(val.get("examples"), list) else []
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
        synthetic_cfg = evaluation_cfg.get("synthetic_data_generation") if isinstance(evaluation_cfg.get("synthetic_data_generation"), dict) else {}
        raw_dims = synthetic_cfg.get("dimensions") if isinstance(synthetic_cfg, dict) else []
        return _normalize_dimensions(raw_dims if isinstance(raw_dims, list) else [])
    
    def _write_yaml_file(path: Path, content: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup_path = path.with_suffix(path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(path, backup_path)
        path.write_text(yaml.safe_dump(content, sort_keys=False, allow_unicode=True), encoding="utf-8")
    
    def _persist_dimensions(manifest_path: Path, dimensions: List[EvaluationDimensionModel]) -> List[Dict[str, Any]]:
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
        evaluation_cfg = dict(data.get("evaluation") or {}) if has_eval_section else dict(data)
        synthetic_cfg = evaluation_cfg.get("synthetic_data_generation") if isinstance(evaluation_cfg.get("synthetic_data_generation"), dict) else {}
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
        files: List[Path] = []
        for directory in _evaluation_dirs(ctx):
            candidate = directory / "scenarios.yml"
            if candidate.is_file():
                files.append(candidate)
        eval_paths = ctx.paths.get("evaluations") or []
        for entry in eval_paths:
            if entry.is_file() and entry.name.endswith(".yml") and entry.name == "scenarios.yml":
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

    def _build_scenario_entries(raw_items: List[Dict[str, Any]], source_path: Path) -> List[ScenarioEntryModel]:
        scenarios: List[ScenarioEntryModel] = []
        for idx, item in enumerate(raw_items):
            name = str(item.get("name") or f"scenario_{idx + 1}")
            prompt = str(item.get("prompt") or "")
            scenario_id = item.get("id")
            dimensions = item.get("dimensions") if isinstance(item.get("dimensions"), dict) else None
            metadata = {
                "index": idx,
                "source_path": str(source_path),
            }
            scenarios.append(
                ScenarioEntryModel(
                    id=str(scenario_id) if scenario_id is not None else None,
                    name=name,
                    prompt=prompt,
                    dimensions=dimensions,
                    metadata=metadata,
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
                updated_at = datetime.fromtimestamp(file.stat().st_mtime).isoformat()
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

    def _build_artifact_set(kind: str, file_path: Path, base_dir: Path) -> ScenarioSetModel:
        raw = _read_scenarios_file(file_path)
        scenarios = _build_scenario_entries(raw, file_path)
        try:
            updated_at = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        except Exception:
            updated_at = None
        metadata: Dict[str, Any] = {"artifact_dir": str(base_dir)}
        manifest_path = base_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                metadata["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                metadata["manifest_error"] = "unreadable"
        label = metadata.get("manifest", {}).get("name") if isinstance(metadata.get("manifest"), dict) else None
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
            if kind == "generated":
                file_path = entry / "generated_scenarios.yml"
            else:
                file_path = entry / "annotated_scenarios.yml"
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
            backup_path = path.with_suffix(path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(path, backup_path)
        path.write_text(yaml.safe_dump(updated, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def _ensure_scenario_ids(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scenario = dict(entry)
            scenario_id = scenario.get("id")
            if not scenario_id:
                dimensions = scenario.get("dimensions") if isinstance(scenario.get("dimensions"), dict) else {}
                blob = json.dumps({"prompt": str(scenario.get("prompt") or ""), "dimensions": dimensions}, sort_keys=True)
                scenario["id"] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
            normalized.append(scenario)
        return normalized

    def _get_scenario_set_by_path(ctx, kind: str, target_path: Path) -> ScenarioSetModel:
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

    def _locate_scenario_entry(entries: List[Dict[str, Any]], scenario_id: Optional[str], scenario_name: Optional[str], index: Optional[int]) -> Optional[Tuple[int, Dict[str, Any]]]:
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

    def _parse_manifest_timestamp(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%Y_%m_%d_%H_%M_%S").isoformat()
        except Exception:
            return value

    def _manifest_metadata(directory: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Path], Optional[str], Optional[int], Optional[str], Optional[str], Optional[str]]:
        manifest_path = directory / "manifest.json"
        manifest_data: Optional[Dict[str, Any]] = None
        if manifest_path.is_file():
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    manifest_data = raw
            except Exception:
                manifest_data = {}
        created_at: Optional[str] = None
        expected_count: Optional[int] = None
        workflow_generate: Optional[str] = None
        workflow_run: Optional[str] = None
        workflow_run_at: Optional[str] = None
        if manifest_data:
            timestamp_value = manifest_data.get("timestamp")
            if timestamp_value:
                created_at = _parse_manifest_timestamp(timestamp_value)
            counts = manifest_data.get("counts")
            if isinstance(counts, dict):
                raw_total = counts.get("total")
                if isinstance(raw_total, int):
                    expected_count = raw_total
                elif isinstance(raw_total, str):
                    try:
                        expected_count = int(raw_total)
                    except Exception:
                        expected_count = None
            workflow_generate = manifest_data.get("workflow_generate") if isinstance(manifest_data.get("workflow_generate"), str) else None
            workflow_run = manifest_data.get("workflow_run") if isinstance(manifest_data.get("workflow_run"), str) else None
            workflow_run_at_value = manifest_data.get("workflow_run_at")
            if isinstance(workflow_run_at_value, str) and workflow_run_at_value:
                workflow_run_at = workflow_run_at_value
        manifest_path_obj = manifest_path if manifest_path.is_file() else None
        return manifest_data if manifest_data else None, manifest_path_obj, created_at, expected_count, workflow_generate, workflow_run, workflow_run_at

    def _build_dataset_record(dataset_path: Path, *, source_kind: str, source_label: Optional[str], source_path: Optional[str], scenario_count: Optional[int]) -> Optional[Tuple[EvaluationDatasetModel, float]]:
        if not dataset_path.is_file():
            return None
        try:
            data = json.loads(dataset_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        runs = data.get("runs")
        if not isinstance(runs, list):
            return None
        valid_runs = [run for run in runs if isinstance(run, dict)]
        run_count = len(valid_runs) if valid_runs else len(runs)
        includes_history = any(isinstance(run.get("history"), list) and run.get("history") for run in valid_runs)
        try:
            stat = dataset_path.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
            updated_ts = float(stat.st_mtime)
            created_default = datetime.fromtimestamp(stat.st_ctime).isoformat()
            size_bytes = int(stat.st_size)
        except Exception:
            updated_at = None
            updated_ts = 0.0
            created_default = None
            size_bytes = None
        manifest_data, manifest_path_obj, created_at_manifest, expected_count, workflow_generate, workflow_run, workflow_run_at = _manifest_metadata(dataset_path.parent)
        created_at = created_at_manifest or created_default
        if expected_count is None:
            expected_count = scenario_count
        manifest_name = manifest_data.get("name") if manifest_data and isinstance(manifest_data.get("name"), str) else None
        base_label = manifest_name or source_label or dataset_path.parent.name or dataset_path.stem
        base_label = str(base_label)
        if dataset_path.name == "generated_runs.json":
            label = f"{base_label} Runs"
        else:
            label = f"{base_label} Runs ({dataset_path.stem})"
        source_kind_normalized = source_kind if source_kind in {"golden", "generated", "annotated"} else "unknown"
        dataset = EvaluationDatasetModel(
            label=label,
            path=str(dataset_path),
            source_kind=source_kind_normalized,
            source_label=source_label,
            source_path=str(source_path) if source_path else None,
            run_count=run_count,
            scenario_count=scenario_count,
            expected_count=expected_count,
            includes_history=includes_history,
            updated_at=updated_at,
            created_at=created_at,
            workflow_generate=workflow_generate,
            workflow_run=workflow_run,
            workflow_run_at=workflow_run_at,
            manifest_path=str(manifest_path_obj) if manifest_path_obj else None,
            size_bytes=size_bytes,
        )
        return dataset, updated_ts

    def _discover_dataset_entries(ctx) -> List[EvaluationDatasetModel]:
        dataset_records: List[Tuple[EvaluationDatasetModel, float]] = []
        seen_paths: Set[str] = set()

        def _register(path: Path, *, source_kind: str, source_label: Optional[str], source_path: Optional[str], scenario_count: Optional[int]) -> None:
            record = _build_dataset_record(path, source_kind=source_kind, source_label=source_label, source_path=source_path, scenario_count=scenario_count)
            if not record:
                return
            model, updated_ts = record
            key_path = Path(model.path).expanduser()
            try:
                key = str(key_path.resolve(strict=False))
            except Exception:
                key = str(key_path)
            if key in seen_paths:
                return
            seen_paths.add(key)
            dataset_records.append((model, updated_ts))

        all_sets: List[ScenarioSetModel] = []
        all_sets.extend(_load_golden_sets(ctx))
        all_sets.extend(_load_artifact_sets(ctx, "generated"))
        all_sets.extend(_load_artifact_sets(ctx, "annotated"))

        directory_map: Dict[str, Tuple[str, ScenarioSetModel]] = {}
        for set_model in all_sets:
            base_path = Path(set_model.path).expanduser()
            directory = base_path.parent
            try:
                directory_key = str(directory.resolve(strict=False))
            except Exception:
                directory_key = str(directory)
            directory_map[directory_key] = (set_model.kind, set_model)

        for directory_key, (kind, set_model) in directory_map.items():
            directory = Path(directory_key)
            if not directory.exists():
                directory = Path(set_model.path).expanduser().parent
            try:
                entries = list(directory.iterdir())
            except Exception:
                entries = []
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != ".json":
                    continue
                if entry.name == "manifest.json":
                    continue
                _register(entry, source_kind=kind, source_label=set_model.label, source_path=set_model.path, scenario_count=set_model.scenario_count)

        artifact_root = _artifact_root(ctx)
        search_roots: List[Path] = []
        for directory in _evaluation_dirs(ctx):
            if directory not in search_roots:
                search_roots.append(directory)
        if artifact_root not in search_roots:
            search_roots.append(artifact_root)

        patterns = ["generated_runs.json", "*_runs.json", "runs_*.json"]
        for root in search_roots:
            if not root.is_dir():
                continue
            for pattern in patterns:
                for entry in root.rglob(pattern):
                    if not entry.is_file():
                        continue
                    try:
                        key = str(entry.resolve(strict=False))
                    except Exception:
                        key = str(entry)
                    if key in seen_paths:
                        continue
                    _register(entry, source_kind="unknown", source_label=None, source_path=None, scenario_count=None)

        dataset_records.sort(key=lambda item: (item[1], item[0].label.lower()), reverse=True)
        return [item[0] for item in dataset_records]
    
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
    
    async def _run_scenario_generation_job(job_id: str, payload: ScenarioGenerationRequest, manifest_path: Path) -> None:
        manager: ScenarioGenerationManager = app.state.scenario_generation_manager
        await manager.update_job(job_id, state="running", started_at=_utc_now())
        try:
            compiled_cfg_path, _ = compile_project(str(manifest_path), select=str(payload.select or ""), exclude=str(payload.exclude or ""), agent_key=(payload.agent or None))
            compiled_path = Path(compiled_cfg_path).resolve()
            cfg = yaml.safe_load(compiled_path.read_text(encoding="utf-8")) or {}
            tracing = cfg.get("tracing") or {}
            workflow_name: Optional[str] = None
            tracing_module = None
            provider = str(tracing.get("provider") or "").lower()
            name_label = None
            if payload.name:
                name_label = payload.name
            elif payload.output_path:
                try:
                    name_label = Path(payload.output_path).resolve().parent.name
                except Exception:
                    name_label = None
            if provider in {"studio", "agentio"}:
                try:
                    from omniagents.studio import tracing as studio_tracing
    
                    tracing_module = studio_tracing
                except ImportError:
                    tracing_module = None
                if tracing_module is None:
                    try:
                        import agentio as legacy_tracing
    
                        tracing_module = legacy_tracing
                    except Exception:
                        tracing_module = None
                if tracing_module is not None:
                    base_name = tracing.get("name") or compiled_path.stem
                    label = name_label or "scenarios"
                    ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                    workflow_name = f"{base_name}_scenarios_{label}_{ts}_generate"
                    resume_mode = (tracing.get("settings") or {}).get("resume", "never")
                    tracing_module.init(project=str(tracing.get("project") or "scenario_generation"), name=workflow_name, resume=str(resume_mode), replace_default=True)
    
            base_dir = manifest_path.parent.resolve()
            output_path_obj: Optional[Path]
            if payload.output_path:
                candidate = Path(payload.output_path)
                if not candidate.is_absolute():
                    candidate = (base_dir / candidate).resolve()
                output_path_obj = candidate
            elif payload.name:
                output_path_obj = (base_dir / "artifacts" / "eval" / payload.name / "generated_scenarios.yml").resolve()
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
                    asyncio.create_task(manager.update_job(job_id, completed=progress["completed"]))
                elif evt == "done":
                    count = event.get("count")
                    if isinstance(count, int) and count >= 0:
                        progress["completed"] = count
                        if progress["target"] == 0:
                            progress["target"] = count
                    asyncio.create_task(manager.update_job(job_id, completed=progress["completed"], total=progress["target"]))
    
            scenarios, out_path, _ = await generate_scenarios(
                Path(compiled_cfg_path),
                count=payload.count,
                seed=payload.seed,
                set_name=payload.name,
                output=output_path_obj,
                workflow_name=workflow_name,
                progress_cb=_progress_cb,
                dimension_filters=payload.dimensions,
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
            await manager.update_job(job_id, state="failed", error=str(exc), finished_at=_utc_now())
    
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
                raise HTTPException(status_code=422, detail="Provide a scenario set name or output_path")
            manifest_path = _get_manifest_path()
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            manager: ScenarioGenerationManager = app.state.scenario_generation_manager
            job = await manager.create_job(project_name, payload)
            task = asyncio.create_task(_run_scenario_generation_job(job.job_id, payload, manifest_path))
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = ScenarioGenerationStatusModel(**status) if status else None
            return {"job_id": job.job_id, "status": status_model.model_dump() if status_model else None}
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
            payload = EvaluationDatasetsResponse(project=project_name, datasets=datasets)
            return payload.model_dump()
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
                raise HTTPException(status_code=404, detail="Scenario set path not found")
    
            raw_scenarios = _read_scenarios_file(source_path)
            target_entry: Optional[Dict[str, Any]] = None
            for idx, entry in enumerate(raw_scenarios):
                if payload.scenario_id is not None and entry.get("id") is not None:
                    if str(entry.get("id")) == payload.scenario_id:
                        target_entry = entry
                        break
                elif payload.scenario_name is not None and entry.get("name") == payload.scenario_name:
                    target_entry = entry
                    break
                elif payload.index is not None and payload.index == idx:
                    target_entry = entry
                    break
            if target_entry is None:
                raise HTTPException(status_code=404, detail="Scenario not found in source set")
    
            golden_file = _resolve_golden_target(ctx)
            if golden_file.is_file():
                try:
                    golden_data = yaml.safe_load(golden_file.read_text(encoding="utf-8")) or {}
                except Exception:
                    golden_data = {}
            else:
                golden_data = {}
    
            if not isinstance(golden_data, dict):
                golden_data = {}
    
            existing = golden_data.get("scenarios") if isinstance(golden_data.get("scenarios"), list) else []
            existing_list: List[Dict[str, Any]] = [e for e in existing if isinstance(e, dict)]
    
            new_entry = dict(target_entry)
    
            scenario_id = new_entry.get("id")
            scenario_name = new_entry.get("name")
    
            for entry in existing_list:
                if scenario_id is not None and str(entry.get("id")) == str(scenario_id):
                    raise HTTPException(status_code=409, detail="Scenario already present in golden set")
                if scenario_id is None and scenario_name and entry.get("name") == scenario_name:
                    raise HTTPException(status_code=409, detail="Scenario already present in golden set")
    
            existing_list.append(new_entry)
            golden_data = dict(golden_data)
            golden_data["scenarios"] = existing_list
    
            golden_file.parent.mkdir(parents=True, exist_ok=True)
            if golden_file.exists():
                backup_path = golden_file.with_suffix(golden_file.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
                shutil.copy2(golden_file, backup_path)
            golden_file.write_text(yaml.safe_dump(golden_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            golden_sets = _load_golden_sets(ctx)
            promoted_entry = ScenarioEntryModel(
                id=str(new_entry.get("id")) if new_entry.get("id") is not None else None,
                name=str(new_entry.get("name") or ""),
                prompt=str(new_entry.get("prompt") or ""),
                dimensions=new_entry.get("dimensions") if isinstance(new_entry.get("dimensions"), dict) else None,
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
    
    async def _run_scenario_job(job_id: str, payload: ScenarioRunRequest, manifest_path: Path, scenario_set: ScenarioSetModel, scenarios: List[Dict[str, Any]], output_path: Path) -> None:
        manager: ScenarioRunManager = app.state.scenario_run_manager
        await manager.update_job(job_id, state="running", started_at=_utc_now(), total=len(scenarios), completed=0)
        try:
            compiled_cfg_path, _ = compile_project(str(manifest_path), select=str(payload.select or ""), exclude=str(payload.exclude or ""), agent_key=(payload.agent or None))
            compiled_path = Path(compiled_cfg_path).resolve()
            cfg = yaml.safe_load(compiled_path.read_text(encoding="utf-8")) or {}
            tracing = cfg.get("tracing") or {}
            workflow_name: Optional[str] = None
            tracing_module = None
            provider = str(tracing.get("provider") or "").lower()
            if provider in {"studio", "agentio"}:
                try:
                    from omniagents.studio import tracing as studio_tracing
    
                    tracing_module = studio_tracing
                except ImportError:
                    tracing_module = None
                if tracing_module is None:
                    try:
                        import agentio as legacy_tracing
    
                        tracing_module = legacy_tracing
                    except Exception:
                        tracing_module = None
                if tracing_module is not None:
                    base_name = tracing.get("name") or compiled_path.stem
                    scenario_dir = Path(scenario_set.path).resolve().parent
                    label = scenario_dir.name or "scenarios"
                    ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                    workflow_name = f"{base_name}_scenarios_{label}_{ts}"
                    resume_mode = (tracing.get("settings") or {}).get("resume", "never")
                    tracing_module.init(project=str(tracing.get("project") or "scenario_runs"), name=workflow_name, resume=str(resume_mode), replace_default=True)
    
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
                    asyncio.create_task(manager.update_job(job_id, completed=progress["completed"]))
                elif evt == "done":
                    count = event.get("count")
                    if isinstance(count, int) and count >= 0:
                        progress["completed"] = count
                    asyncio.create_task(manager.update_job(job_id, completed=progress["completed"]))
    
            runs = await run_scenarios_against_agent(compiled_path, scenarios, max_turns=payload.max_turns, include_history=payload.include_history, workflow_name=workflow_name, progress_cb=_progress_cb)
    
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
    
            manifest_file = Path(scenario_set.path).resolve().parent / "manifest.json"
            if workflow_name:
                try:
                    if manifest_file.exists():
                        data = json.loads(manifest_file.read_text(encoding="utf-8")) or {}
                    else:
                        data = {}
                    data["workflow_run"] = workflow_name
                    data["workflow_run_at"] = _utc_now()
                    manifest_file.parent.mkdir(parents=True, exist_ok=True)
                    manifest_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
    
            await manager.update_job(job_id, state="completed", completed=len(runs), total=progress["total"], finished_at=_utc_now(), runs_path=str(output_path), workflow_name=workflow_name)
        except Exception as exc:
            logger.exception("Scenario run %s failed", job_id)
            await manager.update_job(job_id, state="failed", error=str(exc), finished_at=_utc_now())
    
    def _resolve_runs_output_path(manifest_path: Path, scenario_path: Path, override: Optional[str]) -> Path:
        base_dir = manifest_path.parent.resolve()
        if override:
            candidate = Path(override)
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            return candidate
        return (scenario_path.parent / "generated_runs.json").resolve()
    
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
            output_path = _resolve_runs_output_path(manifest_path, file_path, payload.output_path)
            resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
            project_name = resources.get("project_name") or app.state.default_project
            manager: ScenarioRunManager = app.state.scenario_run_manager
            job = await manager.create_job(project_name, payload, len(scenarios))
            task = asyncio.create_task(_run_scenario_job(job.job_id, payload, manifest_path, scenario_set, scenarios, output_path))
            await manager.register_task(job.job_id, task)
            status = await manager.get_status(job.job_id)
            status_model = ScenarioRunStatusModel(**status) if status else None
            return {"job_id": job.job_id, "status": status_model.model_dump() if status_model else None}
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
                raise HTTPException(status_code=422, detail="Scenario prompt is required")
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
                    raise HTTPException(status_code=422, detail="Scenario dimensions must be an object")
            if scenario_id_value:
                for entry in entries:
                    entry_id_value = _scenario_id_value(entry.get("id"))
                    if entry_id_value == scenario_id_value:
                        raise HTTPException(status_code=409, detail="Scenario id already exists in this set")
            else:
                for entry in entries:
                    entry_id_value = _scenario_id_value(entry.get("id"))
                    if entry_id_value is None and entry.get("name") == name_value:
                        raise HTTPException(status_code=409, detail="Scenario name already exists in this set")
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
            located = _locate_scenario_entry(entries, payload.scenario_id, payload.scenario_name, payload.index)
            if not located:
                raise HTTPException(status_code=404, detail="Scenario not found in target set")
            idx, current_entry = located
            scenario_data = payload.scenario.model_dump(exclude_unset=True)
            name_value = str(scenario_data.get("name", "")).strip()
            if not name_value:
                raise HTTPException(status_code=422, detail="Scenario name is required")
            prompt_value = str(scenario_data.get("prompt", "")).strip()
            if not prompt_value:
                raise HTTPException(status_code=422, detail="Scenario prompt is required")
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
                    raise HTTPException(status_code=422, detail="Scenario dimensions must be an object")
            for entry_index, entry in enumerate(entries):
                if entry_index == idx:
                    continue
                other_id = _scenario_id_value(entry.get("id"))
                if new_id_value and other_id == new_id_value:
                    raise HTTPException(status_code=409, detail="Scenario id already exists in this set")
                if not new_id_value and other_id is None and entry.get("name") == name_value:
                    raise HTTPException(status_code=409, detail="Scenario name already exists in this set")
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
            located = _locate_scenario_entry(entries, payload.scenario_id, payload.scenario_name, payload.index)
            if not located:
                raise HTTPException(status_code=404, detail="Scenario not found in target set")
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
        return _build_metadata_response(base_spec, resources, request, agent_key=entry_key)
    
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
        agents_payload.sort(key=lambda item: (item.get("label") or item.get("key") or "").lower())
        return {"agents": agents_payload}
    
    @app.get("/api/agents/{agent_key}/metadata")
    async def get_agent_metadata(agent_key: str, request: Request):
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_key}' not found")
        return _build_metadata_response(spec, resources, request, agent_key=agent_key)
    
    @app.put("/api/agents/{agent_key}")
    async def update_agent(agent_key: str, payload: AgentUpdatePayload, request: Request):
        try:
            manifest_path = _get_manifest_path()
            ctx = load_project(str(manifest_path))
            agent_path = ctx.agents_map.get(agent_key)
            if not agent_path:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_key}' not found")
            try:
                raw_config = yaml.safe_load(agent_path.read_text(encoding="utf-8")) or {}
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
                    instructions_path = (agent_path.parent / instructions_path).resolve()
                instructions_path.parent.mkdir(parents=True, exist_ok=True)
                if instructions_path.exists():
                    backup_path = instructions_path.with_suffix(instructions_path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
                    shutil.copy2(instructions_path, backup_path)
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
            sanitized_safe_options["skip_approvals"] = bool(safe_options_input.get("skip_approvals", False))
            sanitized_safe_options["halt_on_rejection"] = bool(safe_options_input.get("halt_on_rejection", True))
            tool_set = set(tools_list)
            names_raw = safe_options_input.get("safe_tool_names") if isinstance(safe_options_input.get("safe_tool_names"), list) else []
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
            patterns_raw = safe_options_input.get("safe_tool_patterns") if isinstance(safe_options_input.get("safe_tool_patterns"), list) else []
            patterns_clean: List[str] = []
            for pattern in patterns_raw:
                if not isinstance(pattern, str):
                    continue
                trimmed = pattern.strip()
                if trimmed and trimmed not in patterns_clean:
                    patterns_clean.append(trimmed)
            sanitized_safe_options["safe_tool_patterns"] = patterns_clean
            if payload.use_safe_agent or sanitized_safe_options["safe_tool_names"] or sanitized_safe_options["safe_tool_patterns"] or sanitized_safe_options["skip_approvals"] or not sanitized_safe_options["halt_on_rejection"]:
                raw_config["safe_agent_options"] = sanitized_safe_options
            else:
                raw_config.pop("safe_agent_options", None)
            guardrails_input = payload.guardrails or {}
            input_guardrails = guardrails_input.get("input") if isinstance(guardrails_input.get("input"), list) else []
            output_guardrails = guardrails_input.get("output") if isinstance(guardrails_input.get("output"), list) else []
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
                if output_type_value in {"dict", "dictionary"} and payload.output_schema:
                    raw_config["output_schema"] = payload.output_schema
                else:
                    raw_config.pop("output_schema", None)
            _write_yaml_file(agent_path, raw_config)
            refreshed_ctx = load_project(str(manifest_path))
            base_spec = build_spec_from_project(refreshed_ctx)
            agent_specs: Dict[str, AgentSpec] = {}
            for key in sorted(refreshed_ctx.agents_map.keys()):
                if key == refreshed_ctx.entrypoint:
                    agent_specs[key] = base_spec
                else:
                    try:
                        agent_specs[key] = build_spec_from_project(refreshed_ctx, key)
                    except Exception:
                        continue
            resources = getattr(app.state, "playground_resources", {})
            updated_resources = dict(resources or {})
            project_name = refreshed_ctx.raw.get("name") or resources.get("project_name") or app.state.default_project
            updated_resources["project_name"] = project_name
            updated_resources["tool_registry"] = refreshed_ctx.tool_registry
            updated_resources["guardrails_registry"] = refreshed_ctx.guardrails_registry
            updated_resources["agent_specs"] = agent_specs
            updated_resources["entry_agent_key"] = refreshed_ctx.entrypoint
            updated_resources["manifest_path"] = str(manifest_path)
            base_reference = agent_specs.get(refreshed_ctx.entrypoint)
            if not base_reference and agent_specs:
                base_reference = next(iter(agent_specs.values()))
            if base_reference:
                updated_resources["base_spec"] = base_reference
                updated_resources["default_model_settings"] = base_reference.model_settings or {}
                updated_resources["default_safe_agent"] = bool(getattr(base_reference, "use_safe_agent", False))
                if base_reference.safe_agent_options:
                    updated_resources["default_safe_agent_options"] = asdict(base_reference.safe_agent_options)
                else:
                    updated_resources["default_safe_agent_options"] = {}
                updated_resources["default_guardrails"] = {
                    "input": _guardrail_keys(getattr(base_reference, "input_guardrails", None), refreshed_ctx.guardrails_registry.get("input", {})),
                    "output": _guardrail_keys(getattr(base_reference, "output_guardrails", None), refreshed_ctx.guardrails_registry.get("output", {})),
                }
            app.state.playground_resources = updated_resources
            spec = agent_specs.get(agent_key)
            if not spec:
                raise HTTPException(status_code=500, detail="Agent metadata unavailable after update")
            return _build_metadata_response(spec, updated_resources, request, agent_key=agent_key)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    async def _configure_session(agent_key: Optional[str], payload: PlaygroundSessionRequest) -> Dict[str, str]:
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            if agent_key:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_key}' not found")
            raise HTTPException(status_code=400, detail="Playground is not available")
    
        payload_data = payload.model_dump(exclude_unset=True)
    
        model_name = (payload.model or "").strip()
        if not model_name:
            raise HTTPException(status_code=422, detail="Model name is required")
    
        tool_registry: Dict[str, Any] = resources.get("tool_registry", {})
        guardrails_registry: Dict[str, Any] = resources.get("guardrails_registry", {"input": {}, "output": {}})
        tools_value = payload_data.get("tools")
        unknown_tools = [name for name in (tools_value or []) if name not in tool_registry]
        if unknown_tools:
            raise HTTPException(status_code=400, detail=f"Unknown tools: {', '.join(sorted(unknown_tools))}")
    
        store: PlaygroundSessionStore = app.state.playground_session_store
        config: Dict[str, Any] = {"model": model_name}
    
        if "instructions" in payload_data:
            config["instructions"] = str(payload_data.get("instructions") or "")
    
        if tools_value is not None:
            config["tools"] = tools_value or []
    
        if isinstance(payload.model_settings, dict):
            sanitized_settings = {k: v for k, v in payload.model_settings.items() if v not in (None, "")}
            if sanitized_settings:
                config["model_settings"] = sanitized_settings
    
        if payload.use_safe_agent is not None:
            config["use_safe_agent"] = bool(payload.use_safe_agent)
    
        if isinstance(payload.safe_agent_options, dict) and "safe_agent_options" in payload_data:
            safe_options: Dict[str, Any] = {}
            for key in ("skip_approvals", "halt_on_rejection"):
                if key in payload.safe_agent_options:
                    safe_options[key] = bool(payload.safe_agent_options[key])
            if "safe_tool_names" in payload.safe_agent_options and isinstance(payload.safe_agent_options["safe_tool_names"], list):
                safe_options["safe_tool_names"] = [
                    name
                    for name in payload.safe_agent_options["safe_tool_names"]
                    if isinstance(name, str) and name in tool_registry
                ]
            if "safe_tool_patterns" in payload.safe_agent_options and isinstance(payload.safe_agent_options["safe_tool_patterns"], list):
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
                guardrails_config["input"] = [name for name in input_names if isinstance(name, str) and name in input_registry]
            output_names = payload.guardrails.get("output")
            if isinstance(output_names, list):
                guardrails_config["output"] = [name for name in output_names if isinstance(name, str) and name in output_registry]
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
                    raise HTTPException(status_code=400, detail=f"Unsupported output_type '{raw_output_type}'")
        if "output_type_strict" in payload_data:
            raw_strict = payload_data.get("output_type_strict")
            if raw_strict is not None:
                config["output_type_strict"] = bool(raw_strict)
    
        if "output_schema" in payload_data:
            raw_schema = payload_data.get("output_schema")
            if raw_schema is not None:
                if not isinstance(raw_schema, dict):
                    raise HTTPException(status_code=400, detail="output_schema must be a JSON object")
                if normalized_for_schema not in (None, "dict", "dictionary", "object"):
                    raise HTTPException(status_code=400, detail="output_schema requires output_type 'dict'")
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
    async def configure_agent_session(agent_key: str, payload: PlaygroundSessionRequest):
        return await _configure_session(agent_key, payload)
    
    @app.get("/api/conversations")
    async def get_conversations(
        project: str = Query(..., description="Project name"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: Optional[str] = None,
        has_errors: Optional[bool] = None,
        workflow_name: Optional[str] = None,
        dim: Optional[List[str]] = Query(None, description="Synthetic dimension filters as 'dimension:value'")
    ):
        """Get conversations (grouped traces) with filtering and pagination"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"conversations": [], "total": 0}
            
            # Get many traces for coverage
            traces = storage.get_traces(limit=1000000)
            
            # Group by group_id
            conversations = {}
            for trace in traces:
                group_id = trace.get('group_id') or trace['trace_id']  # Use trace_id if no group
                
                if group_id not in conversations:
                    conversations[group_id] = {
                        'group_id': group_id,
                        'traces': [],
                        'trace_count': 0,
                        'has_errors': False,
                        'created_at': trace['created_at'],
                        'workflow_name': trace.get('workflow_name'),
                        'metadata': trace.get('metadata', {}),
                        'first_input': None  # Will extract from first trace
                    }
                
                conversations[group_id]['traces'].append(trace)
                conversations[group_id]['trace_count'] += 1
                
                # Check for errors in spans and extract first input from first trace
                spans = storage.get_spans_for_trace(trace['trace_id'])
                if any(s.get('error') for s in spans):
                    conversations[group_id]['has_errors'] = True
                
                # Extract first input from the first trace in the conversation
                if conversations[group_id]['first_input'] is None:
                    for span in spans:
                        try:
                            # Parse the data field
                            if isinstance(span.get('data'), str):
                                full_span = json.loads(span['data'])
                            else:
                                full_span = span.get('data', {})
                            
                            span_data = full_span.get('span_data', {})
                            
                            # Look for first user input in response or generation spans
                            if span.get('span_type') in ['response', 'generation'] and span_data.get('input'):
                                input_msgs = span_data['input']
                                user_msgs = [m for m in input_msgs if isinstance(m, dict) and m.get('role') == 'user']
                                if user_msgs:
                                    conversations[group_id]['first_input'] = user_msgs[0].get('content', '')
                                    break
                        except:
                            pass
            
            # Convert to list and sort by created_at
            conv_list = list(conversations.values())
            conv_list.sort(key=lambda x: x['created_at'], reverse=True)
            
            # Apply filters
            if search:
                conv_list = [c for c in conv_list 
                           if search.lower() in (c.get('workflow_name') or '').lower() 
                           or search.lower() in c['group_id'].lower()]
            
            if has_errors is not None:
                conv_list = [c for c in conv_list if c['has_errors'] == has_errors]
    
            if workflow_name:
                wn = workflow_name.lower()
                conv_list = [c for c in conv_list if (c.get('workflow_name') or '').lower() == wn]
    
            # Apply synthetic dimension filters
            if dim:
                dim_map: Dict[str, set] = {}
                for entry in dim:
                    try:
                        k, v = entry.split(":", 1)
                        dim_map.setdefault(k, set()).add(v)
                    except ValueError:
                        continue
                if dim_map:
                    filtered_conv = []
                    for conv in conv_list:
                        matched = False
                        for t in conv.get('traces', []):
                            md = t.get('metadata')
                            try:
                                if isinstance(md, str):
                                    meta = json.loads(md)
                                else:
                                    meta = md or {}
                            except:
                                meta = {}
                            mapping = meta.get('omniagents', {})\
                                               .get('synthetic', {})\
                                               .get('selection', {})\
                                               .get('mapping', {})
                            if all(mapping.get(k) in allowed for k, allowed in dim_map.items()):
                                matched = True
                                break
                        if matched:
                            filtered_conv.append(conv)
                    conv_list = filtered_conv
            
            # Apply pagination
            total = len(conv_list)
            paginated = conv_list[offset:offset + limit]
            
            # Remove full traces from response (just keep count)
            for conv in paginated:
                del conv['traces']
            
            return {
                "conversations": paginated,
                "total": total
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/synthetic_dimensions")
    async def get_synthetic_dimensions(
        project: str = Query(..., description="Project name"),
        workflow_name: Optional[str] = None
    ):
        """Get distinct synthetic dimension definitions for a project/workflow"""
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"dimensions": []}
            traces = storage.get_traces(limit=1000000)
            dims_map: Dict[str, Dict[str, Any]] = {}
            def display_name(s: str) -> str:
                return s.replace('_', ' ').title()
            for t in traces:
                if workflow_name and (t.get('workflow_name') or '').lower() != workflow_name.lower():
                    continue
                md = t.get('metadata')
                try:
                    if isinstance(md, str):
                        meta = json.loads(md)
                    else:
                        meta = md or {}
                except:
                    meta = {}
                dims = meta.get('omniagents', {})\
                           .get('synthetic', {})\
                           .get('catalog', {})\
                           .get('dimensions', [])
                for d in dims or []:
                    name = d.get('name')
                    if not name:
                        continue
                    label = d.get('label') or display_name(name)
                    if name not in dims_map:
                        dims_map[name] = { 'label': label, 'values': {} }
                    for v in d.get('values', []) or []:
                        vname = v.get('name')
                        if not vname:
                            continue
                        vlabel = v.get('label') or display_name(vname)
                        dims_map[name]['values'][vname] = vlabel
            dimensions = []
            for name, defn in dims_map.items():
                values = [{ 'name': vn, 'label': vl } for vn, vl in sorted(defn['values'].items())]
                dimensions.append({ 'name': name, 'label': defn['label'], 'values': values })
            dimensions.sort(key=lambda d: d['label'].lower())
            return { 'dimensions': dimensions }
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
    
    @app.get("/api/conversations/{project}/{group_id}/traces")
    async def get_conversation_traces(
        project: str,
        group_id: str
    ):
        """Get all traces in a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Get all traces for this group_id
            all_traces = storage.get_traces(limit=10000)
            
            # Filter by group_id (or traces where trace_id == group_id for single traces)
            # Make sure we have mutable dicts
            conversation_traces = []
            for t in all_traces:
                if t.get('group_id') == group_id or (not t.get('group_id') and t['trace_id'] == group_id):
                    # Create a new dict to ensure mutability
                    trace_dict = dict(t)
                    conversation_traces.append(trace_dict)
            
            # Sort by created_at
            conversation_traces.sort(key=lambda x: x['created_at'])
            
            # Add span counts and error flags, extract input/output
            for trace in conversation_traces:
                spans = storage.get_spans_for_trace(trace['trace_id'])
                trace['span_count'] = len(spans)
                trace['has_errors'] = any(s.get('error') for s in spans)
                
                # Ensure metadata exists and is a dict
                if 'metadata' not in trace:
                    trace['metadata'] = {}
                elif trace['metadata'] is None:
                    trace['metadata'] = {}
                elif isinstance(trace['metadata'], str):
                    try:
                        trace['metadata'] = json.loads(trace['metadata'])
                        if trace['metadata'] is None:
                            trace['metadata'] = {}
                    except:
                        trace['metadata'] = {}
                
                # Try to extract input/output from spans
                # Sort spans by sequence number to get chronological order
                sorted_spans = sorted(spans, key=lambda x: x.get('sequence_number', 0))
                
                trace_input = None
                trace_outputs = []
                previous_messages = []  # Track messages from previous traces
                
                for span in sorted_spans:
                    try:
                        # Parse the data field which contains the full span JSON
                        if isinstance(span.get('data'), str):
                            full_span = json.loads(span['data'])
                        else:
                            full_span = span.get('data', {})
                        
                        span_data = full_span.get('span_data', {})
                        
                        # Extract input from response or generation spans - find the NEW user input for THIS trace
                        if span.get('span_type') in ['response', 'generation'] and span_data.get('input') and not trace_input:
                            input_msgs = span_data['input']
                            
                            # Look through the input messages to find the most recent user message
                            # that isn't from a previous trace
                            user_msgs = []
                            for msg in input_msgs:
                                if isinstance(msg, dict) and msg.get('role') == 'user':
                                    user_msgs.append(msg.get('content', ''))
                            
                            # For the first trace, use the first user message
                            # For subsequent traces, use the last user message (most recent)
                            if user_msgs:
                                # Check if this is likely a continuation (has multiple user messages)
                                if len(user_msgs) > 1:
                                    # This is a continuation - use the last (most recent) user message
                                    trace_input = user_msgs[-1]
                                else:
                                    # This is the first interaction - use the first message
                                    trace_input = user_msgs[0]
                        
                        # Collect ALL response outputs (we'll use the last one)
                        if span.get('span_type') == 'response' and span_data.get('response'):
                            response = span_data['response']
                            if response.get('output') and isinstance(response['output'], list):
                                for output_item in response['output']:
                                    # Handle message type outputs (actual text responses)
                                    if output_item.get('type') == 'message' and output_item.get('content'):
                                        for content_item in output_item['content']:
                                            if content_item.get('type') == 'output_text':
                                                text = content_item.get('text', '')
                                                if text:
                                                    trace_outputs.append(text)
                                    # Handle function calls (show what function was called)
                                    elif output_item.get('type') == 'function_call':
                                        func_name = output_item.get('name', 'function')
                                        trace_outputs.append(f"→ {func_name}()")
                        
                        # Handle Chat Completions format (generation spans)
                        if span.get('span_type') == 'generation' and span_data.get('output'):
                            output_list = span_data['output']
                            if isinstance(output_list, list) and output_list:
                                # Chat Completions has nested structure: output[0].output[0].content[0].text
                                first_output = output_list[0]
                                if isinstance(first_output, dict) and 'output' in first_output:
                                    inner_outputs = first_output['output']
                                    if isinstance(inner_outputs, list):
                                        for item in inner_outputs:
                                            if isinstance(item, dict):
                                                # Check for message with content
                                                if item.get('type') == 'message' and item.get('role') == 'assistant':
                                                    content_items = item.get('content', [])
                                                    if isinstance(content_items, list):
                                                        for content in content_items:
                                                            if isinstance(content, dict) and content.get('text'):
                                                                trace_outputs.append(content['text'])
                                                # Check for function call
                                                elif item.get('type') == 'function_call':
                                                    func_name = item.get('name', 'function')
                                                    trace_outputs.append(f"→ {func_name}()")
                    except Exception as e:
                        pass
                
                # Use the LAST output as the trace output (final response)
                trace_output = trace_outputs[-1] if trace_outputs else None
                
                # Set the extracted input/output in metadata
                if trace_input:
                    trace['metadata']['input'] = trace_input
                if trace_output:
                    trace['metadata']['output'] = trace_output
                
                # Calculate duration from spans
                if spans:
                    started_times = [s['started_at'] for s in spans if s.get('started_at')]
                    ended_times = [s['ended_at'] for s in spans if s.get('ended_at')]
                    
                    if started_times and ended_times:
                        start = min(datetime.fromisoformat(t) for t in started_times)
                        end = max(datetime.fromisoformat(t) for t in ended_times)
                        trace['duration'] = (end - start).total_seconds() * 1000
                    else:
                        trace['duration'] = 0
                else:
                    trace['duration'] = 0
            
            return {
                "traces": conversation_traces,
                "group_id": group_id,
                "total": len(conversation_traces)
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
        span_type: Optional[str] = None
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
                    t for t in filtered_traces 
                    if search.lower() in (t.get('workflow_name') or '').lower()
                ]
            
            if has_errors is not None:
                # Check if trace has any spans with errors
                traces_with_errors = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    if any(s.get('error') for s in spans):
                        traces_with_errors.add(trace['trace_id'])
                
                if has_errors:
                    filtered_traces = [t for t in filtered_traces if t['trace_id'] in traces_with_errors]
                else:
                    filtered_traces = [t for t in filtered_traces if t['trace_id'] not in traces_with_errors]
            
            if span_type:
                # Filter traces that contain specific span type
                traces_with_type = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    if any(s.get('span_type') == span_type for s in spans):
                        traces_with_type.add(trace['trace_id'])
                filtered_traces = [t for t in filtered_traces if t['trace_id'] in traces_with_type]
            
            # Apply pagination
            paginated = filtered_traces[offset:offset + limit]
            
            return {
                "traces": paginated,
                "total": len(filtered_traces)
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
                span_data = json.loads(span.get('data', '{}'))
                processed_span = {
                    **span,
                    'data': span_data
                }
                
                # Calculate duration if both timestamps exist
                if span.get('started_at') and span.get('ended_at'):
                    start = datetime.fromisoformat(span['started_at'])
                    end = datetime.fromisoformat(span['ended_at'])
                    processed_span['duration'] = (end - start).total_seconds() * 1000
                
                processed_spans.append(processed_span)
            
            # Build span hierarchy
            span_map = {s['span_id']: s for s in processed_spans}
            for span in processed_spans:
                if span.get('parent_id') and span['parent_id'] in span_map:
                    parent = span_map[span['parent_id']]
                    if 'children' not in parent:
                        parent['children'] = []
                    parent['children'].append(span)
            
            return {
                "trace": trace,
                "spans": processed_spans
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/search")
    async def search(
        project: str = Query(..., description="Project name"),
        q: str = Query(..., min_length=1),
        type: str = Query("traces", regex="^(traces|spans)$")
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
                    t for t in traces
                    if q.lower() in (t.get('workflow_name') or '').lower() or
                       q.lower() in t.get('trace_id', '').lower()
                ]
            else:
                # Search in spans
                traces = storage.get_traces(limit=1000)
                for trace in traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    for span in spans:
                        span_data = json.loads(span.get('data', '{}'))
                        if (q.lower() in span.get('span_name', '').lower() or
                            q.lower() in span.get('span_type', '').lower() or
                            q.lower() in json.dumps(span_data).lower()):
                            results.append(span)
            
            return {
                "results": results[:100],  # Limit results
                "total": len(results)
            }
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
                    "spans_by_type": {}
                }
            
            traces = storage.get_traces(limit=10000)
            total_spans = 0
            error_count = 0
            durations = []
            spans_by_type = {}
            
            for trace in traces:
                spans = storage.get_spans_for_trace(trace['trace_id'])
                total_spans += len(spans)
                
                for span in spans:
                    # Count errors
                    if span.get('error'):
                        error_count += 1
                    
                    # Count by type
                    span_type = span.get('span_type', 'unknown')
                    spans_by_type[span_type] = spans_by_type.get(span_type, 0) + 1
                    
                    # Calculate duration
                    if span.get('started_at') and span.get('ended_at'):
                        start = datetime.fromisoformat(span['started_at'])
                        end = datetime.fromisoformat(span['ended_at'])
                        duration = (end - start).total_seconds() * 1000
                        durations.append(duration)
            
            avg_duration = sum(durations) / len(durations) if durations else 0
            error_rate = (error_count / total_spans * 100) if total_spans > 0 else 0
            
            return {
                "total_traces": len(traces),
                "total_spans": total_spans,
                "error_rate": error_rate,
                "avg_duration": avg_duration,
                "spans_by_type": spans_by_type
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/analysis/{project}")
    async def get_all_analysis(project: str, workflow_name: Optional[str] = None):
        """Get all analysis data for a project, optionally filtered by workflow name"""
        try:
            storage = storage_manager.get_storage(project)
            analysis_data = storage.get_all_analysis(project)
            
            # If a workflow filter is provided, determine which group_ids belong to it
            if workflow_name:
                traces = storage.get_traces(limit=10000)
                wn = workflow_name.lower()
                allowed_group_ids = set()
                for t in traces:
                    twn = (t.get('workflow_name') or '').lower()
                    if twn == wn:
                        gid = t.get('group_id') or t.get('trace_id')
                        if gid:
                            allowed_group_ids.add(gid)
                # Filter analysis to only allowed group_ids
                analysis_data = [a for a in analysis_data if a.get('group_id') in allowed_group_ids]
            
            # Convert to frontend format
            result = {}
            for item in analysis_data:
                result[item['group_id']] = {
                    'markedForAnalysis': bool(item['marked_for_analysis']),
                    'judgment': item['judgment'],
                    'notes': item['notes'] or '',
                    'analyzedAt': item['analyzed_at']
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
                    'markedForAnalysis': False,
                    'judgment': None,
                    'notes': '',
                    'analyzedAt': None
                }
            
            return {
                'markedForAnalysis': bool(analysis['marked_for_analysis']),
                'judgment': analysis['judgment'],
                'notes': analysis['notes'] or '',
                'analyzedAt': analysis['analyzed_at']
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.put("/api/analysis/{project}/{group_id}")
    async def update_analysis(
        project: str,
        group_id: str,
        analysis_data: Dict[str, Any]
    ):
        """Update or create analysis data for a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            storage.upsert_analysis(project, group_id, analysis_data)
            
            # Return the updated data
            return {
                'markedForAnalysis': analysis_data.get('markedForAnalysis', False),
                'judgment': analysis_data.get('judgment'),
                'notes': analysis_data.get('notes', ''),
                'analyzedAt': analysis_data.get('analyzedAt')
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
        """Get all failure categories for a project"""
        try:
            storage = storage_manager.get_storage(project)
            categories = storage.get_failure_categories(project)
            
            # Get usage stats
            usage_stats = storage.get_category_usage_stats(project)
            
            # Add usage count to each category
            for category in categories:
                category['usageCount'] = usage_stats.get(category['id'], 0)
            
            return categories
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/categories/{project}")
    async def create_failure_category(
        project: str,
        category_data: Dict[str, Any]
    ):
        """Create a new failure category"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Validate required fields
            if 'name' not in category_data or not category_data['name'].strip():
                raise HTTPException(status_code=400, detail="Category name is required")
            
            # Create the category
            category_id = storage.create_failure_category(
                project,
                category_data['name'].strip(),
                category_data.get('description', '').strip() or None
            )
            
            return {
                'id': category_id,
                'name': category_data['name'].strip(),
                'description': category_data.get('description', '').strip() or None,
                'usageCount': 0
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="Category with this name already exists")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/categories/{project}/{category_id}")
    async def delete_failure_category(project: str, category_id: int):
        """Delete a failure category"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Get usage count before deletion
            usage_count = storage.get_category_usage(project, category_id)
            
            # Delete the category
            deleted = storage.delete_failure_category(project, category_id)
            
            if not deleted:
                raise HTTPException(status_code=404, detail="Category not found")
            
            return {
                'message': 'Category deleted successfully',
                'previousUsageCount': usage_count
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/analysis/{project}/{group_id}/categories")
    async def get_analysis_categories(project: str, group_id: str):
        """Get categories for a specific analysis"""
        try:
            storage = storage_manager.get_storage(project)
            category_ids = storage.get_analysis_categories(group_id)
            return {'categoryIds': category_ids}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.put("/api/analysis/{project}/{group_id}/categories")
    async def update_analysis_categories(
        project: str,
        group_id: str,
        data: Dict[str, Any]
    ):
        """Update categories for an analysis"""
        try:
            storage = storage_manager.get_storage(project)
            category_ids = data.get('categoryIds', [])
            
            # Validate that all category IDs are integers
            try:
                category_ids = [int(cat_id) for cat_id in category_ids]
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid category IDs")
            
            storage.set_analysis_categories(project, group_id, category_ids)
            return {'categoryIds': category_ids}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/export/{project}/{trace_id}")
    async def export_trace(
        project: str,
        trace_id: str,
        format: str = Query("json", regex="^(json|csv)$")
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
                data = {
                    "trace": trace,
                    "spans": spans
                }
                return StreamingResponse(
                    io.BytesIO(json.dumps(data, indent=2).encode()),
                    media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.json"}
                )
            else:
                # CSV format
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=[
                    'span_id', 'span_type', 'span_name', 'started_at', 'ended_at', 
                    'parent_id', 'sequence_number', 'error'
                ])
                writer.writeheader()
                
                for span in spans:
                    writer.writerow({
                        'span_id': span.get('span_id'),
                        'span_type': span.get('span_type'),
                        'span_name': span.get('span_name'),
                        'started_at': span.get('started_at'),
                        'ended_at': span.get('ended_at'),
                        'parent_id': span.get('parent_id'),
                        'sequence_number': span.get('sequence_number'),
                        'error': 'Yes' if span.get('error') else 'No'
                    })
                
                return StreamingResponse(
                    io.BytesIO(output.getvalue().encode()),
                    media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.csv"}
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.websocket("/api/evaluation/scenarios/run/{job_id}/ws")
    async def scenario_run_ws(job_id: str, websocket: WebSocket):
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
    
    @app.websocket("/api/ws/traces")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket for real-time trace updates"""
        await manager.connect(websocket)
        try:
            while True:
                # Keep connection alive
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            manager.disconnect(websocket)
    
    @app.websocket("/api/playground/ws")
    async def playground_ws(websocket: WebSocket):
        logger.debug("[playground] incoming websocket request %s", websocket.query_params)
        resources: Dict[str, Any] = getattr(app.state, "playground_resources", {})
        session_id = websocket.query_params.get("session_id")
        if not session_id:
            logger.debug("[playground] closing websocket: session_id missing")
            await websocket.close(code=4400)
            return
    
        store: PlaygroundSessionStore = app.state.playground_session_store
        config = await store.get(session_id)
        if not config:
            logger.debug("[playground] closing websocket: session config missing for %s", session_id)
            await websocket.close(code=4404)
            return
    
        agent_key = config.get("__agent_key")
        spec = _resolve_agent_spec(resources, agent_key)
        if not spec:
            logger.debug("[playground] closing websocket: spec missing for agent %s", agent_key)
            await websocket.close(code=4404)
            return
        session_label = config.get("__session_label") or ("playground" if not agent_key else f"agent:{agent_key}")
    
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
    
        service = AgentService(spec)
        transport = ServerWebSocketTransport(websocket)
    
        try:
            logger.debug("[playground] accepting websocket for session %s", session_id)
            await transport.connect()
            channel = JsonRpcChannel(transport)
            service.channel = channel
            while transport.is_connected():
                try:
                    data = await transport.receive_json()
                except TransportClosedError:
                    break
                except WebSocketDisconnect:
                    break
    
                call_id = data.get("id")
                method = data.get("method")
                params = data.get("params") or data.get("args") or {}
    
                if method:
                    try:
                        func = getattr(service, method)
                        result = await func(**params)
                        if call_id is not None:
                            await channel.send_result(call_id, result)
                    except Exception as exc:
                        logger.exception("[playground] error handling method %s", method)
                        if call_id is not None:
                            error_payload = {
                                "jsonrpc": "2.0",
                                "id": call_id,
                                "error": {
                                    "code": -32603,
                                    "message": str(exc),
                                    "data": {
                                        "type": exc.__class__.__name__,
                                        "method": method,
                                    },
                                },
                            }
                            await transport.send_json(error_payload)
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
        # Prefer packaged assets under studio/server/static; fall back to local web dist for dev
        candidate_dirs = [
            Path(__file__).parent / "static",
            Path(__file__).parent / "web" / "dist",
        ]
        for static_dir in candidate_dirs:
            if static_dir.exists():
                app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
                break
    
    return app, manager
