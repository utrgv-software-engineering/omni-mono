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

from filelock import FileLock

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


def _materialize_host_remote_checkout(source: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure a host checkout of one ``git-remote`` source and localize it.

    The repo is cloned blobless into the source's ``checkoutPath`` — a
    directory the caller owns, one per project source, so every Workspace of
    that project shares one checkout the way local folders are shared — on
    first materialization and reused verbatim afterwards (the checkout is
    durable working state, so no implicit fetch or re-checkout ever rewrites
    it). A directory that exists but is not a checkout of the same remote
    fails closed. A lock beside the checkout serializes concurrent first
    clones from Workspaces opening at once.

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
    checkout_path = source.get("checkoutPath")
    if not isinstance(checkout_path, str) or not checkout_path.strip():
        raise ValueError(f"git-remote source {mount_name!r} has no checkoutPath")
    dest = Path(checkout_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(dest.parent / f".{dest.name}.lock")):
        _ensure_checkout(dest, url, source, mount_name)

    localized = {
        key: source[key] for key in ("id", "mountName", "writable") if key in source
    }
    localized["kind"] = "local-git"
    localized["path"] = str(dest)
    return localized


def _ensure_checkout(
    dest: Path, url: str, source: Mapping[str, Any], mount_name: str
) -> None:
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
        _run_git([*credential_config, "clone", "--filter=blob:none", url, str(dest)])
        ref = source.get("ref")
        if isinstance(ref, str) and ref.strip():
            _run_git(["-C", str(dest), "checkout", ref.strip()])
        if persisted_helper is not None:
            username, helper = persisted_helper
            _run_git(["-C", str(dest), "config", "credential.username", username])
            _run_git(["-C", str(dest), "config", "credential.helper", helper])


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


#: Backend client types that are genuine containment boundaries — the
#: agent's tools cannot reach the hosting machine. ``unix_local`` is
#: deliberately absent: it carries sandbox sessions but executes raw
#: host processes. Unknown client types fail closed to
#: not-contained.
_CONTAINED_CLIENT_TYPES = frozenset(
    {
        "docker",  # container
        "wasm",  # bashkit + pyodide: no syscalls, workspace-only FS reach
        "e2b",  # remote micro-VM / container providers
        "modal",
        "runloop",
        "daytona",
        "blaxel",
        "vercel",
        "cloudflare",
    }
)


#: Truthful placement family per backend client type. Kind records WHERE
#: the runtime lives — never trust: ``unix_local`` is
#: host-placed even though it carries sandbox sessions. Trust (containment) is the
#: ``contained`` capability above; terminal routing keys on the lease's
#: sandbox runtime. Unknown session-backed client types default to CONTAINER.
_ENVIRONMENT_KINDS = {
    "host": EnvironmentKind.HOST,
    "unix_local": EnvironmentKind.HOST,
    "wasm": EnvironmentKind.HOST,
    "docker": EnvironmentKind.CONTAINER,
    "e2b": EnvironmentKind.REMOTE,
    "modal": EnvironmentKind.REMOTE,
    "runloop": EnvironmentKind.REMOTE,
    "daytona": EnvironmentKind.REMOTE,
    "blaxel": EnvironmentKind.REMOTE,
    "vercel": EnvironmentKind.REMOTE,
    "cloudflare": EnvironmentKind.REMOTE,
}


def _environment_kind(profile_raw: dict[str, Any]) -> EnvironmentKind:
    client_type = (profile_raw.get("client") or {}).get("type")
    if not isinstance(client_type, str):
        return EnvironmentKind.CONTAINER
    return _ENVIRONMENT_KINDS.get(client_type, EnvironmentKind.CONTAINER)


def _wasm_is_offline(network: Any) -> bool:
    """True when the wasm session *boots* with no egress.

    Wasm is off by default: absent boots blocked (contained-and-offline —
    promptless only within "Approve for me") but stays toggleable, and
    ``sandbox.set_network`` keeps the NETWORK capability current when the
    user enables it later. Explicit grants (open / allowlist) boot with
    NETWORK present.
    """
    from omniagents.core.sandbox.wasm import _wasm_network_boots_blocked

    return _wasm_network_boots_blocked(network)


def sandbox_capabilities(profile_raw: dict[str, Any], brought: Any = None) -> frozenset:
    """Capability truth for a sandbox environment at registration time.

    CONTAINED is granted only for client types that are real containment
    boundaries (:data:`_CONTAINED_CLIENT_TYPES`) — the approval policy's
    sandbox auto-approve tiers key on it, so a ``unix_local`` session must
    never carry it. NETWORK is dropped when the environment genuinely
    cannot reach out: docker ``network_mode: "none"`` or a persisted
    offline block re-applied during bring-up. A host-filtered *allowlist*
    still counts as NETWORK — limited egress is egress, and the approval
    ``no_network`` tier must not auto-approve around it. The approval
    policy reads this via ``describe()`` on every request, and
    ``sandbox.set_network`` keeps NETWORK current across live toggles.
    """
    client = profile_raw.get("client") or {}
    client_type = client.get("type")
    capabilities = set(_FULL_CAPABILITIES)
    if client_type in _CONTAINED_CLIENT_TYPES:
        capabilities.add(EnvironmentCapability.CONTAINED)
    if client_type == "wasm":
        # No processes exist in either wasm runtime: nothing to attach a
        # PTY to and nothing to run as a service. Shell exec (bashkit) and
        # python (pyodide) remain, so PROCESS stays.
        capabilities -= {EnvironmentCapability.PTY, EnvironmentCapability.SERVICES}
    offline = (
        # docker: no network stack at all.
        client.get("network_mode") == "none"
        # wasm: both runtimes obey one policy derived from the profile's
        # ``network`` block (bashkit curl/git builtins + pyodide js.fetch),
        # OFF by default. Uses the same boot-state resolver the session
        # uses so the NETWORK capability can never disagree with what
        # Python can actually reach.
        or (client_type == "wasm" and _wasm_is_offline(client.get("network")))
        # docker offline toggle persisted across restarts.
        or bool(getattr(brought, "network_blocked", False))
    )
    if offline:
        capabilities.discard(EnvironmentCapability.NETWORK)
    return frozenset(capabilities)


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
        state_path: Path | None = None,
        network_mode: str | None = None,
    ) -> None:
        self.environment_id = environment_id
        self.profile_id = profile_id
        self.backend = backend
        self.container_id = container_id
        # docker network_mode from the profile; "none" means there is no
        # egress to toggle, so the network toggle reads as unsupported.
        self.network_mode = network_mode
        self.service_urls = service_urls
        self.started_services = started_services
        # Durable container session (docker): ordinary close detaches and
        # leaves the container for reattach; only a discard destroys it.
        self.detachable = state_path is not None
        self.state_path = state_path
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

    async def discard_snapshot(self, lease: EnvironmentLease) -> dict[str, Any]:
        self._validate(lease)
        self.discard_snapshot_on_exit = True
        return {"ok": True}

    def _network_session(self, lease: EnvironmentLease) -> Any:
        """The SDK session for iptables network toggling, or None.

        Docker-only: the block is iptables inside the container (NET_ADMIN).
        Wasm sessions toggle in-process instead (:meth:`_wasm_session`);
        every other backend has no toggle — the host has nothing to fence,
        and the process sandbox is start-time all-or-nothing.
        """
        if self.backend != "docker" or self.network_mode == "none":
            return None
        runtime = lease.sandbox_runtime
        return runtime.session if runtime is not None else None

    def _wasm_session(self, lease: EnvironmentLease) -> Any:
        """The wasm session for in-process network toggling, or None.

        Wasm enforcement is not iptables: the session gates the bashkit
        curl/git builtins and the pyodide worker's egress globals in one
        move via ``set_network_enabled``. State lives with the session —
        which for wasm IS the sandbox lifetime.
        """
        if self.backend != "wasm":
            return None
        runtime = lease.sandbox_runtime
        session = runtime.session if runtime is not None else None
        return session if hasattr(session, "set_network_enabled") else None

    async def set_network(
        self, lease: EnvironmentLease, *, enabled: bool
    ) -> dict[str, Any]:
        """Toggle sandbox egress live. Enforcement only — the caller
        (the ``sandbox.set_network`` server function) flips the NETWORK
        capability on the descriptor so approval routing follows."""
        self._validate(lease)
        wasm = self._wasm_session(lease)
        if wasm is not None:
            if not getattr(wasm, "network_toggleable", False):
                return {
                    "ok": False,
                    "supported": False,
                    "reason": "profile is explicitly offline; no egress to toggle",
                }
            try:
                await wasm.set_network_enabled(enabled)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "supported": True, "reason": str(exc)}
            return {"ok": True, "supported": True, "enabled": enabled}
        session = self._network_session(lease)
        if session is None:
            return {
                "ok": False,
                "supported": False,
                "reason": "environment backend does not support a network toggle",
            }
        from omniagents.core.sandbox import network_policy

        try:
            await network_policy.set_network_blocked(session, not enabled)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "supported": True, "reason": str(exc)}
        return {"ok": True, "supported": True, "enabled": enabled}

    async def get_network(self, lease: EnvironmentLease) -> dict[str, Any]:
        """Report whether the offline block is currently installed."""
        self._validate(lease)
        wasm = self._wasm_session(lease)
        if wasm is not None:
            if not getattr(wasm, "network_toggleable", False):
                return {"ok": True, "supported": False, "enabled": False}
            return {
                "ok": True,
                "supported": True,
                "enabled": bool(wasm.network_enabled),
            }
        session = self._network_session(lease)
        if session is None:
            return {
                "ok": True,
                "supported": False,
                "enabled": self.network_mode != "none",
            }
        from omniagents.core.sandbox import network_policy

        blocked = await network_policy.network_blocked(session)
        return {"ok": True, "supported": True, "enabled": not blocked}

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
            detach=self.detachable and not self.discard_snapshot_on_exit,
            destroy=self.detachable and self.discard_snapshot_on_exit,
            state_path=self.state_path,
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
            # into their caller-owned checkoutPath and then behave like any
            # local-git source (idempotent — an existing checkout of the same
            # remote is reused, anything else fails closed).
            local_sources: list[dict[str, Any]] = []
            for source in sources:
                kind = source.get("kind")
                if kind == "git-remote":
                    local_sources.append(
                        await asyncio.to_thread(
                            _materialize_host_remote_checkout, source
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
            # The host target is the no-sandbox target: nothing contains the
            # agent here. Its shell already reaches the whole machine (host
            # ``exec`` resolves only ``cwd``), so fencing the file tools to
            # the workspace root bought no containment — only a split where
            # `cat /etc/hosts` worked and `read_text("/etc/hosts")` raised.
            # The file tools now agree with the shell.
            #
            # This is not configurable. A "host but fenced" tier would be
            # answering a question no user has: pick a real sandbox
            # (unix_local, docker) to be contained, or host to not be. The
            # wire surfaces that do need the root keep it by calling
            # ``resolve_confined`` — see Workspace.resolve_confined.
            workspace: HostWorkspace
            if use_composite:
                # Mount aliases organize host sources without restricting the
                # host agent. Bounded RPC reads still use resolve_confined.
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

        snapshot_ref = request.workspace.snapshot_ref or request.environment_id
        state_path = self._session_state_path(profile, snapshot_ref)
        brought = await bring_up_session(
            profile=profile,
            sources_parsed=[dict(source) for source in request.workspace.sources],
            credentials_parsed=self._credentials,
            snapshot_ref=snapshot_ref,
            snapshot_dir=self._snapshot_dir,
            state_path=state_path,
        )
        try:
            root = getattr(brought.manifest, "root", None)
            if not isinstance(root, str) or not root:
                raise RuntimeError("provisioned sandbox manifest has no workspace root")
            run_as = _run_as_exec_user(profile.raw)
            raw_network_mode = (profile.raw.get("client") or {}).get("network_mode")
            controller = ServeEnvironmentLifecycleController(
                environment_id=request.environment_id,
                profile_id=request.profile_id,
                backend=brought.client_type,
                container_id=brought.container_id,
                service_urls=brought.service_urls,
                started_services=brought.started_services,
                state_path=state_path,
                network_mode=(
                    raw_network_mode if isinstance(raw_network_mode, str) else None
                ),
            )
            return ProvisionedEnvironment(
                workspace=SandboxWorkspace(
                    session=brought.session,
                    root=root,
                    default_user=run_as,
                ),
                kind=_environment_kind(profile.raw),
                capabilities=sandbox_capabilities(profile.raw, brought),
                lifecycle_controller=controller,
                sandbox_runtime=SandboxExecutionRuntime(
                    session=brought.session,
                    run_as=run_as,
                ),
                terminal_defaults=terminal_defaults,
            )
        except Exception:
            # Post-bring-up registration failure: the workspace is intact, so
            # a durable container session detaches (reattach on retry) rather
            # than being destroyed.
            await teardown_session(
                brought.session,
                brought.started_services,
                detach=state_path is not None,
            )
            raise

    def _session_state_path(
        self, profile: ResolvedProfile, snapshot_ref: str
    ) -> Path | None:
        """Where this Workspace's container session-state record lives.

        Only docker sessions are reattachable; other backends return None
        (their teardown keeps the legacy stop semantics). Records live in
        ``<config>/sandbox-state/`` — a sibling of the snapshot dir the
        product already passes — keyed by the Workspace's durable
        ``snapshot_ref``. A ref that can't be a safe filename disables
        persistence rather than escaping the directory.
        """
        if self._snapshot_dir is None:
            return None
        client_type = (profile.raw.get("client") or {}).get("type")
        if client_type != "docker":
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", snapshot_ref):
            logger.warning(
                "snapshot_ref %r is not a safe filename; container session "
                "state will not persist",
                snapshot_ref,
            )
            return None
        return self._snapshot_dir.parent / "sandbox-state" / f"{snapshot_ref}.json"
