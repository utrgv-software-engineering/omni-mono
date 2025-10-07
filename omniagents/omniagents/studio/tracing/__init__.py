import atexit
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agents.tracing import add_trace_processor, set_trace_processors

from omniagents.core.paths import get_traces_dir

from .processor import StudioTraceProcessor, RemoteStudioTraceProcessor, AgentIOProcessor, RemoteAgentIOProcessor
from .storage import SQLiteStorage
from .utils import generate_readable_name

logger = logging.getLogger(__name__)

__version__ = "0.1.4"

_processor = None
_server_url = None
_current_name = None
_default_project = None


def init(
    project: Optional[str] = "default",
    name: Optional[str] = None,
    server_url: Optional[str] = None,
    db_path: Optional[str] = None,
    token: Optional[str] = None,
    resume: str = "never",
    replace_default: bool = True,
) -> str:
    """Initialize OmniAgents Studio tracing."""
    global _processor, _server_url, _current_name, _default_project

    _default_project = project

    if project is None:
        if replace_default:
            set_trace_processors([])
        print("\n📊 OmniAgents Studio tracing disabled (OpenAI tracing only)")
        print("💡 No Studio traces will be recorded.\n")
        return None

    if _processor:
        _processor.shutdown()
        _processor = None

    if server_url:
        _processor = RemoteStudioTraceProcessor(
            endpoint=f"{server_url.rstrip('/')}/api/trace",
            project=_default_project,
            token=token,
        )
        _server_url = server_url
        logger.info("OmniAgents Studio tracing sending to %s", server_url)
    else:
        _processor = StudioTraceProcessor(default_project=_default_project)

        resolved_db_path: Optional[Path] = None
        if db_path:
            resolved_db_path = Path(db_path).expanduser().resolve()
        elif _default_project:
            resolved_db_path = get_traces_dir() / f"{_default_project}.db"

        if resolved_db_path is not None:
            temp_storage = None

            try:
                if resolved_db_path.exists():
                    temp_storage = SQLiteStorage(str(resolved_db_path))
                    existing_traces = [t["workflow_name"] for t in temp_storage.get_traces()]
                else:
                    existing_traces = []

                if resume == "must":
                    if name is None:
                        raise ValueError("Must provide a trace name when resume='must'")
                    if name not in existing_traces:
                        raise ValueError(f"Trace '{name}' does not exist in project '{project}'")
                elif resume == "allow":
                    if name is not None and name in existing_traces:
                        print(f"* Resuming existing trace: {name}")
                elif resume == "never":
                    if name is not None and name in existing_traces:
                        for index in range(1000):
                            new_name = f"{name}-{index}"
                            if new_name not in existing_traces:
                                name = new_name
                                break
                else:
                    raise ValueError("resume must be one of: 'must', 'allow', or 'never'")

                if name is None:
                    name = generate_readable_name(existing_traces)

                _current_name = name
            finally:
                if temp_storage:
                    temp_storage.close()

        _server_url = None
        print(f"\n📊 OmniAgents Studio tracing initialized for project: {_default_project}")
        if _current_name:
            print(f"📝 Trace name: {_current_name}")
        if _default_project:
            db_hint = resolved_db_path or (get_traces_dir() / f"{_default_project}.db")
            print(f"💾 Database: {db_hint}")
            print(f"💡 Run 'omniagents studio serve --project {_default_project}' to view traces\n")
        else:
            print("💡 Use trace() with project parameter to record traces\n")

    if replace_default:
        set_trace_processors([_processor])
    else:
        add_trace_processor(_processor)

    atexit.register(_cleanup)

    return _server_url


def show(project: Optional[str] = None):
    """Display tracing launch instructions."""
    global _server_url, _default_project, _current_name

    if _processor is None and _server_url is None:
        init(project=project or "default")

    if _processor and not _server_url:
        target_project = project or _default_project or "default"
        db_hint = get_traces_dir() / f"{target_project}.db"
        print(f"\n📊 OmniAgents Studio project: {target_project}")
        print(f"💾 Database: {db_hint}")
        if _current_name:
            print(f"📝 Current trace: {_current_name}")
        print(f"\n💡 To view traces, run: omniagents studio serve --project {target_project}")
    elif _server_url:
        print(f"Remote mode - server at: {_server_url}")
    else:
        print("OmniAgents Studio tracing not initialized")


def serve(
    host: str = "0.0.0.0",
    port: int = 7860,
    db_path: Optional[str] = None,
    project: Optional[str] = None,
    auth_token: Optional[str] = None,
):
    """Run the OmniAgents Studio tracing server."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError("uvicorn is required for server mode. Install with: pip install uvicorn")

    traces_dir = get_traces_dir()
    storage = None
    active_project = None
    created_db = False

    if db_path:
        resolved = Path(db_path).expanduser().resolve()
        existed = resolved.exists()
        storage = SQLiteStorage(str(resolved))
        created_db = not existed
        active_project = project or resolved.stem
    else:
        if project:
            resolved = traces_dir / f"{project}.db"
            existed = resolved.exists()
            storage = SQLiteStorage(str(resolved))
            created_db = not existed
            active_project = project
        else:
            db_files = sorted(traces_dir.glob("*.db"))
            if len(db_files) == 1:
                resolved = db_files[0]
                storage = SQLiteStorage(str(resolved))
                active_project = resolved.stem
                print(f"📂 Found one database, using: {active_project}")
            elif len(db_files) > 1:
                print("📂 Found multiple project databases:\n")
                for db in db_files:
                    try:
                        temp_storage = SQLiteStorage(str(db))
                        trace_count = len(temp_storage.get_traces(limit=1))
                        temp_storage.close()
                        size_kb = db.stat().st_size / 1024
                        print(f"  • {db.stem:<20} ({size_kb:.1f} KB)")
                    except Exception:
                        print(f"  • {db.stem:<20}")

                print("\n✨ Serving all projects. Use the project selector in the UI to switch between them.")
                print("📌 To serve only a specific project, use: omniagents studio serve --project <name>")
            else:
                print(f"📭 No trace databases found in {traces_dir}")
                print("✨ Studio will create databases as traces are recorded or when --project is provided.")

    from omniagents.studio.server import create_api_app

    app, _ = create_api_app(storage, default_project=active_project)

    if storage:
        print(f"🚀 OmniAgents Studio tracing server running at http://{host}:{port}")
        print(f"💾 Database: {storage.db_path}")
        if created_db:
            print("🆕 Initialized new database")
    else:
        print(f"🚀 OmniAgents Studio tracing server running at http://{host}:{port}")
        print(f"📊 Multi-project mode - all databases in {traces_dir} are accessible")
    print(f"🎨 UI: http://{host}:{port}")
    if auth_token:
        print("🔐 Authentication: Enabled")

    uvicorn.run(app, host=host, port=port)


def export(output_path: str, format: str = "json", project: Optional[str] = None):
    """Export traces to a file."""
    target_project = project or _default_project
    if not target_project:
        raise ValueError("Project must be specified for export")

    db_path = get_traces_dir() / f"{target_project}.db"
    if not db_path.exists():
        raise ValueError(f"Project database not found: {target_project}")
    storage = SQLiteStorage(str(db_path))

    traces = storage.get_traces(limit=10000)

    if format == "json":
        import json

        export_data = []
        for trace in traces:
            trace_export = trace.copy()
            trace_export["spans"] = storage.get_spans_for_trace(trace["trace_id"])
            export_data.append(trace_export)

        with open(output_path, "w") as handle:
            json.dump(export_data, handle, indent=2, default=str)

    elif format == "csv":
        import csv

        with open(output_path, "w", newline="") as handle:
            if traces:
                writer = csv.DictWriter(handle, fieldnames=traces[0].keys())
                writer.writeheader()
                writer.writerows(traces)

    elif format == "parquet":
        try:
            import pandas as pd

            df = pd.DataFrame(traces)
            df.to_parquet(output_path)
        except ImportError:
            raise ImportError("pandas and pyarrow are required for parquet export. Install with: pip install pandas pyarrow")

    else:
        raise ValueError(f"Unknown format: {format}")

    print(f"Exported {len(traces)} traces to {output_path}")
    storage.close()


def _cleanup():
    global _processor

    if _processor:
        try:
            _processor.shutdown()
        except Exception:
            pass


from .trace import trace

__all__ = [
    "init",
    "show",
    "serve",
    "export",
    "trace",
    "__version__",
    "get_current_project",
    "set_conversation_analysis",
    "append_conversation_note",
    "AgentIOProcessor",
    "RemoteAgentIOProcessor",
    "StudioTraceProcessor",
    "RemoteStudioTraceProcessor",
]


def get_current_project() -> Optional[str]:
    return _default_project


def set_conversation_analysis(
    group_id: str,
    *,
    judgment: Optional[str] = None,
    notes: Optional[str] = None,
    marked: bool = True,
    analyzed_at: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    target_project = project or _default_project or "default"
    db_path = get_traces_dir() / f"{target_project}.db"
    storage = SQLiteStorage(str(db_path))
    existing = storage.get_analysis(target_project, group_id) or {}
    final_notes = notes if notes is not None else existing.get("notes", "")
    final_judgment = judgment if judgment is not None else existing.get("judgment")
    final_analyzed_at = analyzed_at or existing.get("analyzed_at") or datetime.now(timezone.utc).isoformat()
    payload = {
        "markedForAnalysis": bool(marked),
        "judgment": final_judgment,
        "notes": final_notes,
        "analyzedAt": final_analyzed_at,
    }
    storage.upsert_analysis(target_project, group_id, payload)
    return payload


def append_conversation_note(
    group_id: str,
    text: str,
    *,
    project: Optional[str] = None,
    separator: str = "\n",
) -> dict:
    target_project = project or _default_project or "default"
    db_path = get_traces_dir() / f"{target_project}.db"
    storage = SQLiteStorage(str(db_path))
    existing = storage.get_analysis(target_project, group_id) or {}
    base_notes = existing.get("notes", "") or ""
    joined = base_notes + (separator if base_notes else "") + text
    payload = {
        "markedForAnalysis": existing.get("marked_for_analysis", False),
        "judgment": existing.get("judgment"),
        "notes": joined,
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
    }
    storage.upsert_analysis(target_project, group_id, payload)
    return payload
