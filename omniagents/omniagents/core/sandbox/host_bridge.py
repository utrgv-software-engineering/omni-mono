"""Agents-SDK sandbox backend that targets a user-owned host (laptop) over a
relayed WebSocket — "computer-as-sandbox".

The agent (``SandboxAgent``/``omni serve``) stays where it is (the cloud). Only
the sandbox surface — ``exec`` / file IO / PTYs / exposed ports — is dispatched
to a remote host running ``omni sandbox-host`` (see
``omni_code.sandbox_host_cli``), which hosts a real
``UnixLocalSandboxSession`` and executes against that host's filesystem.

This mirrors :mod:`omniagents.core.sandbox.aci` exactly: the cloud-side
``HostBridgeSandboxSession`` is the "smart" half (it inherits the SDK lifecycle,
command prep, manifest/snapshot orchestration), and the remote host is a thin
executor. Command prep (``sh -lc`` wrapping, ``sudo -u`` for ``run_as``) happens
here via ``BaseSandboxSession.exec``; the already-prepared argv is forwarded and
run on the host with ``shell=False`` so it is executed exactly once.

Transport: a single multiplexed JSON request/response channel per session
(:class:`HostBridgeChannel`). The cloud dials ``options.endpoint`` — in the
launcher topology that is the cloud launcher's loopback relay
(``/proxy/local/<machineId>/<sessionId>/<execPort>``), which forwards frames to
the host over the already-authenticated machine-registry WS. Binary payloads
(file bytes, PTY output) are base64-encoded in the JSON envelope.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any, AsyncIterator, Optional

import websockets

from agents.sandbox.manifest import Manifest
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.pty_types import PtyExecUpdate
from agents.sandbox.session.sandbox_client import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
)
from agents.sandbox.session.sandbox_session import SandboxSession
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot
from agents.sandbox.types import ExecResult, ExposedPortEndpoint, User

from omniagents.core.sandbox.lifecycle_session import LifecycleSessionClientMixin

# Reverse-RPC timeout for a single op. Generous because a forwarded exec may run
# a long-ish command; PTY ops carry their own yield windows well under this.
_OP_TIMEOUT_S = 300.0


# --------------------------------------------------------------------------- #
# Channel: one multiplexed JSON request/response WebSocket per session
# --------------------------------------------------------------------------- #


class HostBridgeError(RuntimeError):
    """A remote op failed on the host side; carries the host's message."""


class HostBridgeChannel:
    """A single WebSocket to the remote host, multiplexing ops by integer id.

    One background reader resolves pending futures by id. All sends are awaited;
    responses are correlated by the ``id`` echoed back. Thread-unsafe by design —
    the SDK drives a session from a single event loop.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pty_stream_queues: dict[int, list[asyncio.Queue[Optional[bytes]]]] = {}
        self._reader: Optional[asyncio.Task[None]] = None
        self._closed = False

    @classmethod
    async def open(cls, endpoint: str) -> "HostBridgeChannel":
        ws = await websockets.connect(endpoint, max_size=None, open_timeout=30)
        chan = cls(ws)
        chan._reader = asyncio.create_task(chan._read_loop())
        return chan

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                rid = msg.get("id")
                if msg.get("event") == "pty_output":
                    pid = msg.get("session_id")
                    if isinstance(pid, int):
                        payload = (
                            None if msg.get("closed") else _b64d(msg.get("data_b64"))
                        )
                        for q in self._pty_stream_queues.get(pid, []):
                            try:
                                q.put_nowait(payload)
                            except asyncio.QueueFull:
                                pass
                    continue
                if not isinstance(rid, int):
                    continue
                fut = self._pending.pop(rid, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except Exception as e:  # connection dropped mid-flight
            self._fail_all(e)

    def _fail_all(self, error: Exception) -> None:
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HostBridgeError(f"host channel closed: {error}"))
        self._pending.clear()

    async def call(self, op: str, **args: Any) -> dict[str, Any]:
        if self._closed:
            raise HostBridgeError("host channel is closed")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps({"id": rid, "op": op, **args}))
        try:
            msg = await asyncio.wait_for(fut, timeout=_OP_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            self._pending.pop(rid, None)
            raise HostBridgeError(f"host op {op!r} timed out") from e
        if not msg.get("ok", False):
            raise HostBridgeError(str(msg.get("error") or f"host op {op!r} failed"))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
        try:
            await self._ws.close()
        except Exception:
            pass

    async def open_pty_stream(self, process_id: int) -> asyncio.Queue[Optional[bytes]]:
        q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=1024)
        self._pty_stream_queues.setdefault(process_id, []).append(q)
        try:
            await self.call("pty_stream_start", session_id=process_id)
        except Exception:
            self._pty_stream_queues.get(process_id, []).remove(q)
            raise
        return q

    def close_pty_stream(
        self, process_id: int, q: asyncio.Queue[Optional[bytes]]
    ) -> None:
        queues = self._pty_stream_queues.get(process_id)
        if not queues:
            return
        try:
            queues.remove(q)
        except ValueError:
            pass
        if not queues:
            self._pty_stream_queues.pop(process_id, None)


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str | None) -> bytes:
    return base64.b64decode(data) if data else b""


def _user_name(user: str | User | None) -> Optional[str]:
    if user is None:
        return None
    return user.name if isinstance(user, User) else user


# --------------------------------------------------------------------------- #
# Options + state
# --------------------------------------------------------------------------- #


class HostBridgeSandboxClientOptions(BaseSandboxClientOptions):
    type: str = "host_bridge"
    # Relay WebSocket the cloud dials to reach the host's `omni sandbox-host`.
    # Defaults empty: the endpoint normally lives in the ``client:`` block (so
    # the client carries it); ``create`` falls back to the client's endpoint.
    endpoint: str = ""
    exposed_ports: tuple[int, ...] = ()


class HostBridgeSandboxSessionState(SandboxSessionState):
    type: str = "host_bridge"
    endpoint: str = ""


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


class HostBridgeSandboxSession(BaseSandboxSession):
    """Forwards the sandbox surface to a remote ``omni sandbox-host``.

    The remote hosts the authoritative ``UnixLocalSandboxSession`` (workspace
    setup, runtime helpers, manifest, PTYs all run there). The cloud-side
    lifecycle hooks that would otherwise materialize a workspace locally are
    delegated to the host on ``create`` and short-circuited here.
    """

    def __init__(self, endpoint: str, state: HostBridgeSandboxSessionState) -> None:
        self._endpoint = endpoint
        self.state = state
        self._chan: Optional[HostBridgeChannel] = None
        self._chan_lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------- #

    async def _ensure_chan(self) -> HostBridgeChannel:
        if self._chan is None:
            async with self._chan_lock:
                if self._chan is None:
                    self._chan = await HostBridgeChannel.open(self._endpoint)
        return self._chan

    async def _ensure_backend_started(self) -> None:
        chan = await self._ensure_chan()
        # The host builds + starts a real UnixLocalSandboxSession with this
        # manifest/options. Workspace prep, runtime helpers, and manifest
        # materialization all happen on the host (where the files live).
        await chan.call(
            "create",
            manifest=self.state.manifest.model_dump(mode="json"),
            exposed_ports=list(self.state.exposed_ports),
        )

    # The host's session owns workspace setup; the cloud must not re-do any of
    # it (the workspace, helpers, and snapshot all live on the host).
    async def _prepare_backend_workspace(self) -> None:
        return

    async def _ensure_runtime_helpers(self) -> None:
        return

    async def _start_workspace(self) -> None:
        return

    async def _probe_workspace_root_for_preserved_resume(self) -> bool:
        return True

    async def _shutdown_backend(self) -> None:
        if self._chan is not None:
            try:
                await self._chan.call("shutdown")
            except Exception:
                pass
            await self._chan.close()
            self._chan = None

    async def running(self) -> bool:
        try:
            chan = await self._ensure_chan()
            res = await chan.call("running")
            return bool(res.get("running"))
        except Exception:
            return False

    # -- exec: cloud preps argv (sh -lc / sudo -u) → host runs it raw ------ #

    async def _exec_internal(
        self, *command: str | object, timeout: float | None = None
    ) -> ExecResult:
        chan = await self._ensure_chan()
        res = await chan.call(
            "exec",
            argv=[str(c) for c in command],
            timeout=timeout,
        )
        return ExecResult(
            stdout=_b64d(res.get("stdout_b64")),
            stderr=_b64d(res.get("stderr_b64")),
            exit_code=int(res.get("exit_code", 1)),
        )

    # -- file IO ----------------------------------------------------------- #

    async def read(self, path, *, user: str | User | None = None) -> io.IOBase:
        chan = await self._ensure_chan()
        res = await chan.call("read", path=str(path), user=_user_name(user))
        return io.BytesIO(_b64d(res.get("data_b64")))

    async def write(
        self, path, data: io.IOBase, *, user: str | User | None = None
    ) -> None:
        raw = data.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        chan = await self._ensure_chan()
        await chan.call(
            "write", path=str(path), data_b64=_b64e(raw), user=_user_name(user)
        )

    # -- PTYs: cloud preps argv → host runs it in a pty (shell=False) ------ #

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
        sanitized = self._prepare_exec_command(
            *(str(c) for c in command), shell=shell, user=user
        )
        chan = await self._ensure_chan()
        res = await chan.call(
            "pty_start",
            argv=[str(c) for c in sanitized],
            tty=tty,
            yield_time_s=yield_time_s,
            max_output_tokens=max_output_tokens,
        )
        return _pty_update_from(res)

    async def pty_write_stdin(
        self,
        *,
        session_id: int,
        chars: str,
        yield_time_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> PtyExecUpdate:
        chan = await self._ensure_chan()
        res = await chan.call(
            "pty_write",
            session_id=session_id,
            chars=chars,
            yield_time_s=yield_time_s,
            max_output_tokens=max_output_tokens,
        )
        return _pty_update_from(res)

    async def pty_terminate_all(self) -> None:
        if self._chan is None:
            return
        try:
            await self._chan.call("pty_terminate")
        except Exception:
            pass

    def pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        return self._pty_stream_output(process_id)

    async def _pty_stream_output(self, process_id: int) -> AsyncIterator[bytes]:
        chan = await self._ensure_chan()
        q = await chan.open_pty_stream(process_id)
        try:
            while True:
                chunk = await q.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            chan.close_pty_stream(process_id, q)

    async def pty_resize(self, process_id: int, *, cols: int, rows: int) -> None:
        chan = await self._ensure_chan()
        await chan.call("pty_resize", session_id=process_id, cols=cols, rows=rows)

    # -- workspace tar (only used by promote-to-cloud; noop snapshot otherwise) #

    async def persist_workspace(self) -> io.IOBase:
        import shlex

        root = str(self.state.manifest.root)
        res = await self.exec(f"tar -czf - -C {shlex.quote(root)} .")
        if not res.ok():
            raise OSError(
                f"persist_workspace failed: {res.stderr.decode('utf-8', 'replace').strip()}"
            )
        return io.BytesIO(res.stdout)

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        import shlex

        root = str(self.state.manifest.root)
        raw = data.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        mk = await self.exec("mkdir", "-p", "--", root, shell=False)
        if not mk.ok():
            raise OSError(
                f"hydrate_workspace mkdir failed: {mk.stderr.decode('utf-8', 'replace').strip()}"
            )
        # Stream the tar through the write op, then extract on the host.
        tmp = f"{root.rstrip('/')}/.omni-hydrate.tar.gz"
        await self.write(tmp, io.BytesIO(raw))
        res = await self.exec(
            f"tar -xzf {shlex.quote(tmp)} -C {shlex.quote(root)} && rm -f {shlex.quote(tmp)}"
        )
        if not res.ok():
            raise OSError(
                f"hydrate_workspace failed: {res.stderr.decode('utf-8', 'replace').strip()}"
            )

    # -- exposed ports ----------------------------------------------------- #

    async def _resolve_exposed_port(self, port: int) -> ExposedPortEndpoint:
        chan = await self._ensure_chan()
        res = await chan.call("resolve_port", port=port)
        return ExposedPortEndpoint(
            host=str(res.get("host", "127.0.0.1")),
            port=int(res.get("port", port)),
            tls=bool(res.get("tls", False)),
        )


def _pty_update_from(res: dict[str, Any]) -> PtyExecUpdate:
    pid = res.get("process_id")
    return PtyExecUpdate(
        process_id=int(pid) if pid is not None else None,
        output=_b64d(res.get("output_b64")),
        exit_code=res.get("exit_code"),
        original_token_count=res.get("original_token_count"),
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class HostBridgeSandboxClient(LifecycleSessionClientMixin, BaseSandboxClient):
    """Connects a cloud agent to a remote host as its sandbox.

    There is nothing to provision: the host already exists and its workspace is
    durable on its own disk. ``create`` just records the endpoint + manifest in
    the session state; ``resume`` re-dials the same host.
    """

    backend_id = "host_bridge"

    def __init__(
        self, *, endpoint: str, instrumentation: Optional[object] = None
    ) -> None:
        self._endpoint = endpoint
        self._instrumentation = instrumentation

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: HostBridgeSandboxClientOptions | None = None,
    ) -> SandboxSession:
        manifest = manifest or Manifest()
        # `options` is None when the profile has no `options:` block — the
        # endpoint then comes from the `client:` block (self._endpoint), which
        # is the normal case for a launcher-written host_bridge profile.
        endpoint = (options.endpoint if options else "") or self._endpoint
        exposed_ports = tuple(options.exposed_ports) if options else ()
        state = HostBridgeSandboxSessionState(
            type="host_bridge",
            # host workspace is durable; a noop snapshot keeps bytes on the host
            # (no tar, no upload) — same as unix_local.
            snapshot=resolve_snapshot(snapshot, "host_bridge"),
            manifest=manifest,
            exposed_ports=exposed_ports,
            endpoint=endpoint,
        )
        inner = HostBridgeSandboxSession(endpoint, state)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    async def delete(self, session: SandboxSession) -> SandboxSession:
        await session.aclose()
        return session

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        if not isinstance(state, HostBridgeSandboxSessionState):
            raise TypeError(
                "HostBridgeSandboxClient.resume expects a HostBridgeSandboxSessionState"
            )
        inner = HostBridgeSandboxSession(state.endpoint or self._endpoint, state)
        # The host workspace persists on disk; treat the workspace as preserved
        # so resume skips a redundant manifest re-apply.
        inner._set_start_state_preserved(True)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    def deserialize_session_state(
        self, payload: dict[str, object]
    ) -> SandboxSessionState:
        return HostBridgeSandboxSessionState.model_validate(payload)


# --------------------------------------------------------------------------- #
# Host side: serve one session over a WebSocket connection
# --------------------------------------------------------------------------- #
#
# Kept here, next to the client, so the wire protocol (op names + payload
# shapes) is defined exactly once. ``omni sandbox-host`` is a thin CLI around
# ``serve_host_connection``.


async def serve_host_connection(ws: Any, *, workspace_root: str | None = None) -> None:
    """Serve one ``HostBridgeSandboxSession`` peer over ``ws``.

    The first ``create`` op builds and starts a real
    ``ExtendedUnixLocalSandboxSession``; subsequent ops forward to it. Command
    prep already happened on the cloud, so ``exec``/``pty`` run with
    ``shell=False`` (argv executed exactly once).

    ``workspace_root`` (when set) OVERRIDES the manifest root from the cloud: the
    cloud doesn't know this host's filesystem layout, so the launcher passes the
    real local project path here and we root the session there.
    """
    session: Any = None
    stream_tasks: dict[int, asyncio.Task[None]] = {}
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            rid = msg.get("id")
            try:
                session, result = await _host_dispatch(
                    session, msg, workspace_root, ws, stream_tasks
                )
                await ws.send(json.dumps({"id": rid, "ok": True, "result": result}))
            except Exception as e:  # noqa: BLE001
                await ws.send(
                    json.dumps(
                        {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}
                    )
                )
    finally:
        for task in stream_tasks.values():
            task.cancel()
        if session is not None:
            try:
                await session.aclose()
            except Exception:
                pass


async def _host_dispatch(
    session: Any,
    msg: dict[str, Any],
    workspace_root: str | None = None,
    ws: Any = None,
    stream_tasks: Optional[dict[int, asyncio.Task[None]]] = None,
) -> tuple[Any, dict[str, Any]]:
    op = msg.get("op")
    if op == "create":
        session = await _host_create(msg, workspace_root)
        root = workspace_root or str(msg.get("manifest", {}).get("root", ""))
        return session, {"root": root}
    if session is None:
        raise RuntimeError("session not created (send 'create' first)")
    if op == "exec":
        argv = [str(a) for a in (msg.get("argv") or [])]
        res = await session.exec(
            *argv, shell=False, user=None, timeout=msg.get("timeout")
        )
        return session, {
            "stdout_b64": _b64e(res.stdout),
            "stderr_b64": _b64e(res.stderr),
            "exit_code": res.exit_code,
        }
    if op == "read":
        stream = await session.read(msg["path"], user=msg.get("user"))
        data = stream.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return session, {"data_b64": _b64e(data)}
    if op == "write":
        await session.write(
            msg["path"], io.BytesIO(_b64d(msg.get("data_b64"))), user=msg.get("user")
        )
        return session, {}
    if op == "pty_start":
        argv = [str(a) for a in (msg.get("argv") or [])]
        update = await session.pty_exec_start(
            *argv,
            shell=False,
            user=None,
            tty=bool(msg.get("tty", False)),
            yield_time_s=msg.get("yield_time_s"),
            max_output_tokens=msg.get("max_output_tokens"),
        )
        return session, _pty_payload(update)
    if op == "pty_write":
        update = await session.pty_write_stdin(
            session_id=int(msg["session_id"]),
            chars=msg.get("chars", ""),
            yield_time_s=msg.get("yield_time_s"),
            max_output_tokens=msg.get("max_output_tokens"),
        )
        return session, _pty_payload(update)
    if op == "pty_drain":
        update = await session.pty_write_stdin(
            session_id=int(msg["session_id"]),
            chars="",
            yield_time_s=msg.get("yield_time_s"),
            max_output_tokens=msg.get("max_output_tokens"),
        )
        return session, _pty_payload(update)
    if op == "pty_stream_start":
        pid = int(msg["session_id"])
        if stream_tasks is not None and pid in stream_tasks:
            return session, {}

        async def _stream() -> None:
            try:
                inner = getattr(session, "_inner", session)
                async for chunk in inner.pty_stream_output(pid):
                    await ws.send(
                        json.dumps(
                            {
                                "event": "pty_output",
                                "session_id": pid,
                                "data_b64": _b64e(chunk),
                            }
                        )
                    )
            finally:
                try:
                    await ws.send(
                        json.dumps(
                            {"event": "pty_output", "session_id": pid, "closed": True}
                        )
                    )
                except Exception:
                    pass

        if stream_tasks is not None:
            stream_tasks[pid] = asyncio.create_task(_stream())
        return session, {}
    if op == "pty_resize":
        resize = getattr(session, "pty_resize", None)
        if resize is not None:
            await resize(
                int(msg["session_id"]),
                cols=int(msg.get("cols") or 80),
                rows=int(msg.get("rows") or 24),
            )
        return session, {}
    if op == "pty_terminate":
        await session.pty_terminate_all()
        return session, {}
    if op == "resolve_port":
        endpoint = await session.resolve_exposed_port(int(msg["port"]))
        return session, {
            "host": endpoint.host,
            "port": endpoint.port,
            "tls": endpoint.tls,
        }
    if op == "running":
        return session, {"running": bool(await session.running())}
    if op == "shutdown":
        if session is not None:
            await session.aclose()
        return None, {}
    raise RuntimeError(f"unknown op {op!r}")


async def _host_create(msg: dict[str, Any], workspace_root: str | None = None) -> Any:
    from agents.sandbox.sandboxes import UnixLocalSandboxClientOptions

    from omniagents.core.sandbox.unix_local_extended import (
        ExtendedUnixLocalSandboxClient,
    )

    manifest = Manifest.model_validate(msg.get("manifest") or {})
    # The cloud doesn't know this host's paths — root the session at the
    # launcher-provided local workspace dir when given.
    if workspace_root:
        manifest = manifest.model_copy(update={"root": workspace_root}, deep=True)
    exposed = tuple(int(p) for p in (msg.get("exposed_ports") or ()))
    client = ExtendedUnixLocalSandboxClient()
    # snapshot=None → noop: the workspace IS this host's directory; nothing is
    # tarred or uploaded (same rationale as serve's unix_local noop).
    session = await client.create(
        snapshot=None,
        manifest=manifest,
        options=UnixLocalSandboxClientOptions(exposed_ports=exposed),
    )
    await session.start()
    return session


def _pty_payload(update: Any) -> dict[str, Any]:
    return {
        "process_id": update.process_id,
        "output_b64": _b64e(update.output or b""),
        "exit_code": update.exit_code,
        "original_token_count": update.original_token_count,
    }
