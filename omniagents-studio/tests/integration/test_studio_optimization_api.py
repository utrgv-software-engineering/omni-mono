import asyncio
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import omniagents_studio.api as studio_api
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents_studio.api import create_api_app
from omniagents.core.tracing.storage import SQLiteStorage


@pytest.fixture
def studio_optimization_env(tmp_path):
    fixtures_root = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "projects"
        / "studio_playground"
    )
    project_dir = tmp_path / "project"
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
    client_ctx = TestClient(app)
    client = client_ctx.__enter__()
    try:
        yield SimpleNamespace(
            client=client,
            portal=client.portal,
            app=app,
            project_dir=project_dir,
            manifest_path=manifest_path,
        )
    finally:
        client_ctx.__exit__(None, None, None)


def provision_dataset(
    project_dir: Path, name: str = "demo-set", with_runs: bool = True
):
    dataset_dir = project_dir / "datasets" / name
    dataset_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "scenario-1", "prompt": "Prompt one", "label": True},
        {"id": "scenario-2", "prompt": "Prompt two", "label": False},
    ]
    splits = {}
    for split in ("train", "dev", "test"):
        split_path = dataset_dir / f"{split}.json"
        split_path.write_text(json.dumps(rows), encoding="utf-8")
        splits[split] = split_path
    artifact_dir = project_dir / "artifacts" / "eval" / name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    runs_path = None
    if with_runs:
        runs_path_obj = artifact_dir / "generated_runs.json"
        runs_payload = {
            "runs": [
                {
                    "scenario_id": "scenario-1",
                    "scenario_name": "initial",
                    "final_assistant_text": "ok",
                    "history": [{"role": "assistant", "content": "ok"}],
                }
            ]
        }
        runs_path_obj.write_text(json.dumps(runs_payload), encoding="utf-8")
        runs_path = runs_path_obj
    return SimpleNamespace(
        set_name=name,
        category="Quality Issues",
        artifact_dir=str(artifact_dir),
        train=str(splits["train"]),
        dev=str(splits["dev"]),
        test=str(splits["test"]),
        runs=str(runs_path) if runs_path else None,
    )


def provision_judge(project_dir: Path, key: str = "quality"):
    judge_dir = project_dir / "evaluations" / "judges" / key
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
                    "output_type_strict": False,
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "reasoning": {"type": "string"},
                            "answer": {"type": "string", "enum": ["Pass", "Fail"]},
                        },
                        "required": ["reasoning", "answer"],
                        "additionalProperties": False,
                    },
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
    return SimpleNamespace(
        key=key,
        instructions=instructions_path,
        template=template_path,
    )


def test_judge_optimization_flow_syncs_channels(studio_optimization_env, monkeypatch):
    env = studio_optimization_env
    dataset = provision_dataset(env.project_dir)
    judge = provision_judge(env.project_dir)
    called = {"value": False}

    def fake_optimize_judge_from_project(*args, **kwargs):
        called["value"] = True
        time.sleep(0.05)
        output_dir = env.project_dir / "artifacts" / "judge_opt"
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "judge_result.json"
        result_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        judge.instructions.write_text("Updated judge instructions", encoding="utf-8")
        judge.template.write_text("Updated judge template", encoding="utf-8")
        return {
            "run_dir": str(output_dir),
            "out_json": str(result_path),
            "instructions": "Updated judge instructions",
            "template": "Updated judge template",
        }

    monkeypatch.setattr(
        studio_api, "optimize_judge_from_project", fake_optimize_judge_from_project
    )
    payload = {
        "judge_key": judge.key,
        "dataset": {
            "set_name": dataset.set_name,
            "category": dataset.category,
            "artifact_dir": dataset.artifact_dir,
            "train_path": dataset.train,
            "dev_path": dataset.dev,
            "test_path": dataset.test,
            "runs_path": dataset.runs,
        },
        "max_metric_calls": 6,
        "seed": 7,
    }
    response = env.client.post("/api/evaluation/judges/optimize", json=payload)
    assert response.status_code == 200
    job_data = response.json()
    job_id = job_data["job_id"]
    status = job_data["status"]
    assert status["judge_key"] == judge.key
    assert status["dataset"]["runs_path"] == dataset.runs
    assert status["state"] in {"pending", "running"}

    for _ in range(50):
        if called["value"]:
            break
        env.portal.call(asyncio.sleep, 0.02)
    assert called["value"] is True
    manager = env.app.state.judge_optimization_manager

    with env.client.websocket_connect(
        f"/api/evaluation/judges/optimize/{job_id}/ws"
    ) as websocket:
        messages = []
        for _ in range(5):
            payload = websocket.receive_json()
            messages.append(payload)
            if payload["status"]["state"] == "completed":
                break
        states = [item["status"]["state"] for item in messages]
        assert "running" in states
        assert states[-1] == "completed"

    result_dir = env.project_dir / "artifacts" / "judge_opt"
    result_path = result_dir / "judge_result.json"

    completed_status = env.client.get(
        f"/api/evaluation/judges/optimize/{job_id}"
    ).json()["status"]
    assert completed_status["state"] == "completed"
    assert Path(completed_status["result_path"]).is_file()
    assert completed_status["run_dir"] == str(result_dir)
    assert (
        judge.instructions.read_text(encoding="utf-8") == "Updated judge instructions"
    )
    assert judge.template.read_text(encoding="utf-8") == "Updated judge template"


def test_agent_optimization_flow_syncs_progress_and_artifacts(
    studio_optimization_env, monkeypatch
):
    env = studio_optimization_env
    dataset = provision_dataset(env.project_dir)
    log_path = env.project_dir / "artifacts" / "agent_opt" / "progress.log"
    called = {"value": False}

    def fake_optimize_agent_from_project(*args, **kwargs):
        called["value"] = True
        log_hook = kwargs.get("log_hook")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("initial\n", encoding="utf-8")
        if log_hook:
            log_hook(f"LOG_PATH:{log_path}")
            log_hook("PROGRESS:4:12.5")
        time.sleep(0.05)
        result_dir = env.project_dir / "artifacts" / "agent_opt" / "result"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "agent_result.json"
        result_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        instructions_path = env.project_dir / "agents" / "primary" / "instructions.md"
        instructions_path.write_text("Agent tuned instructions", encoding="utf-8")
        return {
            "run_dir": str(result_dir),
            "out_json": str(result_path),
            "log_file": str(log_path),
            "instructions": "Agent tuned instructions",
        }

    monkeypatch.setattr(
        studio_api, "optimize_agent_from_project", fake_optimize_agent_from_project
    )
    payload = {
        "agent_key": "primary",
        "dataset": {
            "set_name": dataset.set_name,
            "category": dataset.category,
            "artifact_dir": dataset.artifact_dir,
            "train_path": dataset.train,
            "dev_path": dataset.dev,
            "test_path": dataset.test,
        },
        "positives_only": False,
        "max_metric_calls": 6,
        "seed": 5,
    }
    response = env.client.post("/api/evaluation/agents/optimize", json=payload)
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(50):
        if called["value"]:
            break
        env.portal.call(asyncio.sleep, 0.02)
    assert called["value"] is True

    with env.client.websocket_connect(
        f"/api/evaluation/agents/optimize/{job_id}/ws"
    ) as websocket:
        messages = []
        for _ in range(5):
            payload = websocket.receive_json()
            messages.append(payload)
            if payload["status"]["state"] == "completed":
                break
        states = [item["status"]["state"] for item in messages]
        assert "running" in states
        assert states[-1] == "completed"
        assert messages[-1]["status"]["log_path"] == str(log_path)

    result_dir = env.project_dir / "artifacts" / "agent_opt" / "result"
    result_path = result_dir / "agent_result.json"

    completed_status = env.client.get(
        f"/api/evaluation/agents/optimize/{job_id}"
    ).json()["status"]
    assert completed_status["state"] == "completed"
    assert Path(completed_status["result_path"]).is_file()
    instructions_path = env.project_dir / "agents" / "primary" / "instructions.md"
    assert instructions_path.read_text(encoding="utf-8") == "Agent tuned instructions"
    assert (
        Path(completed_status["log_path"])
        .read_text(encoding="utf-8")
        .startswith("initial")
    )


def test_judge_optimization_rejects_missing_runs_file(studio_optimization_env):
    env = studio_optimization_env
    dataset = provision_dataset(env.project_dir, with_runs=False)
    judge = provision_judge(env.project_dir)
    payload = {
        "judge_key": judge.key,
        "dataset": {
            "set_name": dataset.set_name,
            "category": dataset.category,
            "artifact_dir": dataset.artifact_dir,
            "train_path": dataset.train,
            "dev_path": dataset.dev,
            "test_path": dataset.test,
        },
        "max_metric_calls": 6,
    }
    response = env.client.post("/api/evaluation/judges/optimize", json=payload)
    assert response.status_code == 422
    assert "Runs file not found" in response.json()["detail"]
    assert env.app.state.judge_optimization_manager.jobs == {}


def test_agent_optimization_rejects_missing_split(studio_optimization_env):
    env = studio_optimization_env
    dataset = provision_dataset(env.project_dir)
    missing_train = str(Path(dataset.train).with_name("missing.json"))
    payload = {
        "agent_key": "primary",
        "dataset": {
            "set_name": dataset.set_name,
            "category": dataset.category,
            "artifact_dir": dataset.artifact_dir,
            "train_path": missing_train,
            "dev_path": dataset.dev,
            "test_path": dataset.test,
        },
        "positives_only": False,
        "max_metric_calls": 6,
    }
    response = env.client.post("/api/evaluation/agents/optimize", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Train split not found" in detail
    assert env.app.state.agent_optimization_manager.jobs == {}
