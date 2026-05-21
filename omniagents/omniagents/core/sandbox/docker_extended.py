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

import asyncio
import io
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Sequence

from agents.sandbox.manifest import Manifest
from agents.sandbox.sandboxes.docker import (
    _DOCKER_EXECUTOR,
    DockerSandboxClient,
    DockerSandboxClientOptions,
    DockerSandboxSession,
    DockerSandboxSessionState,
    _DockerPtyProcessEntry,
    _build_docker_volume_mounts,
    _docker_port_key,
    _manifest_requires_fuse,
    _manifest_requires_sys_admin,
)
from agents.sandbox.session import SandboxSession
from agents.sandbox.session.pty_types import PtyExecUpdate
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot
from agents.sandbox.types import ExecResult, User
from docker.types import Mount as DockerSDKMount
from docker.utils import parse_repository_tag

from omniagents.core.sandbox.pty_stream import PtyStreamingSession

_idle_log = logging.getLogger("omniagents.sandbox.idle")
_IDLE_WATCHER_INTERVAL_S = 10.0
# Default-on. Embedders that want a different value call
# ``set_idle_threshold(seconds)`` before ``session.start()``; passing
# ``0`` disables the watcher entirely.
DEFAULT_IDLE_THRESHOLD_S = 15 * 60

if TYPE_CHECKING:
    from docker import DockerClient as DockerSDKClient
    from docker.models.containers import Container


__all__ = ["BindSpec", "ExtendedDockerSandboxClient", "ExtendedDockerSandboxSession"]


# Per-consumer queue size: 1024 chunks at ~4 KB = ~4 MB soft cap before
# overflow drops new chunks (mirrors unix_local_extended).
_STREAM_QUEUE_MAX_CHUNKS = 1024


class ExtendedDockerSandboxSession(DockerSandboxSession, PtyStreamingSession):
    """Session subclass that skips ``provision_manifest_accounts`` and adds
    PTY streaming + resize primitives for interactive terminals.

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

    :meth:`pty_stream_output` and :meth:`pty_resize` extend the SDK's
    batched PTY surface for interactive use (human terminals). See
    :mod:`omniagents.core.sandbox.pty_stream` for the contract.
    """

    def __init__(
        self,
        *,
        docker_client: "DockerSDKClient",
        container: "Container",
        state: DockerSandboxSessionState,
    ) -> None:
        super().__init__(docker_client=docker_client, container=container, state=state)
        # Idle pause state. The watcher arms itself in ``_after_start`` with
        # ``DEFAULT_IDLE_THRESHOLD_S`` — default-on. Embedders override the
        # threshold by calling ``set_idle_threshold(seconds)`` before
        # ``session.start()`` (or pass ``0`` to disable entirely).
        self._idle_threshold_s: float = float(DEFAULT_IDLE_THRESHOLD_S)
        self._idle_last_activity_at: float = time.monotonic()
        self._idle_watcher_task: Optional[asyncio.Task] = None
        self._idle_paused: bool = False
        self._idle_paused_at: Optional[float] = None
        # Serializes pause/unpause transitions so the watcher and a wrapped
        # call's auto-unpause can't fight each other.
        self._idle_lock: asyncio.Lock = asyncio.Lock()

    def should_provision_manifest_accounts_on_resume(self) -> bool:
        return False

    async def _after_start(self) -> None:
        # Inherit the parent's bookkeeping (workspace_root_ready flag,
        # resume-probe clear) then arm the idle watcher.
        await super()._after_start()
        if self._idle_threshold_s and self._idle_threshold_s > 0:
            self.start_idle_watcher(self._idle_threshold_s)

    async def notify_activity(self) -> None:
        """External presence ping. Resets the idle timer (and auto-unpauses
        if paused) without going through a wrapped method. Used by clients
        to signal "the user is engaging with this sandbox" — e.g. chat UI
        focus, code-server keystrokes, browser tab interaction with a
        published port."""
        await self._touch_activity()

    def set_idle_threshold(self, threshold_s: float) -> None:
        """Configure the idle-pause threshold for this session.

        Call before ``session.start()`` to override the 15-minute default;
        ``_after_start`` reads this value when arming the watcher. Pass
        ``0`` or a negative number to disable the watcher entirely.

        Safe to call after start() too, but you'd need to stop / restart
        the watcher explicitly to pick up the new value — the typical
        embedder pattern is one ``set_idle_threshold`` call at boot,
        derived from profile config.
        """
        self._idle_threshold_s = max(0.0, float(threshold_s))

    # ------------------------------------------------------------------
    # Idle pause: watcher + activity tracking
    #
    # Sandbox-level idle is the right level because "is this resource
    # being used?" is exactly what the freezer cgroup operates on. We
    # don't care about agent-level lifecycle (turns, goals, wakeups) —
    # we care whether some caller is making a call against the container.
    # Every entry point that touches docker calls ``_touch_activity()``,
    # which (a) updates the last-activity timestamp and (b) auto-unpauses
    # if currently paused. The caller proceeds normally without knowing
    # about the pause; latency cost is one ``docker unpause`` per
    # re-activation (~100ms).
    # ------------------------------------------------------------------

    def start_idle_watcher(self, threshold_s: float) -> None:
        """Begin watching for idleness; pause after *threshold_s* seconds
        without a sandbox call. Idempotent — calling again is a no-op
        while the existing watcher is alive. Pass ``threshold_s <= 0``
        to disable (the watcher just doesn't start)."""
        if threshold_s <= 0:
            _idle_log.info("idle watcher disabled (threshold=%s)", threshold_s)
            return
        if self._idle_watcher_task is not None and not self._idle_watcher_task.done():
            return
        self._idle_threshold_s = threshold_s
        self._idle_last_activity_at = time.monotonic()
        self._idle_watcher_task = asyncio.create_task(
            self._run_idle_watcher(), name="idle-watcher"
        )
        _idle_log.info("idle watcher started (threshold=%.0fs)", threshold_s)

    async def stop_idle_watcher(self) -> None:
        """Cancel the watcher and unpause if currently paused.

        Important: callers (snapshot persist, container shutdown) need a
        thawed container, so the defensive unpause here is part of the
        contract, not a courtesy.
        """
        task = self._idle_watcher_task
        self._idle_watcher_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._idle_paused:
            async with self._idle_lock:
                if self._idle_paused:
                    try:
                        await self._do_unpause(reason="watcher stop")
                    except Exception:
                        # If unpause itself fails, clear the flag anyway —
                        # the alternative is leaving us believing we're
                        # paused when we may or may not be.
                        self._idle_paused = False
                        self._idle_paused_at = None

    def is_idle_paused(self) -> bool:
        return self._idle_paused

    async def _touch_activity(self) -> None:
        """Mark the sandbox as being used right now. Updates the timestamp
        and transparently unpauses if currently paused. Called from every
        wrapped entry point — the wrappers are the chokepoints that make
        "auto-resume on use" a single-line per call site."""
        self._idle_last_activity_at = time.monotonic()
        if not self._idle_paused:
            return
        async with self._idle_lock:
            if not self._idle_paused:
                return
            try:
                await self._do_unpause(reason="activity")
            except Exception as exc:
                _idle_log.warning("auto-unpause failed: %s", exc)
                # Clear the flag even on failure so the caller isn't stuck
                # in a "I believe I'm paused" loop. The next call will
                # attempt and surface a real error if the container is
                # genuinely broken.
                self._idle_paused = False
                self._idle_paused_at = None

    async def _run_idle_watcher(self) -> None:
        threshold = self._idle_threshold_s
        if threshold is None or threshold <= 0:
            return
        try:
            while True:
                await asyncio.sleep(_IDLE_WATCHER_INTERVAL_S)
                if self._idle_paused:
                    continue
                idle_for = time.monotonic() - self._idle_last_activity_at
                if idle_for < threshold:
                    continue
                async with self._idle_lock:
                    # Re-check inside the lock; ``_touch_activity`` may
                    # have flipped the state between our outer check and
                    # acquiring the lock.
                    idle_for = time.monotonic() - self._idle_last_activity_at
                    if idle_for < threshold or self._idle_paused:
                        continue
                    try:
                        await self._do_pause(idle_for=idle_for)
                    except Exception as exc:
                        _idle_log.warning("auto-pause failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def _do_pause(self, *, idle_for: float) -> None:
        await self.pause()
        self._idle_paused = True
        self._idle_paused_at = time.monotonic()
        _idle_log.info(
            "[idle] sandbox paused after %.0fm of inactivity — will resume on next sandbox call",
            idle_for / 60.0,
        )

    async def _do_unpause(self, *, reason: str) -> None:
        await self.unpause()
        paused_for = (
            (time.monotonic() - self._idle_paused_at)
            if self._idle_paused_at is not None
            else 0.0
        )
        self._idle_paused = False
        self._idle_paused_at = None
        _idle_log.info(
            "[idle] sandbox resumed after %.1fm paused (trigger: %s)",
            paused_for / 60.0,
            reason,
        )

    # ------------------------------------------------------------------
    # Activity-tracking entry-point wrappers.
    #
    # These four cover every path that talks to the docker API for
    # agent work:
    #   * ``_exec_run`` — shell commands, file ops via exec (mkdir, ls,
    #     test, rm, chmod, ...), workspace probes
    #   * ``_stream_into_exec`` — ``read`` / ``write`` / ``hydrate_workspace``
    #     bulk content streaming through exec
    #   * ``pty_exec_start`` — new PTY session creation
    #   * ``pty_write_stdin`` — keystroke input to an existing PTY
    #
    # We deliberately do NOT wrap ``running()``, ``pause()``, ``unpause()``,
    # ``paused()`` — those are lifecycle / introspection and would either
    # infinite-loop (pause auto-unpausing itself) or be wrong (a status
    # check shouldn't wake a paused container).
    # ------------------------------------------------------------------

    async def _exec_run(
        self,
        *,
        cmd: "list[str]",
        workdir: "str | None",
        user: "str | None",
        timeout: "float | None",
        command_for_errors: "tuple[str | Path, ...]",
        kill_on_timeout: bool,
    ) -> ExecResult:
        await self._touch_activity()
        return await super()._exec_run(
            cmd=cmd,
            workdir=workdir,
            user=user,
            timeout=timeout,
            command_for_errors=command_for_errors,
            kill_on_timeout=kill_on_timeout,
        )

    async def _stream_into_exec(
        self,
        *,
        cmd: "list[str]",
        stream: io.IOBase,
        error_path: Path,
        user: "str | User | None" = None,
    ) -> None:
        await self._touch_activity()
        return await super()._stream_into_exec(
            cmd=cmd, stream=stream, error_path=error_path, user=user
        )

    async def pty_exec_start(
        self,
        *command: "str | Path",
        timeout: "float | None" = None,
        shell: "bool | list[str]" = True,
        user: "str | User | None" = None,
        tty: bool = False,
        yield_time_s: "float | None" = None,
        max_output_tokens: "int | None" = None,
    ) -> PtyExecUpdate:
        await self._touch_activity()
        return await super().pty_exec_start(
            *command,
            timeout=timeout,
            shell=shell,
            user=user,
            tty=tty,
            yield_time_s=yield_time_s,
            max_output_tokens=max_output_tokens,
        )

    async def pty_write_stdin(
        self,
        *,
        session_id: int,
        chars: str,
        yield_time_s: "float | None" = None,
        max_output_tokens: "int | None" = None,
    ) -> PtyExecUpdate:
        await self._touch_activity()
        return await super().pty_write_stdin(
            session_id=session_id,
            chars=chars,
            yield_time_s=yield_time_s,
            max_output_tokens=max_output_tokens,
        )

    def pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        return self._pty_stream_output(process_id)

    async def _pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        entry = self._pty_processes.get(process_id)
        if entry is None:
            raise ValueError(f"unknown pty process_id {process_id}")
        q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=_STREAM_QUEUE_MAX_CHUNKS)

        async with entry.output_lock:
            while entry.output_chunks:
                chunk = entry.output_chunks.popleft()
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    break
            consumers = getattr(entry, "stream_consumers", None)
            if consumers is None:
                consumers = []
                setattr(entry, "stream_consumers", consumers)
            consumers.append(q)
            already_closed = entry.output_closed.is_set()

        if already_closed:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        try:
            while True:
                chunk = await q.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            consumers_now = getattr(entry, "stream_consumers", None)
            if consumers_now is not None:
                try:
                    consumers_now.remove(q)
                except ValueError:
                    pass

    async def pause(self) -> None:
        """Freeze every process in the container via the cgroup freezer
        (``container.pause()``). CPU consumption drops to zero, but RAM
        stays allocated — call ``unpause()`` to resume from exactly where
        we left off (queries mid-flight, dev servers in their hot path,
        etc.). Distinct from :meth:`shutdown`, which releases compute
        entirely at the cost of having to restart processes on resume.

        Tolerant of "already paused" / "not running" — the launcher fires
        this on idle detection which may race with explicit user actions;
        a 409 here just means we're already in the requested state and
        callers can carry on.
        """
        loop = asyncio.get_running_loop()

        def _do_pause() -> None:
            try:
                self._container.reload()
            except Exception:
                pass
            try:
                self._container.pause()
            except Exception as exc:
                # docker-py raises APIError for already-paused (409) and
                # for not-running. Treat both as no-ops — the caller
                # asked for "pause"; if we're already there, mission
                # accomplished, and there's nothing useful to do for a
                # stopped container.
                msg = str(exc).lower()
                if "already paused" in msg or "is not running" in msg:
                    return
                raise

        await loop.run_in_executor(_DOCKER_EXECUTOR, _do_pause)

    async def unpause(self) -> None:
        """Thaw a previously-paused container. Sub-second; every process
        resumes from the instruction it was frozen at.

        Idempotent — calling on a non-paused container is a no-op.
        """
        loop = asyncio.get_running_loop()

        def _do_unpause() -> None:
            try:
                self._container.reload()
            except Exception:
                pass
            try:
                self._container.unpause()
            except Exception as exc:
                msg = str(exc).lower()
                if "is not paused" in msg or "not paused" in msg:
                    return
                raise

        await loop.run_in_executor(_DOCKER_EXECUTOR, _do_unpause)

    async def paused(self) -> bool:
        """Return True if the container is currently in the docker ``paused``
        state. Cheap — uses the cached attributes refreshed by ``reload``.
        """
        loop = asyncio.get_running_loop()

        def _check() -> bool:
            try:
                self._container.reload()
            except Exception:
                return False
            try:
                return str(self._container.status) == "paused"
            except Exception:
                return False

        return await loop.run_in_executor(_DOCKER_EXECUTOR, _check)

    async def pty_resize(self, process_id: int, *, cols: int, rows: int) -> None:
        entry = self._pty_processes.get(process_id)
        if entry is None:
            return
        container_client = self._container.client
        if container_client is None:
            return
        api = container_client.api
        exec_id = entry.exec_id
        cols = max(1, int(cols))
        rows = max(1, int(rows))

        def _resize() -> None:
            try:
                api.exec_resize(exec_id, height=rows, width=cols)
            except Exception:
                # exec may have exited between the entry lookup and the
                # resize call; resize is advisory.
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_DOCKER_EXECUTOR, _resize)

    async def _append_pty_output_chunks(
        self, entry: _DockerPtyProcessEntry, chunks: list[bytes]
    ) -> None:
        async with entry.output_lock:
            consumers = getattr(entry, "stream_consumers", None)
            if consumers:
                for chunk in chunks:
                    for q in consumers:
                        try:
                            q.put_nowait(chunk)
                        except asyncio.QueueFull:
                            pass
            else:
                entry.output_chunks.extend(chunks)
        entry.output_notify.set()

    async def _mark_pty_output_closed(self, entry: _DockerPtyProcessEntry) -> None:
        entry.output_closed.set()
        entry.output_notify.set()
        consumers = getattr(entry, "stream_consumers", None)
        if consumers:
            for q in consumers:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


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

    async def resume(
        self,
        state: "DockerSandboxSessionState",
    ) -> SandboxSession:
        # Re-implements the parent's resume to instantiate
        # :class:`ExtendedDockerSandboxSession`. Without this, every resumed
        # session would silently downgrade to the base SDK class — losing
        # PTY streaming for terminals AND the new pause/unpause methods.
        # Behavior is otherwise identical: try to reattach by container_id;
        # fall through to a fresh container + snapshot rehydrate when the
        # original is gone.
        if not isinstance(state, DockerSandboxSessionState):
            raise TypeError(
                "ExtendedDockerSandboxClient.resume expects a DockerSandboxSessionState"
            )
        container = self.get_container(state.container_id)
        reused_existing_container = container is not None
        if container is None:
            container = await self._create_container(
                state.image,
                manifest=state.manifest,
                exposed_ports=state.exposed_ports,
                session_id=state.session_id,
            )
            container_id = container.id
            assert container_id is not None
            state.container_id = container_id
            state.workspace_root_ready = False

        inner = ExtendedDockerSandboxSession(
            docker_client=self.docker_client,
            container=container,
            state=state,
        )
        inner._resume_workspace_probe_pending = True
        inner._set_start_state_preserved(reused_existing_container)
        return self._wrap_session(inner, instrumentation=self._instrumentation)


def _dedup_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
