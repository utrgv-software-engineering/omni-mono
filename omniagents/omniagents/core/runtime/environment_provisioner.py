"""Product adapter for provisioning multiple environments inside one AgentHost."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
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
from omniagents.workspace import (
    GitMetadataHostWorkspace,
    HostWorkspace,
    MountedHostWorkspace,
)
from omniagents.workspace.sandbox import SandboxWorkspace

from .serve_harness import (
    _is_host_profile,
    _run_as_exec_user,
    bring_up_session,
    teardown_session,
)

ProfileResolver = Callable[[EnvironmentProvisionRequest, Path], ResolvedProfile]
WorkspaceDirectoryResolver = Callable[[WorkspaceResource], Path]

logger = logging.getLogger(__name__)

#: Env-var names interpolated into a git credential-helper shell fragment must
#: be plain identifiers — anything else could smuggle shell syntax.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalized_git_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    return cleaned[:-4] if cleaned.endswith(".git") else cleaned


def _validated_mount_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("git-remote source mountName must be a non-empty string")
    name = value.strip()
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid git-remote source mountName: {name!r}")
    return name


def _run_git(args: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ValueError(f"host git materialization failed ({args[0]}): {detail}")
    return result.stdout.strip()


def _materialize_host_remote_checkout(
    workspace_dir: Path, source: Mapping[str, Any]
) -> dict[str, Any]:
    """Ensure a host checkout of one ``git-remote`` source and localize it.

    Host environments get the same remote-source semantics as sandboxes: the
    repo is cloned blobless into ``<workspace_dir>/<mountName>`` — entirely
    launcher-managed space — on first materialization and reused verbatim
    afterwards (the checkout is durable working state, so no implicit fetch or
    re-checkout ever rewrites it). A directory that exists but is not a
    checkout of the same remote fails closed.

    Private remotes authenticate exactly like the container path's
    ``AuthenticatedGitRepo``: the token is read from the environment variable
    named by ``auth.tokenEnv`` and never appears on argv or in the URL. The
    credential helper is persisted repo-locally (never in the user's global
    git config) so the agent's later fetch/push keeps working.

    Returns the source rewritten as a ``local-git`` entry rooted at the
    checkout, ready for the ordinary host mounting path.
    """
    mount_name = _validated_mount_name(source.get("mountName"))
    url = source.get("repoUrl")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"git-remote source {mount_name!r} has no repoUrl")
    url = url.strip()
    dest = workspace_dir / mount_name

    if (dest / ".git").exists():
        origin = _run_git(["-C", str(dest), "remote", "get-url", "origin"])
        if _normalized_git_url(origin) != _normalized_git_url(url):
            raise ValueError(
                f"host checkout {dest} tracks {origin!r}, not {url!r}; "
                "refusing to reuse it"
            )
    elif dest.exists() and any(dest.iterdir()):
        raise ValueError(
            f"host mount directory {dest} exists but is not a checkout of {url!r}"
        )
    else:
        credential_config: list[str] = []
        persisted_helper: tuple[str, str] | None = None
        auth = source.get("auth")
        if isinstance(auth, Mapping):
            token_env = str(auth.get("tokenEnv") or "")
            username = str(auth.get("username") or "x-access-token")
            if token_env and os.environ.get(token_env):
                if not _ENV_NAME_RE.fullmatch(token_env):
                    raise ValueError(f"invalid git token env name: {token_env!r}")
                helper = f'!f() {{ printf "password=%s\\n" "${token_env}"; }}; f'
                credential_config = [
                    "-c",
                    f"credential.username={username}",
                    "-c",
                    f"credential.helper={helper}",
                ]
                persisted_helper = (username, helper)
            else:
                logger.warning(
                    "host materialization: env %r is empty; cloning %r without auth",
                    token_env,
                    mount_name,
                )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _run_git([*credential_config, "clone", "--filter=blob:none", url, str(dest)])
        ref = source.get("ref")
        if isinstance(ref, str) and ref.strip():
            _run_git(["-C", str(dest), "checkout", ref.strip()])
        if persisted_helper is not None:
            username, helper = persisted_helper
            _run_git(["-C", str(dest), "config", "credential.username", username])
            _run_git(["-C", str(dest), "config", "credential.helper", helper])

    localized = {
        key: source[key] for key in ("id", "mountName", "writable") if key in source
    }
    localized["kind"] = "local-git"
    localized["path"] = str(dest)
    return localized

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
            sources = [dict(source) for source in request.workspace.sources]
            materialization_root = workspace_dir.expanduser().resolve()
            # Host parity with the sandbox path: git-remote sources are cloned
            # into the launcher-managed materialization root and then behave
            # like any local-git source (idempotent — an existing checkout of
            # the same remote is reused, anything else fails closed).
            local_sources: list[dict[str, Any]] = []
            for source in sources:
                kind = source.get("kind")
                if kind == "git-remote":
                    local_sources.append(
                        await asyncio.to_thread(
                            _materialize_host_remote_checkout,
                            materialization_root,
                            source,
                        )
                    )
                elif kind in {"local", "local-git"}:
                    local_sources.append(source)
            use_composite = bool(local_sources) and (
                len(local_sources) > 1
                or any(
                    Path(str(source.get("path") or "")).expanduser().resolve()
                    != materialization_root
                    for source in local_sources
                )
            )
            workspace: HostWorkspace
            if use_composite:
                workspace = MountedHostWorkspace(str(workspace_dir), local_sources)
            elif any(
                source.get("gitDir") or source.get("gitCommonDir")
                for source in local_sources
            ):
                workspace = GitMetadataHostWorkspace(str(workspace_dir), local_sources)
            else:
                workspace = HostWorkspace(root=str(workspace_dir))
            terminal_defaults = TerminalExecutionDefaults(
                shell=profile.terminal.command,
                cwd=workspace.root,
                user=profile.terminal.user,
            )
            return ProvisionedEnvironment(
                workspace=workspace,
                kind=EnvironmentKind.HOST,
                capabilities=_FULL_CAPABILITIES,
                terminal_defaults=terminal_defaults,
            )

        brought = await bring_up_session(
            profile=profile,
            sources_parsed=[dict(source) for source in request.workspace.sources],
            credentials_parsed=self._credentials,
            snapshot_ref=request.workspace.snapshot_ref or request.environment_id,
            snapshot_dir=self._snapshot_dir,
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
