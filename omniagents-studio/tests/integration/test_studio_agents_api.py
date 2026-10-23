"""The Agents screen's API: what each agent's runtime resolves, and editing.

``GET /api/agents/<key>`` asks the agent's real runtime
(``AgentService.describe_agent``), so Studio shows what runs. Edits go
through omniagents' agent-file writer: only the edited settings change,
nothing that would not load is written, and the agent's runtime is
rebuilt from the saved files.
"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omniagents.core.project.runtime import load_project
from omniagents_studio.api import create_api_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    destination = tmp_path / "project"
    shutil.copytree(fixture_root, destination)
    return destination


@pytest.fixture
def client(project):
    context = load_project(str(project / "project.yml"))
    app = create_api_app(
        default_project="agents",
        project_resources={
            "project_name": context.product.slug,
            "manifest_path": str(project / "project.yml"),
        },
    )
    with TestClient(app) as test_client:
        yield test_client


def test_lists_the_project_agents_entry_first(client):
    agents = client.get("/api/agents").json()["agents"]
    assert [agent["key"] for agent in agents] == ["primary", "secondary"]
    assert agents[0]["is_entry"] is True
    assert agents[0]["label"] == "Primary Agent"
    assert agents[0]["error"] is None


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
    assert described["guardrails"]["input"] == ["input_policy"]
    assert described["output"]["name"] == "PrimaryOutput"
    assert set(described["output"]["schema"]["required"]) == {"summary", "count"}
    assert described["instructions"]["error"] is None
    assert described["instructions"]["text"]


def test_an_unknown_agent_is_not_found(client):
    assert client.get("/api/agents/nobody").status_code == 404


def test_options_list_what_an_agent_can_name(client):
    options = client.get("/api/agents/options").json()
    assert "greet_tool" in options["tools"]
    assert "filesystem" in {c["name"] for c in options["capabilities"]}
    assert options["agents"] == ["primary", "secondary"]
    assert set(options["guardrails"]) == {
        "input",
        "output",
        "tool_input",
        "tool_output",
    }


def test_an_edit_is_saved_in_place_and_the_runtime_runs_it(client, project):
    agent_yml = project / "agents/primary/agent.yml"
    before = agent_yml.read_text()

    saved = client.patch(
        "/api/agents/primary",
        json={"changes": {"model": "gpt-5.5"}, "instructions": "Be brief."},
    )

    assert saved.status_code == 200
    assert agent_yml.read_text() == before.replace("gpt-4o", "gpt-5.5")
    assert (
        client.get("/api/agents/primary/source").json()["instructions"] == "Be brief."
    )
    described = client.get("/api/agents/primary").json()
    assert described["model"]["name"] == "gpt-5.5"
    assert described["instructions"]["text"] == "Be brief."


def test_an_edit_that_would_not_load_is_refused_with_the_problems(client, project):
    agent_yml = project / "agents/primary/agent.yml"
    before = agent_yml.read_text()

    refused = client.patch(
        "/api/agents/primary", json={"changes": {"capabilities": ["no_such_pack"]}}
    )
    renamed = client.put(
        "/api/agents/primary/source",
        json={"yaml": before.replace("name: primary", "name: other")},
    )

    assert refused.status_code == 422
    assert any("no_such_pack" in p for p in refused.json()["detail"]["problems"])
    assert renamed.status_code == 422
    assert agent_yml.read_text() == before


def test_agents_are_created_and_deleted(client):
    created = client.post(
        "/api/agents", json={"key": "analyst", "model": "gpt-5.5", "label": "Analyst"}
    )
    assert created.status_code == 200
    assert "analyst" in [a["key"] for a in client.get("/api/agents").json()["agents"]]

    removed = client.delete("/api/agents/analyst")
    assert removed.json() == {"removed": "analyst", "entrypoint": "primary"}
    assert "analyst" not in [
        a["key"] for a in client.get("/api/agents").json()["agents"]
    ]


def test_an_agent_that_does_not_load_is_listed_to_be_fixed(client, project):
    (project / "agents/secondary/agent.yml").write_text("name: secondary\nmodel: [\n")
    client.patch("/api/agents/primary", json={"changes": {"label": "Primary"}})

    secondary = [
        a for a in client.get("/api/agents").json()["agents"] if a["key"] == "secondary"
    ]
    assert secondary[0]["error"]
    assert client.get("/api/agents/secondary").status_code == 422
    assert "model: [" in client.get("/api/agents/secondary/source").json()["yaml"]
