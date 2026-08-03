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
from omniagents.workspace import HostWorkspace
from omniagents.workspace.sandbox import SandboxWorkspace


def profile(client_type: str) -> ResolvedProfile:
    return ResolvedProfile(
        raw={"client": {"type": client_type}, "run_as": "agent"},
        init=[],
        services=[],
        terminal=TerminalSpec(command="bash", user="agent", cwd="/workspace"),
        sources=[],
    )


def request(*, sources=()) -> EnvironmentProvisionRequest:
    return EnvironmentProvisionRequest(
        environment_id="environment-1",
        workspace=WorkspaceResource("workspace-1", sources=tuple(sources)),
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
async def test_devbox_profile_provisions_sources_runtime_and_lifecycle(
    monkeypatch, tmp_path
):
    session = object()
    brought = BroughtUpSession(
        session=session,
        started_services=[(SimpleNamespace(name="code"), 4444)],
        service_urls={"code": "http://sandbox/code"},
        resume_tier=None,
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
        session_id="environment-1",
        snapshot_dir=tmp_path / "snapshots",
        prior_container_id=None,
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
        resume_tier=None,
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
        resume_tier=None,
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
