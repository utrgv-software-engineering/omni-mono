"""Extended Docker sandbox client with ``cap_add``, ``security_opt``,
``network_mode``, and container ``user`` overrides.

The SDK's :class:`agents.sandbox.sandboxes.docker.DockerSandboxClient`
hard-codes its container-create kwargs and exposes no hook to add or
override them. Real-world sandboxing needs more:

* ``cap_add: ["NET_ADMIN"]`` for in-container egress filtering via
  ``iptables``.
* ``security_opt: ["seccomp=unconfined"]`` because Docker's default
  seccomp profile blocks the netfilter syscalls iptables-nft needs;
  without this NET_ADMIN alone is useless for ``iptables`` rule
  installation.
* ``network_mode: "none"`` for offline-only sandboxes.
* ``user: "root"`` to override the image's ``USER`` directive when the
  SDK's bookkeeping (PTY pid-path chown, archive writes) needs root.

This module exposes :class:`ExtendedDockerSandboxClient`, a drop-in
subclass that overrides ``_create_container`` to add those fields to the
docker create kwargs. We copy the parent's body rather than wrap
``docker_client.containers.create`` because ``client.containers`` is a
property — it returns a new ``ContainerCollection`` instance on every
access, so monkey-patching ``.create`` on it doesn't stick.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Sequence

from agents.sandbox.manifest import Manifest
from agents.sandbox.sandboxes.docker import (
    DockerSandboxClient,
    DockerSandboxClientOptions,
    DockerSandboxSession,
    DockerSandboxSessionState,
    _build_docker_volume_mounts,
    _docker_port_key,
    _manifest_requires_fuse,
    _manifest_requires_sys_admin,
)
from agents.sandbox.session import SandboxSession
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot
from docker.types import Mount as DockerSDKMount
from docker.utils import parse_repository_tag

if TYPE_CHECKING:
    from docker import DockerClient as DockerSDKClient
    from docker.models.containers import Container


__all__ = ["BindSpec", "ExtendedDockerSandboxClient", "ExtendedDockerSandboxSession"]


class ExtendedDockerSandboxSession(DockerSandboxSession):
    """Session subclass that skips ``provision_manifest_accounts``.

    The SDK's default behavior is to ``useradd`` every entry in
    ``manifest.users`` during :meth:`_start_workspace`. We pre-declare
    the run_as user on the manifest (see ``omni_code.serve_cli._build_sandbox``)
    purely so the SDK's *live-session* manifest equality check passes on
    every ``Runner.run`` — the user we declare is already in the image
    (e.g. ``ubuntu`` for uid 1000), and ``useradd`` would either fail with
    "user already exists" or "invalid user name" if the run_as is given
    as a numeric ``uid:gid``. Skipping the provisioning step entirely
    avoids both failure modes; the image is responsible for making the
    run_as user resolvable.
    """

    def should_provision_manifest_accounts_on_resume(self) -> bool:
        return False


class BindSpec:
    """A host→container bind mount declared on the sandbox client.

    Use this when the workspace is a host directory you want to expose
    live to the container — versus the SDK's :class:`LocalDir` manifest
    entry which *materializes* the directory by file-copy through
    ``session.write``. Materialization fails on symlinks (every typical
    ``node_modules`` tree) and is O(N files) slow even when it works.
    Bind mounts are O(1), preserve symlinks, and let edits round-trip
    between host and container.

    The bind is set on the client (via ``binds=``) rather than the
    manifest because it's a docker-specific transport concern. Other
    sandbox backends (e2b, modal, ...) don't have an equivalent.
    """

    __slots__ = ("source", "target", "read_only")

    def __init__(self, source: str, target: str, *, read_only: bool = False) -> None:
        self.source = source
        self.target = target
        self.read_only = read_only


class ExtendedDockerSandboxClient(DockerSandboxClient):
    """``DockerSandboxClient`` with cap_add / security_opt / network_mode /
    user overrides.

    Constructor accepts (beyond the parent's):

    * ``cap_add`` — capabilities to add to every container. Merged with
      the parent's manifest-driven caps (e.g. ``SYS_ADMIN`` for fuse).
    * ``security_opt`` — security options (``"seccomp=unconfined"``,
      ``"apparmor:unconfined"`` etc.). Merged with the parent's.
    * ``network_mode`` — passed straight through (``"none"`` / ``"bridge"``
      / a named network).
    * ``user`` — overrides the image's default USER directive. Standard
      values: ``"root"`` or ``"0"`` for the SDK's root-needing bookkeeping
      (PTY pid-path chown). The agent's *tools* still run as the
      identity passed per-call to ``session.exec(user=...)``.
    """

    def __init__(
        self,
        docker_client: "DockerSDKClient",
        *,
        cap_add: Sequence[str] = (),
        network_mode: str | None = None,
        security_opt: Sequence[str] = (),
        user: str | None = None,
        binds: Sequence[BindSpec] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(docker_client, **kwargs)
        self._extra_cap_add: tuple[str, ...] = tuple(cap_add)
        self._extra_security_opt: tuple[str, ...] = tuple(security_opt)
        self._network_mode: str | None = network_mode
        self._default_user: str | None = user
        self._binds: tuple[BindSpec, ...] = tuple(binds)

    async def _create_container(
        self,
        image: str,
        *,
        manifest: Manifest | None = None,
        exposed_ports: tuple[int, ...] = (),
        session_id: uuid.UUID | None = None,
    ) -> "Container":
        # Re-implements the parent's body so we can layer in our extras
        # before the final ``containers.create`` call. Wrapping
        # ``docker_client.containers.create`` doesn't work because
        # ``containers`` is a property that returns a new collection
        # on every access — the monkey-patched ``create`` would be on
        # a throwaway instance.
        if not self.image_exists(image):
            repo, tag = parse_repository_tag(image)
            self.docker_client.images.pull(repo, tag=tag or None, all_tags=False)
        assert self.image_exists(image)

        environment: dict[str, str] | None = None
        if manifest:
            environment = await manifest.environment.resolve()

        create_kwargs: dict[str, object] = {
            "entrypoint": ["tail"],
            "image": image,
            "detach": True,
            "command": ["-f", "/dev/null"],
            "environment": environment,
        }
        if manifest is not None:
            docker_mounts = _build_docker_volume_mounts(manifest, session_id=session_id)
            if docker_mounts:
                create_kwargs["mounts"] = docker_mounts
            if _manifest_requires_fuse(manifest):
                create_kwargs.update(
                    devices=["/dev/fuse"],
                    cap_add=["SYS_ADMIN"],
                    security_opt=["apparmor:unconfined"],
                )
            elif _manifest_requires_sys_admin(manifest):
                create_kwargs.update(
                    cap_add=["SYS_ADMIN"],
                    security_opt=["apparmor:unconfined"],
                )
        if exposed_ports:
            create_kwargs["ports"] = {
                _docker_port_key(port): ("127.0.0.1", None) for port in exposed_ports
            }

        # --- Our additions on top of the parent's kwargs ---
        if self._default_user is not None and "user" not in create_kwargs:
            create_kwargs["user"] = self._default_user
        if self._extra_cap_add:
            existing = list(create_kwargs.get("cap_add") or [])  # type: ignore[arg-type]
            create_kwargs["cap_add"] = _dedup_preserving_order(
                [*existing, *self._extra_cap_add]
            )
        if self._extra_security_opt:
            existing = list(create_kwargs.get("security_opt") or [])  # type: ignore[arg-type]
            create_kwargs["security_opt"] = _dedup_preserving_order(
                [*existing, *self._extra_security_opt]
            )
        if self._network_mode is not None and "network_mode" not in create_kwargs:
            create_kwargs["network_mode"] = self._network_mode
        if self._binds:
            # Append our bind mounts to whatever the parent built. We
            # don't deduplicate by target — docker rejects duplicate
            # targets itself with a clear error, which is the right
            # signal for misconfiguration.
            existing_mounts = list(create_kwargs.get("mounts") or [])  # type: ignore[arg-type]
            for bind in self._binds:
                existing_mounts.append(
                    DockerSDKMount(
                        target=bind.target,
                        source=bind.source,
                        type="bind",
                        read_only=bind.read_only,
                    )
                )
            create_kwargs["mounts"] = existing_mounts

        return self.docker_client.containers.create(**create_kwargs)

    async def create(
        self,
        *,
        snapshot: "SnapshotSpec | SnapshotBase | None" = None,
        manifest: Manifest | None = None,
        options: DockerSandboxClientOptions,
    ) -> SandboxSession:
        # Re-implements the parent's body so we can instantiate
        # :class:`ExtendedDockerSandboxSession` instead of the SDK's
        # hard-coded :class:`DockerSandboxSession`. The subclass disables
        # ``provision_manifest_accounts`` — see its docstring for the
        # full rationale.
        image = options.image
        session_id = uuid.uuid4()
        manifest = manifest or Manifest()

        container = await self._create_container(
            image,
            manifest=manifest,
            exposed_ports=options.exposed_ports,
            session_id=session_id,
        )
        container.start()

        container_id = container.id
        assert container_id is not None
        snapshot_id = str(session_id)
        snapshot_instance = resolve_snapshot(snapshot, snapshot_id)
        state = DockerSandboxSessionState(
            session_id=session_id,
            manifest=manifest,
            image=image,
            snapshot=snapshot_instance,
            container_id=container_id,
            exposed_ports=options.exposed_ports,
        )

        inner = ExtendedDockerSandboxSession(
            docker_client=self.docker_client,
            container=container,
            state=state,
        )
        return self._wrap_session(inner, instrumentation=self._instrumentation)


def _dedup_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
