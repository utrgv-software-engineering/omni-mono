import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.studio.tracing.processor import StudioTraceProcessor
from omniagents.studio.tracing.storage import SQLiteStorage


@pytest.fixture()
def storage(tmp_path):
    db_path = tmp_path / "project.db"
    store = SQLiteStorage(str(db_path))
    yield store
    store.close()


def test_sqlite_storage_initializes_tables_and_wal(tmp_path):
    db_path = tmp_path / "project.db"
    store = SQLiteStorage(str(db_path))
    try:
        cur = store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert {"traces", "spans", "analysis"}.issubset(tables)
        columns = store.conn.execute("PRAGMA table_info(analysis)").fetchall()
        column_names = {row[1] for row in columns}
        assert "categories" in column_names
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        store.close()


def test_sqlite_storage_migrates_legacy_categories(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE analysis (
                group_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                marked_for_analysis BOOLEAN,
                judgment TEXT,
                notes TEXT,
                analyzed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO analysis (group_id, project, marked_for_analysis, judgment, notes) VALUES (?, ?, ?, ?, ?)",
            ("group-1", "proj", 1, "acceptable", "sample"),
        )
        conn.execute(
            "CREATE TABLE analysis_categories (group_id TEXT, category_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_categories (group_id, category_id) VALUES (?, ?)",
            [("group-1", 1), ("group-1", 2)],
        )
        conn.execute(
            "CREATE TABLE failure_categories (id INTEGER PRIMARY KEY, name TEXT)"
        )
        conn.executemany(
            "INSERT INTO failure_categories (id, name) VALUES (?, ?)",
            [(1, "Critical Failure"), (2, "Edge Case")],
        )
        conn.commit()
    finally:
        conn.close()
    store = SQLiteStorage(str(db_path))
    try:
        row = store.conn.execute(
            "SELECT categories FROM analysis WHERE group_id = ?",
            ("group-1",),
        ).fetchone()
        assert row is not None
        categories = json.loads(row[0])
        assert categories == ["critical_failure", "edge_case"]
        remaining = store.conn.execute(
            "SELECT COUNT(*) FROM analysis_categories"
        ).fetchone()[0]
        assert remaining == 0
    finally:
        store.close()


def test_insert_and_bulk_insert_assign_sequence_and_preserve_error(storage):
    storage.insert_trace(
        {
            "id": "trace-1",
            "workflow_name": "wf",
            "group_id": "group-a",
            "metadata": {"key": "value"},
        }
    )
    storage.insert_span(
        {
            "id": "span-1",
            "trace_id": "trace-1",
            "span_data": {"type": "generation", "name": "first"},
            "started_at": "2024-01-01T00:00:00Z",
            "ended_at": "2024-01-01T00:00:01Z",
        }
    )
    storage.bulk_insert_spans(
        [
            {
                "id": "span-2",
                "trace_id": "trace-1",
                "span_data": {"type": "response", "name": "second"},
                "error": {"message": "oops"},
            },
            {
                "id": "span-3",
                "trace_id": "trace-1",
                "span_data": {"type": "tool", "name": "third"},
            },
        ]
    )
    spans = storage.get_spans_for_trace("trace-1")
    assert [row["sequence_number"] for row in spans] == [0, 1, 2]
    assert spans[0]["span_type"] == "generation"
    assert spans[1]["span_name"] == "second"
    assert json.loads(spans[1]["error"]) == {"message": "oops"}
    payload = json.loads(spans[2]["data"])
    assert payload["id"] == "span-3"
    assert payload["span_data"]["type"] == "tool"


def test_analysis_helpers_manage_categories_and_counts(storage):
    storage.upsert_analysis(
        "proj-a",
        "group-1",
        {
            "markedForAnalysis": True,
            "judgment": "acceptable",
            "notes": "note",
            "analyzedAt": "2024-01-01T00:00:00Z",
        },
    )
    storage.upsert_analysis(
        "proj-a",
        "group-2",
        {
            "markedForAnalysis": False,
            "judgment": "unacceptable",
            "notes": "note-2",
            "analyzedAt": None,
        },
    )
    storage.set_analysis_categories("proj-a", "group-1", ["beta", "alpha", "alpha"])
    storage.set_analysis_categories("proj-a", "group-2", ["beta"])
    assert storage.get_analysis("proj-a", "group-1")["judgment"] == "acceptable"
    categories = storage.get_analysis_categories("group-1")
    assert categories == ["alpha", "beta"]
    usage = storage.get_analysis_category_usage("proj-a")
    assert usage == {"alpha": 1, "beta": 2}
    storage.remove_analysis_category_slug("proj-a", "beta")
    assert storage.get_analysis_categories("group-1") == ["alpha"]
    assert storage.get_analysis_categories("group-2") == []
    all_analysis = storage.get_all_analysis("proj-a")
    assert {row["group_id"] for row in all_analysis} == {"group-1", "group-2"}
    storage.delete_analysis("group-2")
    assert storage.get_analysis("proj-a", "group-2") is None


def test_get_projects_discovers_databases(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    traces_dir = home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    first = SQLiteStorage(str(traces_dir / "one.db"))
    second = SQLiteStorage(str(traces_dir / "two.db"))
    try:
        assert SQLiteStorage.get_projects() == ["one", "two"]
    finally:
        first.close()
        second.close()


class DummyTrace:
    def __init__(self, trace_id, metadata=None, group_id=None, workflow_name=None):
        self.trace_id = trace_id
        self.metadata = metadata
        self.group_id = group_id
        self.workflow_name = workflow_name

    def export(self):
        data = {
            "id": self.trace_id,
            "trace_id": self.trace_id,
            "group_id": self.group_id,
            "workflow_name": self.workflow_name,
            "metadata": self.metadata,
        }
        return {k: v for k, v in data.items() if v is not None}


class DummySpan:
    def __init__(
        self, span_id, trace_id, span_type, name, response=None, input_payload=None
    ):
        self.span_id = span_id
        self.trace_id = trace_id
        self.span_data = SimpleNamespace(
            type=span_type,
            name=name,
            response=response,
            input=input_payload,
        )

    def export(self):
        return {
            "id": self.span_id,
            "trace_id": self.trace_id,
            "span_data": {"type": self.span_data.type, "name": self.span_data.name},
        }


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self):
        return self.payload


def test_processor_routes_traces_by_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = StudioTraceProcessor(default_project="fallback")
    try:
        trace = DummyTrace(
            "trace-1",
            metadata={"studio_project": "project-x"},
            group_id="group-1",
            workflow_name="wf",
        )
        processor.on_trace_start(trace)
        default_trace = DummyTrace("trace-2")
        processor.on_trace_start(default_trace)
        storage_x = processor._get_storage("project-x")
        storage_default = processor._get_storage("fallback")
        assert Path(home / "traces" / "project-x.db").exists()
        assert storage_x.get_trace("trace-1")["group_id"] == "group-1"
        traces_default = storage_default.get_traces()
        assert any(row["trace_id"] == "trace-2" for row in traces_default)
    finally:
        processor.shutdown()


def test_processor_augments_response_spans(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = StudioTraceProcessor(default_project="fallback")
    trace = DummyTrace(
        "trace-1",
        metadata={"studio_project": "project-y"},
        group_id="group-a",
        workflow_name="wf",
    )
    processor.on_trace_start(trace)
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: trace)
    response = DummyResponse({"text": "hello"})
    span = DummySpan(
        "span-1",
        "trace-1",
        span_type="response",
        name="completion",
        response=response,
        input_payload={"prompt": "hi"},
    )
    processor.on_span_end(span)
    storage_y = processor._get_storage("project-y")
    spans = storage_y.get_spans_for_trace("trace-1")
    assert len(spans) == 1
    payload = json.loads(spans[0]["data"])
    assert payload["span_data"]["response"] == {"text": "hello"}
    assert payload["span_data"]["input"] == {"prompt": "hi"}
    assert spans[0]["span_type"] == "response"
    processor.shutdown()
