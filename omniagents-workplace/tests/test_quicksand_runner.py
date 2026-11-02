"""Runner boundaries and opt-in real Docker-in-VM workflow tests (no model calls)."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from omniagents_workplace.github.actions import ActionsRunner, Workflow
from omniagents_workplace.github.act_runtime import runtime_jobs
from omniagents_workplace.github.agent_jobs import AgentJobSpace
from omniagents_workplace.github.quicksand_space import QuicksandSettings


def test_host_runtime_requires_admin_repository_and_named_actions():
    spec = {"runs-on": "self-hosted", "steps": [{"uses": "./.github/actions/evaluate"}]}
    workflow = Workflow("ci.yml", "CI", {"jobs": {"runtime": spec}})
    with pytest.raises(ValueError, match="admin-allowed"):
        runtime_jobs(workflow, SimpleNamespace(self_hosted=False))
    assert runtime_jobs(workflow, SimpleNamespace(self_hosted=True)) == ["runtime"]
    spec["steps"].append({"run": "echo unsafe"})
    with pytest.raises(ValueError, match="only"):
        runtime_jobs(workflow, SimpleNamespace(self_hosted=True))


def test_runtime_does_not_expose_host_environment_to_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_SECRET", "host-only")
    monkeypatch.setenv("BASH_ENV", "/untrusted/startup")
    (tmp_path / "profile.yml").write_text("client: {type: quicksand}\n")
    space = AgentJobSpace(1, tmp_path / "profile.yml")
    try:
        assert "MODEL_SECRET" not in space.base_env()
        assert "BASH_ENV" not in space.runtime_env()
        assert space.runtime_env()["MODEL_SECRET"] == "host-only"
        command = space.action_command(
            "./.github/actions/evaluate", {"agent": "generalist", "scenario": "proof"}
        )
        assert command[1:4] == ["-P", "-m", "omniagents_workplace.github.agent_jobs"]
    finally:
        space.close()


def _wait(run, seconds=240):
    deadline = time.monotonic() + seconds
    while run.status != "completed":
        assert time.monotonic() < deadline, "Workflow did not finish"
        time.sleep(0.1)
    return run


@pytest.fixture
def vm_repo(workplace_server, monkeypatch):
    image = os.environ.get("OMNI_TEST_QUICKSAND_RUNNER_IMAGE")
    if not image:
        pytest.skip("set OMNI_TEST_QUICKSAND_RUNNER_IMAGE to a prepared runner save")
    workplace, _ = workplace_server
    state = workplace.github
    state.actions = ActionsRunner(
        state, lambda: workplace.base_url, quicksand=QuicksandSettings(image)
    )
    repo = state.create_repo("acme", "vm-proof", "admin", auto_init=True)
    monkeypatch.setenv("HOST_ONLY_SECRET", "must-not-reach-job")
    yield workplace, state, repo


@pytest.mark.timeout(900)
def test_real_workflow_docker_compose_and_job_containers(vm_repo):
    workplace, state, repo = vm_repo
    import hashlib

    payload = os.urandom(300000)  # More than one transport chunk, not compressible.
    digest = hashlib.sha256(payload).hexdigest()
    flow = r"""
on: push
jobs:
  containers:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: proof
        run: |
          test -z "${HOST_ONLY_SECRET:-}"
          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
          test "$(sha256sum payload.bin | cut -d' ' -f1)" = PAYLOAD_SHA
          touch /tmp/previous-job-marker
          printf 'FROM alpine:3.21\nRUN echo built-inside-vm > /proof\nCMD ["cat", "/proof"]\n' > Dockerfile
          docker build -t vm-proof .
          docker run --rm vm-proof
          printf 'services:\n  proof:\n    image: vm-proof\n' > compose.yml
          docker compose up --abort-on-container-exit --exit-code-from proof
          docker compose down
          echo 'result=verified' >> "$GITHUB_OUTPUT"
          echo 'SHARED=step-value' >> "$GITHUB_ENV"
      - run: test '${{ steps.proof.outputs.result }}' = verified && test "$SHARED" = step-value
  fresh:
    needs: containers
    runs-on: ubuntu-latest
    steps:
      - run: test ! -f /tmp/previous-job-marker
"""
    flow = flow.replace("PAYLOAD_SHA", digest)
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/proof.yml", flow.encode()), ("payload.bin", payload)],
        "Proof",
    )
    run = _wait(repo.runs[-1], 600)
    assert run.conclusion == "success", "\n".join(j.log for j in run.jobs)
    assert "built-inside-vm" in run.jobs[0].log


def test_real_vm_step_timeout_runs_cleanup(vm_repo):
    workplace, state, repo = vm_repo
    flow = """
on: push
jobs:
  timeout:
    runs-on: ubuntu-latest
    steps:
      - run: echo ready; sleep 600
        timeout-minutes: 0.02
      - if: failure()
        run: echo cleanup-after-timeout
"""
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/proof.yml", flow.encode())],
        "Timeout",
    )
    run = _wait(repo.runs[-1])
    assert run.conclusion == "failure"
    assert "timeout" in run.jobs[0].log.lower() or "deadline" in run.jobs[0].log.lower()
    assert "cleanup-after-timeout" in run.jobs[0].log


def test_real_vm_cancellation(vm_repo):
    workplace, state, repo = vm_repo
    flow = "on: push\njobs:\n  wait:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo guest-ready; sleep 600\n"
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/proof.yml", flow.encode())],
        "Cancel",
    )
    run = repo.runs[-1]
    deadline = time.monotonic() + 180
    while not state.actions._procs.get(run.id):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    state.cancel_run(repo, run)
    assert _wait(run).conclusion == "cancelled"


def test_host_action_executes_installed_runtime_but_rejects_shell_steps(
    workplace_server, tmp_path, monkeypatch
):
    monkeypatch.delenv("PRODUCTION_BACKUPS", raising=False)
    monkeypatch.delenv("PRODUCTION_HOME", raising=False)
    monkeypatch.setenv("EVALUATIONS_ARCHIVE", str(tmp_path / "archive"))
    workplace, _ = workplace_server
    state = workplace.github
    profile = tmp_path / "tools.yml"
    profile.write_text("client: {type: quicksand}\n")
    state.actions = ActionsRunner(
        state,
        lambda: workplace.base_url,
        quicksand=QuicksandSettings("never-booted"),
        agent_profile=profile,
    )
    repo = state.create_repo("acme", "trusted-factory", "admin", auto_init=True)
    repo.self_hosted = True
    marker = tmp_path / "host-escape"
    hook = tmp_path / "untrusted-hook"
    hook.write_text(f"touch {marker}\n")
    flow = f"""on: push
jobs:
  runtime:
    runs-on: self-hosted
    env:
      BASH_ENV: "{hook}"
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/staging
      - run: touch {marker}
"""
    state.write_files(
        repo,
        "admin",
        "main",
        [(".github/workflows/runtime.yml", flow.encode())],
        "Runtime boundary",
    )
    run = _wait(repo.runs[-1])
    assert not marker.exists()
    assert run.conclusion == "failure"
    assert "only" in run.jobs[0].log


@pytest.mark.asyncio
async def test_factory_evaluation_tools_use_vm_and_private_fixture_library(
    tmp_path, monkeypatch
):
    image = os.environ.get("OMNI_TEST_QUICKSAND_RUNNER_IMAGE")
    if not image:
        pytest.skip("set OMNI_TEST_QUICKSAND_RUNNER_IMAGE to a prepared runner save")
    import yaml
    from omniagents_workplace.evaluation import build
    from omniagents.execution import AgentHostResources

    factory = Path(__file__).resolve().parents[3] / "omni-factory"
    if not factory.is_dir():
        pytest.skip("sibling factory checkout required")
    profile = tmp_path / "tools.yml"
    profile.write_text(
        yaml.safe_dump(
            {
                "client": {"type": "quicksand", "state_dir": str(tmp_path / "vms")},
                "options": {
                    "image": image,
                    "disk_size": "20G",
                    "memory": "2G",
                    "network_mode": "full",
                    "startup": ["systemctl start docker"],
                    "save_interval_seconds": 0,
                },
                "manifest": {"root": "/workspace"},
            }
        )
    )
    monkeypatch.setenv("OMNIAGENTS_SANDBOX_PROFILE", str(profile))
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOST_ONLY_SECRET", "agent-process-only")
    production_library = tmp_path / "production-library"
    production_library.mkdir()
    (production_library / "secret").write_text("must-not-be-mounted")
    monkeypatch.setenv("LIBRARY_FILES_HOST_PATH", str(production_library))
    env = build(
        {
            "seed": "acme",
            "fixtures_dir": "fixtures/workspaces",
            "github_dir": "fixtures/github",
        },
        project_root=factory,
        eval_dir=factory / "evaluations",
    )
    context = await env.setup({"github": "issues.yml", "fixture": "api_service"})
    try:
        await env.register_environment(
            AgentHostResources.create("outside-vm-proof"), context
        )
        live = env._live[context.session_id]
        sandbox = live["sandbox"].session
        assert sandbox.state.type == "quicksand"
        result = await sandbox.exec(
            'test -z "${HOST_ONLY_SECRET:-}" && test ! -e /workspace/Library/secret && git -C /workspace/api-service status --porcelain && docker info --format "{{.ServerVersion}}"'
        )
        assert result.ok(), result.stderr
        workplace = live["server"].workplace
        assert workplace.sandbox_url == "http://10.0.2.101:7300"
        artifact = workplace.library.create("Boundary proof", "document", "test")
        result = await sandbox.exec(
            f"printf fixture-library > /workspace/Library/{artifact['id']}/index.md"
        )
        assert result.ok(), result.stderr
        assert (
            workplace.library.root / artifact["id"] / "index.md"
        ).read_text() == "fixture-library"
        assert (production_library / "secret").read_text() == "must-not-be-mounted"
    finally:
        await env.teardown(context)


@pytest.mark.asyncio
async def test_agent_vm_docker_data_survives_restart(tmp_path, monkeypatch):
    image = os.environ.get("OMNI_TEST_QUICKSAND_RUNNER_IMAGE")
    if not image:
        pytest.skip("set OMNI_TEST_QUICKSAND_RUNNER_IMAGE to a prepared runner save")
    import yaml
    from omniagents.integrations.environment import RunEnvironments, GitHubConfig

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    profile = tmp_path / "tools.yml"
    profile.write_text(
        yaml.safe_dump(
            {
                "client": {"type": "quicksand", "state_dir": str(tmp_path / "vms")},
                "options": {
                    "image": image,
                    "disk_size": "20G",
                    "memory": "1G",
                    "network_mode": "full",
                    "startup": ["systemctl start docker"],
                    "save_interval_seconds": 0,
                },
                "manifest": {"root": "/workspace"},
            }
        )
    )
    library = tmp_path / "library"
    library.mkdir()
    env = RunEnvironments(
        profile,
        GitHubConfig(token=""),
        explicit_profile=True,
        library_root=str(library),
    )
    state = tmp_path / "factory.json"
    async with env.sandbox("restart-proof", state_path=state) as first:
        identity = first.container_id
        result = await first.session.exec("docker volume create agent-kept")
        assert result.ok(), result.stderr
    async with env.sandbox("restart-proof", state_path=state) as second:
        assert second.container_id == identity
        result = await second.session.exec(
            'docker volume inspect agent-kept && docker info --format "{{.ServerVersion}}"'
        )
        assert result.ok() and b"agent-kept" in result.stdout, result.stderr
