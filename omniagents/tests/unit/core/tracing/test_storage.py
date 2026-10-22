import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.core.tracing.processor import LocalTraceProcessor
from omniagents.core.tracing.storage import SQLiteStorage


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
        conn.execute("""
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
            """)
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


def test_sqlite_storage_migrates_categories_without_failure_table(tmp_path):
    db_path = tmp_path / "legacy_no_failure.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
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
            """)
        conn.execute(
            "INSERT INTO analysis (group_id, project) VALUES (?, ?)",
            ("group-2", "proj"),
        )
        conn.execute(
            "CREATE TABLE analysis_categories (group_id TEXT, category_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_categories (group_id, category_id) VALUES (?, ?)",
            [("group-2", 3), ("group-2", 1)],
        )
        conn.commit()
    finally:
        conn.close()
    store = SQLiteStorage(str(db_path))
    try:
        row = store.conn.execute(
            "SELECT categories FROM analysis WHERE group_id = ?",
            ("group-2",),
        ).fetchone()
        assert row is not None
        categories = json.loads(row[0])
        assert categories == ["1", "3"]
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


def test_get_traces_by_group_and_workflow_names(storage):
    storage.insert_trace(
        {
            "id": "trace-1",
            "workflow_name": "beta",
            "group_id": "group-x",
        }
    )
    storage.insert_trace(
        {
            "id": "trace-2",
            "workflow_name": "Alpha",
            "group_id": "group-x",
        }
    )
    storage.insert_trace(
        {
            "id": "trace-3",
            "workflow_name": "",
            "group_id": "group-y",
        }
    )
    storage.insert_trace(
        {
            "id": "trace-4",
            "group_id": "group-y",
        }
    )
    group_traces = storage.get_traces_by_group("group-x")
    assert {row["trace_id"] for row in group_traces} == {"trace-1", "trace-2"}
    names = storage.get_workflow_names()
    assert names == ["Alpha", "beta"]


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


def test_insert_trace_handles_unserializable_metadata(storage):
    class CustomObject:
        def __init__(self):
            self.value = {1, 2}

    metadata = {
        "timestamp": datetime(2024, 1, 1, 12, 0, 0),
        "paths": {Path("alpha"), Path("beta")},
        "bytes": b"payload",
        "custom": CustomObject(),
    }
    storage.insert_trace(
        {
            "id": "trace-meta",
            "workflow_name": "wf",
            "metadata": metadata,
        }
    )
    row = storage.get_trace("trace-meta")
    assert row is not None
    parsed = json.loads(row["metadata"])
    assert parsed["timestamp"].startswith("2024-01-01T12:00:00")
    assert sorted(parsed["paths"]) == ["alpha", "beta"]
    assert set(parsed["custom"]["value"]) == {1, 2}
    assert parsed["bytes"] == "payload"


def test_insert_span_ignores_duplicate_ids(storage):
    storage.insert_trace({"id": "trace-dup", "workflow_name": "wf"})
    payload = {
        "id": "span-dup",
        "trace_id": "trace-dup",
        "span_data": {"type": "tool", "name": "duplicate"},
    }
    storage.insert_span(dict(payload))
    storage.insert_span(dict(payload))
    spans = storage.get_spans_for_trace("trace-dup")
    assert len(spans) == 1
    assert spans[0]["sequence_number"] == 0


def test_bulk_insert_spans_handles_duplicates_without_gaps(storage):
    storage.insert_trace({"id": "trace-bulk", "workflow_name": "wf"})
    storage.insert_span(
        {
            "id": "span-0",
            "trace_id": "trace-bulk",
            "span_data": {"type": "generation", "name": "first"},
        }
    )
    storage.bulk_insert_spans(
        [
            {
                "id": "span-0",
                "trace_id": "trace-bulk",
                "span_data": {"type": "generation", "name": "first"},
            },
            {
                "id": "span-1",
                "trace_id": "trace-bulk",
                "span_data": {"type": "response", "name": "second"},
                "error": {"details": {Path("here"), "value"}},
            },
            {
                "id": "span-2",
                "trace_id": "trace-bulk",
                "span_data": {"type": "tool", "name": "third"},
            },
        ]
    )
    spans = storage.get_spans_for_trace("trace-bulk")
    assert [row["sequence_number"] for row in spans] == [0, 1, 2]
    assert len(spans) == 3
    payload = json.loads(spans[2]["data"])
    assert payload["id"] == "span-2"


def test_bulk_insert_spans_handles_multiple_traces(storage):
    storage.insert_trace({"id": "trace-one", "workflow_name": "wf"})
    storage.insert_trace({"id": "trace-two", "workflow_name": "wf"})
    storage.bulk_insert_spans(
        [
            {
                "id": "span-one-0",
                "trace_id": "trace-one",
                "span_data": {"type": "generation", "name": "first"},
            },
            {
                "id": "span-two-0",
                "trace_id": "trace-two",
                "span_data": {"type": "generation", "name": "other"},
            },
            {
                "id": "span-one-1",
                "trace_id": "trace-one",
                "span_data": {"type": "tool", "name": "second"},
            },
        ]
    )
    spans_one = storage.get_spans_for_trace("trace-one")
    spans_two = storage.get_spans_for_trace("trace-two")
    assert [row["sequence_number"] for row in spans_one] == [0, 1]
    assert [row["sequence_number"] for row in spans_two] == [0]


def test_insert_span_concurrent_sequence_numbers_are_contiguous(storage):
    storage.insert_trace({"id": "trace-thread", "workflow_name": "wf"})
    workers = 5
    per_worker = 6

    def worker(prefix: str) -> None:
        for index in range(per_worker):
            storage.insert_span(
                {
                    "id": f"{prefix}-{index}",
                    "trace_id": "trace-thread",
                    "span_data": {"type": "test", "name": f"{prefix}-{index}"},
                }
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker, f"worker-{i}") for i in range(workers)]
        for future in futures:
            future.result()

    spans = storage.get_spans_for_trace("trace-thread")
    assert len(spans) == workers * per_worker
    assert [row["sequence_number"] for row in spans] == list(range(len(spans)))


def test_garbage_collect_vacuum_no_data(storage):
    result = storage.garbage_collect(vacuum=True)
    assert result == {"traces": 0, "spans": 0, "analysis": 0}


def test_garbage_collect_prunes_old_traces_and_limits_total(storage):
    for index in range(5):
        trace_id = f"trace-{index}"
        storage.insert_trace({"id": trace_id, "workflow_name": "wf"})
        storage.insert_span(
            {
                "id": f"span-{index}",
                "trace_id": trace_id,
                "span_data": {"type": "test", "name": f"span-{index}"},
            }
        )
    storage.upsert_analysis(
        "proj",
        "group-old",
        {
            "markedForAnalysis": True,
            "judgment": "acceptable",
            "notes": "old",
            "analyzedAt": None,
        },
    )
    storage.upsert_analysis(
        "proj",
        "group-new",
        {
            "markedForAnalysis": False,
            "judgment": "unacceptable",
            "notes": "new",
            "analyzedAt": None,
        },
    )
    now = datetime.now(UTC)
    timestamps = {
        "trace-0": now - timedelta(days=2),
        "trace-1": now - timedelta(days=2, minutes=5),
        "trace-2": now - timedelta(minutes=30),
        "trace-3": now - timedelta(minutes=20),
        "trace-4": now - timedelta(minutes=10),
    }
    old_mark = now - timedelta(days=2)
    with storage.conn:
        storage.conn.execute(
            "UPDATE analysis SET updated_at = ? WHERE group_id = ?",
            (old_mark.strftime("%Y-%m-%d %H:%M:%S"), "group-old"),
        )
        for trace_id, stamp in timestamps.items():
            storage.conn.execute(
                "UPDATE traces SET created_at = ? WHERE trace_id = ?",
                (stamp.strftime("%Y-%m-%d %H:%M:%S"), trace_id),
            )
    result = storage.garbage_collect(max_age_hours=24, retain_last=2)
    assert result["traces"] == 3
    assert result["spans"] == 3
    assert result["analysis"] == 1
    remaining = {row["trace_id"] for row in storage.get_traces(limit=10)}
    assert remaining == {"trace-4", "trace-3"}
    assert storage.get_trace("trace-0") is None
    assert storage.get_analysis("proj", "group-old") is None
    assert storage.get_analysis("proj", "group-new") is not None


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
    processor = LocalTraceProcessor(default_project="fallback")
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
    processor = LocalTraceProcessor(default_project="fallback")
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


def test_processor_handles_generation_span_inputs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = LocalTraceProcessor(default_project="fallback")
    trace = DummyTrace(
        "trace-gen",
        metadata={"studio_project": "project-gen"},
        group_id="group-gen",
        workflow_name="wf",
    )
    processor.on_trace_start(trace)
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: trace)
    span = DummySpan(
        "span-gen",
        "trace-gen",
        span_type="generation",
        name="generate",
        input_payload={"prompt": "content"},
    )
    processor.on_span_end(span)
    storage = processor._get_storage("project-gen")
    spans = storage.get_spans_for_trace("trace-gen")
    assert len(spans) == 1
    payload = json.loads(spans[0]["data"])
    assert payload["span_data"]["input"] == {"prompt": "content"}
    assert "response" not in payload["span_data"]
    processor.shutdown()


def test_processor_skips_span_without_active_trace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = LocalTraceProcessor(default_project="fallback")
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: None)
    span = DummySpan("span-orphan", "trace-missing", span_type="generation", name="gen")
    processor.on_span_end(span)
    assert processor._storages == {}
    processor.shutdown()


def test_bulk_insert_spans_noop(storage):
    storage.bulk_insert_spans([])
    assert storage.get_spans_for_trace("missing") == []


def test_set_analysis_categories_creates_record(storage):
    storage.set_analysis_categories("proj-new", "group-new", [])
    record = storage.get_analysis("proj-new", "group-new")
    assert record is not None
    assert record["categories"] is None


def test_remove_analysis_category_slug_ignores_empty(storage):
    storage.set_analysis_categories("proj-slug", "group-slug", ["one"])
    storage.remove_analysis_category_slug("proj-slug", " ")
    assert storage.get_analysis_categories("group-slug") == ["one"]


def test_get_traces_respects_limit(storage):
    for index in range(3):
        storage.insert_trace(
            {
                "id": f"trace-{index}",
                "workflow_name": "wf",
                "group_id": "group",
            }
        )
    traces = storage.get_traces(limit=2)
    assert len(traces) == 2
