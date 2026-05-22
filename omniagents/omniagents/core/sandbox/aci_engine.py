"""Core Azure Container Instances (ACI) engine: container-group lifecycle plus
an exec-WebSocket control channel.

Deliberately free of any ``agents`` SDK imports so it can be unit-tested with a
fake WebSocket and live-tested against a real subscription on its own. The SDK
``SandboxSession``/``SandboxClient`` adapter (``aci.py``) sits on top of this.

Why an exec channel instead of a Docker daemon: serverless ACI exposes no
``dockerd`` — only an exec endpoint that hands back a WebSocket to a PTY (the
same channel ``az container exec`` uses). So the agent drives the container the
way a person at a terminal would, and we reconstruct structured results
(exit codes, separated stdout/stderr, binary-safe file contents) from the raw
PTY byte stream using sentinels. Proven against real ACI before this was built.
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import websockets
from azure.identity.aio import DefaultAzureCredential
from azure.mgmt.containerinstance import models as m
from azure.mgmt.containerinstance.aio import ContainerInstanceManagementClient


# --------------------------------------------------------------------------- #
# Config / specs
# --------------------------------------------------------------------------- #


@dataclass
class AciConfig:
    subscription_id: str
    resource_group: str
    location: str = "southcentralus"


@dataclass
class AciFileShare:
    """Azure Files share mounted into the container — the durable workspace, so
    the container itself stays disposable."""

    account_name: str
    account_key: str
    share_name: str
    mount_path: str


@dataclass
class AciRegistry:
    server: str
    # Admin user/password OR a user-assigned managed-identity resource id. When
    # `identity` is set the group pulls via that MI (AcrPull) — no shared admin
    # password — and username/password are ignored.
    username: Optional[str] = None
    password: Optional[str] = None
    identity: Optional[str] = None


@dataclass
class AciContainerSpec:
    name: str
    image: str
    cpu: float = 2.0
    memory_gb: float = 4.0
    env: dict[str, str] = field(default_factory=dict)
    # Keep the container alive; the agent does its work via exec, not this PID 1.
    command: list[str] = field(default_factory=lambda: ["tail", "-f", "/dev/null"])
    exposed_ports: tuple[int, ...] = ()
    file_share: Optional[AciFileShare] = None
    registry: Optional[AciRegistry] = None
    # When set, the group joins this delegated subnet and gets a *private* IP
    # (reachable only inside the VNet — e.g. by a VNet-integrated launcher).
    # When unset, exposed ports are published on a *public* IP.
    subnet_id: Optional[str] = None


@dataclass
class ExecOutcome:
    """Backend-neutral exec result. The SDK adapter maps this to
    ``agents.sandbox.types.ExecResult``."""

    exit_code: int
    stdout: bytes
    stderr: bytes


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


class AciManager:
    """Container-group lifecycle + exec-endpoint minting via ARM."""

    def __init__(self, config: AciConfig, credential: Optional[object] = None) -> None:
        self._config = config
        self._credential = credential or DefaultAzureCredential()
        self._client = ContainerInstanceManagementClient(self._credential, config.subscription_id)

    def _build_group(self, spec: AciContainerSpec) -> m.ContainerGroup:
        env = [m.EnvironmentVariable(name=k, value=v) for k, v in spec.env.items()]
        volumes: list[m.Volume] = []
        mounts: list[m.VolumeMount] = []
        if spec.file_share is not None:
            fs = spec.file_share
            volumes.append(
                m.Volume(
                    name="workspace",
                    azure_file=m.AzureFileVolume(
                        share_name=fs.share_name,
                        storage_account_name=fs.account_name,
                        storage_account_key=fs.account_key,
                    ),
                )
            )
            mounts.append(m.VolumeMount(name="workspace", mount_path=fs.mount_path))

        container = m.Container(
            name=spec.name,
            image=spec.image,
            command=spec.command,
            environment_variables=env,
            resources=m.ResourceRequirements(
                requests=m.ResourceRequests(cpu=spec.cpu, memory_in_gb=spec.memory_gb)
            ),
            volume_mounts=mounts or None,
            ports=[m.ContainerPort(port=p) for p in spec.exposed_ports] or None,
        )

        registries = None
        group_identity = None
        if spec.registry is not None:
            if spec.registry.identity:
                # Pull via the user-assigned managed identity (AcrPull) — no
                # admin password. The MI is assigned to the group and named in
                # the registry credential.
                registries = [
                    m.ImageRegistryCredential(
                        server=spec.registry.server, identity=spec.registry.identity
                    )
                ]
                group_identity = m.ContainerGroupIdentity(
                    type=m.ResourceIdentityType.USER_ASSIGNED,
                    user_assigned_identities={spec.registry.identity: {}},
                )
            else:
                registries = [
                    m.ImageRegistryCredential(
                        server=spec.registry.server,
                        username=spec.registry.username,
                        password=spec.registry.password,
                    )
                ]

        # Group-level IP exposure. Container-level ``ports`` alone don't make a
        # service reachable — the group needs an ``ip_address`` listing them.
        # A subnet_id ⇒ private IP inside the VNet (no public surface); otherwise
        # a public IP. ACI exec runs over the ARM management plane either way, so
        # a private group is still fully drivable.
        # A subnet_id makes the group join that VNet subnet (private IP), whether
        # or not it exposes ports — so a no-service sandbox can still reach
        # private endpoints (Storage/ACR) inside the VNet. ip_address is only set
        # when ports are exposed: Private (in-VNet) or Public (no subnet).
        subnet_ids = [m.ContainerGroupSubnetId(id=spec.subnet_id)] if spec.subnet_id else None
        ip_address = None
        if spec.exposed_ports:
            ports = [m.Port(port=p, protocol="TCP") for p in spec.exposed_ports]
            ip_type = (
                m.ContainerGroupIpAddressType.PRIVATE
                if spec.subnet_id
                else m.ContainerGroupIpAddressType.PUBLIC
            )
            ip_address = m.IpAddress(type=ip_type, ports=ports)

        return m.ContainerGroup(
            location=self._config.location,
            containers=[container],
            os_type=m.OperatingSystemTypes.LINUX,
            restart_policy=m.ContainerGroupRestartPolicy.NEVER,
            image_registry_credentials=registries,
            identity=group_identity,
            volumes=volumes or None,
            ip_address=ip_address,
            subnet_ids=subnet_ids,
        )

    async def create(self, spec: AciContainerSpec) -> None:
        poller = await self._client.container_groups.begin_create_or_update(
            self._config.resource_group, spec.name, self._build_group(spec)
        )
        await poller.result()

    async def get_state(self, name: str) -> str:
        group = await self._client.container_groups.get(self._config.resource_group, name)
        iv = getattr(group, "instance_view", None)
        return (getattr(iv, "state", None) or "Unknown") if iv else "Unknown"

    async def get_ip(self, name: str) -> str:
        """The group's IP — private (VNet) or public, depending on how it was
        created. Empty string if the group has no exposed ports / IP yet."""
        group = await self._client.container_groups.get(self._config.resource_group, name)
        ip = getattr(group, "ip_address", None)
        return (getattr(ip, "ip", None) or "") if ip else ""

    async def wait_running(self, name: str, timeout: float = 240.0, interval: float = 3.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            state = await self.get_state(name)
            if state == "Running":
                return
            if state in ("Failed", "Terminated"):
                raise RuntimeError(f"ACI container {name} entered state {state}")
            if loop.time() >= deadline:
                raise TimeoutError(f"ACI container {name} not Running after {timeout}s (state={state})")
            await asyncio.sleep(interval)

    async def delete(self, name: str) -> None:
        poller = await self._client.container_groups.begin_delete(self._config.resource_group, name)
        await poller.result()

    async def open_exec(
        self, group: str, container: str, *, command: str = "/bin/sh", rows: int = 24, cols: int = 120
    ) -> tuple[str, str]:
        """Mint an exec WebSocket. Returns ``(web_socket_uri, password)``."""
        resp = await self._client.containers.execute_command(
            self._config.resource_group,
            group,
            container,
            m.ContainerExecRequest(
                command=command,
                terminal_size=m.ContainerExecRequestTerminalSize(rows=rows, cols=cols),
            ),
        )
        return resp.web_socket_uri, resp.password

    async def close(self) -> None:
        await self._client.close()
        cred_close = getattr(self._credential, "close", None)
        if cred_close is not None:
            await cred_close()


# --------------------------------------------------------------------------- #
# Exec channel (one WebSocket == one PTY)
# --------------------------------------------------------------------------- #

# File bytes per read block. The ACI exec terminal has no output flow control,
# so a base64 burst can drop bytes — *intermittently*, even at a few KB. We keep
# each block small (base64 ~2.7 KB) AND verify every block against its true byte
# length, retrying on any drop. Smaller block ⇒ fewer drops ⇒ fewer retries.
_READ_CHUNK_BYTES = 2048
# Per-block retries before giving up — drops are intermittent, so a re-read
# almost always succeeds.
_READ_CHUNK_RETRIES = 8


def _rand_token() -> str:
    return uuid.uuid4().hex


class AciExecChannel:
    """A single exec WebSocket into a container. Two modes of use:

    * **control shell** — :meth:`run` executes a command and reconstructs a
      structured :class:`ExecOutcome` (exit code + separated, binary-safe
      stdout/stderr) from the PTY stream using sentinels. Calls are serialized.
    * **raw PTY** — :meth:`write` / :meth:`iter_output` for interactive
      terminals (the SDK's ``pty_*`` surface opens one channel per PTY).
    """

    def __init__(self, ws: object) -> None:
        self._ws = ws
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        manager: AciManager,
        group: str,
        container: str,
        *,
        command: str = "/bin/sh",
        drain_banner: bool = True,
    ) -> "AciExecChannel":
        uri, password = await manager.open_exec(group, container, command=command)
        ws = await websockets.connect(uri, max_size=None, open_timeout=30)
        await ws.send(password)  # ACI exec: the first frame must be the password.
        chan = cls(ws)
        if drain_banner:
            await chan._drain(0.5)  # discard the initial banner/prompt (control shell)
        # PTY channels keep the banner/prompt — it's part of the terminal output.
        return chan

    # -- raw PTY surface ---------------------------------------------------- #

    async def write(self, data: str) -> None:
        await self._ws.send(data)

    async def iter_output(self) -> AsyncIterator[bytes]:
        async for frame in self._ws:
            yield frame if isinstance(frame, (bytes, bytearray)) else frame.encode("utf-8", "replace")

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass

    # -- control-shell surface ---------------------------------------------- #

    async def run(self, cmd: str, *, timeout: float = 60.0) -> ExecOutcome:
        """Run *cmd* in the persistent shell. stdout/stderr are redirected to
        temp files (so the exit-code sentinel isn't polluted), then read back
        base64-encoded (binary-safe, and immune to PTY echo)."""
        async with self._lock:
            tok = _rand_token()
            out_f = f"/tmp/.omni_o_{tok}"
            err_f = f"/tmp/.omni_e_{tok}"
            exit_marker = f"__OMNIX_{tok}__"
            # Real number after the marker on the executed line; the echoed
            # input line carries a literal "$?", so the regex matches only output.
            await self._ws.send(f'{{ {cmd} ; }} >{out_f} 2>{err_f}; printf "{exit_marker}%d\\n" "$?"\n')
            exit_code = await self._read_exit(exit_marker, timeout)
            stdout = await self._read_file_b64(out_f, tok, timeout)
            stderr = await self._read_file_b64(err_f, tok, timeout)
            await self._ws.send(f"rm -f {out_f} {err_f}\n")  # best-effort cleanup
            return ExecOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    async def _recv(self, timeout: float) -> str:
        frame = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        return frame if isinstance(frame, str) else frame.decode("utf-8", "replace")

    async def _drain(self, window: float) -> None:
        try:
            while True:
                await asyncio.wait_for(self._ws.recv(), timeout=window)
        except (asyncio.TimeoutError, Exception):
            return

    async def _read_exit(self, marker: str, timeout: float) -> int:
        pat = re.compile(re.escape(marker) + r"(\d+)")
        buf = ""
        while True:
            buf += await self._recv(timeout)
            mt = pat.search(buf)
            if mt:
                return int(mt.group(1))

    async def _read_file_b64(self, path: str, tok: str, timeout: float) -> bytes:
        # The ACI exec PTY has no output flow control: a base64 burst beyond a
        # few KB overruns the terminal buffer and silently drops bytes (verified
        # — ≤4 KB is exact, ≥16 KB always lossy). So read the file in bounded
        # blocks whose base64 burst stays in the proven-safe range, looping until
        # a short/empty block signals EOF (no size query → small reads stay one
        # round trip). dd ``skip``/``count`` are in block units, so each block is
        # ``[i*CHUNK, (i+1)*CHUNK)`` and decodes independently (CHUNK is a whole
        # number of bytes, so per-block base64 padding never corrupts the join).
        out = bytearray()
        block = 0
        while True:
            chunk = await self._read_chunk_b64(path, block, tok, timeout)
            out += chunk
            if len(chunk) < _READ_CHUNK_BYTES:
                break  # short (or empty) block ⇒ end of file
            block += 1
        return bytes(out)

    async def _read_chunk_b64(self, path: str, block: int, tok: str, timeout: float) -> bytes:
        # Emit the block's base64 AND its true byte length, then verify the
        # decode matches — a dropped burst yields a bad length / invalid base64
        # and is retried (a fresh attempt marker keeps stale output from
        # false-matching). ``dd`` runs twice (once for data, once for the count);
        # the count is tiny so it never overruns the PTY.
        dd = f"dd if={path} bs={_READ_CHUNK_BYTES} skip={block} count=1 2>/dev/null"
        last_err: Exception | None = None
        for attempt in range(_READ_CHUNK_RETRIES):
            s = f"__OMNS_{tok}_{block}_{attempt}__"
            e = f"__OMNE_{tok}_{block}_{attempt}__"
            z = f"__OMNZ_{tok}_{block}_{attempt}__"
            # `echo` emits real newlines, so payload is ``<s>\n<base64>\n<e>``;
            # the echoed command keeps markers on one line, so it can't
            # false-match. The bare ``echo`` guarantees a newline even for an
            # empty block. ``<e>\n<len>\n<z>`` carries the verification length.
            await self._ws.send(f"echo {s}; {dd} | base64; echo; echo {e}; {dd} | wc -c; echo {z}\n")
            pat = re.compile(
                re.escape(s) + r"\r?\n(.*?)\r?\n" + re.escape(e) + r"\r?\n\s*(\d+)\s*\r?\n" + re.escape(z),
                re.DOTALL,
            )
            buf = ""
            while True:
                buf += await self._recv(timeout)
                mt = pat.search(buf)
                if not mt:
                    continue
                expected = int(mt.group(2))
                payload = re.sub(r"\s+", "", mt.group(1))
                try:
                    data = base64.b64decode(payload) if payload else b""
                except ValueError as ex:  # binascii.Error subclasses ValueError
                    last_err = ex
                    break
                if len(data) == expected:
                    return data
                last_err = ValueError(f"block {block}: got {len(data)} bytes, expected {expected}")
                break
        raise OSError(f"exec read: block {block} failed after {_READ_CHUNK_RETRIES} attempts ({last_err})")


# --------------------------------------------------------------------------- #
# Azure Files fast path (workspace IO without the exec PTY)
# --------------------------------------------------------------------------- #


class AciFilesChannel:
    """Read/write the workspace Azure Files share directly from the host.

    The launcher holds the storage key and the container mounts the same share
    at ``mount_path``, so a file written here over the Files REST API appears
    inside the container instantly — no base64-over-PTY, no size limit, no
    per-block round trips. Used for paths under the mount; off-mount paths still
    go through the exec channel.
    """

    def __init__(self, file_share: AciFileShare) -> None:
        self._fs = file_share
        self._svc: object | None = None  # lazy ShareServiceClient (aio)

    def _share(self):
        if self._svc is None:
            from azure.storage.fileshare.aio import ShareServiceClient

            self._svc = ShareServiceClient(
                f"https://{self._fs.account_name}.file.core.windows.net",
                credential=self._fs.account_key,
            )
        return self._svc.get_share_client(self._fs.share_name)  # type: ignore[union-attr]

    def rel_path(self, abs_path: str) -> Optional[str]:
        """Map a container absolute path to a share-relative path, or None when
        it's not under the mount (caller falls back to the exec channel)."""
        mount = self._fs.mount_path.rstrip("/")
        if abs_path == mount:
            return ""
        prefix = mount + "/"
        if abs_path.startswith(prefix):
            return abs_path[len(prefix):]
        return None

    async def _ensure_dirs(self, share, rel_dir: str) -> None:
        # Azure Files has no ``mkdir -p`` — create each level, ignoring the
        # 409 when it already exists.
        if not rel_dir:
            return
        cur = ""
        for part in rel_dir.split("/"):
            if not part:
                continue
            cur = f"{cur}/{part}" if cur else part
            try:
                await share.get_directory_client(cur).create_directory()
            except Exception:
                pass

    async def write(self, rel_path: str, data: bytes) -> None:
        share = self._share()
        parent = "/".join(rel_path.split("/")[:-1])
        await self._ensure_dirs(share, parent)
        fc = share.get_file_client(rel_path)
        await fc.upload_file(data)

    async def read(self, rel_path: str) -> bytes:
        share = self._share()
        stream = await share.get_file_client(rel_path).download_file()
        return await stream.readall()

    async def hydrate(self, root_rel: str, tar_bytes: bytes) -> None:
        """Extract a tar.gz onto the share under ``root_rel`` (regular files +
        directories; other member types are skipped)."""
        import io
        import tarfile

        share = self._share()
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            for m in tar.getmembers():
                name = m.name.lstrip("./")
                if not name:
                    continue
                rel = f"{root_rel}/{name}" if root_rel else name
                if m.isdir():
                    await self._ensure_dirs(share, rel)
                elif m.isfile():
                    await self._ensure_dirs(share, "/".join(rel.split("/")[:-1]))
                    f = tar.extractfile(m)
                    await share.get_file_client(rel).upload_file(f.read() if f else b"")

    async def close(self) -> None:
        if self._svc is not None:
            await self._svc.close()  # type: ignore[union-attr]
            self._svc = None
