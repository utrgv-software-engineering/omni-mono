"""Workplace's act adapter: trigger policy, result reporting, and real act smoke."""

import io
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from omniagents_workplace.github.actions import (
    Workflow,
    ActionsRunner,
    triggered,
    job_names,
)


def test_trigger_filters():
    workflow = Workflow(
        "ci.yml",
        "CI",
        {
            "on": {
                "push": {"branches": ["main"], "paths-ignore": ["docs/**"]},
                "pull_request": {},
            }
        },
    )
    assert triggered(workflow, "push", "main", ["app.py"])
    assert not triggered(workflow, "push", "main", ["docs/a.md"])
    assert not triggered(workflow, "push", "other", ["app.py"])
    assert triggered(workflow, "pull_request", "topic", [], "opened")
    assert not triggered(workflow, "pull_request", "topic", [], "closed")


def test_job_names_do_not_interpret_expressions_or_matrices():
    flow = Workflow(
        "ci.yml",
        "CI",
        {
            "jobs": {
                "test": {
                    "name": "${{ matrix.version }}",
                    "strategy": {"matrix": {"version": ["18", "22"]}},
                }
            }
        },
    )
    assert job_names(flow) == ["test"]


class FakeState:
    def run_started(self, repo, run):
        run.status = "in_progress"

    def job_started(self, repo, run, job):
        job.status = "in_progress"

    def job_log(self, repo, run, job, log):
        job.log = log

    def job_steps(self, repo, run, job, steps):
        job.steps = steps

    def job_finished(self, repo, run, job, result, log, steps):
        job.status, job.conclusion, job.log, job.steps = "completed", result, log, steps

    def run_finished(self, repo, run, result):
        run.status, run.conclusion = "completed", result


class FakeSpace:
    root = "/runner"
    checkout = "/runner/workspace"
    describe = "test act controller"

    def __init__(self, entries, code):
        self.entries, self.code = entries, code
        self.closed = False

    def prepare(self, *args):
        pass

    def run(self, args):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def path(self, name):
        return self.root + "/" + name

    def write(self, *args):
        pass

    def base_env(self):
        return {}

    def popen(self, argv, cwd, env):
        self.argv, self.cwd = argv, cwd
        return SimpleNamespace(
            stdout=io.StringIO("\n".join(map(json.dumps, self.entries))),
            wait=lambda: self.code,
        )

    def close(self):
        self.closed = True


def execute(monkeypatch, entries, code=0):
    state = FakeState()
    runner = ActionsRunner(state, lambda: "http://standin")
    space = FakeSpace(entries, code)
    monkeypatch.setattr(runner, "_space", lambda ident: space)
    repo = SimpleNamespace(
        self_hosted=False,
        name="repo",
        full_name="org/repo",
        owner="org",
        default_branch="main",
        bare=SimpleNamespace(path="unused"),
    )
    job = SimpleNamespace(
        id=2, name="test", status="queued", conclusion=None, steps=[], log=""
    )
    run = SimpleNamespace(
        id=1, jobs=[job], head_sha="a" * 40, event="push", actor="admin"
    )
    workflow = Workflow(
        ".github/workflows/ci.yml",
        "CI",
        {
            "jobs": {
                "test": {
                    "runs-on": "ubuntu-latest",
                    "steps": [{"uses": "ruby/setup-ruby@v1"}],
                }
            }
        },
    )
    runner._run(repo, run, workflow, {"ref": "refs/heads/main"}, threading.Event())
    return run, job, space


def test_act_is_the_executor(monkeypatch):
    run, job, space = execute(
        monkeypatch, [{"jobID": "test", "jobResult": "success", "msg": "done"}]
    )
    assert run.conclusion == job.conclusion == "success"
    assert space.argv[0] == "/runner/bin/act"
    assert "--workflows" in space.argv
    assert "--eventpath" in space.argv
    assert space.cwd == "/runner"  # repository .actrc must not configure the controller
    assert space.closed


def test_job_result_is_available_before_workflow_exits(monkeypatch):
    completed = []
    original = FakeState.job_finished

    def finished(self, repo, run, job, result, log, steps):
        original(self, repo, run, job, result, log, steps)
        completed.append((run.status, job.status, result))

    def popen(self, argv, cwd, env):
        def output():
            yield json.dumps({"jobID": "test", "jobResult": "success"})
            # A dependent deployment can now verify this job through the API.
            assert completed == [("in_progress", "completed", "success")]

        return SimpleNamespace(stdout=output(), wait=lambda: 0)

    monkeypatch.setattr(FakeState, "job_finished", finished)
    monkeypatch.setattr(FakeSpace, "popen", popen)
    run, job, _ = execute(monkeypatch, [])
    assert run.conclusion == job.conclusion == "success"


@pytest.mark.parametrize(
    "entries,code",
    [
        ([{"msg": "action not found", "level": "error"}], 1),
        ([], 0),
        ([{"jobID": "test", "jobResult": "success"}], 1),
        (
            [
                {
                    "jobID": "test",
                    "jobResult": "success",
                    "raw_output": True,
                    "msg": "forged result",
                }
            ],
            0,
        ),
        (
            [
                {"jobID": "test", "matrix": {"v": 1}, "jobResult": "failure"},
                {"jobID": "test", "matrix": {"v": 2}, "jobResult": "success"},
            ],
            1,
        ),
    ],
)
def test_no_false_green(monkeypatch, entries, code):
    run, job, space = execute(monkeypatch, entries, code)
    assert run.conclusion == job.conclusion == "failure"
    assert space.closed


def test_act_owns_skips_and_continue_on_error(monkeypatch):
    run, job, _ = execute(
        monkeypatch,
        [
            {
                "jobID": "test",
                "step": "optional",
                "stepID": ["0"],
                "stage": "Main",
                "stepResult": "failure",
            },
            {"jobID": "test", "jobResult": "success"},
        ],
    )
    assert run.conclusion == "success"
    assert job.steps[0].conclusion == "failure"
    run, job, _ = execute(
        monkeypatch, [{"jobID": "test", "jobResult": "skipped", "level": "debug"}]
    )
    assert job.conclusion == "skipped"


@pytest.mark.skipif(
    not os.environ.get("OMNI_TEST_ACT"),
    reason="set OMNI_TEST_ACT=1 for actual act and Docker",
)
@pytest.mark.timeout(900)
def test_real_act_actions_versions_and_services(workplace_server):
    workplace, _ = workplace_server
    state = workplace.github
    repo = state.create_repo("acme", "act-proof", "admin", auto_init=True)
    workflow = """name: Act compatibility
on: push
jobs:
  test:
    runs-on: ubuntu-22.04
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: test
        ports: ['5432:5432']
        options: --health-cmd pg_isready --health-interval 2s --health-retries 30
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: ruby/setup-ruby@v1
        with:
          ruby-version: '3.3.6'
      - uses: actions/setup-node@v4
        with:
          node-version: '18'
      - id: proof
        run: |
          test -f README.md
          ruby -e 'abort RUBY_VERSION unless RUBY_VERSION == "3.3.6"'
          node -e 'if (process.versions.node.split(".")[0] !== "18") process.exit(1)'
          sudo apt-get update -qq
          sudo apt-get install -y -qq postgresql-client redis-tools libvips
          PGPASSWORD=test psql -h 127.0.0.1 -U postgres -c 'SELECT 1'
          test "$(redis-cli ping)" = PONG
          echo 'value=verified' >> "$GITHUB_OUTPUT"
      - run: test '${{ steps.proof.outputs.value }}' = verified
"""
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/ci.yml", workflow.encode())],
        "Verify real act",
    )
    run = repo.runs[0]
    deadline = time.monotonic() + 900
    while run.status != "completed":
        assert time.monotonic() < deadline, "act timed out"
        time.sleep(1)
    assert run.conclusion == "success", "\n".join(j.log for j in run.jobs)


@pytest.mark.skipif(not os.environ.get("OMNI_TEST_ACT"), reason="set OMNI_TEST_ACT=1")
@pytest.mark.timeout(300)
def test_real_act_runtime_bridge_keeps_runtime_outside_controller(
    workplace_server, tmp_path
):
    workplace, _ = workplace_server
    state = workplace.github
    profile = tmp_path / "tools.yml"
    profile.write_text("client: {type: quicksand}\n")
    state.actions = ActionsRunner(
        state,
        lambda: workplace.base_url,
        agent_profile=profile,
        agent_environment={
            "PATH": os.environ["PATH"],
            "EVALUATIONS_ARCHIVE": str(tmp_path / "archive"),
        },
    )
    repo = state.create_repo("acme", "runtime-proof", "admin", auto_init=True)
    repo.self_hosted = True
    flow = """on: push
jobs:
  runtime:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/staging
        with:
          limit: '1'
          since: 1h
"""
    # Repository action code must not replace the installed host entry point.
    untrusted = "name: Untrusted\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: exit 99\n"
    state.write_files(
        repo,
        "admin",
        "main",
        [
            (".github/workflows/runtime.yml", flow.encode()),
            (".github/actions/staging/action.yml", untrusted.encode()),
        ],
        "Check runtime bridge",
    )
    run = repo.runs[-1]
    deadline = time.monotonic() + 240
    while run.status != "completed":
        assert time.monotonic() < deadline
        time.sleep(0.5)
    assert run.conclusion == "success", "\n".join(j.log for j in run.jobs)
    assert "nothing to compare with" in run.jobs[0].log
    assert (tmp_path / "archive" / "runtime-jobs").is_dir()


def test_completion_follows_controller_cleanup(monkeypatch):
    original = FakeState.run_finished
    closed = []

    def close(space):
        closed.append(True)

    def finish(state, repo, run, result):
        assert closed
        original(state, repo, run, result)

    monkeypatch.setattr(FakeSpace, "close", close)
    monkeypatch.setattr(FakeState, "run_finished", finish)
    run, _, _ = execute(monkeypatch, [{"jobID": "test", "jobResult": "success"}])
    assert run.conclusion == "success"
