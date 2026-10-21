import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import omniagents.studio.server.api as studio_api


def _create_structured_results(
    base_dir: Path,
    runs_data: dict,
    run_id: str = None,
    workflow_name: str = None,
) -> Path:
    """Helper to create structured results in the new format.

    Creates:
        base_dir/results/runs.json (index)
        base_dir/results/<run_id>/results.json
        base_dir/results/<run_id>/metadata.json

    Returns the path to results.json
    """
    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    results_dir = base_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write results.json
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(runs_data, indent=2), encoding="utf-8")

    # Write metadata.json
    runs = runs_data.get("runs", [])
    total = len(runs)
    passed = sum(
        1 for r in runs if all(m.get("passed", False) for m in r.get("measures", []))
    )
    metadata = {
        "source": "test",
        "scenario_path": str(base_dir / "scenarios.yml"),
        "scenario_name": base_dir.name,
        "started_at": datetime.now().isoformat(),
        "completed_at": datetime.now().isoformat(),
        "total_scenarios": total,
        "passed": passed,
        "failed": total - passed,
    }
    if workflow_name:
        metadata["workflow_name"] = workflow_name
    meta_path = run_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Write/update runs.json index
    index_path = results_dir / "runs.json"
    run_entry = {
        "id": run_id,
        "timestamp": metadata["completed_at"],
        "path": str(results_path),
        "workflow_name": workflow_name or "",
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": total - passed,
        },
    }
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"schema_version": 1, "runs": []}
    index_data["runs"].insert(0, run_entry)
    index_data["latest_run_id"] = run_id
    index_path.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    return results_path


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


# --- Evaluation Results API Tests ---


def test_evaluation_results_returns_empty_list_when_no_results(studio_environment):
    """GET /api/evaluation/results returns empty list when no results exist."""
    client = studio_environment.client
    response = client.get("/api/evaluation/results")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == "demo"
    assert payload["results"] == []


def test_evaluation_results_lists_runs_with_summary(studio_environment):
    """GET /api/evaluation/results returns summaries for each result file."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    # Create structured results
    scenario_dir = project_dir / "artifacts" / "eval" / "demo-set"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    runs_data = {
        "runs": [
            {
                "scenario_id": "s1",
                "scenario_name": "Test Scenario 1",
                "type": "scripted",
                "measures": [
                    {"name": "check_greeting", "passed": True, "reason": None}
                ],
            },
            {
                "scenario_id": "s2",
                "scenario_name": "Test Scenario 2",
                "type": "scripted",
                "measures": [
                    {
                        "name": "check_greeting",
                        "passed": False,
                        "reason": "No greeting found",
                    }
                ],
            },
        ]
    }
    results_path = _create_structured_results(scenario_dir, runs_data, run_id="run_001")

    response = client.get("/api/evaluation/results")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == "demo"
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["total_scenarios"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert "run_at" in result


def test_evaluation_results_detail_returns_scenarios(studio_environment):
    """GET /api/evaluation/results/detail returns scenario details."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    scenario_dir = project_dir / "artifacts" / "eval" / "demo-set"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    runs_data = {
        "runs": [
            {
                "scenario_id": "s1",
                "scenario_name": "Greeting Test",
                "type": "scripted",
                "measures": [
                    {"name": "has_greeting", "passed": True, "reason": None},
                    {
                        "name": "is_polite",
                        "passed": False,
                        "reason": "Missing please",
                    },
                ],
            }
        ]
    }
    results_path = _create_structured_results(scenario_dir, runs_data, run_id="run_001")

    response = client.get(f"/api/evaluation/results/detail?path={results_path}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == "demo"
    assert payload["path"] == str(results_path)
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["passed"] == 0
    assert payload["summary"]["failed"] == 1
    assert len(payload["scenarios"]) == 1
    scenario = payload["scenarios"][0]
    assert scenario["scenario_id"] == "s1"
    assert scenario["scenario_name"] == "Greeting Test"
    assert scenario["passed"] is False
    assert len(scenario["measures"]) == 2
    assert scenario["measures"][0]["name"] == "has_greeting"
    assert scenario["measures"][0]["passed"] is True
    assert scenario["measures"][1]["passed"] is False
    assert scenario["measures"][1]["reason"] == "Missing please"


def test_evaluation_results_detail_returns_404_for_missing_file(studio_environment):
    """GET /api/evaluation/results/detail returns 404 when file doesn't exist."""
    client = studio_environment.client
    response = client.get("/api/evaluation/results/detail?path=/nonexistent/path.json")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_evaluation_results_includes_workflow_name(studio_environment):
    """GET /api/evaluation/results includes workflow_name from run metadata."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    scenario_dir = project_dir / "artifacts" / "eval" / "workflow-run"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    runs_data = {"runs": [{"scenario_name": "test", "measures": []}]}
    results_path = _create_structured_results(
        scenario_dir, runs_data, run_id="run_001", workflow_name="eval-run-2024-01-15"
    )

    response = client.get("/api/evaluation/results")
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["workflow_name"] == "eval-run-2024-01-15"


def test_evaluation_results_handles_synthetic_scenarios(studio_environment):
    """Results correctly parse synthetic scenario types."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    scenario_dir = project_dir / "artifacts" / "eval" / "synthetic-set"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    runs_data = {
        "runs": [
            {
                "scenario_name": "synthetic-test",
                "type": "synthetic",
                "measures": [{"name": "check", "passed": True}],
            }
        ]
    }
    results_path = _create_structured_results(scenario_dir, runs_data, run_id="run_001")

    response = client.get(f"/api/evaluation/results/detail?path={results_path}")
    assert response.status_code == 200
    scenario = response.json()["scenarios"][0]
    assert scenario["type"] == "synthetic"


def test_evaluation_results_extracts_group_id(studio_environment):
    """Results correctly extract group_id from metadata."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    scenario_dir = project_dir / "artifacts" / "eval" / "grouped-set"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    runs_data = {
        "runs": [
            {
                "scenario_name": "grouped-test",
                "measures": [],
                "metadata": {"group_id": "conversation-123"},
            }
        ]
    }
    results_path = _create_structured_results(scenario_dir, runs_data, run_id="run_001")

    response = client.get(f"/api/evaluation/results/detail?path={results_path}")
    assert response.status_code == 200
    scenario = response.json()["scenarios"][0]
    assert scenario["group_id"] == "conversation-123"


def test_evaluation_results_includes_error_info(studio_environment):
    """Results correctly include error info from failed runs."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    scenario_dir = project_dir / "artifacts" / "eval" / "error-set"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    runs_data = {
        "runs": [
            {
                "scenario_name": "error-test",
                "measures": [],
                "error": {"type": "RuntimeError", "message": "Agent crashed"},
            }
        ]
    }
    results_path = _create_structured_results(scenario_dir, runs_data, run_id="run_001")

    response = client.get(f"/api/evaluation/results/detail?path={results_path}")
    assert response.status_code == 200
    scenario = response.json()["scenarios"][0]
    assert scenario["error"]["type"] == "RuntimeError"
    assert scenario["error"]["message"] == "Agent crashed"


def test_evaluation_results_sorted_by_timestamp(studio_environment):
    """Results are sorted with most recent first."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    # Create first result (older timestamp)
    scenario_dir1 = project_dir / "artifacts" / "eval" / "first-set"
    scenario_dir1.mkdir(parents=True, exist_ok=True)
    _create_structured_results(
        scenario_dir1,
        {"runs": [{"scenario_name": "first", "measures": []}]},
        run_id="20240101_100000",
    )

    # Create second result (newer timestamp)
    scenario_dir2 = project_dir / "artifacts" / "eval" / "second-set"
    scenario_dir2.mkdir(parents=True, exist_ok=True)
    _create_structured_results(
        scenario_dir2,
        {"runs": [{"scenario_name": "second", "measures": []}]},
        run_id="20240102_100000",
    )

    response = client.get("/api/evaluation/results")
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    # Most recent should be first (based on timestamp in runs.json)
    assert "second-set" in results[0]["path"]
    assert "first-set" in results[1]["path"]


# --- Scenario Run Filtering Tests ---


def test_scenario_run_rejects_empty_filter_result(studio_environment):
    """POST /api/evaluation/scenarios/run returns 422 when select filter matches nothing."""
    client = studio_environment.client
    project_dir = studio_environment.project_dir

    # Create a scenario file with known IDs
    scenarios_path = project_dir / "evaluations" / "scenarios.yml"
    write_yaml(
        scenarios_path,
        {
            "scenarios": [
                {"id": "scenario-1", "name": "Test 1", "prompt": "Hello"},
                {"id": "scenario-2", "name": "Test 2", "prompt": "World"},
            ]
        },
    )

    response = client.post(
        "/api/evaluation/scenarios/run",
        json={
            "kind": "golden",
            "set_path": str(scenarios_path),
            "select": "id:nonexistent-id",
        },
    )
    assert response.status_code == 422
    assert "no scenarios match" in response.json()["detail"].lower()
