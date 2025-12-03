from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from omniagents.studio.server import api as studio_api


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer secret"}


def _wait_for_completion(portal, url, client):
    for _ in range(10):
        portal.call(asyncio.sleep, 0.05)
        response = client.get(url, headers=_auth_header())
        if response.status_code != 200:
            continue
        status = response.json().get("status") or {}
        if status.get("state") in {"completed", "failed"}:
            return status
    pytest.fail("job did not complete")


def test_evaluation_suite_job_lifecycle(monkeypatch, studio_api_env):
    compiled_path = studio_api_env.project_dir / "compiled_suite.yml"

    def _fake_compile_project(manifest_path, select="", exclude="", agent_key=None):
        compiled_path.write_text("tracing:\n  provider: none\n", encoding="utf-8")
        return str(compiled_path), {}

    def _fake_run_evaluation(
        compiled_cfg,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        if progress_cb:
            progress_cb({"event": "start", "total": 2})
            progress_cb({"event": "progress", "index": 1})
            progress_cb({"event": "progress", "index": 2})
        return {
            "runs": [
                {"measures": [{"passed": True}, {"passed": False}]},
                {"measures": [{"passed": None}]},
            ],
            "metrics": [
                {"target": {"value": 1}, "meets_target": True},
                {"target": None, "meets_target": False},
            ],
            "stats": {
                "total_scenarios": 2,
                "included_scenarios": 2,
                "skipped_unmeasured": 1,
            },
        }

    monkeypatch.setattr(studio_api, "compile_project", _fake_compile_project)
    monkeypatch.setattr(studio_api, "run_evaluation", _fake_run_evaluation)
    output_path = (
        studio_api_env.project_dir / "artifacts" / "eval" / "suite" / "run.json"
    )
    payload = {
        "name": "suite",
        "output_path": str(output_path),
        "include_unmeasured": True,
    }
    response = studio_api_env.client.post(
        "/api/evaluation/suite/run", json=payload, headers=_auth_header()
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status = _wait_for_completion(
        studio_api_env.portal,
        f"/api/evaluation/suite/run/{job_id}",
        studio_api_env.client,
    )
    assert status["state"] == "completed"
    assert status["include_unmeasured"] is True
    assert status["result_path"] == str(output_path)
    assert Path(status["result_path"]).is_file()
    summary = status["summary"]
    assert summary["total_checks"] == 5
    assert summary["failed_checks"] == 1
    assert summary["skipped_checks"] == 2


def test_scenario_run_job_progress(monkeypatch, studio_api_env):
    compiled_path = studio_api_env.project_dir / "compiled_scenarios.yml"

    def _fake_compile_project(manifest_path, select="", exclude="", agent_key=None):
        compiled_path.write_text("tracing:\n  provider: none\n", encoding="utf-8")
        return str(compiled_path), {}

    async def _fake_run_scenarios_against_agent(
        compiled_cfg,
        scenarios,
        max_turns=None,
        include_history=False,
        workflow_name=None,
        progress_cb=None,
        scenario_source_path=None,
    ):
        if progress_cb:
            progress_cb({"event": "start", "total": len(scenarios)})
            for index in range(len(scenarios)):
                progress_cb({"event": "progress", "current": index + 1})
            progress_cb({"event": "done", "count": len(scenarios)})
        return [{"scenario": entry.get("id"), "measures": []} for entry in scenarios]

    monkeypatch.setattr(studio_api, "compile_project", _fake_compile_project)
    monkeypatch.setattr(
        studio_api,
        "run_scenarios_against_agent",
        _fake_run_scenarios_against_agent,
    )
    scenario_path = studio_api_env.project_dir / "evaluations" / "scenarios.yml"
    output_path = studio_api_env.project_dir / "runs" / "golden_output.json"
    payload = {
        "kind": "golden",
        "set_path": str(scenario_path),
        "output_path": str(output_path),
    }
    response = studio_api_env.client.post(
        "/api/evaluation/scenarios/run", json=payload, headers=_auth_header()
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status = _wait_for_completion(
        studio_api_env.portal,
        f"/api/evaluation/scenarios/run/{job_id}",
        studio_api_env.client,
    )
    assert status["state"] == "completed"
    assert status["runs_path"] == str(output_path)
    data = json.loads(Path(status["runs_path"]).read_text(encoding="utf-8"))
    assert "runs" in data
    assert status["completed"] == status["total"]


def test_scenario_run_missing_file_returns_404(studio_api_env):
    payload = {
        "kind": "golden",
        "set_path": str(studio_api_env.project_dir / "evaluations" / "missing.yml"),
    }
    response = studio_api_env.client.post(
        "/api/evaluation/scenarios/run", json=payload, headers=_auth_header()
    )
    assert response.status_code == 404
    assert response.json()["detail"] in {
        "Scenario file not found",
        "Scenario set is empty",
        "Scenario set not found",
    }


def test_failure_category_crud_flow(studio_api_env):
    base_url = "/api/categories/demo"
    headers = _auth_header()
    initial = studio_api_env.client.get(base_url, headers=headers)
    assert initial.status_code == 200
    create_payload = {
        "name": "Safety",
        "description": "Ensure safe responses",
        "measures": ["response_coherence"],
    }
    created = studio_api_env.client.post(base_url, json=create_payload, headers=headers)
    assert created.status_code == 200
    category = created.json()
    assert category["name"] == "Safety"
    update_payload = {
        "description": "Updated description",
        "measures": [],
    }
    updated = studio_api_env.client.put(
        f"{base_url}/{category['id']}", json=update_payload, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated description"
    deleted = studio_api_env.client.delete(
        f"{base_url}/{category['id']}", headers=headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["previousUsageCount"] == 0
    final = studio_api_env.client.get(base_url, headers=headers)
    assert final.status_code == 200
    names = {entry["name"] for entry in final.json()}
    assert "Safety" not in names


def test_failure_category_validation_errors(studio_api_env):
    headers = _auth_header()
    duplicate = studio_api_env.client.post(
        "/api/categories/demo",
        json={"name": "Quality Issues"},
        headers=headers,
    )
    assert duplicate.status_code == 409
    invalid_measure = studio_api_env.client.post(
        "/api/categories/demo",
        json={"name": "Reliability", "measures": ["unknown"]},
        headers=headers,
    )
    assert invalid_measure.status_code == 400


def test_evaluation_suite_sets_environment_variables_from_config(
    monkeypatch, studio_api_env
):
    """Test that environment variables from evaluation.environment.config are set."""
    import os

    compiled_path = studio_api_env.project_dir / "compiled_env_test.yml"
    captured_env_vars = {}

    def _fake_compile_project(manifest_path, select="", exclude="", agent_key=None):
        # Return a compiled config with environment settings
        compiled_path.write_text(
            """
tracing:
  provider: none
evaluation:
  environment:
    class: some.module.EnvClass
    config:
      api_base: "${TEST_API_BASE:-http://default-api.com}"
      api_token: "static-token-value"
""",
            encoding="utf-8",
        )
        return str(compiled_path), {}

    def _fake_run_evaluation(
        compiled_cfg,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        # Capture environment variables during evaluation
        captured_env_vars["API_BASE"] = os.environ.get("API_BASE")
        captured_env_vars["API_TOKEN"] = os.environ.get("API_TOKEN")
        if progress_cb:
            progress_cb({"event": "start", "total": 1})
            progress_cb({"event": "progress", "index": 1})
        return {"runs": [], "metrics": [], "stats": {"total_scenarios": 0}}

    monkeypatch.setattr(studio_api, "compile_project", _fake_compile_project)
    monkeypatch.setattr(studio_api, "run_evaluation", _fake_run_evaluation)

    # Ensure env vars don't exist before test
    os.environ.pop("API_BASE", None)
    os.environ.pop("API_TOKEN", None)

    payload = {"name": "env-test"}
    response = studio_api_env.client.post(
        "/api/evaluation/suite/run", json=payload, headers=_auth_header()
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = _wait_for_completion(
        studio_api_env.portal,
        f"/api/evaluation/suite/run/{job_id}",
        studio_api_env.client,
    )
    assert status["state"] == "completed"

    # Verify env vars were set during evaluation (default was used for api_base)
    assert captured_env_vars["API_BASE"] == "http://default-api.com"
    assert captured_env_vars["API_TOKEN"] == "static-token-value"

    # Verify env vars are cleaned up after evaluation
    assert os.environ.get("API_BASE") is None
    assert os.environ.get("API_TOKEN") is None


def test_evaluation_suite_propagates_warnings_from_result(monkeypatch, studio_api_env):
    """Test that warnings from run_evaluation result appear in job status."""
    compiled_path = studio_api_env.project_dir / "compiled_warnings_test.yml"

    def _fake_compile_project(manifest_path, select="", exclude="", agent_key=None):
        compiled_path.write_text("tracing:\n  provider: none\n", encoding="utf-8")
        return str(compiled_path), {}

    def _fake_run_evaluation(
        compiled_cfg,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        if progress_cb:
            progress_cb({"event": "start", "total": 1})
            progress_cb({"event": "progress", "index": 1})
        return {
            "runs": [],
            "metrics": [],
            "stats": {"total_scenarios": 0},
            "warnings": [
                "Environment loading failed: Failed to load environment class: my.EnvClass"
            ],
        }

    monkeypatch.setattr(studio_api, "compile_project", _fake_compile_project)
    monkeypatch.setattr(studio_api, "run_evaluation", _fake_run_evaluation)

    payload = {"name": "warnings-test"}
    response = studio_api_env.client.post(
        "/api/evaluation/suite/run", json=payload, headers=_auth_header()
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = _wait_for_completion(
        studio_api_env.portal,
        f"/api/evaluation/suite/run/{job_id}",
        studio_api_env.client,
    )
    assert status["state"] == "completed"
    assert status["warnings"] is not None
    assert len(status["warnings"]) == 1
    assert "Environment loading failed" in status["warnings"][0]
