"""Where a workflow job runs: this machine, or a container of its own.

A job's space holds its clone of the commit (``workspace/``), the files its
steps report through (``GITHUB_OUTPUT``, ``GITHUB_ENV``, ``GITHUB_PATH``) and
its step scripts, and runs its processes.

- :class:`HostSpace` runs them as the workplace's own user, with this
  machine's tools: for a workplace on your own computer.
- :class:`DockerSpace` runs each job in a throwaway container (an image with
  the toolchains, its own network, limits on memory, CPUs and processes),
  removed when the job ends: for a workplace other people push to, where a
  workflow is code anyone with push access could have written. The
  self-hosted runner is one of these too, made with more: Docker, and what
  an admin gives it (``serve --self-hosted-*``).
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["ContainerSettings", "DockerSpace", "HostSpace", "Space"]


class Space:
    #: Where the job's files are, as its processes see them.
    root: str
    #: The workflow's description of the runner, in its log.
    describe: str = ""

    @property
    def checkout(self) -> str:
        return f"{self.root}/workspace"

    def path(self, name: str) -> str:
        return f"{self.root}/{name}"

    def prepare(self, bare: Path, sha: str) -> Optional[str]:
        """Clone ``bare`` at ``sha`` into the workspace; an error message if that failed."""
        raise NotImplementedError

    def base_env(self) -> Dict[str, str]:
        raise NotImplementedError

    def read(self, name: str) -> str:
        raise NotImplementedError

    def write(self, name: str, text: str) -> None:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def popen(self, argv: List[str], cwd: str, env: Dict[str, str]) -> subprocess.Popen:
        raise NotImplementedError

    def run(
        self, argv: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def which(self, name: str, path: Optional[str]) -> Optional[str]:
        raise NotImplementedError

    def kill(self, proc: subprocess.Popen) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class HostSpace(Space):
    describe = "this machine (container and services are not used here)"

    def __init__(self, job_id: int, *, own_environment: bool = False):
        """``own_environment``: the job runs in the workplace's own Python environment
        (its bin directory first on PATH), as the self-hosted runner on this machine.
        A factory installs the framework beside the workplace, and its jobs run it,
        whatever else this machine's PATH has (a global or older install)."""
        self._work = Path(tempfile.mkdtemp(prefix=f"workplace-job-{job_id}-"))
        self.root = str(self._work)
        self.own_environment = own_environment

    def prepare(self, bare: Path, sha: str) -> Optional[str]:
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--no-checkout", str(bare), self.checkout],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", sha],
                cwd=self.checkout,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            return exc.stderr.strip()
        return None

    def base_env(self) -> Dict[str, str]:
        # Steps start shells without startup files (as GitHub's runner does);
        # BASH_ENV and ENV would run them anyway, and put their PATH back.
        env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV")}
        if self.own_environment:
            import sys

            env["PATH"] = os.pathsep.join(
                [str(Path(sys.executable).parent), env.get("PATH", "")]
            )
            env["VIRTUAL_ENV"] = sys.prefix
        return env

    def read(self, name: str) -> str:
        return Path(self.path(name)).read_text(errors="replace")

    def write(self, name: str, text: str) -> None:
        Path(self.path(name)).write_text(text)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def popen(self, argv: List[str], cwd: str, env: Dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )

    def run(
        self, argv: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, env=env)

    def which(self, name: str, path: Optional[str]) -> Optional[str]:
        return shutil.which(name, path=path)

    def kill(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

    def close(self) -> None:
        shutil.rmtree(self._work, ignore_errors=True)


@dataclass
class ContainerSettings:
    """How a job's container is made (``serve --workflows docker``)."""

    image: str
    #: The Docker network jobs are on: never the one the workplace and the factory share.
    network: str = "bridge"
    memory: str = "2g"
    cpus: str = "2"
    pids: int = 1024
    docker: str = "docker"
    extra: List[str] = field(default_factory=list)
    #: The self-hosted runner's: the jobs start containers of their own on this
    #: machine's Docker, and may reach the containers they start at the job's
    #: name (``OMNIAGENTS_WORKPLACE_HOST``), on ``network``.
    docker_socket: bool = False
    #: ``SRC:DST[:ro]`` volumes, and environment variables, every job gets.
    volumes: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    #: The job log's name for it.
    label: str = "a container of its own"


class DockerSpace(Space):
    root = "/runner"

    #: Every job container's, and the workplace it belongs to.
    LABEL = "omniagents-workplace=job"
    INSTANCE_LABEL = "omniagents-workplace.instance"

    @classmethod
    def remove_leftovers(cls, docker: str, instance: str) -> int:
        """Remove the job containers a previous run of this workplace left; how many."""
        if not instance:
            return 0
        listed = subprocess.run(
            [docker, "ps", "-aq", "--filter", f"label={cls.INSTANCE_LABEL}={instance}"],
            capture_output=True,
            text=True,
        )
        ids = listed.stdout.split() if listed.returncode == 0 else []
        if ids:
            subprocess.run(
                [docker, "rm", "--force", *ids], capture_output=True, text=True
            )
        return len(ids)

    def __init__(self, job_id: int, settings: ContainerSettings, instance: str = ""):
        self.settings = settings
        self.instance = instance
        self.name = f"workplace-job-{job_id}-{uuid.uuid4().hex[:8]}"
        self.describe = (
            f"{settings.label} ({settings.image}); container and services are not used"
        )
        self._env: Dict[str, str] = {}
        self._started = False

    def _docker(
        self, *args: str, input: Optional[str] = None, check: bool = False
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.settings.docker, *args],
            capture_output=True,
            text=True,
            input=input,
            check=check,
        )

    def prepare(self, bare: Path, sha: str) -> Optional[str]:
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="workplace-clone-") as tmp:
            local = Path(tmp) / "workspace"
            try:
                subprocess.run(
                    ["git", "clone", "--quiet", "--no-checkout", str(bare), str(local)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "checkout", "--quiet", "--detach", sha],
                    cwd=local,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                # The clone's origin is the workplace's disk, which the container does not have.
                subprocess.run(
                    ["git", "remote", "remove", "origin"],
                    cwd=local,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                return exc.stderr.strip()
            given: List[str] = []
            for volume in s.volumes:
                given += ["--volume", volume]
            for key, value in s.env.items():
                given += ["--env", f"{key}={value}"]
            if s.docker_socket:
                given += [
                    "--volume",
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "--hostname",
                    self.name,
                    "--env",
                    f"OMNIAGENTS_WORKPLACE_HOST={self.name}",
                ]
            started = self._docker(
                "run",
                "--detach",
                "--name",
                self.name,
                "--network",
                s.network,
                "--memory",
                s.memory,
                "--cpus",
                s.cpus,
                "--pids-limit",
                str(s.pids),
                "--security-opt",
                "no-new-privileges",
                "--label",
                self.LABEL,
                "--label",
                f"{self.INSTANCE_LABEL}={self.instance}",
                *given,
                *s.extra,
                "--entrypoint",
                "sleep",
                s.image,
                "infinity",
            )
            if started.returncode != 0:
                return f"Could not start the job's container: {started.stderr.strip()}"
            self._started = True
            made = self._docker("exec", self.name, "mkdir", "-p", self.root)
            copied = (
                self._docker("cp", f"{local}/.", f"{self.name}:{self.checkout}")
                if made.returncode == 0
                else made
            )
            if copied.returncode != 0:
                return f"Could not copy the commit into the container: {copied.stderr.strip()}"
        listed = self._docker("exec", self.name, "env")
        for line in listed.stdout.splitlines():
            key, _, value = line.partition("=")
            if key and key != "HOSTNAME":
                self._env[key] = value
        return None

    def base_env(self) -> Dict[str, str]:
        return dict(self._env)

    def read(self, name: str) -> str:
        out = self._docker("exec", self.name, "cat", self.path(name))
        return out.stdout if out.returncode == 0 else ""

    def write(self, name: str, text: str) -> None:
        self._docker(
            "exec",
            "-i",
            self.name,
            "sh",
            "-c",
            f"cat > {shlex.quote(self.path(name))}",
            input=text,
            check=True,
        )

    def exists(self, path: str) -> bool:
        return self._docker("exec", self.name, "test", "-e", path).returncode == 0

    def _exec(
        self, argv: List[str], cwd: Optional[str], env: Optional[Dict[str, str]]
    ) -> List[str]:
        cmd = [self.settings.docker, "exec", "-i"]
        if cwd:
            cmd += ["-w", cwd]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        return cmd + [self.name, *argv]

    def popen(self, argv: List[str], cwd: str, env: Dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            self._exec(argv, cwd, env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )

    def run(
        self, argv: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._exec(argv, None, env), capture_output=True, text=True
        )

    def which(self, name: str, path: Optional[str]) -> Optional[str]:
        env = {"PATH": path} if path else None
        out = self.run(["sh", "-c", f"command -v {shlex.quote(name)}"], env)
        found = out.stdout.strip()
        return found if out.returncode == 0 and found else None

    def kill(self, proc: subprocess.Popen) -> None:
        # Everything in the container but its init: the step and what it started.
        self._docker("exec", self.name, "sh", "-c", "kill -9 -1 2>/dev/null; true")
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

    def close(self) -> None:
        if self._started:
            self._docker("rm", "--force", self.name)
