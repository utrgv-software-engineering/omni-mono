"""Regression cases for the factory workflow scorer and event adapter."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.core.eval.environment import EnvironmentContext
from omniagents.core.evaluation.context import EvalContext, ToolCall, ToolOutput

FACTORY = Path(__file__).resolve().parents[3] / "omni-factory"


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location(
        "coordination_measures", FACTORY / "evaluations/measures.py"
    )
    module = importlib.util.module_from_spec(spec)
    from omniagents.core.evaluation import registry

    previous = dict(registry._MEASURES)
    registry._MEASURES.clear()
    try:
        spec.loader.exec_module(module)
    finally:
        registry._MEASURES.clear()
        registry._MEASURES.update(previous)
    return module.factory_coordination


def snapshot(pr=None):
    return {
        "github": {
            "repositories": [
                {
                    "repository": "acme/api-service",
                    "main_sha": "main",
                    "items": [pr] if pr else [],
                }
            ]
        },
        "slack": {"messages": []},
    }


def context(workflow="clear"):
    pr = dict(
        number=33,
        kind="pull_request",
        state="open",
        merged=False,
        draft=False,
        base="main",
        head_sha="fixed",
        files=["app/api.py", "docs/api.md"],
        reviews=[],
    )
    final = snapshot(pr)
    final.update(
        dispatches=[dict(status="completed", agent="foreman", session_id="same")],
        validation=dict(exit_code=0, head_sha="fixed"),
    )
    final["slack"]["messages"] = [
        dict(
            user="factory",
            thread_ts="123",
            text="Ready: http://workplace/github/acme/api-service/pull/33",
        )
    ]
    baseline = snapshot()
    phases = [final]
    names = ["implementation", "review"]
    if workflow == "correction":
        baseline = snapshot({**pr, "head_sha": "broken"})
        names = ["review", "implementation", "review"]
    if workflow == "approval":
        first = snapshot(
            {**pr, "files": ["docs/spec.md"], "head_sha": "spec", "draft": True}
        )
        first["history_end"] = 2
        phases.insert(0, first)
        final["dispatches"] *= 2
        pr["reviews"] = [dict(author="priya", state="APPROVED")]
        names = ["spec", "implementation", "review"]
    data = dict(
        event_phases=phases,
        event_baseline=baseline,
        event_threads={"request": "123"},
        event_actor="factory",
    )
    return EvalContext(
        metadata={"scenario": {"expect": {"workflow": workflow}}},
        _environment_context=EnvironmentContext("test", data=data),
        _tool_calls=[
            ToolCall(
                index=i * 2,
                name=n,
                args={},
                output=ToolOutput(index=i * 2 + 1, output="Completed"),
            )
            for i, n in enumerate(names)
        ],
    )


@pytest.mark.parametrize("workflow", ["clear", "approval", "correction"])
def test_completed_workflow_passes(measure, workflow):
    assert measure(context(workflow))["passed"]


@pytest.mark.parametrize(
    "failure",
    [
        "no_dispatch",
        "failed_dispatch",
        "merged",
        "main_changed",
        "bad_code",
        "stale_validation",
        "no_reply",
        "human_reply",
        "wrong_thread",
        "no_review",
        "no_docs",
        "rejected_delegation",
    ],
)
def test_incomplete_or_unsafe_workflow_fails(measure, failure):
    ctx = context()
    data = ctx._environment_context.data
    final = data["event_phases"][-1]
    pr = final["github"]["repositories"][0]["items"][0]
    if failure == "no_dispatch":
        final["dispatches"] = []
    elif failure == "failed_dispatch":
        final["dispatches"][0]["status"] = "failed"
    elif failure == "merged":
        pr["merged"] = True
    elif failure == "main_changed":
        final["github"]["repositories"][0]["main_sha"] = "changed"
    elif failure == "bad_code":
        final["validation"]["exit_code"] = 1
    elif failure == "stale_validation":
        final["validation"]["head_sha"] = "old"
    elif failure == "no_reply":
        final["slack"]["messages"] = []
    elif failure == "human_reply":
        final["slack"]["messages"][0]["user"] = "priya"
    elif failure == "wrong_thread":
        final["slack"]["messages"][0]["thread_ts"] = "elsewhere"
    elif failure == "no_review":
        ctx._tool_calls.pop()
    elif failure == "no_docs":
        pr["files"] = ["app/api.py"]
    elif failure == "rejected_delegation":
        ctx._tool_calls[-1].output = ToolOutput(
            index=3, output_json={"error": "rejected"}
        )
    assert not measure(ctx)["passed"]


@pytest.mark.parametrize(
    "failure",
    [
        "early_implementation",
        "code_in_spec",
        "different_pr",
        "no_approval",
        "different_session",
    ],
)
def test_approval_boundary_fails(measure, failure):
    ctx = context("approval")
    first, final = ctx._environment_context.data["event_phases"]
    pr = final["github"]["repositories"][0]["items"][0]
    if failure == "early_implementation":
        first["history_end"] = 4
    elif failure == "code_in_spec":
        first["github"]["repositories"][0]["items"][0]["files"] += ["app/api.py"]
    elif failure == "different_pr":
        pr["number"] = 35
    elif failure == "no_approval":
        pr["reviews"] = []
    elif failure == "different_session":
        final["dispatches"][1] = {**final["dispatches"][1], "session_id": "new"}
    assert not measure(ctx)["passed"]


def test_correction_requires_an_actual_fix_and_another_review(measure):
    ctx = context("correction")
    ctx._tool_calls.pop()
    assert not measure(ctx)["passed"]
    ctx = context("correction")
    ctx._environment_context.data["event_baseline"]["github"]["repositories"][0][
        "items"
    ][0]["head_sha"] = "fixed"
    assert not measure(ctx)["passed"]


@pytest.mark.asyncio
async def test_events_use_production_matching_and_resume_thread(tmp_path):
    from omniagents_workplace.evaluation import build
    from omniagents_workplace.evaluation_events import FactoryEventRun
    from omniagents_workplace.server import Workplace

    env = build(
        {
            "seed": "acme",
            "fixtures_dir": "fixtures/workspaces",
            "github_dir": "fixtures/github",
        },
        project_root=FACTORY,
        eval_dir=FACTORY / "evaluations",
    )
    w = Workplace(
        tmp_path / "workplace",
        base_url="http://127.0.0.1:7399",
        seed=env._seed({"fixture": "api_service", "github": "issues.yml"}),
        seed_dir=tmp_path,
        run_workflows=False,
    )
    ctx = EnvironmentContext("test")
    env._live["test"] = {"server": SimpleNamespace(workplace=w)}
    started = []

    class Run:
        available_tools = []

        def __init__(self):
            self.items = []

        def history(self):
            return self.items

        def run_error(self):
            return None

        async def run_turn(self, prompt, config):
            self.items += [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "Done"},
            ]
            return {}

        async def cleanup(self):
            pass

    class Target:
        def _project_context(self):
            return SimpleNamespace(root=FACTORY, entrypoint="foreman")

        def for_agent(self, key):
            assert key == "foreman"
            return self

        async def start(self, scenario, **kwargs):
            assert "events" not in scenario
            started.append(scenario)
            return Run()

    run = FactoryEventRun(
        Target(),
        {
            "events": [
                {"provider": "slack", "text": "@factory First request"},
                {"provider": "slack", "text": "@factory Follow up"},
            ]
        },
        {},
        env,
        ctx,
    )
    try:
        await run.run_turn("unused")
        assert run.run_error() is None
        assert len(started) == 1
        dispatches = ctx.data["event_dispatches"]
        assert len(dispatches) == 2
        assert len({d["session_id"] for d in dispatches}) == 1
        assert [p["history_end"] for p in ctx.data["event_phases"]] == [2, 4]
        assert "Someone @-mentioned" in run.history()[0]["content"]
    finally:
        await run.cleanup()
        w.close()


def test_ci_requires_exactly_the_active_scenarios():
    import yaml

    scenarios = [
        s
        for p in (FACTORY / "evaluations/scenarios").glob("*.yml")
        for s in yaml.safe_load(p.read_text())["scenarios"]
    ]
    workflow = yaml.safe_load(
        (FACTORY / ".github/workflows/evaluations.yml").read_text()
    )
    matrix = workflow["jobs"]["scenario"]["strategy"]["matrix"]["include"]
    required = yaml.safe_load((FACTORY / "workplace.yml").read_text())["factory"][
        "repository"
    ]["required_checks"]
    assert (
        {s["name"] for s in scenarios}
        == {s["scenario"] for s in matrix}
        == set(required)
    )
    assert {s["name"]: s["agent"] for s in scenarios} == {
        s["scenario"]: s["agent"] for s in matrix
    }


@pytest.mark.parametrize("fixture", ["api_service", "api_service_pr_pagination"])
def test_acceptance_rejects_missing_or_broken_pagination(fixture):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(FACTORY / "evaluations/checks/pagination.py")],
        cwd=FACTORY / "evaluations/fixtures/workspaces" / fixture,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr or "KeyError: 'page'" in result.stderr


def test_review_must_start_after_implementation_completes(measure):
    ctx = context()
    ctx._tool_calls[0].output = ToolOutput(index=5, output="Completed")
    assert not measure(ctx)["passed"]


def test_rejected_attempt_does_not_fail_a_completed_approval_workflow(measure):
    ctx = context("approval")
    ctx._tool_calls.insert(
        0,
        ToolCall(
            index=0,
            name="implementation",
            args={},
            output=ToolOutput(index=1, output_json={"error": "not approved"}),
        ),
    )
    assert measure(ctx)["passed"]


def test_http_acceptance_allows_a_changed_internal_handler_signature(tmp_path):
    """A valid query-pair handler must not fail because the oracle passes dicts."""
    import os
    import shutil
    import subprocess
    import sys

    checkout = tmp_path / "app"
    shutil.copytree(
        FACTORY / "evaluations/fixtures/workspaces/api_service",
        checkout,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    api = checkout / "app/api.py"
    text = api.read_text()
    start, end = text.index("def list_tasks("), text.index("def get_task(")
    text = text[:start] + """def list_tasks(store, query):
    params = {key: value for key, value in query}
    try:
        page = int(params.get("page", "1"))
        size = int(params.get("per_page", "10"))
    except ValueError:
        return 400, {"error": "invalid pagination"}
    if page < 1 or not 1 <= size <= 100:
        return 400, {"error": "invalid pagination"}
    tasks = store.all()
    return 200, {"tasks": [t.to_dict() for t in tasks[(page-1)*size:page*size]],
                 "page": page, "pages": (len(tasks)+size-1)//size}


""" + text[end:]
    api.write_text(text)
    server = checkout / "app/serve.py"
    server.write_text(
        server.read_text().replace("dict(parse_qsl(url.query))", "parse_qsl(url.query)")
    )
    result = subprocess.run(
        [sys.executable, str(FACTORY / "evaluations/checks/pagination.py")],
        cwd=checkout,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def outcome(measure):
    return measure.__globals__["factory_outcome"]


@pytest.mark.parametrize("workflow", ["clear", "approval", "correction"])
@pytest.mark.parametrize("entrypoint", ["foreman", "generalist"])
def test_outcome_accepts_direct_work_without_role_calls(outcome, workflow, entrypoint):
    ctx = context(workflow)
    ctx.metadata["scenario"]["expect"]["entrypoint"] = entrypoint
    for d in ctx._environment_context.data["event_phases"][-1]["dispatches"]:
        d["agent"] = entrypoint
    ctx._tool_calls = []
    assert outcome(ctx)["passed"]


@pytest.mark.parametrize(
    "failure",
    [
        "no_dispatch",
        "failed_dispatch",
        "merged",
        "main_changed",
        "bad_code",
        "stale_validation",
        "no_reply",
        "human_reply",
        "wrong_thread",
        "no_docs",
    ],
)
def test_shared_outcome_keeps_acceptance_and_safety_checks(outcome, failure):
    test_incomplete_or_unsafe_workflow_fails(outcome, failure)


@pytest.mark.parametrize(
    "failure", ["code_in_spec", "different_pr", "no_approval", "different_session"]
)
def test_shared_outcome_keeps_approval_boundary(outcome, failure):
    test_approval_boundary_fails(outcome, failure)


def test_outcome_requires_correction_on_original_pr(outcome):
    ctx = context("correction")
    ctx._environment_context.data["event_baseline"]["github"]["repositories"][0][
        "items"
    ][0]["head_sha"] = "fixed"
    assert not outcome(ctx)["passed"]


def test_outcome_rejects_wrong_entrypoint_and_unready_pr(outcome):
    ctx = context()
    final = ctx._environment_context.data["event_phases"][-1]
    final["dispatches"][0]["agent"] = "generalist"
    assert not outcome(ctx)["passed"]
    final["dispatches"][0]["agent"] = "foreman"
    final["github"]["repositories"][0]["items"][0]["draft"] = True
    assert not outcome(ctx)["passed"]


def test_generalist_profile_changes_only_entrypoint_and_uses_shared_base(monkeypatch):
    import yaml
    from omniagents.core.project.runtime import load_project, build_spec_from_project

    monkeypatch.setenv("OMNIAGENTS_PROJECT_OVERLAYS", "")
    baseline = load_project(str(FACTORY))
    overlay_path = FACTORY / "profiles/generalist.yml"
    assert yaml.safe_load(overlay_path.read_text()) == {
        "agents": {"entrypoint": "generalist"}
    }
    monkeypatch.setenv("OMNIAGENTS_PROJECT_OVERLAYS", str(overlay_path))
    personal = load_project(str(FACTORY))
    assert baseline.entrypoint == "foreman"
    assert personal.entrypoint == "generalist"
    assert {k: v for k, v in baseline.raw.items() if k != "agents"} == {
        k: v for k, v in personal.raw.items() if k != "agents"
    }
    spec = build_spec_from_project(personal)
    assert {tool.tool_name for tool in spec.agent_tool_configs} == {"explore"}
    assert str(FACTORY / "agents/generalist/skills") in spec.skill_roots
    assert str(FACTORY / "skills") in spec.skill_roots
    definition = yaml.safe_load((FACTORY / "agents/generalist/agent.yml").read_text())
    assert definition["extends"] == "base"
