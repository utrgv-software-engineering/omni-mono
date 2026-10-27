import time

import pytest

from omniagents_workplace.github.actions import (
    Workflow,
    evaluate,
    expand_jobs,
    triggered,
)


def _wf(doc):
    return Workflow(
        path=".github/workflows/ci.yml", name=doc.get("name", "ci"), doc=doc
    )


def test_triggers_filter_by_branch_and_path():
    wf = _wf(
        {
            True: {
                "push": {
                    "branches": ["main", "release/**"],
                    "paths-ignore": ["docs/**"],
                },
                "pull_request": {"branches": ["main"]},
            }
        }
    )
    assert triggered(wf, "push", "main", ["app/api.py"])
    assert triggered(wf, "push", "release/1.2/x", ["app/api.py"])
    assert not triggered(wf, "push", "feature", ["app/api.py"])
    assert not triggered(wf, "push", "main", ["docs/a.md"])
    assert triggered(wf, "pull_request", "main", ["x"], "opened")
    assert not triggered(wf, "pull_request", "main", ["x"], "closed")
    assert not triggered(wf, "workflow_dispatch", "main")
    assert triggered(_wf({"on": "push"}), "push", "anything", [])
    assert triggered(
        _wf({"on": ["push", "pull_request"]}), "pull_request", "main", [], "synchronize"
    )


def test_expressions_read_as_github_reads_them():
    ctx = {
        "github": {"event_name": "push", "ref": "refs/heads/main"},
        "matrix": {"py": "3.12"},
        "steps": {"a": {"outputs": {"x": "1"}}},
    }
    assert evaluate("github.event_name == 'push' && matrix.py == '3.12'", ctx) is True
    assert evaluate("startsWith(github.ref, 'refs/heads/')", ctx) is True
    assert evaluate("steps.a.outputs.x", ctx) == "1"
    assert evaluate("contains(fromJSON('[\"a\",\"b\"]'), 'b')", ctx) is True
    assert evaluate("format('{0}-{1}', 'a', 2)", ctx) == "a-2"
    assert evaluate("!(github.event_name != 'push')", ctx) is True


def test_a_matrix_is_one_job_per_combination():
    jobs = expand_jobs(
        _wf(
            {
                "jobs": {
                    "test": {
                        "strategy": {
                            "matrix": {
                                "py": ["3.11", "3.12"],
                                "os": ["a"],
                                "include": [{"py": "3.12", "extra": "yes"}],
                            }
                        }
                    }
                }
            }
        )
    )
    # GitHub's names list every matrix value, an include's too.
    assert [name for *_, name in jobs] == ["test (3.11, a)", "test (3.12, a, yes)"]
    assert jobs[1][2] == {"py": "3.12", "os": "a", "extra": "yes"}
    # Only includes: each its own combination, and no job without values.
    only = expand_jobs(
        _wf(
            {
                "jobs": {
                    "eval": {
                        "name": "${{ matrix.scenario }}",
                        "strategy": {
                            "matrix": {
                                "include": [
                                    {"agent": "a", "scenario": "one"},
                                    {"agent": "b", "scenario": "two"},
                                ]
                            }
                        },
                    }
                }
            }
        )
    )
    assert [name for *_, name in only] == ["one", "two"]


WORKFLOW = """
name: CI
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: hello
        run: echo "greeting=hi" >> "$GITHUB_OUTPUT"
      - run: test "${{ steps.hello.outputs.greeting }}" = hi && test -f README.md
      - uses: some/marketplace-action@v1
  test:
    needs: lint
    runs-on: windows-latest
    strategy:
      matrix:
        n: [1, 2]
    steps:
      - run: echo "matrix ${{ matrix.n }} on ${{ runner.os }}"
      - run: test -f FAIL && exit 1 || true
      - run: |
          if [ -f BROKEN ]; then echo broken; exit 3; fi
      - run: echo after-failure
      - if: failure()
        run: echo cleanup-after-failure
"""


def _wait(run, seconds=60):
    deadline = time.time() + seconds
    while run.status != "completed":
        if time.time() > deadline:
            raise AssertionError(f"run {run.name} still {run.status}")
        time.sleep(0.1)
    return run


@pytest.fixture
def repo(workplace_server):
    workplace, _ = workplace_server
    state = workplace.github
    repo = state.create_repo("acme", "svc", "admin", auto_init=True)
    return workplace, state, repo


def test_a_push_runs_its_workflow_on_this_machine(repo):
    workplace, state, repo = repo
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/ci.yml", WORKFLOW.encode())],
        "Add CI",
    )
    [run] = list(repo.runs)
    _wait(run)
    assert (run.event, run.conclusion) == ("push", "success")
    assert [j.name for j in run.jobs] == ["lint", "test (1)", "test (2)"]
    lint, test1, _ = run.jobs
    assert "Skipped: some/marketplace-action@v1 does not run" in lint.log
    assert "matrix 1 on Linux" in test1.log
    assert [s.conclusion for s in test1.steps][-2] == "skipped"  # the failure() step
    assert state.workflows(repo) == [
        {
            "id": run.workflow_id,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "dispatch": True,
        }
    ]


def test_a_failing_pull_request_run_fails_its_checks(repo):
    workplace, state, repo = repo
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/ci.yml", WORKFLOW.encode())],
        "Add CI",
    )
    _wait(repo.runs[-1])
    state.create_branch(repo, "priya", "priya/broken", "main")
    state.write_files(repo, "priya", "priya/broken", [("BROKEN", b"x\n")], "Break it")
    pr = state.create_pull(repo, "priya", "Broken", "priya/broken", "main")
    run = _wait([r for r in repo.runs if r.event == "pull_request"][-1])
    assert run.conclusion == "failure" and run.pull_requests == [pr.number]
    test1 = run.jobs[1]
    assert "Process completed with exit code 3." in test1.log
    assert [s.conclusion for s in test1.steps[1:-1]] == [
        "success",
        "success",
        "failure",
        "skipped",
        "success",
    ]
    assert (
        "cleanup-after-failure" in test1.log
        and "after-failure\n"
        not in test1.log.split("cleanup")[0].split("exit code 3")[1]
    )
    assert state.mergeable_state(repo, pr) == "unstable"


def test_paths_filters_cancel_dispatch_and_rerun(repo):
    workplace, state, repo = repo
    slow = """
on:
  push:
    paths: ["app/**"]
  workflow_dispatch:
jobs:
  slow:
    runs-on: ubuntu-latest
    steps:
      - run: sleep 30
"""
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/slow.yml", slow.encode())],
        "Add slow",
    )
    assert not list(repo.runs)  # the push did not touch app/
    run = state.dispatch(repo, "slow.yml", "main", "admin")
    time.sleep(1)
    state.cancel_run(repo, run)
    _wait(run, 10)
    assert run.conclusion == "cancelled"
    state.rerun(repo, run, "admin")
    assert run.run_attempt == 2
    state.cancel_run(repo, run)
    _wait(run, 10)


def test_a_broken_workflow_file_fails_its_run(repo):
    workplace, state, repo = repo
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/bad.yml", b"on: push\njobs: [unclosed\n")],
        "bad",
    )
    run = _wait(list(repo.runs)[-1])
    assert run.conclusion == "failure" and "is not valid YAML" in run.jobs[0].log


def _docker_image():
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return None
    image = "node:22-bookworm"  # has bash, Python 3 and Node: the acme factory's sandbox base
    found = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return image if found.returncode == 0 else None


@pytest.mark.skipif(
    _docker_image() is None, reason="needs Docker and the node:22-bookworm image"
)
def test_jobs_run_in_containers_of_their_own(repo, monkeypatch):
    import os
    import subprocess

    from omniagents_workplace.github.actions import ActionsRunner
    from omniagents_workplace.github.spaces import ContainerSettings

    workplace, state, repo = repo
    monkeypatch.setenv("WORKPLACE_SECRET", "do-not-leak")
    state.actions = ActionsRunner(
        state, lambda: workplace.base_url, ContainerSettings(image=_docker_image())
    )
    flow = """
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: where
        run: |
          echo "inside=$(test -f /.dockerenv && echo yes || echo no)" >> "$GITHUB_OUTPUT"
          echo "secret=${WORKPLACE_SECRET:-none}" >> "$GITHUB_OUTPUT"
          echo "EXTRA=from-env" >> "$GITHUB_ENV"
          python3 -c "import sys; print('python in', sys.prefix)"
          test -f README.md
      - run: |
          test "${{ steps.where.outputs.inside }}" = yes
          test "${{ steps.where.outputs.secret }}" = none
          test "$EXTRA" = from-env
"""
    state.write_files(
        repo, "admin", "main", [(".github/workflows/ci.yml", flow.encode())], "Add CI"
    )
    run = _wait(repo.runs[-1], seconds=120)
    [job] = run.jobs
    assert run.conclusion == "success", job.log
    assert "Runner: a container of its own" in job.log and "python in /usr" in job.log
    # The container is gone with the job.
    left = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=omniagents-workplace=job",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert not [n for n in left if n.startswith(f"workplace-job-{job.id}-")]
    assert os.environ["WORKPLACE_SECRET"] == "do-not-leak"


SELF_HOSTED = """
on: push
jobs:
  evaluate:
    runs-on: self-hosted
    steps:
      - run: echo "on the self-hosted runner"
      - run: echo "python is $(command -v python3)"
"""


def test_only_the_repositories_an_admin_allows_use_the_self_hosted_runner(repo):
    workplace, state, repo = repo
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/eval.yml", SELF_HOSTED.encode())],
        "Add evals",
    )
    run = _wait(repo.runs[-1])
    assert run.conclusion == "failure"
    assert (
        "acme/svc may not use the self-hosted runner: an admin allows it"
        in run.jobs[0].log
    )
    state.update_repo(repo, "admin", self_hosted=True)
    state.rerun(repo, run, "admin")
    run = _wait(run)
    # On this machine (workflows on the host), self-hosted jobs run here too.
    assert (
        run.conclusion == "success" and "on the self-hosted runner" in run.jobs[0].log
    )
    # In the workplace's own environment, where a factory's framework is, whatever PATH has first.
    import sys
    from pathlib import Path

    assert f"python is {Path(sys.executable).parent}/python3" in run.jobs[0].log


@pytest.mark.skipif(
    _docker_image() is None, reason="needs Docker and the node:22-bookworm image"
)
def test_the_self_hosted_runner_has_docker_and_what_it_was_given(repo, tmp_path):
    import subprocess

    from omniagents_workplace.github.actions import ActionsRunner
    from omniagents_workplace.github.spaces import ContainerSettings

    workplace, state, repo = repo
    given = tmp_path / "given"
    given.mkdir()
    (given / "sign-in.json").write_text("{}")
    network = "workplace-test-self-hosted"
    subprocess.run(["docker", "network", "create", network], capture_output=True)
    try:
        hosted = ContainerSettings(image=_docker_image())
        runner = ContainerSettings(
            image=_docker_image(),
            network=network,
            docker_socket=True,
            volumes=[f"{given}:/given:ro"],
            env={"GIVEN": "yes"},
            label="the self-hosted runner",
        )
        state.actions = ActionsRunner(state, lambda: workplace.base_url, hosted, runner)
        state.update_repo(repo, "admin", self_hosted=True)
        flow = """
on: push
jobs:
  evaluate:
    runs-on: [self-hosted, linux]
    steps:
      - run: |
          test -S /var/run/docker.sock && test -f /given/sign-in.json && test "$GIVEN" = yes
          test "$(hostname)" = "$OMNIAGENTS_WORKPLACE_HOST"
  hosted:
    runs-on: ubuntu-latest
    steps:
      - run: test ! -e /var/run/docker.sock && test -z "${GIVEN:-}"
"""
        state.write_files(
            repo,
            "admin",
            "main",
            [(".github/workflows/eval.yml", flow.encode())],
            "Add evals",
        )
        run = _wait(repo.runs[-1], seconds=120)
        evaluate, hosted_job = run.jobs
        assert run.conclusion == "success", evaluate.log + hosted_job.log
        assert "Runner: the self-hosted runner" in evaluate.log
        assert "Runner: a container of its own" in hosted_job.log
    finally:
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def test_required_checks_hold_a_merge_until_they_pass(repo):
    from omniagents_workplace.github.state import GitHubError

    workplace, state, repo = repo
    flow = """
on: pull_request
jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - run: test ! -f BROKEN
"""
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/eval.yml", flow.encode())],
        "Add evals",
    )
    state.update_repo(repo, "admin", protected=["main"])
    with pytest.raises(GitHubError):  # only a protected branch requires checks
        state.update_repo(repo, "admin", required_checks={"dev": ["evaluate"]})
    state.update_repo(repo, "admin", required_checks={"main": ["evaluate", "security"]})
    state.create_branch(repo, "priya", "priya/broken", "main")
    state.write_files(repo, "priya", "priya/broken", [("BROKEN", b"x\n")], "Break it")
    pr = state.create_pull(repo, "priya", "Broken", "priya/broken", "main")
    _wait([r for r in repo.runs if r.event == "pull_request"][-1])
    state.create_review(repo, pr, "admin", event="APPROVE")
    assert state.unmet_checks(repo, pr) == [
        ("evaluate", "failing"),
        ("security", "expected"),
    ]
    assert state.mergeable_state(repo, pr) == "blocked"
    with pytest.raises(
        GitHubError, match='Required status check "evaluate" is failing'
    ):
        state.merge_pull(repo, pr, "admin")
    # Fixed, and one check no longer required: it merges.
    state.write_files(repo, "priya", "priya/broken", [("BROKEN", None)], "Fix it")
    _wait([r for r in repo.runs if r.event == "pull_request"][-1])
    state.update_repo(repo, "admin", required_checks={"main": ["evaluate"]})
    assert (
        state.unmet_checks(repo, pr) == []
        and state.mergeable_state(repo, pr) == "clean"
    )
    state.merge_pull(repo, pr, "admin")
    # Unprotecting a branch drops what it required.
    state.update_repo(repo, "admin", protected=[])
    assert repo.required_checks == {}


@pytest.mark.skipif(
    _docker_image() is None, reason="needs Docker and the node:22-bookworm image"
)
def test_a_restarted_workplace_removes_the_job_containers_it_left_and_no_one_elses():
    import subprocess

    from omniagents_workplace.github.spaces import DockerSpace

    def start(name, instance):
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                DockerSpace.LABEL,
                "--label",
                f"{DockerSpace.INSTANCE_LABEL}={instance}",
                "--entrypoint",
                "sleep",
                _docker_image(),
                "300",
            ],
            capture_output=True,
            check=True,
        )

    start("workplace-test-ours", "test-ours")
    start("workplace-test-theirs", "test-theirs")
    try:
        assert DockerSpace.remove_leftovers("docker", "test-ours") == 1
        names = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert "workplace-test-ours" not in names and "workplace-test-theirs" in names
    finally:
        subprocess.run(
            ["docker", "rm", "-f", "workplace-test-ours", "workplace-test-theirs"],
            capture_output=True,
        )


def test_a_dispatch_takes_the_workflows_input_defaults(repo):
    from omniagents_workplace.github.state import GitHubError

    workplace, state, repo = repo
    flow = """
on:
  workflow_dispatch:
    inputs:
      suite: {required: true}
      configs: {default: evaluations/benchmark-configs.yml}
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - run: echo "suite=${{ inputs.suite }} configs=${{ inputs.configs }}"
"""
    state.write_files(
        repo, "admin", "main", [(".github/workflows/b.yml", flow.encode())], "Add"
    )
    with pytest.raises(GitHubError, match="Required input 'suite'"):
        state.dispatch(repo, "b.yml", "main", "admin", {})
    run = _wait(state.dispatch(repo, "b.yml", "main", "admin", {"suite": "api"}))
    assert "suite=api configs=evaluations/benchmark-configs.yml" in run.jobs[0].log
