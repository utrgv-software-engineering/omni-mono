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
    username: str
    password: str


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
        if spec.registry is not None:
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
        ip_address = None
        subnet_ids = None
        if spec.exposed_ports:
            ports = [m.Port(port=p, protocol="TCP") for p in spec.exposed_ports]
            if spec.subnet_id:
                ip_address = m.IpAddress(type=m.ContainerGroupIpAddressType.PRIVATE, ports=ports)
                subnet_ids = [m.ContainerGroupSubnetId(id=spec.subnet_id)]
            else:
                ip_address = m.IpAddress(type=m.ContainerGroupIpAddressType.PUBLIC, ports=ports)

        return m.ContainerGroup(
            location=self._config.location,
            containers=[container],
            os_type=m.OperatingSystemTypes.LINUX,
            restart_policy=m.ContainerGroupRestartPolicy.NEVER,
            image_registry_credentials=registries,
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
        start = f"__OMNS_{tok}__"
        end = f"__OMNE_{tok}__"
        # `echo` emits real newlines, so the payload is delimited by
        # ``<start>\n<base64>\n<end>``; the echoed command line has the marker
        # text followed by ``;`` (no newline), so it can't false-match. The
        # extra bare ``echo`` guarantees a newline between start and end even
        # when the file is empty (``base64`` of empty emits nothing), so the
        # regex still matches with an empty payload.
        await self._ws.send(f"echo {start}; base64 {path} 2>/dev/null; echo; echo {end}\n")
        pat = re.compile(re.escape(start) + r"\r?\n(.*?)\r?\n" + re.escape(end), re.DOTALL)
        buf = ""
        while True:
            buf += await self._recv(timeout)
            mt = pat.search(buf)
            if mt:
                payload = re.sub(r"\s+", "", mt.group(1))
                return base64.b64decode(payload) if payload else b""
