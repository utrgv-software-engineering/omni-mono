from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

import omniagents_studio.api as studio_api

AUTH_HEADER = {"Authorization": "Bearer secret"}


def _provision_dataset(base_dir: Path, name: str = "demo-set", with_runs: bool = True):
    dataset_dir = base_dir / "datasets" / name
    dataset_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "scenario-1", "prompt": "Prompt one", "label": True},
        {"id": "scenario-2", "prompt": "Prompt two", "label": False},
    ]
    split_paths: dict[str, Path] = {}
    for split in ("train", "dev", "test"):
        split_path = dataset_dir / f"{split}.json"
        split_path.write_text(json.dumps(rows), encoding="utf-8")
        split_paths[split] = split_path
    artifact_dir = base_dir / "artifacts" / "eval" / name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    runs_path = None
    if with_runs:
        runs_payload = {
            "runs": [
                {
                    "scenario_id": "scenario-1",
                    "scenario_name": "initial",
                    "final_assistant_text": "ok",
                }
            ]
        }
        runs_path = artifact_dir / "generated_runs.json"
        runs_path.write_text(json.dumps(runs_payload), encoding="utf-8")
    return {
        "set_name": name,
        "category": "Quality Issues",
        "artifact_dir": str(artifact_dir),
        "train_path": str(split_paths["train"]),
        "dev_path": str(split_paths["dev"]),
        "test_path": str(split_paths["test"]),
        "runs_path": str(runs_path) if runs_path else None,
    }


def _provision_judge(base_dir: Path, key: str = "quality") -> str:
    judge_dir = base_dir / "evaluations" / "judges" / key
    judge_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = judge_dir / "instructions.md"
    template_path = judge_dir / "template.md"
    instructions_path.write_text("Original judge instructions", encoding="utf-8")
    template_path.write_text("Original judge template", encoding="utf-8")
    judge_cfg = {
        "judges": {
            key: {
                "agent": {
                    "name": "QualityJudge",
                    "label": "Quality Judge",
                    "model": "gpt-4o",
                    "output_type": "dict",
                    "instructions_file": "instructions.md",
                },
                "input": {
                    "source": "final_assistant_text",
                    "template_file": "template.md",
                },
            }
        }
    }
    cfg_path = judge_dir / "judge.yml"
    cfg_path.write_text(yaml.safe_dump(judge_cfg, sort_keys=False), encoding="utf-8")
    return key


def _wait_for_completion(env, url: str):
    for _ in range(20):
        env.portal.call(asyncio.sleep, 0.05)
        response = env.client.get(url, headers=AUTH_HEADER)
        if response.status_code != 200:
            continue
        status = response.json().get("status") or {}
        if status.get("state") in {"completed", "failed"}:
            return status
    pytest.fail("job did not complete")


def test_judge_optimization_uses_artifact_runs(monkeypatch, studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=True)
    runs_path = Path(dataset["artifact_dir"]) / "generated_runs.json"
    dataset_payload = {
        key: value for key, value in dataset.items() if key != "runs_path"
    }
    judge_key = _provision_judge(env.project_dir)
    called = {"value": False}

    def fake_optimize(*args, **kwargs):
        called["value"] = True
        result_dir = env.project_dir / "artifacts" / "judge_opt"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "judge_result.json"
        result_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        return {
            "run_dir": str(result_dir),
            "out_json": str(result_path),
        }

    monkeypatch.setattr(studio_api, "optimize_judge_from_project", fake_optimize)
    payload = {
        "judge_key": judge_key,
        "dataset": dataset_payload,
        "max_metric_calls": 8,
    }
    response = env.client.post(
        "/api/evaluation/judges/optimize", json=payload, headers=AUTH_HEADER
    )
    assert response.status_code == 200
    data = response.json()
    job_id = data["job_id"]
    status = data["status"]
    assert status["dataset"]["runs_path"] == str(runs_path)
    final_status = _wait_for_completion(
        env, f"/api/evaluation/judges/optimize/{job_id}"
    )
    assert called["value"] is True
    assert final_status["state"] == "completed"
    assert Path(final_status["result_path"]).is_file()


def test_judge_optimization_requires_runs(monkeypatch, studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=False)
    dataset_payload = {
        key: value for key, value in dataset.items() if key != "runs_path"
    }
    judge_key = _provision_judge(env.project_dir)
    response = env.client.post(
        "/api/evaluation/judges/optimize",
        json={
            "judge_key": judge_key,
            "dataset": dataset_payload,
            "max_metric_calls": 6,
        },
        headers=AUTH_HEADER,
    )
    assert response.status_code == 422
    assert "Runs file not found" in response.json()["detail"]
    manager = env.app.state.judge_optimization_manager
    assert manager.jobs == {}


def test_agent_optimization_succeeds_with_progress(monkeypatch, studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=False)

    def fake_optimize(*args, **kwargs):
        log_hook = kwargs.get("log_hook")
        log_dir = env.project_dir / "artifacts" / "agent_opt"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "progress.log"
        log_path.write_text("seed\n", encoding="utf-8")
        if log_hook:
            log_hook(f"LOG_PATH:{log_path}")
            log_hook("PROGRESS:4:7.5")
        result_dir = log_dir / "result"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "agent_result.json"
        result_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        instructions_path = env.project_dir / "agents" / "primary" / "instructions.md"
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("Updated agent instructions", encoding="utf-8")
        return {
            "run_dir": str(result_dir),
            "out_json": str(result_path),
            "log_file": str(log_path),
            "instructions": "Updated agent instructions",
        }

    monkeypatch.setattr(studio_api, "optimize_agent_from_project", fake_optimize)
    payload = {
        "agent_key": "primary",
        "dataset": {
            "set_name": dataset["set_name"],
            "category": dataset["category"],
            "artifact_dir": dataset["artifact_dir"],
            "train_path": dataset["train_path"],
            "dev_path": dataset["dev_path"],
            "test_path": dataset["test_path"],
        },
        "max_metric_calls": 8,
        "positives_only": False,
        "seed": 3,
    }
    response = env.client.post(
        "/api/evaluation/agents/optimize", json=payload, headers=AUTH_HEADER
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    final_status = _wait_for_completion(
        env, f"/api/evaluation/agents/optimize/{job_id}"
    )
    assert final_status["state"] == "completed"
    assert Path(final_status["result_path"]).is_file()
    assert Path(final_status["log_path"]).read_text(encoding="utf-8").startswith("seed")


def test_agent_optimization_requires_existing_splits(studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=False)
    missing_train = str(Path(dataset["train_path"]).with_name("missing.json"))
    payload = {
        "agent_key": "primary",
        "dataset": {
            "set_name": dataset["set_name"],
            "category": dataset["category"],
            "artifact_dir": dataset["artifact_dir"],
            "train_path": missing_train,
            "dev_path": dataset["dev_path"],
            "test_path": dataset["test_path"],
        },
        "max_metric_calls": 6,
    }
    response = env.client.post(
        "/api/evaluation/agents/optimize", json=payload, headers=AUTH_HEADER
    )
    assert response.status_code == 422
    assert "Train split not found" in response.json()["detail"]
    manager = env.app.state.agent_optimization_manager
    assert manager.jobs == {}


def test_agent_optimization_enforces_minimum_budget(studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=False)
    payload = {
        "agent_key": "primary",
        "dataset": {
            "set_name": dataset["set_name"],
            "category": dataset["category"],
            "artifact_dir": dataset["artifact_dir"],
            "train_path": dataset["train_path"],
            "dev_path": dataset["dev_path"],
            "test_path": dataset["test_path"],
        },
        "max_metric_calls": 4,
    }
    response = env.client.post(
        "/api/evaluation/agents/optimize", json=payload, headers=AUTH_HEADER
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(
        entry.get("msg") == "Input should be greater than or equal to 6"
        for entry in detail
    )
    manager = env.app.state.agent_optimization_manager
    assert manager.jobs == {}


def test_agent_optimization_requires_known_agent(studio_api_env):
    env = studio_api_env
    dataset = _provision_dataset(env.project_dir, with_runs=False)
    payload = {
        "agent_key": "unknown",
        "dataset": {
            "set_name": dataset["set_name"],
            "category": dataset["category"],
            "artifact_dir": dataset["artifact_dir"],
            "train_path": dataset["train_path"],
            "dev_path": dataset["dev_path"],
            "test_path": dataset["test_path"],
        },
        "max_metric_calls": 6,
    }
    response = env.client.post(
        "/api/evaluation/agents/optimize", json=payload, headers=AUTH_HEADER
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"
    manager = env.app.state.agent_optimization_manager
    assert manager.jobs == {}
