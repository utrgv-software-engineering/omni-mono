import json
from types import SimpleNamespace

import omniagents.agentio as agentio


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
        return {key: value for key, value in data.items() if value is not None}


class DummySpan:
    def __init__(
        self,
        span_id,
        trace_id,
        span_type,
        name,
        response=None,
        input_payload=None,
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
            "span_data": {
                "type": self.span_data.type,
                "name": self.span_data.name,
            },
        }


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self):
        return self.payload


class MissingTraceSpan:
    def __init__(self, span_id, span_type, name):
        self.span_id = span_id
        self.span_data = SimpleNamespace(
            type=span_type, name=name, response=None, input=None
        )

    def export(self):
        return {
            "id": self.span_id,
            "span_data": {
                "type": self.span_data.type,
                "name": self.span_data.name,
            },
        }


def test_agentio_serializes_trace_and_response(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = agentio.AgentIOProcessor(default_project="fallback")
    trace = DummyTrace(
        "trace-serialize",
        metadata={"studio_project": "project-a"},
        group_id="group-1",
        workflow_name="workflow-a",
    )
    processor.on_trace_start(trace)
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: trace)
    response = DummyResponse({"text": "ok"})
    span = DummySpan(
        "span-serialize",
        "trace-serialize",
        span_type="response",
        name="completion",
        response=response,
        input_payload={"prompt": "hello"},
    )
    processor.on_span_end(span)
    storage = processor._get_storage("project-a")
    stored_trace = storage.get_trace("trace-serialize")
    assert stored_trace is not None
    assert json.loads(stored_trace["metadata"])["studio_project"] == "project-a"
    spans = storage.get_spans_for_trace("trace-serialize")
    assert len(spans) == 1
    payload = json.loads(spans[0]["data"])
    assert payload["span_data"]["response"] == {"text": "ok"}
    assert payload["span_data"]["input"] == {"prompt": "hello"}
    directory = dir(agentio)
    assert "trace" in directory
    processor.shutdown()


def test_agentio_rejects_reserved_metadata(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = agentio.AgentIOProcessor(default_project="fallback")
    trace = DummyTrace(
        "trace-invalid",
        metadata={"studio_project": "project-b", "trace_id": "bad"},
    )
    processor.on_trace_start(trace)
    storage = processor._get_storage("project-b")
    assert storage.get_trace("trace-invalid") is None
    processor.shutdown()


def test_agentio_skips_spans_missing_trace_id(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    processor = agentio.AgentIOProcessor(default_project="fallback")
    trace = DummyTrace(
        "trace-span",
        metadata={"studio_project": "project-c"},
    )
    processor.on_trace_start(trace)
    import agents.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "get_current_trace", lambda: trace)
    span = MissingTraceSpan("span-missing", span_type="response", name="completion")
    processor.on_span_end(span)
    storage = processor._get_storage("project-c")
    assert storage.get_spans_for_trace("trace-span") == []
    processor.shutdown()
