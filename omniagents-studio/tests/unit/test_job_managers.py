import asyncio
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from omniagents_studio.api import (
    AgentOptimizationDatasetPayload,
    AgentOptimizationManager,
    AgentOptimizationRequest,
    EvaluationSuiteRunRequest,
    JudgeOptimizationDatasetPayload,
    JudgeOptimizationManager,
    JudgeOptimizationRequest,
    ScenarioGenerationManager,
    ScenarioGenerationRequest,
    ScenarioRunManager,
    ScenarioRunRequest,
    SuiteRunManager,
    create_api_app,
)


def build_api_app():
    app, _ = create_api_app()
    return app


def portal_update(portal, manager, job_id, **fields):
    async def runner():
        return await manager.update_job(job_id, **fields)

    return portal.call(runner)


class FakeWebSocket:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append(message)


async def create_scenario_run_job(manager: ScenarioRunManager):
    payload = ScenarioRunRequest(kind="golden", set_path="/tmp/set.json")
    job = await manager.create_job("project-scenario", payload, 4)
    return job, "project-scenario"


async def create_scenario_generation_job(manager: ScenarioGenerationManager):
    payload = ScenarioGenerationRequest(
        name="generated",
        output_path="/tmp/generated.json",
        count=3,
    )
    job = await manager.create_job("project-generation", payload)
    return job, "project-generation"


async def create_suite_run_job(manager: SuiteRunManager):
    payload = EvaluationSuiteRunRequest(
        name="suite",
        agent="agent-one",
        output_path="/tmp/report.json",
    )
    job = await manager.create_job("project-suite", payload)
    return job, "project-suite"


async def create_judge_optimization_job(manager: JudgeOptimizationManager):
    dataset = JudgeOptimizationDatasetPayload(
        set_name="set",
        category="category",
        train_path="/tmp/train.json",
        dev_path="/tmp/dev.json",
        test_path="/tmp/test.json",
    )
    payload = JudgeOptimizationRequest(
        judge_key="judge",
        dataset=dataset,
        max_metric_calls=6,
        seed=7,
    )
    job = await manager.create_job("project-judge", payload)
    return job, "project-judge"


async def create_agent_optimization_job(manager: AgentOptimizationManager):
    dataset = AgentOptimizationDatasetPayload(
        set_name="set",
        category="category",
        train_path="/tmp/train.json",
        dev_path="/tmp/dev.json",
        test_path="/tmp/test.json",
    )
    payload = AgentOptimizationRequest(
        agent_key="agent",
        dataset=dataset,
        positives_only=True,
        max_metric_calls=8,
        seed=5,
    )
    job = await manager.create_job("project-agent", payload)
    return job, "project-agent"


MANAGER_CASES = [
    {
        "name": "scenario_run",
        "factory": ScenarioRunManager,
        "creator": create_scenario_run_job,
        "running": {
            "state": "running",
            "completed": 1,
            "started_at": "2025-01-01T00:00:00Z",
        },
        "final": {
            "state": "cancelled",
            "completed": 3,
            "finished_at": "2025-01-01T00:00:05Z",
            "total": 1,
            "runs_path": "/tmp/runs.json",
        },
        "expect": {"runs_path": "/tmp/runs.json"},
    },
    {
        "name": "scenario_generation",
        "factory": ScenarioGenerationManager,
        "creator": create_scenario_generation_job,
        "running": {
            "state": "running",
            "completed": 2,
            "started_at": "2025-01-01T00:00:01Z",
        },
        "final": {
            "state": "cancelled",
            "completed": 3,
            "finished_at": "2025-01-01T00:00:06Z",
            "scenarios_path": "/tmp/scenarios.json",
        },
        "expect": {"scenarios_path": "/tmp/scenarios.json"},
    },
    {
        "name": "suite_run",
        "factory": SuiteRunManager,
        "creator": create_suite_run_job,
        "running": {
            "state": "running",
            "completed": 0,
            "total": 1,
            "started_at": "2025-01-01T00:00:02Z",
        },
        "final": {
            "state": "cancelled",
            "completed": 1,
            "finished_at": "2025-01-01T00:00:07Z",
            "result_path": "/tmp/result.json",
            "report_path": "/tmp/report.json",
            "summary": {"total_checks": 1},
        },
        "expect": {
            "result_path": "/tmp/result.json",
            "report_path": "/tmp/report.json",
            "summary": {"total_checks": 1},
        },
    },
    {
        "name": "judge_optimization",
        "factory": JudgeOptimizationManager,
        "creator": create_judge_optimization_job,
        "running": {
            "state": "running",
            "completed": 0,
            "started_at": "2025-01-01T00:00:03Z",
        },
        "final": {
            "state": "cancelled",
            "completed": 1,
            "finished_at": "2025-01-01T00:00:08Z",
            "run_dir": "/tmp/run",
            "result_path": "/tmp/judge.json",
        },
        "expect": {
            "run_dir": "/tmp/run",
            "result_path": "/tmp/judge.json",
        },
    },
    {
        "name": "agent_optimization",
        "factory": AgentOptimizationManager,
        "creator": create_agent_optimization_job,
        "running": {
            "state": "running",
            "completed": 4,
            "eta_seconds": 14.0,
            "log_path": "/tmp/log.txt",
        },
        "final": {
            "state": "cancelled",
            "completed": 8,
            "finished_at": "2025-01-01T00:00:09Z",
            "result_path": "/tmp/agent.json",
            "log_path": "/tmp/log.txt",
            "eta_seconds": 0.0,
        },
        "expect": {
            "result_path": "/tmp/agent.json",
            "log_path": "/tmp/log.txt",
            "eta_seconds": 0.0,
        },
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MANAGER_CASES, ids=lambda c: c["name"])
async def test_manager_lifecycle_handles_updates_and_cancellation(case):
    manager = case["factory"]()
    job, project = await case["creator"](manager)
    status = await manager.get_status(job.job_id)
    assert status["state"] == "pending"
    await manager.update_job(job.job_id, **case["running"])
    running_status = await manager.get_status(job.job_id)
    assert running_status["state"] == case["running"]["state"]
    assert running_status["completed"] == case["running"]["completed"]
    await manager.update_job(job.job_id, **case["final"])
    final_status = await manager.get_status(job.job_id)
    assert final_status["state"] == case["final"]["state"]
    assert final_status["completed"] == case["final"]["completed"]
    assert final_status["project"] == project
    assert final_status["total"] >= final_status["completed"]
    for key, value in case["expect"].items():
        assert final_status[key] == value
    missing = await manager.update_job("missing", state="completed")
    assert missing is None
    task = asyncio.create_task(asyncio.sleep(0.05))
    await manager.register_task(job.job_id, task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    await asyncio.sleep(0.01)
    assert job.job_id not in manager.tasks
    await manager.clear_task(job.job_id)
    await manager.broadcast(job.job_id)


@pytest.mark.asyncio
async def test_broadcast_prunes_stale_connections():
    manager = ScenarioRunManager()
    job, _ = await create_scenario_run_job(manager)
    ok_ws = FakeWebSocket()
    ok_ws.accepted = True
    manager.connections[job.job_id].append(ok_ws)
    await manager.update_job(job.job_id, state="running")
    stale_ws = FakeWebSocket(fail=True)
    stale_ws.accepted = True
    manager.connections[job.job_id].append(stale_ws)
    await manager.broadcast(job.job_id)
    connections = manager.connections.get(job.job_id, [])
    assert stale_ws not in connections
    assert ok_ws.messages
    assert ok_ws.messages[-1]["status"]["state"] == "running"


def test_scenario_run_websocket_broadcasts_to_multiple_clients():
    app = build_api_app()
    with TestClient(app) as client:
        manager: ScenarioRunManager = client.app.state.scenario_run_manager
        payload = ScenarioRunRequest(kind="golden", set_path="/tmp/ws.json")
        portal = client.portal
        job = portal.call(manager.create_job, "project-ws", payload, 2)
        with ExitStack() as stack:
            sockets = [
                stack.enter_context(
                    client.websocket_connect(
                        f"/api/evaluation/scenarios/run/{job.job_id}/ws"
                    )
                )
                for _ in range(2)
            ]
            initial = [ws.receive_json() for ws in sockets]
            assert all(item["status"]["state"] == "pending" for item in initial)
            portal_update(
                portal,
                manager,
                job.job_id,
                state="running",
                completed=1,
                started_at="2025-01-01T00:00:10Z",
            )
            running_messages = [ws.receive_json() for ws in sockets]
            assert all(
                item["status"]["state"] == "running" for item in running_messages
            )
            portal_update(
                portal,
                manager,
                job.job_id,
                state="completed",
                completed=2,
                finished_at="2025-01-01T00:00:11Z",
                runs_path="/tmp/ws-runs.json",
            )
            final_messages = [ws.receive_json() for ws in sockets]
            assert all(
                item["status"]["state"] == "completed" for item in final_messages
            )
            assert all(
                item["status"]["runs_path"] == "/tmp/ws-runs.json"
                for item in final_messages
            )


def test_polling_routes_return_consistent_snapshots():
    app = build_api_app()
    with TestClient(app) as client:
        project = "project-poll"
        portal = client.portal

        scenario_manager: ScenarioRunManager = client.app.state.scenario_run_manager
        scenario_payload = ScenarioRunRequest(kind="golden", set_path="/tmp/run.json")
        scenario_job = portal.call(
            scenario_manager.create_job,
            project,
            scenario_payload,
            3,
        )
        portal_update(
            portal,
            scenario_manager,
            scenario_job.job_id,
            state="running",
            completed=2,
            total=3,
            started_at="2025-01-02T00:00:00Z",
            runs_path="/tmp/run-result.json",
        )
        scenario_response = client.get(
            f"/api/evaluation/scenarios/run/{scenario_job.job_id}"
        )
        assert scenario_response.status_code == 200
        scenario_status = scenario_response.json()["status"]
        assert scenario_status["project"] == project
        assert scenario_status["completed"] == 2
        assert scenario_status["total"] == 3
        assert scenario_status["runs_path"] == "/tmp/run-result.json"

        generation_manager: ScenarioGenerationManager = (
            client.app.state.scenario_generation_manager
        )
        generation_payload = ScenarioGenerationRequest(
            name="batch",
            output_path="/tmp/gen.json",
            count=4,
        )
        generation_job = portal.call(
            generation_manager.create_job,
            project,
            generation_payload,
        )
        portal_update(
            portal,
            generation_manager,
            generation_job.job_id,
            state="completed",
            completed=4,
            finished_at="2025-01-02T00:00:01Z",
            scenarios_path="/tmp/gen-path.json",
        )
        generation_response = client.get(
            f"/api/evaluation/scenarios/generate/{generation_job.job_id}"
        )
        assert generation_response.status_code == 200
        generation_status = generation_response.json()["status"]
        assert generation_status["project"] == project
        assert generation_status["completed"] == 4
        assert generation_status["scenarios_path"] == "/tmp/gen-path.json"

        suite_manager: SuiteRunManager = client.app.state.suite_run_manager
        suite_payload = EvaluationSuiteRunRequest(
            name="suite",
            agent="agent",
            output_path="/tmp/report.json",
        )
        suite_job = portal.call(
            suite_manager.create_job,
            project,
            suite_payload,
        )
        suite_summary = {
            "total_checks": 2,
            "passed_checks": 2,
            "failed_checks": 0,
            "skipped_checks": 0,
            "measures_total": 1,
            "measures_failed": 0,
            "measures_skipped": 0,
            "metrics_total": 1,
            "metrics_failed": 0,
            "metrics_skipped": 0,
            "scenarios_total": 1,
            "scenarios_included": 1,
            "scenarios_skipped": 0,
        }
        portal_update(
            portal,
            suite_manager,
            suite_job.job_id,
            state="completed",
            total=1,
            completed=1,
            finished_at="2025-01-02T00:00:02Z",
            result_path="/tmp/suite-result.json",
            report_path="/tmp/report.json",
            summary=suite_summary,
        )
        suite_response = client.get(f"/api/evaluation/suite/run/{suite_job.job_id}")
        assert suite_response.status_code == 200
        suite_status = suite_response.json()["status"]
        assert suite_status["project"] == project
        assert suite_status["summary"]["total_checks"] == suite_summary["total_checks"]
        assert suite_status["result_path"] == "/tmp/suite-result.json"

        judge_manager: JudgeOptimizationManager = (
            client.app.state.judge_optimization_manager
        )
        judge_dataset = JudgeOptimizationDatasetPayload(
            set_name="set",
            category="category",
            train_path="/tmp/train.json",
            dev_path="/tmp/dev.json",
            test_path="/tmp/test.json",
        )
        judge_request = JudgeOptimizationRequest(
            judge_key="judge",
            dataset=judge_dataset,
            max_metric_calls=5,
            seed=4,
        )
        judge_job = portal.call(
            judge_manager.create_job,
            project,
            judge_request,
        )
        portal_update(
            portal,
            judge_manager,
            judge_job.job_id,
            state="completed",
            completed=1,
            finished_at="2025-01-02T00:00:03Z",
            run_dir="/tmp/judge-run",
            result_path="/tmp/judge-result.json",
        )
        judge_response = client.get(
            f"/api/evaluation/judges/optimize/{judge_job.job_id}"
        )
        assert judge_response.status_code == 200
        judge_status = judge_response.json()["status"]
        assert judge_status["project"] == project
        assert judge_status["run_dir"] == "/tmp/judge-run"
        assert judge_status["result_path"] == "/tmp/judge-result.json"

        agent_manager: AgentOptimizationManager = (
            client.app.state.agent_optimization_manager
        )
        agent_dataset = AgentOptimizationDatasetPayload(
            set_name="set",
            category="category",
            train_path="/tmp/train.json",
            dev_path="/tmp/dev.json",
            test_path="/tmp/test.json",
        )
        agent_request = AgentOptimizationRequest(
            agent_key="agent",
            dataset=agent_dataset,
            positives_only=False,
            max_metric_calls=9,
            seed=3,
        )
        agent_job = portal.call(
            agent_manager.create_job,
            project,
            agent_request,
        )
        portal_update(
            portal,
            agent_manager,
            agent_job.job_id,
            state="running",
            completed=4,
            eta_seconds=8.5,
            log_path="/tmp/agent.log",
        )
        portal_update(
            portal,
            agent_manager,
            agent_job.job_id,
            state="completed",
            completed=9,
            finished_at="2025-01-02T00:00:04Z",
            result_path="/tmp/agent-result.json",
            log_path="/tmp/agent.log",
            eta_seconds=0.0,
        )
        agent_response = client.get(
            f"/api/evaluation/agents/optimize/{agent_job.job_id}"
        )
        assert agent_response.status_code == 200
        agent_status = agent_response.json()["status"]
        assert agent_status["project"] == project
        assert agent_status["log_path"] == "/tmp/agent.log"
        assert agent_status["eta_seconds"] == 0.0
