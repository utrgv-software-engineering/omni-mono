"""Product adapter for provisioning multiple environments inside one AgentHost."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from omniagents.core.sandbox.profiles import ResolvedProfile
from omniagents.execution import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentProvisionRequest,
    ProvisionedEnvironment,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
    WorkspaceResource,
)
from omniagents.workspace import HostWorkspace
from omniagents.workspace.sandbox import SandboxWorkspace

from .serve_harness import (
    _is_host_profile,
    _run_as_exec_user,
    bring_up_session,
    teardown_session,
)

ProfileResolver = Callable[[EnvironmentProvisionRequest, Path], ResolvedProfile]
WorkspaceDirectoryResolver = Callable[[WorkspaceResource], Path]

_FULL_CAPABILITIES = frozenset(
    {
        EnvironmentCapability.WORKSPACE_READ,
        EnvironmentCapability.WORKSPACE_WRITE,
        EnvironmentCapability.PROCESS,
        EnvironmentCapability.NETWORK,
        EnvironmentCapability.PTY,
        EnvironmentCapability.HTTP,
        EnvironmentCapability.SERVICES,
        EnvironmentCapability.ENVIRONMENT_LIFECYCLE,
    }
)


class ServeEnvironmentLifecycleController:
    """Lifecycle behavior owned by one AgentHost execution environment."""

    def __init__(
        self,
        *,
        environment_id: str,
        profile_id: str,
        backend: str | None,
        container_id: str | None,
        service_urls: dict[str, Any],
        started_services: list[tuple[Any, int]],
    ) -> None:
        self.environment_id = environment_id
        self.profile_id = profile_id
        self.backend = backend
        self.container_id = container_id
        self.service_urls = service_urls
        self.started_services = started_services
        self.discard_snapshot_on_exit = False
        self._ready = asyncio.Event()
        self._ready.set()
        self._closed = False

    def _validate(self, lease: EnvironmentLease) -> None:
        if lease.environment_id != self.environment_id:
            raise RuntimeError("lifecycle controller/environment mismatch")

    def state(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        return {
            "available": not self._closed,
            "ready": self._ready.is_set() and not self._closed,
            "switching": False,
            "profile": self.profile_id,
            "backend": self.backend,
            "container_id": self.container_id,
            "last_error": None,
        }

    async def wait_until_ready(
        self, lease: EnvironmentLease, timeout_seconds: float
    ) -> dict[str, Any]:
        self._validate(lease)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return {"ready": False, "profile": self.profile_id, "reason": "timeout"}
        return {"ready": not self._closed, "profile": self.profile_id}

    @staticmethod
    def _backend(lease: EnvironmentLease) -> Any:
        from omniagents.core.sandbox.lifecycle_session import (
            resolve_backend_session,
        )

        workspace = lease.workspace
        if not isinstance(workspace, SandboxWorkspace):
            return None
        return resolve_backend_session(workspace.session)

    async def pause(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        return await self._set_paused(lease, paused=True)

    async def unpause(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        return await self._set_paused(lease, paused=False)

    async def _set_paused(
        self, lease: EnvironmentLease, *, paused: bool
    ) -> dict[str, Any]:
        backend = self._backend(lease)
        method = getattr(backend, "pause" if paused else "unpause", None)
        if not callable(method):
            return {
                "ok": False,
                "supported": False,
                "reason": "environment backend does not implement pause",
            }
        try:
            await method()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "supported": True, "reason": str(exc)}
        return {"ok": True, "supported": True, "paused": paused}

    async def notify_activity(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        backend = self._backend(lease)
        notify = getattr(backend, "notify_activity", None)
        if not callable(notify):
            return {"ok": False}
        try:
            await notify()
        except Exception:  # noqa: BLE001
            return {"ok": False}
        return {"ok": True}

    async def discard_snapshot(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        self.discard_snapshot_on_exit = True
        return {"ok": True}

    async def close(self, lease: EnvironmentLease) -> None:
        self._validate(lease)
        if self._closed:
            return
        runtime = lease.sandbox_runtime
        session = runtime.session if runtime is not None else None
        await teardown_session(
            session,
            self.started_services,
            discard_snapshot=self.discard_snapshot_on_exit,
        )
        self.started_services = []
        self._closed = True
        self._ready.clear()


class ServeEnvironmentProvisioner:
    """Provision host or sandbox adapters from explicit Workspace/profile data."""

    def __init__(
        self,
        *,
        profile_resolver: ProfileResolver,
        workspace_directory_resolver: WorkspaceDirectoryResolver,
        snapshot_dir: Path | None = None,
        credentials: Sequence[dict[str, Any]] = (),
    ) -> None:
        self._profile_resolver = profile_resolver
        self._workspace_directory_resolver = workspace_directory_resolver
        self._snapshot_dir = snapshot_dir
        self._credentials = [dict(item) for item in credentials]

    async def provision(
        self, request: EnvironmentProvisionRequest
    ) -> ProvisionedEnvironment:
        workspace_dir = Path(
            self._workspace_directory_resolver(request.workspace)
        ).expanduser()
        profile = self._profile_resolver(request, workspace_dir)
        terminal_defaults = TerminalExecutionDefaults(
            shell=profile.terminal.command,
            cwd=profile.terminal.cwd,
            user=profile.terminal.user,
        )
        if _is_host_profile(profile.raw):
            return ProvisionedEnvironment(
                workspace=HostWorkspace(root=str(workspace_dir)),
                kind=EnvironmentKind.HOST,
                capabilities=_FULL_CAPABILITIES,
                terminal_defaults=terminal_defaults,
            )

        brought = await bring_up_session(
            profile=profile,
            sources_parsed=[dict(source) for source in request.workspace.sources],
            credentials_parsed=self._credentials,
            session_id=request.workspace.snapshot_ref or request.environment_id,
            snapshot_dir=self._snapshot_dir,
            prior_container_id=None,
        )
        try:
            root = getattr(brought.manifest, "root", None)
            if not isinstance(root, str) or not root:
                raise RuntimeError("provisioned sandbox manifest has no workspace root")
            run_as = _run_as_exec_user(profile.raw)
            controller = ServeEnvironmentLifecycleController(
                environment_id=request.environment_id,
                profile_id=request.profile_id,
                backend=brought.client_type,
                container_id=brought.container_id,
                service_urls=brought.service_urls,
                started_services=brought.started_services,
            )
            return ProvisionedEnvironment(
                workspace=SandboxWorkspace(
                    session=brought.session,
                    root=root,
                    default_user=run_as,
                ),
                kind=EnvironmentKind.DEVBOX,
                capabilities=_FULL_CAPABILITIES,
                lifecycle_controller=controller,
                sandbox_runtime=SandboxExecutionRuntime(
                    session=brought.session,
                    run_as=run_as,
                ),
                terminal_defaults=terminal_defaults,
            )
        except Exception:
            await teardown_session(brought.session, brought.started_services)
            raise
