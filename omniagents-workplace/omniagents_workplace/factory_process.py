"""One supervised agent process and execution profile per factory instance."""

import asyncio
import os
import socket
import sys
from pathlib import Path

import yaml


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


#: How long a stopping runtime has to suspend its sandbox VMs (flush their
#: disks) before it is killed.
STOP_GRACE = 60


def kill_vms(directory: Path) -> int:
    """Kill QEMU processes still running for this factory's VMs; how many.

    A runtime killed before it suspended its sandboxes can leave their VMs
    running. Their command lines name the factory's VM directory."""
    proc = Path("/proc")
    if not proc.is_dir():
        return 0
    marker = str(directory)
    killed = 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if (
            argv
            and b"qemu" in Path(os.fsdecode(argv[0])).name.encode()
            and any(marker in os.fsdecode(arg) for arg in argv)
        ):
            try:
                os.kill(int(entry.name), 9)
                killed += 1
            except OSError:
                pass
    return killed


def base_environment():
    # No ambient service/model credentials or runtime homes cross factories.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


class FactoryProcess:
    def __init__(self, directory: Path, project: Path):
        self.directory = directory
        self.project = project
        self.process = None
        self.log = None
        self.lock = asyncio.Lock()
        self.error = None
        self.ready = False
        self.monitor = None

    def status(self):
        return {
            "running": self.process is not None and self.process.returncode is None,
            "exit_code": self.process.returncode if self.process else None,
            "ready": self.ready
            and self.process is not None
            and self.process.returncode is None,
            "error": self.error
            or (
                "Factory process exited; inspect runtime.log"
                if self.process and self.process.returncode not in (None, 0, -15)
                else None
            ),
        }

    def environment(self, workplace, port, token, control_token, profile):
        app = workplace.apps.active()[0]
        env = base_environment()
        env.update(workplace.apps.env(app))
        env.update(
            FACTORY_PORT=str(port),
            FACTORY_AUTH_TOKEN=token,
            OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN=control_token,
            HOME=str(self.directory / "home"),
            USERPROFILE=str(self.directory / "home"),
            XDG_CONFIG_HOME=str(self.directory / "config"),
            APPDATA=str(self.directory / "config"),
            XDG_CACHE_HOME=str(self.directory / "cache"),
            XDG_DATA_HOME=str(self.directory / "data"),
            OMNIAGENTS_HOME=str(self.directory / "runtime"),
            OMNIAGENTS_QUICKSAND_LAUNCH_LOCK=str(
                self.directory.parent.parent / "quicksand-launch.lock"
            ),
            OMNIAGENTS_CODEX_HOME=str(self.directory / "credentials" / "codex"),
            CODEX_HOME=str(self.directory / "credentials" / "codex"),
            GH_CONFIG_DIR=str(self.directory / "credentials" / "gh"),
            EVALUATIONS_ARCHIVE=str(self.directory / "evaluations"),
            OMNIAGENTS_SANDBOX_PROFILE=str(profile),
            PYTHONUNBUFFERED="1",
        )
        # Under the strict policy: the runtime's audit, set where the project
        # cannot override it.
        env.update(workplace.governance.runtime_environment(self.directory))
        return env

    def prepare_profile(self, host_port, runner_image=None):
        # The source configuration belongs to this factory, never another one.
        source = self.project / "sandbox_profile_quicksand.yml"
        if not source.is_file():
            source = self.project / "sandbox_profile_default.yml"
        if not source.is_file():
            raise ValueError("The factory project needs a sandbox profile")
        profile = yaml.safe_load(source.read_text()) or {}
        if (profile.get("client") or {}).get("type") != "quicksand":
            raise ValueError("Managed factories currently require a QuickSand profile")
        profile["client"]["state_dir"] = str(self.directory / "vms")
        # The Library is mounted explicitly from this factory by RunEnvironments.
        profile["client"].pop("binds", None)
        options = profile.setdefault("options", {})
        options.pop("mounts", None)
        options["host_services"] = {7300: host_port}
        options["port_forwards"] = {
            port: free_port() for port in options.get("exposed_ports", [])
        }
        if runner_image:
            options["image"] = str(Path(runner_image).resolve())
            options["setup"] = []
        target = self.directory / "sandbox.yml"
        target.write_text(yaml.safe_dump(profile))
        return target

    async def start(self, workplace, record, host_port, runner_image=None):
        async with self.lock:
            if self.status()["running"]:
                return
            self.error = None
            self.ready = False
            if not (self.project / "project.yml").is_file():
                raise ValueError("The factory project has no project.yml")
            import importlib.util

            if importlib.util.find_spec("omniagents") is None:
                raise ValueError(
                    "Install omniagents[sandbox-quicksand] beside Workplace to start agents"
                )
            profile = self.prepare_profile(host_port)
            port = free_port()
            config = workplace.factory_config.raw()
            config["factory"]["url"] = f"http://127.0.0.1:{port}"
            workplace.factory_config.path.write_text(
                yaml.safe_dump(config, sort_keys=False)
            )
            # Reapply destinations to the gateway; only its worker follows
            # the runtime's changing address.
            workplace.factory_config.apply(workplace)
            env = self.environment(
                workplace,
                port,
                config["factory"]["auth_token"],
                config["factory"]["admin_auth_token"],
                profile,
            )
            for path in (
                env["OMNIAGENTS_HOME"],
                env["CODEX_HOME"],
                env["GH_CONFIG_DIR"],
                env["HOME"],
                env["XDG_CONFIG_HOME"],
            ):
                Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.log:
                self.log.close()
            self.log = (self.directory / "runtime.log").open("ab", buffering=0)
            try:
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "omniagents",
                    "run",
                    "--project",
                    str(self.project / "project.yml"),
                    "--mode",
                    "server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--auth-token",
                    config["factory"]["auth_token"],
                    cwd=self.project,
                    env=env,
                    stdout=self.log,
                    stderr=self.log,
                )
            except Exception:
                self.log.close()
                self.log = None
                raise
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            self.monitor = asyncio.create_task(
                self.wait_ready(port, config["factory"]["auth_token"])
            )
            # Catch immediate invocation failures. VM provisioning may take longer.
            await asyncio.sleep(0.2)
            if self.process.returncode is not None:
                self.error = "Factory process exited; inspect runtime.log"

    async def wait_ready(self, port, token):
        import httpx

        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            while self.process and self.process.returncode is None:
                try:
                    response = await client.get(
                        f"http://127.0.0.1:{port}/integrations/architecture",
                        headers={"Authorization": "Bearer " + token},
                    )
                    if response.is_success:
                        self.ready = True
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)

    async def stop(self):
        async with self.lock:
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
                self.monitor = None
            self.ready = False
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), STOP_GRACE)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                    kill_vms(self.directory)
            if self.log:
                self.log.close()
                self.log = None
