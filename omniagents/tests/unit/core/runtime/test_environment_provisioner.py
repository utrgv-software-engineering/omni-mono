import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
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
        build=[],
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


def _remote(tmp_path: Path, url: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "git-remote",
        "mountName": "remote",
        "repoUrl": url,
        "checkoutPath": str(tmp_path / "checkouts" / "remote"),
        **extra,
    }


@pytest.mark.asyncio
async def test_host_profile_materializes_remote_source_and_reuses_the_checkout(
    tmp_path,
):
    url = _make_remote(tmp_path)
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)
    remote_source = _remote(tmp_path, url)

    provisioned = await provisioner.provision(request(sources=[remote_source]))

    assert provisioned.kind is EnvironmentKind.HOST
    assert isinstance(provisioned.workspace, MountedHostWorkspace)
    assert provisioned.terminal_defaults.cwd == str(layout)
    checkout = tmp_path / "checkouts" / "remote"
    assert (checkout / ".git").is_dir()
    assert (layout / "remote").resolve() == checkout.resolve()
    assert await provisioned.workspace.read_text("remote/README.md") == (
        "hello from remote\n"
    )

    # Reuse is verbatim: durable working state survives re-provisioning.
    (checkout / "wip.txt").write_text("uncommitted work\n")
    again = await provisioner.provision(request(sources=[remote_source]))
    assert await again.workspace.read_text("remote/wip.txt") == "uncommitted work\n"


@pytest.mark.asyncio
async def test_workspaces_of_one_project_share_its_remote_checkout(tmp_path):
    """Two chats on a project get one clone, the way local folders are shared."""
    url = _make_remote(tmp_path)
    remote_source = _remote(tmp_path, url)
    first, second = await asyncio.gather(
        _host_provisioner(tmp_path / "chat-a").provision(
            request(sources=[remote_source])
        ),
        _host_provisioner(tmp_path / "chat-b").provision(
            request(sources=[remote_source])
        ),
    )

    (tmp_path / "checkouts" / "remote" / "shared.txt").write_text("seen by both\n")
    assert await first.workspace.read_text("remote/shared.txt") == "seen by both\n"
    assert await second.workspace.read_text("remote/shared.txt") == "seen by both\n"
    assert (
        not (tmp_path / "chat-a" / "remote")
        .resolve()
        .is_relative_to(tmp_path / "chat-a")
    )


@pytest.mark.asyncio
async def test_host_remote_source_requires_a_checkout_path(tmp_path):
    url = _make_remote(tmp_path)
    source = _remote(tmp_path, url)
    del source["checkoutPath"]

    with pytest.raises(ValueError, match="has no checkoutPath"):
        await _host_provisioner(tmp_path / "layout").provision(
            request(sources=[source])
        )


@pytest.mark.asyncio
async def test_host_remote_materialization_honors_ref(tmp_path):
    url = _make_remote(tmp_path)
    layout = tmp_path / "layout"
    provisioner = _host_provisioner(layout)

    provisioned = await provisioner.provision(
        request(sources=[_remote(tmp_path, url, ref="feature")])
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
    await provisioner.provision(request(sources=[_remote(tmp_path, url)]))

    with pytest.raises(ValueError, match="refusing to reuse"):
        await provisioner.provision(request(sources=[_remote(tmp_path, str(other))]))


@pytest.mark.asyncio
async def test_host_profile_refuses_non_checkout_mount_directory(tmp_path):
    url = _make_remote(tmp_path)
    stray = tmp_path / "checkouts" / "remote"
    stray.mkdir(parents=True)
    (stray / "existing.txt").write_text("not a clone\n")
    provisioner = _host_provisioner(tmp_path / "layout")

    with pytest.raises(ValueError, match="is not a checkout"):
        await provisioner.provision(request(sources=[_remote(tmp_path, url)]))


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
                _remote(
                    tmp_path,
                    url,
                    auth={
                        "tokenEnv": "OMNI_TEST_GIT_TOKEN",
                        "username": "x-access-token",
                    },
                )
            ]
        )
    )

    checkout = tmp_path / "checkouts" / "remote"
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
                    _remote(
                        tmp_path, url, auth={"tokenEnv": "BAD NAME", "username": "x"}
                    )
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


# ---------------------------------------------------------------------------
#  Host means no sandbox — including for the file tools.
#
#  The host target's shell already reaches the whole machine (host ``exec``
#  resolves only ``cwd``), so a workspace-root fence on the file tools
#  contained nothing and only made the two tool families disagree about what
#  exists. These assert the *effect*, not a profile declaration: the flag that
#  used to express this sat unread in a YAML file for months precisely because
#  the only test on it checked that the key was written down.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_profile_workspace_is_unconfined(tmp_path):
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("host"),
        workspace_directory_resolver=lambda workspace: tmp_path,
    )

    provisioned = await provisioner.provision(request())
    workspace = provisioned.workspace

    assert workspace.enforces_confinement is False
    # An absolute path outside the root names what it says.
    assert workspace.resolve("/etc/hostname") == str(
        (Path(workspace.root) / "/etc/hostname").resolve()
    )
    # Relative paths are still anchored at the root — unconfined lifts the
    # fence, it does not move the frame of reference.
    assert workspace.resolve("sub/file.txt") == str(tmp_path / "sub" / "file.txt")


@pytest.mark.asyncio
async def test_host_profile_git_metadata_workspace_is_unconfined(tmp_path):
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
    assert provisioned.workspace.enforces_confinement is False
    assert provisioned.workspace.resolve("/etc/hostname") == str(
        (Path(provisioned.workspace.root) / "/etc/hostname").resolve()
    )
    # The authorized-metadata overrides still answer the boundary questions
    # git_* asks, which is what they were always for.
    assert provisioned.workspace.contains_path(str(metadata / "config"))


@pytest.mark.asyncio
async def test_host_profile_composite_workspace_is_unconfined(tmp_path):
    layout = tmp_path / "layout"
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
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
    assert provisioned.workspace.enforces_confinement is False


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

    assert provisioned.kind is EnvironmentKind.CONTAINER
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
        state_path=tmp_path / "sandbox-state" / "snapshot-1.json",
    )


@pytest.mark.asyncio
async def test_devbox_capabilities_reflect_network_truth(monkeypatch, tmp_path):
    def _provision_with(profile_raw_extra, *, network_blocked=False):
        brought = BroughtUpSession(
            session=object(),
            started_services=[],
            service_urls={},
            manifest=SimpleNamespace(root="/workspace"),
            client_type="docker",
            container_id="container-1",
            network_blocked=network_blocked,
        )
        monkeypatch.setattr(module, "bring_up_session", AsyncMock(return_value=brought))
        base = profile("docker")
        raw = dict(base.raw)
        raw["client"] = {**raw["client"], **profile_raw_extra}
        resolved = ResolvedProfile(
            raw=raw,
            build=[],
            init=[],
            services=[],
            terminal=base.terminal,
            sources=[],
        )
        provisioner = ServeEnvironmentProvisioner(
            profile_resolver=lambda request, workspace_dir: resolved,
            workspace_directory_resolver=lambda workspace: tmp_path,
        )
        return provisioner.provision(request())

    from omniagents.execution import EnvironmentCapability

    # Ordinary docker sandbox: full network, and a genuine containment
    # boundary — the approval tiers key on CONTAINED, never on kind.
    provisioned = await _provision_with({})
    assert EnvironmentCapability.NETWORK in provisioned.capabilities
    assert EnvironmentCapability.CONTAINED in provisioned.capabilities

    # network_mode "none": no egress exists, and the toggle is unsupported.
    provisioned = await _provision_with({"network_mode": "none"})
    assert EnvironmentCapability.NETWORK not in provisioned.capabilities
    result = await provisioned.lifecycle_controller.set_network(
        SimpleNamespace(environment_id="environment-1", sandbox_runtime=None),
        enabled=True,
    )
    assert result["supported"] is False

    # Persisted offline marker re-applied during bring-up: NETWORK dropped.
    provisioned = await _provision_with({}, network_blocked=True)
    assert EnvironmentCapability.NETWORK not in provisioned.capabilities
    # Everything else stays granted — this is an egress statement only.
    assert EnvironmentCapability.PROCESS in provisioned.capabilities

    # unix_local: host-PLACED (kind host) despite carrying a sandbox
    # session, and runs raw host processes — NOT contained, so no
    # auto-approve tier can ever treat it as a boundary. Its host network
    # stays truthful.
    provisioned = await _provision_with({"type": "unix_local"})
    assert provisioned.kind is EnvironmentKind.HOST
    assert EnvironmentCapability.CONTAINED not in provisioned.capabilities
    assert EnvironmentCapability.NETWORK in provisioned.capabilities

    # wasm sandbox (bashkit + pyodide): host-placed, genuinely contained,
    # no processes to attach PTYs or services to. Network is OFF by
    # default — the sandbox boots contained-and-offline (auto-approvable)
    # and sandbox.set_network keeps NETWORK current if the user enables
    # egress later.
    provisioned = await _provision_with({"type": "wasm"})
    assert provisioned.kind is EnvironmentKind.HOST
    assert EnvironmentCapability.CONTAINED in provisioned.capabilities
    assert EnvironmentCapability.PTY not in provisioned.capabilities
    assert EnvironmentCapability.SERVICES not in provisioned.capabilities
    assert EnvironmentCapability.NETWORK not in provisioned.capabilities
    assert EnvironmentCapability.PROCESS in provisioned.capabilities
    # Explicit grants boot networked: an allowlist...
    provisioned = await _provision_with(
        {"type": "wasm", "network": {"allow": ["example.com"]}}
    )
    assert EnvironmentCapability.NETWORK in provisioned.capabilities
    # ...or allow_all.
    provisioned = await _provision_with(
        {"type": "wasm", "network": {"allow_all": True}}
    )
    assert EnvironmentCapability.NETWORK in provisioned.capabilities
    # The explicit opt-out is offline too — hard-off, with no toggle.
    provisioned = await _provision_with({"type": "wasm", "network": "none"})
    assert EnvironmentCapability.NETWORK not in provisioned.capabilities
    assert EnvironmentCapability.CONTAINED in provisioned.capabilities

    # docker keeps the local-container placement family.
    provisioned = await _provision_with({})
    assert provisioned.kind is EnvironmentKind.CONTAINER


@pytest.mark.asyncio
async def test_lifecycle_controller_network_toggle_uses_session(monkeypatch, tmp_path):
    class _ExecSession:
        def __init__(self):
            self.calls = []
            self.blocked = False

        async def exec(self, cmd, *, shell=False, user=None):
            self.calls.append((cmd, user))
            if cmd.startswith("test -f"):
                return SimpleNamespace(
                    exit_code=0 if self.blocked else 1, stdout=b"", stderr=b""
                )
            if "touch" in cmd:
                self.blocked = True
            elif "rm -f" in cmd:
                self.blocked = False
            return SimpleNamespace(exit_code=0, stdout=b"", stderr=b"")

    session = _ExecSession()
    brought = BroughtUpSession(
        session=session,
        started_services=[],
        service_urls={},
        manifest=SimpleNamespace(root="/workspace"),
        client_type="docker",
        container_id="container-1",
    )
    monkeypatch.setattr(module, "bring_up_session", AsyncMock(return_value=brought))
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("docker"),
        workspace_directory_resolver=lambda workspace: tmp_path,
    )
    provisioned = await provisioner.provision(request())
    controller = provisioned.lifecycle_controller
    lease = SimpleNamespace(
        environment_id="environment-1",
        sandbox_runtime=SimpleNamespace(session=session),
    )

    assert await controller.get_network(lease) == {
        "ok": True,
        "supported": True,
        "enabled": True,
    }
    result = await controller.set_network(lease, enabled=False)
    assert result == {"ok": True, "supported": True, "enabled": False}
    # Enforcement ran as root inside the container.
    assert all(user == "root" for (_, user) in session.calls)
    assert (await controller.get_network(lease))["enabled"] is False
    result = await controller.set_network(lease, enabled=True)
    assert result["enabled"] is True
    assert (await controller.get_network(lease))["enabled"] is True

    # Non-docker backends report unsupported instead of pretending.
    controller.backend = "unix_local"
    assert (await controller.set_network(lease, enabled=False))["supported"] is False
    assert (await controller.get_network(lease))["supported"] is False


@pytest.mark.asyncio
async def test_lifecycle_controller_network_toggle_wasm_session(monkeypatch, tmp_path):
    """Wasm toggles in-process via the session, never via iptables exec."""

    class _WasmSession:
        def __init__(self, toggleable=True):
            self.network_toggleable = toggleable
            self.network_enabled = True

        async def set_network_enabled(self, enabled):
            self.network_enabled = enabled

    session = _WasmSession()
    brought = BroughtUpSession(
        session=session,
        started_services=[],
        service_urls={},
        manifest=SimpleNamespace(root="/workspace"),
        client_type="wasm",
        container_id=None,
    )
    monkeypatch.setattr(module, "bring_up_session", AsyncMock(return_value=brought))
    provisioner = ServeEnvironmentProvisioner(
        profile_resolver=lambda request, workspace_dir: profile("wasm"),
        workspace_directory_resolver=lambda workspace: tmp_path,
    )
    provisioned = await provisioner.provision(request())
    controller = provisioned.lifecycle_controller
    lease = SimpleNamespace(
        environment_id="environment-1",
        sandbox_runtime=SimpleNamespace(session=session),
    )

    assert await controller.get_network(lease) == {
        "ok": True,
        "supported": True,
        "enabled": True,
    }
    result = await controller.set_network(lease, enabled=False)
    assert result == {"ok": True, "supported": True, "enabled": False}
    assert session.network_enabled is False
    assert (await controller.get_network(lease))["enabled"] is False
    result = await controller.set_network(lease, enabled=True)
    assert result["enabled"] is True
    assert session.network_enabled is True

    # An explicitly-offline wasm profile (network: none) reads as
    # unsupported — its contained-and-offline auto-approval basis cannot
    # be revoked mid-session.
    offline = _WasmSession(toggleable=False)
    lease_offline = SimpleNamespace(
        environment_id="environment-1",
        sandbox_runtime=SimpleNamespace(session=offline),
    )
    result = await controller.set_network(lease_offline, enabled=True)
    assert result["supported"] is False
    assert offline.network_enabled is True  # untouched
    assert await controller.get_network(lease_offline) == {
        "ok": True,
        "supported": False,
        "enabled": False,
    }


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
    teardown.assert_awaited_once_with(
        session,
        [],
        discard_snapshot=False,
        detach=False,
        destroy=False,
        state_path=None,
    )
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

    teardown.assert_awaited_once_with(session, [], detach=False)
