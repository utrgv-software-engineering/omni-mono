"""One opt-in live factory task, using disposable Workplace data and a real VM.

OMNI_TEST_QUICKSAND_FACTORY=1 pytest tests/integration/test_quicksand_factory.py -s
Requires a sibling omni-factory checkout (or OMNI_FACTORY_ROOT) and model sign-in.
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest
import yaml

import socket


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("OMNI_TEST_QUICKSAND_FACTORY") != "1",
        reason="opt-in live factory task",
    ),
]


async def test_generalist_repository_and_library(tmp_path, monkeypatch):
    import uvicorn
    from omniagents_workplace.server import Workplace, create_app
    from omniagents_workplace.evaluation import build
    from omniagents.core.providers import codex
    from omniagents.core.project.runtime import load_project
    from omniagents.integrations.environment import RunEnvironments, GitHubConfig
    from omniagents.integrations.server import _run_starter
    from omniagents.execution import AgentHostResources

    source = Path(
        os.environ.get(
            "OMNI_FACTORY_ROOT", Path(__file__).resolve().parents[4] / "omni-factory"
        )
    )
    assert (source / "project.yml").is_file(), source
    # Fail before expensive VM provisioning if the selected sign-in cannot refresh.
    tokens = codex.load_tokens()
    assert tokens, "Sign in first or set OMNIAGENTS_CODEX_HOME to an existing sign-in"
    await codex._CodexTokenManager(tokens).access_token()
    # Preserve only the model sign-in; local product overlays and services are isolated.
    monkeypatch.setenv("OMNIAGENTS_CODEX_HOME", str(codex._config_dir()))
    project_dir = tmp_path / "factory"
    shutil.copytree(
        source,
        project_dir,
        ignore=shutil.ignore_patterns(
            ".git", ".env*", ".venv", ".workplace", "node_modules", "__pycache__"
        ),
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    fixture = build(
        {
            "seed": "acme",
            "fixtures_dir": "fixtures/workspaces",
            "github_dir": "fixtures/github",
        },
        project_root=project_dir,
        eval_dir=project_dir / "evaluations",
    )
    port = free_port()
    workplace = Workplace(
        tmp_path / "workplace",
        base_url=f"http://127.0.0.1:{port}",
        sandbox_url="http://10.0.2.101:7300",
        seed=fixture._seed({"github": "issues.yml", "fixture": "api_service"}),
        run_workflows=False,
    )
    app = workplace.apps.by_username("acme-factory")
    assert app
    for key, value in workplace.apps.env(app).items():
        monkeypatch.setenv(key, value)
    profile_path = project_dir / "sandbox_profile_quicksand.yml"
    profile = yaml.safe_load(profile_path.read_text())
    profile["client"]["state_dir"] = str(tmp_path / "vms")
    profile["options"]["host_services"] = {7300: port}
    profile["options"]["port_forwards"] = {8000: free_port()}
    profile_path.write_text(yaml.safe_dump(profile))
    monkeypatch.setenv("OMNIAGENTS_SANDBOX_PROFILE", str(profile_path))
    project = load_project(str(project_dir), overlays=[])
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(workplace, static_dir=tmp_path),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if serving.done():
                await serving
            await asyncio.sleep(0.05)
        github = GitHubConfig(
            repositories=("acme/api-service",),
            git_url=os.environ["GITHUB_GIT_URL"],
            token_env="GITHUB_MCP_TOKEN",
        )
        environments = RunEnvironments(profile_path, github)
        async with environments.sandbox(
            "quicksand-proof", state_path=tmp_path / "factory-state.json"
        ) as sandbox:
            resources = AgentHostResources.create("quicksand-factory-proof")
            sandbox.register(resources)

            async def principal():
                return None

            steps = []

            async def progress(step):
                steps.append(step)
                print(
                    "Factory:",
                    step.name if step.kind == "tool" else step.text,
                    flush=True,
                )

            run = _run_starter(project, resources, None, principal)
            result = await asyncio.wait_for(
                run(
                    "generalist",
                    "quicksand-factory-proof",
                    (
                        "This is a disposable integration test workspace. In the connected acme/api-service repository, "
                        "inspect the code, create a Git worktree on branch quicksand-proof at /workspace/proof-worktree, "
                        "and add QUICKSAND_PROOF.md containing the exact marker quicksand-factory-verified and a short accurate description of the code. "
                        "Run the repository's existing tests. Create a Library document titled QuickSand factory proof, "
                        "and edit its file in /workspace/Library to include quicksand-factory-verified and the verified test result. "
                        "Use your normal tools and approvals. Leave edits uncommitted, do not push or contact anyone. "
                        "No worker delegation is needed. Finish with a concise report."
                    ),
                    progress,
                ),
                900,
            )
            assert result.error is None, result
            proof = await sandbox.session.exec(
                "cat /workspace/proof-worktree/QUICKSAND_PROOF.md && git -C /workspace/proof-worktree status --porcelain"
            )
            assert (
                proof.ok()
                and b"quicksand-factory-verified" in proof.stdout
                and b"?? QUICKSAND_PROOF.md" in proof.stdout
            ), proof
            artifacts = [
                a
                for a in workplace.library.list()
                if a["title"] == "QuickSand factory proof"
            ]
            assert len(artifacts) == 1, artifacts
            artifact = artifacts[0]
            assert (
                "quicksand-factory-verified"
                in (
                    workplace.library.root / artifact["id"] / artifact["main"]
                ).read_text()
            )
            assert any(s.kind == "tool" for s in steps)
            print(
                "Real generalist repository/worktree/Library proof passed", flush=True
            )
    finally:
        server.should_exit = True
        await serving
        workplace.close()
