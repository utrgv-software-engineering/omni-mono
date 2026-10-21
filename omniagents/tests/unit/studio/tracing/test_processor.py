import json
from types import SimpleNamespace

import pytest

import omniagents.studio.tracing.processor as processor_module
from omniagents.studio.tracing.processor import (
    RemoteStudioTraceProcessor,
    StudioTraceProcessor,
)
from omniagents.studio.tracing.storage import SQLiteStorage


class TraceStub:
    def __init__(self, trace_id, metadata=None, group_id=None, workflow_name=None):
        self.trace_id = trace_id
        self.metadata = metadata
        self.group_id = group_id
        self.workflow_name = workflow_name

    def export(self):
        payload = {"id": self.trace_id}
        if self.workflow_name is not None:
            payload["workflow_name"] = self.workflow_name
        if self.group_id is not None:
            payload["group_id"] = self.group_id
        if self.metadata is not None:
            payload["metadata"] = self.metadata
        return payload


class SpanStub:
    def __init__(self, span_id, trace_id, span_data=None):
        self.span_id = span_id
        self.trace_id = trace_id
        if span_data is not None:
            self.span_data = span_data

    def export(self):
        payload = {"id": self.span_id, "trace_id": self.trace_id}
        if hasattr(self, "span_data"):
            payload["span_data"] = {
                "type": getattr(self.span_data, "type", None),
                "name": getattr(self.span_data, "name", None),
            }
        return payload


class ResponseStub:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self):
        return self.payload


@pytest.fixture
def home_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    return home


def test_get_project_prefers_metadata():
    processor = StudioTraceProcessor(default_project="fallback")
    trace_primary = TraceStub("trace-primary", metadata={"studio_project": "alpha"})
    assert processor._get_project_from_trace(trace_primary) == "alpha"
    trace_default = TraceStub("trace-default")
    assert processor._get_project_from_trace(trace_default) == "fallback"


def test_on_trace_start_invalid_metadata_logs(home_env, caplog):
    processor = StudioTraceProcessor(default_project="fallback")
    bad_trace = TraceStub(
        "bad-trace",
        metadata={"trace_id": "reserved"},
        group_id="group",
        workflow_name="wf",
    )
    caplog.set_level("DEBUG")
    processor.on_trace_start(bad_trace)
    storage = processor._get_storage("fallback")
    assert storage.get_traces() == []
    assert "Failed to insert trace" in caplog.text
    processor.shutdown()


def test_on_span_end_unknown_type(home_env, monkeypatch):
    processor = StudioTraceProcessor(default_project="fallback")
    trace = TraceStub(
        "trace-x",
        metadata={"studio_project": "proj-x"},
        group_id="group",
        workflow_name="wf",
    )
    processor.on_trace_start(trace)
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: trace)
    span = SpanStub("span-unknown", "trace-x")
    processor.on_span_end(span)
    storage = processor._get_storage("proj-x")
    spans = storage.get_spans_for_trace("trace-x")
    assert spans[0]["span_type"] == "unknown"
    payload = json.loads(spans[0]["data"])
    assert payload["id"] == "span-unknown"
    processor.shutdown()


def test_processor_shutdown_closes_storages(home_env, monkeypatch):
    closed = []

    def fake_close(self):
        closed.append(str(self.db_path))

    monkeypatch.setattr(SQLiteStorage, "close", fake_close)
    processor = StudioTraceProcessor(default_project="fallback")
    processor._get_storage("first")
    processor._get_storage("second")
    processor.shutdown()
    assert len(closed) == 2
    assert processor._storages == {}


def test_remote_processor_requires_httpx(monkeypatch):
    monkeypatch.setattr(processor_module, "httpx", None)
    with pytest.raises(ImportError):
        RemoteStudioTraceProcessor("http://example/api/trace", "proj")


def test_remote_processor_queue_and_flush(monkeypatch, caplog):
    posts = []
    closed = []

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def get(self, url, timeout=None):
            return None

        def post(self, endpoint, json=None, headers=None, timeout=None):
            posts.append((endpoint, json, headers, timeout))

        def close(self):
            closed.append(True)

    class DummyTimeout:
        def __init__(self, value):
            self.value = value

    dummy_httpx = SimpleNamespace(Client=DummyClient, Timeout=DummyTimeout)
    monkeypatch.setattr(processor_module, "httpx", dummy_httpx)

    class ControllableThread:
        def __init__(self, target):
            self._target = target
            self.daemon = False
            self.started = False

        def start(self):
            self.started = True

        def trigger(self):
            self._target()

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(processor_module.threading, "Thread", ControllableThread)
    remote = RemoteStudioTraceProcessor(
        "http://example/api/trace", "proj", token="secret"
    )
    remote._send("trace_start", {"id": "queued"})
    assert len(remote._queued_events) == 1
    remote._client_thread.trigger()
    assert len(remote._queued_events) == 0
    assert posts[0][1]["event"] == "trace_start"
    assert posts[0][2]["Authorization"] == "Bearer secret"
    trace = TraceStub(
        "trace-1",
        metadata={"studio_project": "alpha"},
        group_id="group",
        workflow_name="wf",
    )
    remote.on_trace_start(trace)
    remote.on_trace_end(trace)
    response_span = SpanStub(
        "span-response",
        "trace-1",
        span_data=SimpleNamespace(
            type="response",
            name="reply",
            response=ResponseStub({"text": "ok"}),
            input={"prompt": "ask"},
        ),
    )
    remote.on_span_end(response_span)
    generation_span = SpanStub(
        "span-generation",
        "trace-1",
        span_data=SimpleNamespace(
            type="generation",
            name="generate",
            input={"prompt": "write"},
        ),
    )
    remote.on_span_end(generation_span)
    events = [payload[1] for payload in posts]
    span_event = next(
        item for item in events if item["data"].get("id") == "span-response"
    )
    response_payload = span_event["data"]["span_data"]
    assert response_payload["response"] == {"text": "ok"}
    assert response_payload["input"] == {"prompt": "ask"}
    gen_event = next(
        item for item in events if item["data"].get("id") == "span-generation"
    )
    assert gen_event["data"]["span_data"]["input"] == {"prompt": "write"}
    caplog.set_level("DEBUG")
    remote.on_trace_start(TraceStub("bad", metadata={"trace_id": "reserved"}))
    assert "Failed to process trace start" in caplog.text
    remote.force_flush()
    remote.shutdown()
    assert closed
    assert isinstance(remote.client, DummyClient)
