"""Docker runtime control for hosted installations and adopted factories."""

import asyncio
import json
import shlex
from pathlib import Path

import httpx
import yaml

from .config import write_env
from .factory_process import STOP_GRACE, FactoryProcess


class DockerFactoryProcess(FactoryProcess):
    def __init__(self, directory, project, installation, record):
        super().__init__(directory, project)
        self.installation = installation
        self.record = record
        self.adopted = bool(record.get("runtime_container"))
        self.name = record.get("runtime_container") or "omni-factory-" + record["id"]
        self.running = False
        self.exit_code = None

    def status(self):
        return {
            "running": self.running,
            "ready": self.ready,
            "exit_code": self.exit_code,
            "error": self.error,
        }

    async def docker(self, *args, check=True):
        p = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await p.communicate()
        if p.returncode and check:
            raise ValueError("Docker runtime operation failed: " + err.decode()[-1000:])
        return p.returncode, out.decode()

    def host_path(self, path):
        return str(
            Path(self.installation.docker_host_data)
            / Path(path).relative_to(self.installation.directory)
        )

    def prepare(self, workplace):
        config = workplace.factory_config.raw()
        if self.adopted:
            workplace.factory_config.write_environment(workplace)
            return config["factory"]
        source = self.project / "sandbox_profile_default.yml"
        profile = yaml.safe_load(source.read_text())
        if (profile.get("client") or {}).get("type") != "docker":
            raise ValueError("Hosted factories require a Docker sandbox profile")
        profile["client"]["network_mode"] = self.installation.docker_network
        target = self.directory / "sandbox.yml"
        target.write_text(yaml.safe_dump(profile))
        config["factory"]["url"] = f"http://{self.name}:8765"
        config["env_file"] = str(self.directory / "runtime.env")
        for key in ("github_webhook", "linear_webhook", "memory_webhook"):
            service = key.removesuffix("_webhook")
            config["app"][
                key
            ] = f"http://{self.name}:8765/integrations/{service}/events"
        workplace.factory_config.path.write_text(yaml.safe_dump(config))
        workplace.factory_config.apply(workplace)
        env = self.environment(
            workplace,
            8765,
            config["factory"]["auth_token"],
            config["factory"]["admin_auth_token"],
            target,
        )
        release = self.release_manifest()
        if release:
            env["FACTORY_VERSION"] = release["revision"]
            env["FACTORY_PROMOTION_MODE"] = "release"
        # Use this image's installed runtime, not the installation host's PATH.
        env.pop("PATH", None)
        write_env(Path(config["env_file"]), env)
        return config["factory"]

    def release_manifest(self):
        path = self.directory / "releases" / "runtime.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def release_in_progress(self):
        path = self.directory / "releases" / "state.json"
        return path.exists() and json.loads(path.read_text()).get("phase") in {
            "activating",
            "recovery_required",
        }

    async def start(self, workplace, record, *_args):
        async with self.lock:
            if self.release_in_progress():
                raise ValueError("A factory release is in progress or needs recovery")
            cfg = self.prepare(workplace)
            release = self.release_manifest()
            code, output = await self.docker("inspect", self.name, check=False)
            if code:
                if self.adopted:
                    raise ValueError("The adopted runtime container no longer exists")
                await self.docker(
                    "create",
                    "--name",
                    self.name,
                    "--restart",
                    "unless-stopped",
                    "--label",
                    "omniagents.factory=" + record["id"],
                    "--network",
                    self.installation.docker_network,
                    "--mount",
                    f"type=bind,src={self.host_path(self.directory)},dst={self.directory}",
                    "--mount",
                    "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
                    "--workdir",
                    str(self.project),
                    "--entrypoint",
                    "/bin/sh",
                    release.get("image") or self.installation.docker_image,
                    "-c",
                    'set -a; . "$1"; exec /factory/.venv/bin/omniagents run --project "$2" --mode server --host 0.0.0.0 --port 8765 --auth-token "$FACTORY_AUTH_TOKEN"',
                    "factory-runtime",
                    str(self.directory / "runtime.env"),
                    release.get("project") or str(self.project / "project.yml"),
                )
            else:
                metadata = json.loads(output)[0]
                if (
                    not self.adopted
                    and metadata.get("Config", {})
                    .get("Labels", {})
                    .get("omniagents.factory")
                    != record["id"]
                ):
                    raise ValueError("Runtime container belongs to another factory")
            await self.docker("start", self.name)
            self.running, self.ready, self.error = True, False, None
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            self.monitor = asyncio.create_task(self.observe(workplace, cfg))

    @staticmethod
    def environment_values(path):
        if not path or not path.exists():
            return {}
        return dict(
            value.split("=", 1)
            for line in path.read_text().splitlines()
            for value in shlex.split(line, comments=True)
            if "=" in value
        )

    async def observe(self, workplace, cfg):
        env_path = workplace.factory_config.env_file
        previous = self.environment_values(env_path)
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            while True:
                try:
                    code, output = await self.docker("inspect", self.name, check=False)
                    state = json.loads(output)[0]["State"] if not code else {}
                    self.running = bool(state.get("Running"))
                    self.exit_code = state.get("ExitCode")
                    self.ready = False
                    if self.running:
                        response = await client.get(
                            cfg["url"] + "/integrations/architecture",
                            headers={"Authorization": "Bearer " + cfg["auth_token"]},
                        )
                        self.ready = response.is_success
                        current = self.environment_values(env_path)
                        if (
                            not self.adopted
                            and not self.release_in_progress()
                            and not response.json().get("release_paused")
                            and current != previous
                            and self.ready
                            and response.json().get("busy") == 0
                        ):
                            await self.docker("restart", "--time", "45", self.name)
                            self.ready = False
                            previous = current
                    elif self.exit_code:
                        self.error = (
                            "Factory process exited; inspect its container logs"
                        )
                except (httpx.HTTPError, ValueError, OSError):
                    self.ready = False
                await asyncio.sleep(3)

    async def stop(self):
        async with self.lock:
            if self.release_in_progress():
                raise ValueError("A factory release is in progress or needs recovery")
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
                self.monitor = None
            # The grace lets the runtime suspend its sandbox VMs; stopping the
            # container ends anything left in it.
            await self.docker("stop", "--time", str(STOP_GRACE), self.name, check=False)
            self.running = self.ready = False
