"""Disposable act workflow VMs. The Docker daemon and all step commands stay inside.

Build a clean runner image once with:
  python -m omniagents_workplace.github.quicksand_space --output /path/to/runner
The saved image contains tools, never job data or host credentials.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import queue
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .spaces import Space


@dataclass(frozen=True)
class QuicksandSettings:
    image: str
    memory: str = "2G"
    cpus: int = 2


class GuestProcess:
    """The small Popen interface consumed by the act adapter, over guest execution."""

    def __init__(self, space, command):
        self._lines = queue.Queue()
        self.stdout = self._output()
        self._future = space._submit(self._execute(space, command))

    async def _execute(self, space, command):
        pending = ""

        def output(chunk):
            nonlocal pending
            pending += chunk
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                self._lines.put(line + "\n")

        try:
            result = await space.vm.execute(
                command, stdin=b"", on_stdout=output, on_stderr=output, timeout=86400
            )
            return result.exit_code
        except asyncio.CancelledError:
            return -9
        except Exception as exc:
            self._lines.put(f"Guest execution failed: {exc}\n")
            return 1
        finally:
            if pending:
                self._lines.put(pending)
            self._lines.put(None)

    def _output(self):
        while True:
            line = self._lines.get()
            if line is None:
                return
            yield line

    def poll(self):
        if not self._future.done():
            return None
        return self.wait()

    def wait(self):
        try:
            return self._future.result()
        except concurrent.futures.CancelledError:
            return -9

    def kill(self):
        self._future.cancel()


class QuicksandSpace(Space):
    root = "/runner"
    describe = "a disposable QuickSand VM with its own Docker Engine"

    def __init__(self, job_id, settings: QuicksandSettings):
        self.settings = settings
        self.vm = None
        self._processes = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def _call(self, coroutine):
        return self._submit(coroutine).result()

    async def _start(self):
        from quicksand import Sandbox, NetworkMode

        # No host mounts, Docker socket, credentials, or inherited host environment.
        self.vm = Sandbox(
            image=self.settings.image,
            memory=self.settings.memory,
            cpus=self.settings.cpus,
            network_mode=NetworkMode.FULL,
        )
        await self.vm.start()
        result = await self.vm.execute(
            "systemctl start docker && docker info >/dev/null", stdin=b"", timeout=120
        )
        if result.exit_code:
            raise RuntimeError(f"Guest Docker daemon failed: {result.stderr[-4000:]}")

    def prepare(self, bare: Path, sha: str):
        self._call(self._start())
        # Read Git objects on the host, but checkout files only in the VM.
        # Repository attributes/filters must not run during a host checkout.
        with tempfile.TemporaryDirectory(prefix="workplace-bundle-") as temporary:
            repository = Path(temporary) / "repo.git"
            bundle = Path(temporary) / "commit.bundle"
            try:
                for command in (
                    ["git", "init", "--bare", "--quiet", str(repository)],
                    [
                        "git",
                        "-C",
                        str(repository),
                        "-c",
                        "core.hooksPath=/dev/null",
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        str(bare),
                        f"{sha}:refs/heads/workflow",
                    ],
                    [
                        "git",
                        "-C",
                        str(repository),
                        "bundle",
                        "create",
                        str(bundle),
                        "refs/heads/workflow",
                    ],
                ):
                    subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                return exc.stderr.strip()

            async def bundle_chunks():
                with bundle.open("rb") as source:
                    # Multiples of three avoid padding between base64 chunks.
                    while chunk := await asyncio.to_thread(source.read, 3 * 65536):
                        yield base64.b64encode(chunk)

            result = self._call(
                self.vm.execute(
                    "mkdir -p /runner && base64 -d > /runner/commit.bundle && "
                    "git clone --quiet /runner/commit.bundle /runner/workspace && "
                    f"git -C /runner/workspace checkout --quiet --detach {shlex.quote(sha)} && "
                    "git -C /runner/workspace remote remove origin && "
                    f"git -C /runner/workspace update-ref refs/heads/workplace-run {shlex.quote(sha)} && "
                    "rm /runner/commit.bundle",
                    stdin=bundle_chunks(),
                )
            )
            return result.stderr if result.exit_code else None

    def base_env(self):
        result = self.run(["env"])
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    def read(self, name):
        return self.run(["cat", self.path(name)]).stdout

    def write(self, name, text):
        result = self._call(
            self.vm.execute(
                f"base64 -d > {shlex.quote(self.path(name))}",
                stdin=base64.b64encode(text.encode()),
            )
        )
        if result.exit_code:
            raise OSError(result.stderr)

    def exists(self, path):
        return self.run(["test", "-e", path]).returncode == 0

    @staticmethod
    def _command(argv, cwd=None, env=None):
        command = (
            shlex.join(["env", "-i", *[f"{k}={v}" for k, v in env.items()], *argv])
            if env is not None
            else shlex.join(argv)
        )
        return f"cd {shlex.quote(cwd)} && exec {command}" if cwd else f"exec {command}"

    def popen(self, argv, cwd, env):
        proc = GuestProcess(self, self._command(argv, cwd, env))
        self._processes.append(proc)
        return proc

    def run(self, argv, env=None):
        result = self._call(
            self.vm.execute(self._command(argv, env=env), stdin=b"", timeout=900)
        )
        return subprocess.CompletedProcess(
            argv, result.exit_code, result.stdout, result.stderr
        )

    def which(self, name, path):
        found = self.run(
            ["sh", "-c", f"command -v {shlex.quote(name)}"],
            {"PATH": path} if path else None,
        )
        return found.stdout.strip() if found.returncode == 0 else None

    def kill(self, proc):
        # QuickSand's cancellable stdin transport terminates the command group.
        # Keep the VM for always()/failure() cleanup steps; close() then removes
        # the entire VM, including detached processes and Docker containers.
        proc.kill()

    def close(self):
        async def shutdown():
            if self.vm:
                await self.vm.stop()

        try:
            for proc in self._processes:
                if proc.poll() is None:
                    proc.kill()
            self._call(shutdown())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()


async def build_image(output: Path, profile: Path | None = None):
    from filelock import AsyncFileLock

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncFileLock(str(output) + ".lock"):
        await _build_image_locked(output, profile)


async def _build_image_locked(output: Path, profile: Path | None):
    from quicksand import Sandbox, NetworkMode

    if output.exists():
        Sandbox.validate_save(output)
        print(f"Using existing clean runner image: {output}", flush=True)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = {}
    if profile:
        import yaml

        raw = yaml.safe_load(profile.read_text()) or {}
    options = raw.get("options") or {}
    image = options.get("image", "ubuntu")
    if image not in {"ubuntu", "ubuntu-desktop", "quicksand-cua"}:
        raise ValueError(
            "The image builder requires ubuntu, ubuntu-desktop, or quicksand-cua"
        )
    from omniagents.core.sandbox.quicksand_runtime import sandbox_class

    Sandbox = sandbox_class()
    async with Sandbox(
        image=image,
        enable_display=options.get("enable_display", False),
        memory="2G",
        cpus=2,
        disk_size="20G",
        network_mode=NetworkMode.FULL,
    ) as vm:
        commands = [
            "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ca-certificates curl python3-venv python-is-python3 ripgrep docker.io docker-compose-v2",
            "curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/node-setup && bash /tmp/node-setup && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs && rm /tmp/node-setup",
            "python3 -m venv /opt/runner-python && /opt/runner-python/bin/pip install --quiet uv pip-audit && ln -s /opt/runner-python/bin/uv /usr/local/bin/uv && ln -s /opt/runner-python/bin/pip-audit /usr/local/bin/pip-audit",
            "npm install -g --silent @playwright/cli@latest && $(npm root -g)/@playwright/cli/node_modules/.bin/playwright install --with-deps chromium",
        ]
        if profile:
            commands = [commands[0], *options.get("setup", [])]
        from .act_space import ACT_INSTALL

        commands.append(ACT_INSTALL)
        for command in commands:
            print("Installing runner tools", flush=True)
            result = await vm.execute(command, stdin=b"", timeout=1200)
            if result.exit_code:
                raise RuntimeError(result.stderr[-4000:])
        # Installed packages suppress daemon startup in this base image. The
        # job starts its own daemon explicitly on every boot, not during saving.
        await vm.save(output.name, workspace=output.parent)
        if image == "ubuntu-desktop":
            from quicksand_ubuntu_desktop import image as desktop_image
            from omniagents.core.sandbox.quicksand import include_parent_overlays

            # Save v6 omits the inherited desktop overlay; retain it just as
            # persistent workspace generations retain their parent layers.
            include_parent_overlays(desktop_image.images_dir, output)
        print(f"Saved clean runner image: {output}", flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        help="Factory profile whose one-time setup is baked into the clean image",
    )
    args = parser.parse_args()
    asyncio.run(build_image(args.output, args.profile))
