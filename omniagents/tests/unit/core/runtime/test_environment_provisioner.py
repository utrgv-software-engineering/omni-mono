import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.runtime import environment_provisioner as module
from omniagents.core.runtime.environment_provisioner import (
    ServeEnvironmentProvisioner,
)
from omniagents.core.runtime.serve_harness import BroughtUpSession
from omniagents.core.sandbox.profiles import ResolvedProfile, TerminalSpec
from omniagents.execution import (
    AgentHostResources,
    EnvironmentKind,
    EnvironmentProvisionRequest,
    WorkspaceResource,
)
from omniagents.workspace import (
    GitMetadataHostWorkspace,
    HostWorkspace,
    MountedHostWorkspace,
)
from omniagents.workspace.sandbox import SandboxWorkspace


def profile(client_type: str) -> ResolvedProfile:
    return ResolvedProfile(
        raw={"client": {"type": client_type}, "run_as": "agent"},
        init=[],
        services=[],
        terminal=TerminalSpec(command="bash", user="agent", cwd="/workspace"),
        sources=[],
    )


def request(*, sources=(), snapshot_ref="snapshot-1") -> EnvironmentProvisionRequest:
    return EnvironmentProvisionRequest(
        environment_id="environment-1",
        workspace=WorkspaceResource(
            "workspace-1", snapshot_ref=snapshot_ref, sources=tuple(sources)
        ),
        profile_id="devbox",
    )


@pytest.mark.asyncio
async def test_host_profile_uses_explicit_workspace_directory(tmp_path):
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("host"),
        workspace_directory_resolver=lambda workspace: tmp_path,
    )

    provisioned = await provisioner.provision(request())

    assert provisioned.kind is EnvironmentKind.HOST
    assert isinstance(provisioned.workspace, HostWorkspace)
    assert provisioned.workspace.root == str(tmp_path)
    assert provisioned.sandbox_runtime is None


@pytest.mark.asyncio
async def test_host_profile_mounts_multiple_local_sources_under_one_logical_root(
    tmp_path,
):
    layout = tmp_path / "layout"
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    (frontend / "identity.txt").write_text("frontend")
    (backend / "identity.txt").write_text("backend")
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("host"),
        workspace_directory_resolver=lambda workspace: layout,
    )

    provisioned = await provisioner.provision(
        request(
            sources=[
                {"kind": "local", "mountName": "frontend", "path": str(frontend)},
                {"kind": "local", "mountName": "backend", "path": str(backend)},
            ]
        )
    )

    assert isinstance(provisioned.workspace, MountedHostWorkspace)
    assert provisioned.terminal_defaults.cwd == str(layout)
    assert [entry.name for entry in await provisioned.workspace.list_dir()] == [
        "backend",
        "frontend",
    ]
    assert await provisioned.workspace.read_text("frontend/identity.txt") == "frontend"
    assert await provisioned.workspace.read_text("backend/identity.txt") == "backend"


def _run_git(*args: str, cwd=None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_remote(tmp_path: Path) -> str:
    """A local bare repo standing in for the remote (main + a `feature` branch)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git("init", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("hello from remote\n")
    _run_git("add", ".", cwd=seed)
    _run_git(
        "-c",
        "user.email=test@test",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "init",
        cwd=seed,
    )
    _run_git("branch", "feature", cwd=seed)
    (seed / "feature.txt").write_text("only on feature\n")
    _run_git("checkout", "feature", cwd=seed)
    _run_git("add", ".", cwd=seed)
    _run_git(
        "-c",
        "user.email=test@test",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "feature",
        cwd=seed,
    )
    _run_git("checkout", "main", cwd=seed)
    bare = tmp_path / "remote.git"
    _run_git("clone", "--bare", str(seed), str(bare))
    return str(bare)


def _host_provisioner(layout: Path) -> ServeEnvironmentProvisioner:
    return ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("host"),
        workspace_directory_resolver=lambda workspace: layout,
    )


@pytest.mark.asyncio
async def test_host_profile_materializes_remote_source_and_reuses_the_checkout(
    tmp_path,
):
    url = _make_remote(tmp_path)
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)
    remote_source = {"kind": "git-remote", "mountName": "remote", "repoUrl": url}

    provisioned = await provisioner.provision(request(sources=[remote_source]))

    assert provisioned.kind is EnvironmentKind.HOST
    assert isinstance(provisioned.workspace, MountedHostWorkspace)
    assert provisioned.terminal_defaults.cwd == str(layout)
    checkout = layout / "remote"
    assert (checkout / ".git").is_dir()
    assert await provisioned.workspace.read_text("remote/README.md") == (
        "hello from remote\n"
    )

    # Reuse is verbatim: durable working state survives re-provisioning.
    (checkout / "wip.txt").write_text("uncommitted work\n")
    again = await provisioner.provision(request(sources=[remote_source]))
    assert await again.workspace.read_text("remote/wip.txt") == "uncommitted work\n"


@pytest.mark.asyncio
async def test_host_remote_materialization_honors_ref(tmp_path):
    url = _make_remote(tmp_path)
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)

    provisioned = await provisioner.provision(
        request(
            sources=[
                {
                    "kind": "git-remote",
                    "mountName": "remote",
                    "repoUrl": url,
                    "ref": "feature",
                }
            ]
        )
    )

    assert await provisioned.workspace.read_text("remote/feature.txt") == (
        "only on feature\n"
    )


@pytest.mark.asyncio
async def test_host_profile_refuses_checkout_of_a_different_remote(tmp_path):
    url = _make_remote(tmp_path)
    other = tmp_path / "other.git"
    _run_git("clone", "--bare", url, str(other))
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)
    await provisioner.provision(
        request(sources=[{"kind": "git-remote", "mountName": "remote", "repoUrl": url}])
    )

    with pytest.raises(ValueError, match="refusing to reuse"):
        await provisioner.provision(
            request(
                sources=[
                    {
                        "kind": "git-remote",
                        "mountName": "remote",
                        "repoUrl": str(other),
                    }
                ]
            )
        )


@pytest.mark.asyncio
async def test_host_profile_refuses_non_checkout_mount_directory(tmp_path):
    url = _make_remote(tmp_path)
    layout = tmp_path / "layout"
    stray = layout / "remote"
    stray.mkdir(parents=True)
    (stray / "existing.txt").write_text("not a clone\n")
    provisioner = _host_provisioner(layout)

    with pytest.raises(ValueError, match="is not a checkout"):
        await provisioner.provision(
            request(
                sources=[{"kind": "git-remote", "mountName": "remote", "repoUrl": url}]
            )
        )


@pytest.mark.asyncio
async def test_host_remote_auth_persists_repo_local_helper_without_the_token(
    tmp_path, monkeypatch
):
    url = _make_remote(tmp_path)
    monkeypatch.setenv("OMNI_TEST_GIT_TOKEN", "secret-token-value")
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)

    await provisioner.provision(
        request(
            sources=[
                {
                    "kind": "git-remote",
                    "mountName": "remote",
                    "repoUrl": url,
                    "auth": {
                        "tokenEnv": "OMNI_TEST_GIT_TOKEN",
                        "username": "x-access-token",
                    },
                }
            ]
        )
    )

    checkout = layout / "remote"
    helper = _run_git("-C", str(checkout), "config", "credential.helper")
    assert "$OMNI_TEST_GIT_TOKEN" in helper
    assert "secret-token-value" not in helper
    assert (
        _run_git("-C", str(checkout), "config", "credential.username")
        == "x-access-token"
    )


@pytest.mark.asyncio
async def test_host_remote_auth_rejects_shell_syntax_in_token_env_name(
    tmp_path, monkeypatch
):
    url = _make_remote(tmp_path)
    monkeypatch.setenv("BAD NAME", "token")
    provisioner = _host_provisioner(tmp_path / "layout")

    with pytest.raises(ValueError, match="invalid git token env name"):
        await provisioner.provision(
            request(
                sources=[
                    {
                        "kind": "git-remote",
                        "mountName": "remote",
                        "repoUrl": url,
                        "auth": {"tokenEnv": "BAD NAME", "username": "x"},
                    }
                ]
            )
        )


@pytest.mark.asyncio
async def test_host_profile_keeps_a_direct_source_root_with_declared_git_metadata(
    tmp_path,
):
    source = tmp_path / "checkout"
    metadata = tmp_path / "primary" / ".git"
    source.mkdir()
    (metadata / "worktrees" / "checkout").mkdir(parents=True)
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("host"),
        workspace_directory_resolver=lambda workspace: source,
    )

    provisioned = await provisioner.provision(
        request(
            sources=[
                {
                    "kind": "local-git",
                    "mountName": "checkout",
                    "path": str(source),
                    "gitDir": str(metadata / "worktrees" / "checkout"),
                    "gitCommonDir": str(metadata),
                }
            ]
        )
    )

    assert isinstance(provisioned.workspace, GitMetadataHostWorkspace)
    assert provisioned.workspace.root == str(source)
    assert provisioned.terminal_defaults.cwd == str(source)
    assert provisioned.workspace.contains_path(str(metadata / "config"))
    assert provisioned.workspace.relative_path(str(metadata / "config")) is None
    assert provisioned.workspace.io_path(str(metadata / "config")) == str(
        metadata / "config"
    )


@pytest.mark.asyncio
async def test_devbox_profile_provisions_sources_runtime_and_lifecycle(
    monkeypatch, tmp_path
):
    session = object()
    brought = BroughtUpSession(
        session=session,
        started_services=[(SimpleNamespace(name="code"), 4444)],
        service_urls={"code": "http://sandbox/code"},
        manifest=SimpleNamespace(root="/workspace"),
        client_type="docker",
        container_id="container-1",
    )
    bring_up = AsyncMock(return_value=brought)
    monkeypatch.setattr(module, "bring_up_session", bring_up)
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("docker"),
        workspace_directory_resolver=lambda workspace: tmp_path,
        snapshot_dir=tmp_path / "snapshots",
        credentials=[{"tokenEnv": "GITHUB_TOKEN"}],
    )
    source = {"kind": "local-git", "mountName": "repo", "path": "/host/repo"}

    provisioned = await provisioner.provision(request(sources=[source]))

    assert provisioned.kind is EnvironmentKind.DEVBOX
    assert isinstance(provisioned.workspace, SandboxWorkspace)
    assert provisioned.workspace.root == "/workspace"
    assert provisioned.sandbox_runtime.session is session
    assert provisioned.sandbox_runtime.run_as == "agent"
    assert provisioned.lifecycle_controller.state(
        SimpleNamespace(environment_id="environment-1")
    ) == {
        "available": True,
        "ready": True,
        "switching": False,
        "profile": "devbox",
        "backend": "docker",
        "container_id": "container-1",
        "last_error": None,
    }
    bring_up.assert_awaited_once_with(
        profile=profile("docker"),
        sources_parsed=[source],
        credentials_parsed=[{"tokenEnv": "GITHUB_TOKEN"}],
        snapshot_ref="snapshot-1",
        snapshot_dir=tmp_path / "snapshots",
    )


@pytest.mark.asyncio
async def test_environment_manager_stop_closes_provisioned_session(
    monkeypatch, tmp_path
):
    session = object()
    brought = BroughtUpSession(
        session=session,
        started_services=[],
        service_urls={},
        manifest=SimpleNamespace(root="/workspace"),
        client_type="docker",
        container_id="container-1",
    )
    monkeypatch.setattr(module, "bring_up_session", AsyncMock(return_value=brought))
    teardown = AsyncMock()
    monkeypatch.setattr(module, "teardown_session", teardown)
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("docker"),
        workspace_directory_resolver=lambda workspace: tmp_path,
    )
    resources = AgentHostResources.create(
        "serve-provisioner-host", environment_provisioner=provisioner
    )
    resources.workspaces.register(WorkspaceResource("workspace-1"))
    descriptor = await resources.environments.resolve_automatic("workspace-1", "devbox")

    stopped = await resources.environments.stop(descriptor.environment_id)

    assert stopped.state.value == "stopped"
    teardown.assert_awaited_once_with(session, [], discard_snapshot=False)
    assert (
        await resources.environments.sandbox_runtime(descriptor.environment_id) is None
    )


@pytest.mark.asyncio
async def test_invalid_brought_up_manifest_is_torn_down(monkeypatch, tmp_path):
    session = object()
    brought = BroughtUpSession(
        session=session,
        started_services=[],
        service_urls={},
        manifest=SimpleNamespace(root=None),
        client_type="docker",
        container_id="container-1",
    )
    monkeypatch.setattr(module, "bring_up_session", AsyncMock(return_value=brought))
    teardown = AsyncMock()
    monkeypatch.setattr(module, "teardown_session", teardown)
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("docker"),
        workspace_directory_resolver=lambda workspace: Path(tmp_path),
    )

    with pytest.raises(RuntimeError, match="no workspace root"):
        await provisioner.provision(request())

    teardown.assert_awaited_once_with(session, [])
