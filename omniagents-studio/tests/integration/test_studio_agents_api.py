"""The Agents screen's API: what each project agent's runtime resolves.

``GET /api/agents/<key>`` asks the agent's real runtime
(``AgentService.describe_agent``), so Studio shows what runs. No endpoint
writes agent files: people edit them themselves.
"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents_studio.api import create_api_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    project = tmp_path / "project"
    shutil.copytree(fixture_root, project)
    context = load_project(str(project / "project.yml"))
    app = create_api_app(
        default_project="agents",
        project_resources={
            "project_name": context.product.slug,
            "manifest_path": str(project / "project.yml"),
            "agent_specs": {
                key: build_spec_from_project(context, key)
                for key in context.agents_map.keys()
            },
            "entry_agent_key": context.entrypoint,
        },
    )
    with TestClient(app) as test_client:
        yield test_client


def test_lists_the_project_agents_entry_first(client):
    agents = client.get("/api/agents").json()["agents"]
    assert [agent["key"] for agent in agents] == ["primary", "secondary"]
    assert agents[0]["is_entry"] is True
    assert agents[0]["label"] == "Primary Agent"


def test_describes_what_a_run_of_the_agent_is_given(client):
    described = client.get("/api/agents/primary").json()
    assert described["model"]["name"] == "gpt-4o"
    assert described["model"]["settings"]["temperature"] == 0.4
    tools = {tool["name"]: tool for tool in described["tools"]}
    # The YAML lists one tool; the runtime also gives every agent its
    # retained-output reader. Both are what a run has.
    assert set(tools) == {"greet_tool", "read_tool_output"}
    # The safe agent decides per call (greet_tool is on its allow list).
    assert tools["greet_tool"]["approval"] == "conditional"
    assert described["policy"]["approvals"] == "safe_agent"
    assert described["policy"]["safe_tool_names"] == ["greet_tool"]
    assert described["instructions"]["error"] is None
    assert described["instructions"]["text"]


def test_an_unknown_agent_is_not_found(client):
    assert client.get("/api/agents/nobody").status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/agents"),
        ("put", "/api/agents/primary"),
        ("delete", "/api/agents/primary"),
    ],
)
def test_no_endpoint_writes_agent_files(client, method, path):
    assert client.request(method.upper(), path).status_code == 405
