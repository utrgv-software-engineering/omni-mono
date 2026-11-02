"""act controllers: a private Docker daemon or the existing QuickSand VM."""

import subprocess
import time
from dataclasses import replace

from .spaces import ContainerSettings, DockerSpace

ACT_VERSION = "0.2.92"
RUNNER_IMAGE = "ghcr.io/catthehacker/ubuntu:act-22.04"
JOB_IMAGE = "omni-act-job:ubuntu-22.04-v1"
JOB_DOCKERFILE = (
    "FROM " + RUNNER_IMAGE + "\nRUN npm install -g yarn@1.22.22 && apt-get update -qq\n"
)
# Official release checksums, pinned alongside the version.
ACT_INSTALL = r"""set -eu
mkdir -p /runner/bin
case "$(uname -m)" in
  x86_64) arch=x86_64; sum=c9e13f0eb77948a6d02eb2e06fa7220addfc5b9927ed2a1b35bcdae5441ac7a1;;
  aarch64|arm64) arch=arm64; sum=b604e8f3e5d844e2221caedd2396bd8455dcb10fb3b2f0a43b5d67a65f883e4a;;
  *) echo 'Unsupported act architecture' >&2; exit 1;;
esac
curl --connect-timeout 15 --max-time 180 --retry 3 -fsSL "https://github.com/nektos/act/releases/download/v0.2.92/act_Linux_${arch}.tar.gz" -o /runner/act.tar.gz
printf '%s  /runner/act.tar.gz\n' "$sum" | sha256sum -c -
tar -xzf /runner/act.tar.gz -C /runner/bin act
rm /runner/act.tar.gz
env HOME=/root /runner/bin/act --version
"""


class ActDockerSpace(DockerSpace):
    describe = "act with a private Docker daemon (no host Docker socket)"

    def __init__(self, run_id, settings=None, instance=""):
        settings = settings or ContainerSettings(image=RUNNER_IMAGE)
        # The controller has no factory credentials or host filesystem mounts.
        super().__init__(
            run_id,
            replace(
                settings,
                image="docker:27-dind",
                docker_socket=False,
                volumes=[],
                env={"DOCKER_TLS_CERTDIR": ""},
                extra=["--privileged"],
            ),
            instance,
        )
        self.describe = type(self).describe
        self._network = None

    @classmethod
    def remove_leftovers(cls, docker, instance):
        count = super().remove_leftovers(docker, instance)
        if instance:
            found = subprocess.run(
                [
                    docker,
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    "label=omniagents-workplace.act-network",
                    "--filter",
                    f"label={cls.INSTANCE_LABEL}={instance}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            for network in found.stdout.split() if found.returncode == 0 else []:
                subprocess.run(
                    [docker, "network", "rm", network], capture_output=True, check=False
                )
        return count

    def prepare(self, bare, sha):
        # Service ports published by the inner daemon must not be reachable from
        # another factory's controller on Docker's shared default bridge.
        self._network = self.name + "-net"
        made = self._docker(
            "network",
            "create",
            "--label",
            "omniagents-workplace.act-network",
            "--label",
            f"{self.INSTANCE_LABEL}={self.instance}",
            self._network,
        )
        if made.returncode:
            return "Could not create workflow network: " + made.stderr.strip()
        self.settings = replace(self.settings, network=self._network)
        error = super().prepare(bare, sha)
        if error:
            return error
        setup = self.run(
            [
                "sh",
                "-c",
                "apk add --no-cache bash git curl python3 tar coreutils >/dev/null && nohup dockerd-entrypoint.sh dockerd --host=unix:///var/run/docker.sock >/runner/docker.log 2>&1 </dev/null &",
            ]
        )
        if setup.returncode:
            return "Could not prepare act's Docker controller"
        for _ in range(120):
            if self.run(["docker", "info"]).returncode == 0:
                self._env.update(
                    {
                        "PATH": "/runner/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                        "DOCKER_HOST": "unix:///var/run/docker.sock",
                    }
                )
                return None
            time.sleep(1)
        return (
            "The private workflow Docker daemon did not start: "
            + self.read("docker.log")[-2000:]
        )

    def close(self):
        if self._started:
            self._docker("rm", "--force", "--volumes", self.name)
        if self._network:
            self._docker("network", "rm", self._network)
