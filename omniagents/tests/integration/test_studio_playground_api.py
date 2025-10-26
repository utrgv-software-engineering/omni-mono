import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.studio.server.api import (
    build_playground_spec,
    create_api_app,
    _guardrail_keys,
)


@pytest.fixture
def studio_project(tmp_path):
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    destination = tmp_path / "project"
    shutil.copytree(fixture_root, destination)
    return destination


@pytest.fixture
def studio_app(studio_project):
    manifest_path = studio_project / "project.yml"
    context = load_project(str(manifest_path))
    agent_specs = {
        key: build_spec_from_project(context, key) for key in context.agents_map.keys()
    }
    base_spec = agent_specs[context.entrypoint]
    resources = {
        "project_name": context.raw.get("name"),
        "tool_registry": context.tool_registry,
        "guardrails_registry": context.guardrails_registry,
        "agent_specs": agent_specs,
        "entry_agent_key": context.entrypoint,
        "manifest_path": str(manifest_path),
        "base_spec": base_spec,
        "default_model_settings": base_spec.model_settings or {},
        "default_safe_agent": bool(getattr(base_spec, "use_safe_agent", False)),
        "default_safe_agent_options": (
            asdict(base_spec.safe_agent_options) if base_spec.safe_agent_options else {}
        ),
        "default_guardrails": {
            "input": _guardrail_keys(
                getattr(base_spec, "input_guardrails", None),
                context.guardrails_registry.get("input", {}),
            ),
            "output": _guardrail_keys(
                getattr(base_spec, "output_guardrails", None),
                context.guardrails_registry.get("output", {}),
            ),
        },
    }
    app, _ = create_api_app(
        default_project=context.raw.get("name"), playground_resources=resources
    )
    return app


@pytest.fixture
def studio_client(studio_app):
    client = TestClient(studio_app)
    try:
        yield client
    finally:
        client.close()


def test_get_studio_project_available(studio_client, studio_project):
    response = studio_client.get("/api/studio/project")
    data = response.json()
    assert data["available"] is True
    assert data["project"] == "Studio Fixture"
    assert Path(data["manifest_path"]) == studio_project / "project.yml"


def test_get_studio_project_missing_manifest():
    app, _ = create_api_app(default_project="Fallback", playground_resources={})
    with TestClient(app) as client:
        data = client.get("/api/studio/project").json()
    assert data == {
        "available": False,
        "project": "Fallback",
        "manifest_path": None,
    }


def test_get_playground_metadata_reports_project_state(studio_app, studio_client):
    studio_app.state.playground_resources["project_name"] = None
    response = studio_client.get("/api/playground/metadata")
    data = response.json()
    assert data["available"] is True
    assert data["project_name"] == "Studio Fixture"
    assert data["entry_agent"] == "primary"
    assert data["agent_key"] == "primary"
    assert data["agent_label"] == "Primary Agent"
    assert data["default_model"] == "gpt-4o"
    assert "Primary agent instructions" in data["default_instructions"]
    assert data["playground_ws_url"].startswith("ws://testserver/api/playground/ws")
    structured = data["structured_output"]
    assert structured["default_type"] == "custom"
    assert structured["default_label"] == "PrimaryOutput"
    assert structured["default_strict"] is False
    assert structured["default_schema"]["title"] == "PrimaryOutput"
    assert structured["default_schema"]["type"] == "object"
    assert structured["default_schema"]["required"] == ["summary", "count"]
    assert data["guardrails"]["default_input"] == ["input_policy"]
    assert data["guardrails"]["default_output"] == ["output_policy"]
    assert data["safe_defaults"] == {
        "use_safe_agent": True,
        "options": {
            "skip_approvals": False,
            "halt_on_rejection": True,
            "safe_tool_names": ["greet_tool"],
            "safe_tool_patterns": [],
        },
    }
    tool_names = [item["name"] for item in data["tools"]]
    assert "greet_tool" in tool_names


def test_configure_playground_session_persists_overrides(studio_app, studio_client):
    payload = {
        "model": "gpt-4.1-mini",
        "instructions": "override",
        "tools": ["greet_tool"],
        "model_settings": {"temperature": 0.7, "top_p": None},
        "use_safe_agent": False,
        "safe_agent_options": {
            "skip_approvals": True,
            "halt_on_rejection": False,
            "safe_tool_names": ["greet_tool", "missing"],
            "safe_tool_patterns": ["allow.*", ""],
        },
        "guardrails": {
            "input": ["input_policy", "ghost"],
            "output": ["output_policy"],
        },
        "output_type": "dict",
        "output_type_strict": True,
        "output_schema": {
            "type": "object",
            "properties": {"extra": {"type": "string"}},
        },
    }
    response = studio_client.post("/api/playground/session", json=payload)
    session_id = response.json()["session_id"]
    store = studio_app.state.playground_session_store
    stored = asyncio.run(store.get(session_id))
    assert stored["model"] == "gpt-4.1-mini"
    assert stored["instructions"] == "override"
    assert stored["tools"] == ["greet_tool"]
    assert stored["model_settings"] == {"temperature": 0.7}
    assert stored["safe_agent_options"] == {
        "skip_approvals": True,
        "halt_on_rejection": False,
        "safe_tool_names": ["greet_tool"],
        "safe_tool_patterns": ["allow.*"],
    }
    assert stored["guardrails"] == {
        "input": ["input_policy"],
        "output": ["output_policy"],
    }
    assert stored["output_type"] == "dict"
    assert stored["output_type_strict"] is True
    assert stored["output_schema"] == {
        "type": "object",
        "properties": {"extra": {"type": "string"}},
    }
    assert stored["__agent_key"] is None
    assert stored["__session_label"] == "playground"
    resources = studio_app.state.playground_resources
    spec = build_playground_spec(
        resources["base_spec"],
        resources["tool_registry"],
        resources["guardrails_registry"],
        stored,
        session_label="playground",
    )
    assert spec.model_name == "gpt-4.1-mini"
    assert len(spec.available_tools) == 1
    assert spec.available_tools[0].name == "greet_tool"
    assert spec.use_safe_agent is False
    assert spec.safe_agent_options.skip_approvals is True
    assert spec.safe_agent_options.halt_on_rejection is False
    assert spec.safe_agent_options.safe_tool_names == ["greet_tool"]
    assert spec.safe_agent_options.safe_tool_patterns == ["allow.*"]
    assert getattr(spec.output_type, "__name__", "") == "primaryStructuredOutput"
    assert spec.output_type_strict is True
    assert spec.tracing_config.get("workflow_name") == "playground"


def test_configure_agent_session_uses_defaults_and_reuses_id(studio_app, studio_client):
    payload = {
        "model": "gpt-4o-mini",
        "session_id": "existing",
        "instructions": "agent override",
        "output_type": "default",
        "guardrails": {"input": ["input_policy"], "output": []},
    }
    response = studio_client.post("/api/agents/secondary/session", json=payload)
    assert response.json()["session_id"] == "existing"
    store = studio_app.state.playground_session_store
    stored = asyncio.run(store.get("existing"))
    assert stored["model"] == "gpt-4o-mini"
    assert stored["instructions"] == "agent override"
    assert "output_type" not in stored
    assert "output_schema" not in stored
    assert "output_type_strict" not in stored
    assert stored["guardrails"] == {"input": ["input_policy"], "output": []}
    assert stored["__agent_key"] == "secondary"
    assert stored["__session_label"] == "agent:secondary"
    resources = studio_app.state.playground_resources
    secondary_spec = resources["agent_specs"]["secondary"]
    spec = build_playground_spec(
        secondary_spec,
        resources["tool_registry"],
        resources["guardrails_registry"],
        stored,
        session_label="agent:secondary",
    )
    assert spec.output_type is str
    assert spec.output_type_strict is True
    assert spec.use_safe_agent is False
    assert spec.model_name == "gpt-4o-mini"
    assert spec.tracing_config.get("workflow_name") == "agent:secondary"
