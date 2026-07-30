"""Agents-SDK sandbox backend for Azure Container Instances (ACI).

Host-runs-agent: ``omni serve`` runs outside (e.g. the launcher container) and
drives a serverless ACI container — the devbox image — over the exec WebSocket.
NOT container-runs-agent.

The SDK's ``BaseSandboxSession`` implements the whole high-level surface
(``exec``/``ls``/``mkdir``/``rm``/``apply_patch``/manifest application) on top of
six abstract primitives. Every one of them here routes through
:class:`~omniagents.core.sandbox.aci_engine.AciExecChannel` (a control shell over
the exec WebSocket, with sentinel parsing for exit codes + separated, binary-safe
stdout/stderr — proven against real ACI). The container itself is disposable; the
durable workspace is an Azure Files mount.

Interactive PTYs (``pty_*``) are a follow-up — the agent's exec + file ops do not
need them; human terminals do.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import io
import shlex
import time
import uuid
from typing import AsyncIterator, Deque, Optional

from agents.sandbox.manifest import Manifest
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.pty_types import (
    PtyExecUpdate,
    allocate_pty_process_id,
    clamp_pty_yield_time_ms,
    resolve_pty_write_yield_time_ms,
    truncate_text_by_tokens,
)
from agents.sandbox.session.sandbox_client import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
)
from agents.sandbox.session.sandbox_session import SandboxSession
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot
from agents.sandbox.types import ExecResult, ExposedPortEndpoint, User

from omniagents.core.sandbox.lifecycle_session import LifecycleSessionClientMixin
from omniagents.core.sandbox.aci_engine import (
    AciConfig,
    AciContainerSpec,
    AciExecChannel,
    AciFilesChannel,
    AciFileShare,
    AciManager,
    AciRegistry,
)

# Marker for streaming base64 payloads into the container via a heredoc. The
# exec channel sends each command as a single PTY line, and canonical-mode line
# discipline silently truncates input past ~4 KB — so a large payload inlined as
# one ``printf`` arg hangs. Feeding it as many short heredoc lines keeps every
# line under the limit. base64's alphabet never contains ``_``, so this marker
# can't collide with the body.
_B64_HEREDOC_MARKER = "OMNI_B64_HEREDOC_EOF"


def _b64_heredoc_lines(raw: bytes) -> str:
    """base64-encode *raw*, wrapped at 76 cols so each line is a short PTY line."""
    b64 = base64.b64encode(raw).decode("ascii")
    return "\n".join(b64[i : i + 76] for i in range(0, len(b64), 76))


class _AciPtyEntry:
    """One interactive PTY = one dedicated exec WebSocket, with a background
    reader draining frames into a buffer the batched ``pty_*`` API collects from.

    ACI exec gives a raw PTY but no exit status on close, so a clean WebSocket
    close is reported as ``exit_code=0`` (the process ended)."""

    def __init__(self, chan: AciExecChannel) -> None:
        self.chan = chan
        self.output: Deque[bytes] = collections.deque()
        self.lock = asyncio.Lock()
        self.notify = asyncio.Event()
        self.closed = asyncio.Event()
        self.exit_code: Optional[int] = None
        self.stream_consumers: list[asyncio.Queue[Optional[bytes]]] = []
        self.last_used = time.monotonic()
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for frame in self.chan.iter_output():
                async with self.lock:
                    self.output.append(frame)
                    for q in self.stream_consumers:
                        try:
                            q.put_nowait(frame)
                        except asyncio.QueueFull:
                            pass
                self.notify.set()
        except Exception:
            pass
        finally:
            if self.exit_code is None:
                self.exit_code = 0  # ws closed → command ended (ACI gives no code)
            async with self.lock:
                for q in self.stream_consumers:
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
            self.closed.set()
            self.notify.set()

    async def terminate(self) -> None:
        if not self.reader.done():
            self.reader.cancel()
        await self.chan.close()


# --------------------------------------------------------------------------- #
# Options / state
# --------------------------------------------------------------------------- #


class AciSandboxClientOptions(BaseSandboxClientOptions):
    type: str = "aci"
    image: str
    exposed_ports: tuple[int, ...] = ()
    cpu: float = 2.0
    memory_gb: float = 4.0


class AciSandboxSessionState(SandboxSessionState):
    type: str = "aci"
    container_name: str
    # Spec essentials persisted so resume can recreate a vanished container
    # group (its workspace lives on Azure Files, so recreate is non-destructive).
    image: str = ""
    cpu: float = 2.0
    memory_gb: float = 4.0


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


class AciSandboxSession(BaseSandboxSession):
    """Drives one ACI container group via a control-shell exec channel."""

    def __init__(
        self,
        manager: AciManager,
        name: str,
        state: AciSandboxSessionState,
        file_share: Optional[AciFileShare] = None,
    ) -> None:
        self._manager = manager
        self._name = name
        self.state = state
        self._chan: Optional[AciExecChannel] = None
        self._chan_lock = asyncio.Lock()
        self._pty_lock = asyncio.Lock()
        self._pty_processes: dict[int, _AciPtyEntry] = {}
        self._reserved_pty_process_ids: set[int] = set()
        self._ip: Optional[str] = None
        # When the workspace is an Azure Files mount, the launcher writes/reads
        # it directly over the Files API (fast, no PTY) for paths under the
        # mount; off-mount paths fall back to the exec channel.
        self._files: Optional[AciFilesChannel] = (
            AciFilesChannel(file_share) if file_share else None
        )

    # -- lifecycle hooks ---------------------------------------------------- #

    async def _ensure_chan(self) -> AciExecChannel:
        if self._chan is None:
            async with self._chan_lock:
                if self._chan is None:
                    self._chan = await AciExecChannel.open(
                        self._manager, self._name, self._name
                    )
        return self._chan

    async def _ensure_backend_started(self) -> None:
        await self._manager.wait_running(self._name)
        await self._ensure_chan()

    async def _shutdown_backend(self) -> None:
        await self.pty_terminate_all()
        if self._chan is not None:
            await self._chan.close()
            self._chan = None
        if self._files is not None:
            await self._files.close()
        await self._manager.delete(self._name)

    # -- interactive PTYs (one exec WebSocket per terminal) ----------------- #

    def supports_pty(self) -> bool:
        return True

    async def pty_exec_start(
        self,
        *command: str | object,
        timeout: float | None = None,
        shell: bool | list[str] = True,
        user: str | User | None = None,
        tty: bool = False,
        yield_time_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> PtyExecUpdate:
        await self._ensure_backend_started()
        sanitized = self._prepare_exec_command(
            *(str(c) for c in command), shell=shell, user=None
        )
        joined = shlex.join(str(c) for c in sanitized)
        # Every PTY is a fresh /bin/sh; `exec` the requested command into it so
        # the WebSocket close maps to the command exiting.
        chan = await AciExecChannel.open(
            self._manager, self._name, self._name, command="/bin/sh", drain_banner=False
        )
        if joined:
            await chan.write(f"exec {joined}\n")
        entry = _AciPtyEntry(chan)
        async with self._pty_lock:
            process_id = allocate_pty_process_id(self._reserved_pty_process_ids)
            self._reserved_pty_process_ids.add(process_id)
            self._pty_processes[process_id] = entry
        yield_time_ms = 10_000 if yield_time_s is None else int(yield_time_s * 1000)
        output, tokens = await self._collect_pty_output(
            entry, clamp_pty_yield_time_ms(yield_time_ms), max_output_tokens
        )
        return await self._finalize_pty(process_id, entry, output, tokens)

    async def pty_write_stdin(
        self,
        *,
        session_id: int,
        chars: str,
        yield_time_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> PtyExecUpdate:
        async with self._pty_lock:
            entry = self._resolve_pty_session_entry(
                pty_processes=self._pty_processes, session_id=session_id
            )
        if chars:
            await entry.chan.write(chars)
            await asyncio.sleep(0.1)
        yield_time_ms = 250 if yield_time_s is None else int(yield_time_s * 1000)
        output, tokens = await self._collect_pty_output(
            entry,
            resolve_pty_write_yield_time_ms(
                yield_time_ms=yield_time_ms, input_empty=chars == ""
            ),
            max_output_tokens,
        )
        entry.last_used = time.monotonic()
        return await self._finalize_pty(session_id, entry, output, tokens)

    async def pty_terminate_all(self) -> None:
        async with self._pty_lock:
            entries = list(self._pty_processes.values())
            self._pty_processes.clear()
            self._reserved_pty_process_ids.clear()
        for entry in entries:
            await entry.terminate()

    def pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        return self._pty_stream_output(process_id)

    async def _pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        async with self._pty_lock:
            entry = self._pty_processes.get(process_id)
        if entry is None:
            raise ValueError(f"unknown pty process_id {process_id}")
        q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=1024)
        async with entry.lock:
            while entry.output:
                try:
                    q.put_nowait(entry.output.popleft())
                except asyncio.QueueFull:
                    break
            entry.stream_consumers.append(q)
            already_closed = entry.closed.is_set()
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
            async with entry.lock:
                try:
                    entry.stream_consumers.remove(q)
                except ValueError:
                    pass

    async def pty_resize(self, process_id: int, *, cols: int, rows: int) -> None:
        _ = (process_id, cols, rows)
        return None

    async def _collect_pty_output(
        self, entry: _AciPtyEntry, yield_time_ms: int, max_output_tokens: int | None
    ) -> tuple[bytes, int | None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (yield_time_ms / 1000)
        buf = bytearray()
        while True:
            async with entry.lock:
                while entry.output:
                    buf.extend(entry.output.popleft())
            if loop.time() >= deadline:
                break
            if entry.closed.is_set():
                async with entry.lock:
                    while entry.output:
                        buf.extend(entry.output.popleft())
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(entry.notify.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            entry.notify.clear()
        text = bytes(buf).decode("utf-8", "replace")
        truncated, tokens = truncate_text_by_tokens(text, max_output_tokens)
        return truncated.encode("utf-8", "replace"), tokens

    async def _finalize_pty(
        self, process_id: int, entry: _AciPtyEntry, output: bytes, tokens: int | None
    ) -> PtyExecUpdate:
        exit_code = entry.exit_code if entry.closed.is_set() else None
        live_process_id: int | None = process_id
        if exit_code is not None:
            async with self._pty_lock:
                removed = self._pty_processes.pop(process_id, None)
                self._reserved_pty_process_ids.discard(process_id)
            if removed is not None:
                await removed.terminate()
            live_process_id = None
        return PtyExecUpdate(
            process_id=live_process_id,
            output=output,
            exit_code=exit_code,
            original_token_count=tokens,
        )

    async def running(self) -> bool:
        try:
            return (await self._manager.get_state(self._name)) == "Running"
        except Exception:
            return False

    # -- exposed ports ------------------------------------------------------ #

    async def _resolve_exposed_port(self, port: int) -> ExposedPortEndpoint:
        """Map an exposed container port to the group's IP. Private (VNet) or
        public depending on how the group was created; the caller (a
        VNet-integrated launcher, in the private case) reaches it directly.
        TLS is off — the host/launcher proxy terminates TLS in front."""
        await self._manager.wait_running(self._name)
        if not self._ip:
            self._ip = await self._manager.get_ip(self._name)
        if not self._ip:
            return await super()._resolve_exposed_port(port)
        return ExposedPortEndpoint(host=self._ip, port=port, tls=False)

    # -- the one true primitive: everything else rides on this -------------- #

    async def _exec_internal(
        self, *command: str | object, timeout: float | None = None
    ) -> ExecResult:
        chan = await self._ensure_chan()
        joined = shlex.join(str(c) for c in command)
        outcome = await chan.run(joined, timeout=timeout or 120.0)
        return ExecResult(
            stdout=outcome.stdout, stderr=outcome.stderr, exit_code=outcome.exit_code
        )

    # -- file IO ------------------------------------------------------------ #
    #
    # Workspace paths (under the Azure Files mount) go straight over the Files
    # API — fast and exact, no PTY. Everything else rides the exec channel,
    # which is binary-safe via base64 (chunked + verified on read, heredoc on
    # write to survive the PTY's line/output limits).

    def _share_rel(self, abs_path: str) -> Optional[str]:
        """Share-relative path if ``abs_path`` is under the Files mount, else None."""
        return self._files.rel_path(abs_path) if self._files else None

    async def read(self, path, *, user: str | User | None = None) -> io.IOBase:
        wp = await self._validate_path_access(path)
        rel = self._share_rel(str(wp))
        if rel:
            try:
                return io.BytesIO(await self._files.read(rel))  # type: ignore[union-attr]
            except Exception as e:
                raise FileNotFoundError(f"{wp}: {e}") from e
        res = await self.exec("cat", "--", str(wp), shell=False, user=user)
        if not res.ok():
            raise FileNotFoundError(
                f"{wp}: {res.stderr.decode('utf-8', 'replace').strip()}"
            )
        return io.BytesIO(res.stdout)

    async def write(
        self, path, data: io.IOBase, *, user: str | User | None = None
    ) -> None:
        wp = await self._validate_path_access(path, for_write=True)
        raw = data.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")

        # Fast path: workspace files go straight to the Azure Files share. The
        # container sees them via the mount; chown isn't applicable (the share
        # maps everything to the mount's uid/gid).
        rel = self._share_rel(str(wp))
        if rel:
            await self._files.write(rel, raw)  # type: ignore[union-attr]
            return

        await self.mkdir(wp.parent, parents=True)
        # Stream the payload as many short heredoc lines, decoded in-container.
        # We drive ``chan.run`` directly rather than ``self.exec``: the latter
        # wraps the command in ``sh -lc '…'``, and that extra quoting breaks the
        # embedded heredoc. The ``__e``/``(exit $__e)`` trailer keeps the heredoc
        # body off the final token so the control shell's ``{ … ; }`` wrapper
        # stays valid while still surfacing base64's real exit code.
        chan = await self._ensure_chan()
        marker = _B64_HEREDOC_MARKER
        cmd = (
            f"base64 -d > {shlex.quote(str(wp))} <<'{marker}'\n"
            f"{_b64_heredoc_lines(raw)}\n{marker}\n__e=$?\n(exit $__e)"
        )
        outcome = await chan.run(cmd, timeout=120.0)
        if outcome.exit_code != 0:
            raise OSError(
                f"write {wp} failed: {outcome.stderr.decode('utf-8', 'replace').strip()}"
            )
        # Ownership follows the exec user (root) by default; honor an explicit
        # ``user`` with a follow-up chown (a small command, no payload).
        if user is not None:
            uname = user.name if isinstance(user, User) else user
            res = await self.exec("chown", "--", uname, str(wp), shell=False)
            if not res.ok():
                raise OSError(
                    f"write {wp}: chown -> {uname} failed: {res.stderr.decode('utf-8', 'replace').strip()}"
                )

    async def persist_workspace(self) -> io.IOBase:
        root = str(self.state.manifest.root)
        res = await self.exec(f"tar -czf - -C {shlex.quote(root)} .")
        if not res.ok():
            raise OSError(
                f"persist_workspace failed: {res.stderr.decode('utf-8', 'replace').strip()}"
            )
        return io.BytesIO(res.stdout)

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        root = str(self.state.manifest.root)
        raw = data.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")

        # Fast path: when the workspace root is the Azure Files mount, extract
        # the tar straight onto the share (no exec, no PTY transfer).
        root_rel = self._share_rel(root)
        if root_rel is not None and self._files is not None:
            await self._files.hydrate(root_rel, raw)
            return

        # mkdir first (small command), then stream the tar via a heredoc and
        # decode|extract — same PTY-line-limit reasoning as ``write``.
        mk = await self.exec("mkdir", "-p", "--", root, shell=False)
        if not mk.ok():
            raise OSError(
                f"hydrate_workspace mkdir failed: {mk.stderr.decode('utf-8', 'replace').strip()}"
            )
        chan = await self._ensure_chan()
        marker = _B64_HEREDOC_MARKER
        cmd = (
            f"base64 -d <<'{marker}' | tar -xzf - -C {shlex.quote(root)}\n"
            f"{_b64_heredoc_lines(raw)}\n{marker}\n__e=$?\n(exit $__e)"
        )
        outcome = await chan.run(cmd, timeout=120.0)
        if outcome.exit_code != 0:
            raise OSError(
                f"hydrate_workspace failed: {outcome.stderr.decode('utf-8', 'replace').strip()}"
            )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class AciSandboxClient(LifecycleSessionClientMixin, BaseSandboxClient):
    """Provisions and reattaches ACI container groups as agent sandboxes."""

    backend_id = "aci"

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        location: str = "southcentralus",
        file_share: Optional[dict | AciFileShare] = None,
        registry: Optional[dict | AciRegistry] = None,
        subnet_id: Optional[str] = None,
        credential: Optional[object] = None,
        instrumentation: Optional[object] = None,
    ) -> None:
        self._manager = AciManager(
            AciConfig(subscription_id, resource_group, location), credential
        )
        self._file_share = _coerce_file_share(file_share)
        self._registry = _coerce_registry(registry)
        self._subnet_id = subnet_id or None
        self._instrumentation = instrumentation

    async def _build_spec(
        self, name: str, options: AciSandboxClientOptions, manifest: Manifest
    ) -> AciContainerSpec:
        env = await manifest.environment.resolve() if manifest is not None else {}
        return AciContainerSpec(
            name=name,
            image=options.image,
            cpu=options.cpu,
            memory_gb=options.memory_gb,
            env=dict(env or {}),
            exposed_ports=tuple(options.exposed_ports),
            file_share=self._file_share,
            registry=self._registry,
            subnet_id=self._subnet_id,
        )

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: AciSandboxClientOptions,
    ) -> SandboxSession:
        manifest = manifest or Manifest()
        session_id = uuid.uuid4()
        name = f"omni-{session_id.hex[:16]}"
        await self._manager.create(await self._build_spec(name, options, manifest))
        await self._manager.wait_running(name)
        state = AciSandboxSessionState(
            type="aci",
            session_id=session_id,
            snapshot=resolve_snapshot(snapshot, str(session_id)),
            manifest=manifest,
            exposed_ports=tuple(options.exposed_ports),
            container_name=name,
            image=options.image,
            cpu=options.cpu,
            memory_gb=options.memory_gb,
        )
        inner = AciSandboxSession(
            self._manager, name, state, file_share=self._file_share
        )
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    async def delete(self, session: SandboxSession) -> SandboxSession:
        await session.aclose()
        return session

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        if not isinstance(state, AciSandboxSessionState):
            raise TypeError("AciSandboxClient.resume expects an AciSandboxSessionState")
        name = state.container_name
        alive = (await self._manager.get_state(name)) == "Running"
        if not alive:
            # Original group is gone; recreate from the persisted spec and let
            # start() rehydrate from the snapshot (workspace persists on Azure
            # Files regardless).
            env = await state.manifest.environment.resolve() if state.manifest else {}
            await self._manager.create(
                AciContainerSpec(
                    name=name,
                    image=state.image,
                    cpu=state.cpu,
                    memory_gb=state.memory_gb,
                    env=dict(env or {}),
                    exposed_ports=tuple(state.exposed_ports),
                    file_share=self._file_share,
                    registry=self._registry,
                    subnet_id=self._subnet_id,
                )
            )
            await self._manager.wait_running(name)
            state.workspace_root_ready = False
        inner = AciSandboxSession(
            self._manager, name, state, file_share=self._file_share
        )
        if alive:
            inner._set_start_state_preserved(True)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    def deserialize_session_state(
        self, payload: dict[str, object]
    ) -> SandboxSessionState:
        return AciSandboxSessionState.model_validate(payload)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _coerce_file_share(value: Optional[dict | AciFileShare]) -> Optional[AciFileShare]:
    if value is None or isinstance(value, AciFileShare):
        return value
    return AciFileShare(**value)


def _coerce_registry(value: Optional[dict | AciRegistry]) -> Optional[AciRegistry]:
    if value is None or isinstance(value, AciRegistry):
        return value
    return AciRegistry(**value)
