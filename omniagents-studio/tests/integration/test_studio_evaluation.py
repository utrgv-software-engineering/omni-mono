"""Studio's evaluation API is the project's EvalWorkspace over HTTP: what it
writes is what ``omniagents eval`` reads, refusals come back as 4xx with
the reason, and long work runs as jobs a page follows."""

from __future__ import annotations

import json
import time

import yaml

from omniagents.core.eval import pipeline

AUTH = {"Authorization": "Bearer secret"}


def _get(env, path, **kwargs):
    response = env.client.get(path, headers=AUTH, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def _project_set(env):
    sets = _get(env, "/api/evaluation/scenario-sets")["sets"]
    return next(s for s in sets if s["kind"] == "project")


def _storage(env):
    """The trace store Studio keeps reviews in, under the project's name."""
    name = env.app.state.project_resources["project_name"]
    return name, env.app.state.storage_manager.get_storage(name)


def _wait(env, job_id):
    for _ in range(200):
        job = _get(env, f"/api/evaluation/jobs/{job_id}")
        if job["state"] in ("completed", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_categories_count_scenarios_and_reviews(studio_api_env):
    env = studio_api_env
    name, storage = _storage(env)
    storage.set_analysis_categories(name, "group-1", ["quality_issues"])

    [quality] = _get(env, "/api/evaluation/categories")["categories"]

    assert quality["id"] == "quality_issues"
    assert quality["scenario_count"] == 1
    assert quality["review_count"] == 1


def test_refusals_answer_with_the_reason(studio_api_env):
    env = studio_api_env

    unknown_measure = env.client.post(
        "/api/evaluation/categories",
        json={"name": "Rude", "measures": ["no_such_measure"]},
        headers=AUTH,
    )
    missing = env.client.patch(
        "/api/evaluation/judges/nobody", json={"model": "m"}, headers=AUTH
    )
    outside = env.client.post(
        "/api/evaluation/scenario-sets/scenarios",
        json={"set_path": "/etc/hosts", "scenario": {"name": "x", "prompt": "y"}},
        headers=AUTH,
    )

    assert unknown_measure.status_code == 422
    assert "no_such_measure" in unknown_measure.json()["detail"]
    assert missing.status_code == 404
    assert outside.status_code == 422


def test_scenarios_and_judges_are_written_where_omniagents_reads_them(studio_api_env):
    env = studio_api_env
    project_set = _project_set(env)

    added = env.client.post(
        "/api/evaluation/scenario-sets/scenarios",
        json={
            "set_path": project_set["path"],
            "scenario": {"name": "rude_user", "prompt": "You are useless."},
        },
        headers=AUTH,
    )
    judge = env.client.post(
        "/api/evaluation/judges",
        json={
            "key": "politeness",
            "model": "gpt-5.5",
            "failure_category": "Quality Issues",
        },
        headers=AUTH,
    )

    assert added.status_code == 200 and judge.status_code == 200
    scenarios = yaml.safe_load(
        (env.project_dir / "evaluations/scenarios/main.yml").read_text()
    )
    assert [s["name"] for s in scenarios["scenarios"]] == ["initial", "rude_user"]
    assert (env.project_dir / "evaluations/judges/politeness/judge.yml").is_file()
    [quality] = _get(env, "/api/evaluation/categories")["categories"]
    assert "politeness_agent" in quality["measures"]


def _fake_engine(monkeypatch, passes):
    calls = []

    def run(agent_path, **kwargs):
        calls.append(kwargs)
        scenarios = kwargs["eval_cfg"]["scenarios"]
        return {
            "runs": [
                {
                    "scenario": s["name"],
                    "scenario_config": s,
                    "measures": [{"name": "response_coherence", "passed": passed}],
                    "answered": True,
                    "history": [],
                    "final_assistant_text": f"reply to {s['name']}",
                    "trace_group_id": f"{s['name']}-group",
                }
                for s, passed in zip(scenarios, passes)
            ],
            "metrics": [],
            "stats": {},
            "manifest": {"model": "gpt-4o", "scenarios": len(scenarios)},
            "workflow_name": None,
        }

    monkeypatch.setattr(pipeline, "run_evaluation", run)
    return calls


def test_a_run_job_saves_a_run_that_is_listed_read_and_compared(
    studio_api_env, monkeypatch
):
    env = studio_api_env
    _fake_engine(monkeypatch, [True])

    first = env.client.post("/api/evaluation/jobs", json={"kind": "run"}, headers=AUTH)
    finished = _wait(env, first.json()["id"])
    _fake_engine(monkeypatch, [False])
    second = _wait(
        env,
        env.client.post(
            "/api/evaluation/jobs", json={"kind": "run"}, headers=AUTH
        ).json()["id"],
    )

    assert finished["state"] == "completed", finished
    runs = _get(env, "/api/evaluation/runs")["runs"]
    assert [r["path"] for r in runs] == [
        second["result"]["path"],
        finished["result"]["path"],
    ]
    detail = _get(env, "/api/evaluation/runs/detail", params={"path": runs[0]["path"]})
    assert detail["overview"]["pass_rate"] == 0.0
    comparison = _get(
        env,
        "/api/evaluation/runs/compare",
        params={"a": runs[1]["path"], "b": runs[0]["path"]},
    )
    assert comparison["comparable"] is True
    assert comparison["scenarios"] == [
        {"scenario": "abc123", "name": "initial", "a": 1.0, "b": 0.0}
    ]


def test_a_job_socket_follows_the_job_to_its_end(studio_api_env, monkeypatch):
    env = studio_api_env
    _fake_engine(monkeypatch, [True])
    job = env.client.post(
        "/api/evaluation/jobs", json={"kind": "run"}, headers=AUTH
    ).json()

    states = []
    with env.client.websocket_connect(
        f"/api/evaluation/jobs/{job['id']}/ws", headers=AUTH
    ) as socket:
        while True:
            snapshot = socket.receive_json()
            states.append(snapshot["state"])
            if snapshot["state"] in ("completed", "failed"):
                break

    assert states[-1] == "completed"


def test_a_review_of_an_evaluation_answer_is_committed_with_it(
    studio_api_env, monkeypatch
):
    env = studio_api_env
    _fake_engine(monkeypatch, [False])
    run = _wait(
        env,
        env.client.post(
            "/api/evaluation/jobs", json={"kind": "run"}, headers=AUTH
        ).json()["id"],
    )
    name, storage = _storage(env)

    judged = env.client.put(
        f"/api/analysis/{name}/initial-group",
        json={
            "judgment": "unacceptable",
            "notes": "off topic",
            "markedForAnalysis": True,
        },
        headers=AUTH,
    )
    labelled = env.client.put(
        f"/api/analysis/{name}/initial-group/categories",
        json={"categoryIds": ["Quality Issues"]},
        headers=AUTH,
    )
    unknown = env.client.put(
        f"/api/analysis/{name}/initial-group/categories",
        json={"categoryIds": ["not a category"]},
        headers=AUTH,
    )
    # Any other conversation's review stays in this machine's trace database.
    elsewhere = env.client.put(
        f"/api/analysis/{name}/session_abc",
        json={"judgment": "acceptable", "notes": "", "markedForAnalysis": False},
        headers=AUTH,
    )

    assert judged.json()["judgment"] == "unacceptable"
    assert labelled.json() == {"categoryIds": ["quality_issues"]}
    assert unknown.status_code == 400 and elsewhere.status_code == 200
    run_id = run["result"]["path"].split("/")[-2]
    [line] = (
        (env.project_dir / f"evaluations/reviews/{run_id}.jsonl")
        .read_text()
        .splitlines()
    )
    review = json.loads(line)
    assert review["verdict"] == "unacceptable" and review["notes"] == "off topic"
    assert review["failure_categories"] == ["quality_issues"]
    assert review["reply"] == "reply to initial" and review["reviewer"]
    assert storage.get_analysis(name, "initial-group") is None
    assert storage.get_analysis(name, "session_abc")["judgment"] == "acceptable"
    # The scenario keeps only what people wrote.
    [scenario] = yaml.safe_load(
        (env.project_dir / "evaluations/scenarios/main.yml").read_text()
    )["scenarios"]
    assert "analysis" not in scenario

    everything = _get(env, f"/api/analysis/{name}")
    assert everything["initial-group"]["judgment"] == "unacceptable"
    assert everything["session_abc"]["judgment"] == "acceptable"
    assert _get(env, f"/api/analysis/{name}/initial-group/categories") == {
        "categoryIds": ["quality_issues"]
    }
    [quality] = _get(env, "/api/evaluation/categories")["categories"]
    assert quality["review_count"] == 1

    dataset = env.client.post(
        "/api/evaluation/judge-datasets",
        json={"category": "quality_issues"},
        headers=AUTH,
    )
    assert dataset.status_code == 200, dataset.text
    assert sum(s["fails"] for s in dataset.json()["splits"]) == 1

    kept = env.client.post(
        "/api/evaluation/baselines",
        json={"path": run["result"]["path"], "name": "first"},
        headers=AUTH,
    )
    assert kept.status_code == 200, kept.text
    # Listed beside the run it copies (both carry the run's time).
    listed = _get(env, "/api/evaluation/runs")["runs"]
    assert sorted(r["baseline"] or "" for r in listed) == ["", "first"]

    env.client.delete(f"/api/analysis/{name}/initial-group", headers=AUTH)
    assert not (env.project_dir / f"evaluations/reviews/{run_id}.jsonl").exists()
