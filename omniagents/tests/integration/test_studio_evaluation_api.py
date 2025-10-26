import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import omniagents.studio.server.api as studio_api
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.studio.server.api import create_api_app
from omniagents.studio.tracing.storage import SQLiteStorage


@pytest.fixture
def studio_environment(tmp_path):
    fixtures_root = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "projects"
        / "studio_playground"
    )
    project_dir = tmp_path / "studio_project"
    shutil.copytree(fixtures_root, project_dir)
    storage_path = tmp_path / "storage" / "demo.db"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(str(storage_path))
    app, _ = create_api_app(storage=storage, default_project="demo")
    manifest_path = project_dir / "project.yml"
    ctx = load_project(str(manifest_path))
    base_spec = build_spec_from_project(ctx)
    resources = app.state.playground_resources
    resources["manifest_path"] = str(manifest_path)
    resources["project_name"] = "demo"
    resources["base_spec"] = base_spec
    resources["agent_specs"] = {ctx.entrypoint: base_spec}
    resources["entry_agent_key"] = ctx.entrypoint
    resources["tool_registry"] = ctx.tool_registry
    resources["guardrails_registry"] = ctx.guardrails_registry
    client = TestClient(app)
    return SimpleNamespace(
        client=client,
        project_dir=project_dir,
        manifest_path=manifest_path,
        storage=storage,
        app=app,
    )


def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data):
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def test_dimensions_round_trip_persists_updates(studio_environment):
    client = studio_environment.client
    evaluation_path = studio_environment.project_dir / "evaluations" / "evaluation.yml"
    response = client.get("/api/evaluation/dimensions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == "demo"
    assert payload["dimensions"][0]["name"] == "tone"
    updated_payload = {
        "dimensions": [
            {
                "name": "tone",
                "label": "Tone",
                "description": "How responses sound",
                "values": [
                    {
                        "name": "formal",
                        "label": "Formal",
                        "description": "Formal tone",
                        "examples": ["Example"],
                    },
                    {
                        "name": "casual",
                        "label": "Casual",
                        "description": "Casual tone",
                        "examples": [],
                    },
                ],
            },
            {
                "name": "topic",
                "label": "Topic",
                "description": None,
                "values": [],
            },
        ]
    }
    update_response = client.put("/api/evaluation/dimensions", json=updated_payload)
    assert update_response.status_code == 200
    persisted = update_response.json()["dimensions"]
    assert persisted[0]["values"][0]["examples"] == ["Example"]
    saved = read_yaml(evaluation_path)
    dims = saved["evaluation"]["synthetic_data_generation"]["dimensions"]
    assert dims[0]["values"][0]["examples"] == ["Example"]
    assert dims[1]["name"] == "topic"
    repeat = client.get("/api/evaluation/dimensions")
    assert repeat.status_code == 200
    assert repeat.json()["dimensions"][0]["values"][0]["examples"] == ["Example"]


def test_measures_crud_updates_files_and_counts(studio_environment):
    client = studio_environment.client
    project_dir = studio_environment.project_dir
    measures_response = client.post(
        "/api/evaluation/measures",
        json={
            "name": "risk_score",
            "label": "Risk Score",
            "description": "Evaluates risk",
            "createStub": True,
        },
    )
    assert measures_response.status_code == 200
    evaluation_path = project_dir / "evaluations" / "evaluation.yml"
    evaluation_yaml = read_yaml(evaluation_path)
    measure_names = [m["name"] for m in evaluation_yaml["evaluation"]["measures"]]
    assert "risk_score" in measure_names
    stub_path = project_dir / "evaluations" / "measures.py"
    stub_text = stub_path.read_text(encoding="utf-8")
    assert stub_text.count("def risk_score") == 1
    scenarios_path = project_dir / "evaluations" / "scenarios.yml"
    scenarios_yaml = read_yaml(scenarios_path)
    scenarios_yaml["scenarios"][0]["measures"] = ["risk_score"]
    write_yaml(scenarios_path, scenarios_yaml)
    update_response = client.put(
        "/api/evaluation/measures",
        json={
            "name": "risk_score",
            "label": "Risk Score Updated",
            "description": "Updated description",
        },
    )
    assert update_response.status_code == 200
    refreshed = read_yaml(evaluation_path)
    target = next(
        item
        for item in refreshed["evaluation"]["measures"]
        if item["name"] == "risk_score"
    )
    assert target["label"] == "Risk Score Updated"
    measures_list = client.get("/api/evaluation/measures").json()["measures"]
    usage = next(item for item in measures_list if item["name"] == "risk_score")
    assert usage["usageCount"] == 1
    delete_response = client.delete("/api/evaluation/measures/risk_score")
    assert delete_response.status_code == 200
    after_delete = read_yaml(evaluation_path)
    names_after = [m["name"] for m in after_delete["evaluation"].get("measures", [])]
    assert "risk_score" not in names_after
    stub_after = stub_path.read_text(encoding="utf-8")
    assert stub_after.count("def risk_score") == 1


def test_failure_categories_crud_and_usage(studio_environment):
    client = studio_environment.client
    project_dir = studio_environment.project_dir
    create_measure = client.post(
        "/api/evaluation/measures",
        json={
            "name": "risk_score",
            "label": "Risk Score",
            "description": "Evaluates risk",
            "createStub": False,
        },
    )
    assert create_measure.status_code == 200
    response = client.post(
        "/api/categories/demo",
        json={
            "name": "Safety Check",
            "description": "Detects unsafe outputs",
            "measures": ["risk_score"],
        },
    )
    assert response.status_code == 200
    category = response.json()
    assert category["id"] == "safety_check"
    scenarios_path = project_dir / "evaluations" / "scenarios.yml"
    scenarios_yaml = read_yaml(scenarios_path)
    scenarios_yaml["scenarios"][0]["failure_categories"] = ["Safety Check"]
    write_yaml(scenarios_path, scenarios_yaml)
    studio_environment.storage.set_analysis_categories(
        "demo", "conversation-1", ["safety_check"]
    )
    updated = client.get("/api/categories/demo").json()
    created_entry = next(item for item in updated if item["id"] == "safety_check")
    assert created_entry["usageCount"] == 2
    edit_response = client.put(
        "/api/categories/demo/safety_check",
        json={"description": "Updated", "measures": ["risk_score"]},
    )
    assert edit_response.status_code == 200
    evaluation_path = project_dir / "evaluations" / "evaluation.yml"
    evaluation_yaml = read_yaml(evaluation_path)
    stored_category = next(
        item
        for item in evaluation_yaml["evaluation"]["failure_categories"]
        if item["name"] == "Safety Check"
    )
    assert stored_category["description"] == "Updated"
    delete_response = client.delete("/api/categories/demo/safety_check")
    assert delete_response.status_code == 200
    after_delete = read_yaml(evaluation_path)
    names_after = [
        item["name"]
        for item in after_delete["evaluation"].get("failure_categories", [])
    ]
    assert "Safety Check" not in names_after
    refreshed_scenarios = read_yaml(scenarios_path)
    assert "failure_categories" not in refreshed_scenarios["scenarios"][0]


def test_scenario_promote_annotate_and_delete(studio_environment, monkeypatch):
    client = studio_environment.client
    project_dir = studio_environment.project_dir
    generated_path = (
        project_dir / "artifacts" / "eval" / "demo-set" / "generated_scenarios.yml"
    )
    promote_response = client.post(
        "/api/evaluation/scenarios/promote",
        json={
            "source_kind": "generated",
            "set_path": str(generated_path),
            "scenario_name": "generated-one",
        },
    )
    assert promote_response.status_code == 200
    golden_path = project_dir / "evaluations" / "scenarios.yml"
    golden_yaml = read_yaml(golden_path)
    names = [item["name"] for item in golden_yaml["scenarios"]]
    assert "generated-one" in names

    def fake_annotate(agent_yaml, scenarios_path, studio_project, workflow_name):
        data = read_yaml(scenarios_path)
        annotated = []
        for entry in data.get("scenarios", []):
            item = dict(entry)
            item["failure_categories"] = ["annotated-tag"]
            annotated.append(item)
        return annotated, data.get("scenarios", []), scenarios_path

    monkeypatch.setattr(studio_api, "annotate_scenarios", fake_annotate)
    annotate_response = client.post(
        "/api/evaluation/scenarios/annotate",
        json={
            "kind": "generated",
            "set_path": str(generated_path),
            "agent": "primary",
        },
    )
    assert annotate_response.status_code == 200
    annotated_path = Path(annotate_response.json()["annotated_path"])
    annotated_yaml = read_yaml(annotated_path)
    assert annotated_yaml["scenarios"][0]["failure_categories"] == ["annotated-tag"]
    delete_response = client.post(
        "/api/evaluation/scenarios/delete",
        json={"kind": "generated", "set_path": str(generated_path)},
    )
    assert delete_response.status_code == 200
    assert not generated_path.exists()
