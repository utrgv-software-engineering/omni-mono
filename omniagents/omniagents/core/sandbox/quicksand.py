"""Experimental local QEMU backend with generation-based disk persistence.

No host shell execution. Files and commands go through QuickSand's guest agent.
Interactive PTYs use a guest-side bridge; pipe-based jobs stream output and
accept stdin. Durable close saves disk and stops the VM (processes do not resume).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Literal
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field
from agents.sandbox.manifest import Manifest
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.sandbox_client import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
)
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.session.pty_types import PtyExecUpdate, truncate_text_by_tokens
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.types import ExecResult, ExposedPortEndpoint
from .lifecycle_session import LifecycleSessionClientMixin

logger = logging.getLogger(__name__)


class QuicksandMount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    read_only: bool = False


class QuicksandOptions(BaseSandboxClientOptions):
    model_config = ConfigDict(extra="forbid")
    type: Literal["quicksand"] = "quicksand"
    image: str = "ubuntu"
    enable_display: bool = False
    # CUA's Xvfb display is served by guest x11vnc, not QEMU's VGA display.
    desktop_vnc_port: int | None = Field(default=None, ge=1, le=65535)
    memory: str = "2G"
    cpus: int = Field(default=2, ge=1)
    disk_size: str = "10G"
    exposed_ports: tuple[int, ...] = ()
    # Explicit host ports avoid an allocation race between discovery and QEMU.
    port_forwards: dict[int, int] = Field(default_factory=dict)  # guest -> host
    host_services: dict[int, int] = Field(default_factory=dict)  # guest -> host
    network_mode: Literal["none", "mounts_only", "full"] = "mounts_only"
    mounts: list[QuicksandMount] = Field(default_factory=list)
    setup: list[str] = Field(default_factory=list)
    startup: list[str] = Field(default_factory=list)
    boot_timeout: float = 180
    save_interval_seconds: float = Field(default=60, ge=0)


class QuicksandState(SandboxSessionState):
    type: Literal["quicksand"] = "quicksand"
    # Compatibility identity for the harness; this is a VM identity, not Docker.
    container_id: str
    directory: str
    options: QuicksandOptions


def _atomic_text(path: Path, text: str):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def _generation(directory: Path) -> Path:
    name = (directory / "CURRENT").read_text().strip()
    if not name.startswith("save-") or Path(name).name != name:
        raise ValueError("Invalid QuickSand generation pointer")
    target = directory / name
    if not target.is_dir():
        raise RuntimeError("QuickSand saved disk is missing; refusing a fresh VM")
    return target


def include_parent_overlays(parent: Path, generation: Path):
    """quick-sandbox 0.13 excludes loaded-save layers from subsequent saves.

    Its v6 loader reconstructs the backing chain from numbered overlays.
    Prepend the frozen parent layers so each published generation contains
    the whole accumulated environment, not only changes since the last boot.
    Only touch a new, unpublished generation; never rewrite a live disk.
    """
    metadata = json.loads((generation / "manifest.json").read_text())
    if metadata.get("version") != 6:
        raise RuntimeError("Unsupported QuickSand save format; previous save retained")
    current = generation / "overlays"
    combined = generation / "combined"
    combined.mkdir()

    def layers(path):
        return sorted(path.glob("*.qcow2"), key=lambda p: int(p.stem))

    sources = layers(parent / "overlays") + layers(current)
    for index, source in enumerate(sources):
        shutil.copy2(source, combined / f"{index:08d}.qcow2")
    shutil.rmtree(current)
    combined.rename(current)


def normalize_saved_overlay_order(generation: Path):
    """SDK v6 loads filenames lexically: unpadded 10 otherwise precedes 2.

    Only rename newly saved, frozen layers before publishing the generation.
    The SDK rebases their backing references when loading the saved chain.
    """
    directory = generation / "overlays"
    layers = sorted(directory.glob("*.qcow2"), key=lambda p: int(p.stem))
    for index, source in enumerate(layers):
        target = directory / f"{index:08d}.qcow2"
        if source != target:
            if target.exists():
                raise ValueError("Duplicate QuickSand overlay index")
            source.rename(target)


def compact_saved_overlay_chain(generation: Path, qemu_img, *, max_layers=32):
    """Consolidate an unpublished save, verifying guest-visible bytes first.

    Periodic SDK snapshots append layers. Unbounded chains eventually prevent
    timely guest boots. Keep the package base, including explicit zero clusters,
    and collapse only this generation's private, frozen overlays.
    """
    overlays = generation / "overlays"
    layers = sorted(overlays.glob("*.qcow2"), key=lambda p: int(p.stem))
    if len(layers) <= max_layers:
        return

    def run(*args):
        return subprocess.run(
            [str(qemu_img), *map(str, args)], check=True, capture_output=True, text=True
        )

    info = json.loads(run("info", "--output=json", layers[0]).stdout)
    base = Path(info["full-backing-filename"])
    if not base.is_absolute() or not base.is_file():
        raise RuntimeError("Cannot resolve QuickSand package base for consolidation")
    previous = base
    for layer in layers:
        run("rebase", "-u", "-b", previous, "-F", "qcow2", layer)
        previous = layer
    compact = generation / "compact"
    compact.mkdir()
    output = compact / "00000000.qcow2"
    # Fold private copies bottom-up. Each commit has a shallow backing chain;
    # converting a years-long chain directly makes even zero-block reads costly.
    shutil.copy2(layers[0], output)
    scratch = compact / "step.qcow2"
    for layer in layers[1:]:
        shutil.copy2(layer, scratch)
        run("rebase", "-u", "-b", output, "-F", "qcow2", scratch)
        run("commit", "-q", "-d", scratch)
        scratch.unlink()
    run("compare", "-q", "-f", "qcow2", "-F", "qcow2", layers[-1], output)
    old = generation / "uncompacted"
    overlays.rename(old)
    compact.rename(overlays)
    shutil.rmtree(old)


class QuicksandSession(BaseSandboxSession):
    def __init__(
        self, state: QuicksandState, *, resumed=False, recover_last_save=False
    ):
        self.state = state
        self.resumed = resumed
        self.recover_last_save = recover_last_save
        self.vm = None
        self.runtime_environment_file = None
        self._lock = None
        self._save_lock = asyncio.Lock()
        self._guest_ready = asyncio.Event()
        self._guest_ready.set()
        self._save_task = None
        self._jobs = {}
        self._tty_dirs = {}
        self._stream_consumers = {}
        self._helper_ready = False
        self._loaded_generation = None
        self._set_start_state_preserved(resumed)

    async def _ensure_backend_started(self):
        from filelock import FileLock
        from quicksand import Sandbox, Forward, Host, Guest, NetworkMode, Mount

        directory = Path(self.state.directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self._lock = FileLock(str(directory / "owner.lock"))
        self._lock.acquire(timeout=0)
        try:
            if (directory / "RUNNING").exists() and not self.recover_last_save:
                raise RuntimeError(
                    "QuickSand stopped uncleanly. Latest changes may not be saved. "
                    "Inspect the previous VM before explicitly setting recover_last_save=true."
                )
            options = self.state.options
            image = str(_generation(directory)) if self.resumed else options.image
            if self.resumed or Path(image).is_dir():
                Sandbox.validate_save(image)
                self._loaded_generation = Path(image)
            elif image == "ubuntu-desktop":
                from quicksand_ubuntu_desktop import image as desktop_image

                desktop_image.resolve()
                self._loaded_generation = desktop_image.images_dir
            forwards = [
                Forward(Host(host), Guest(guest))
                for guest, host in options.port_forwards.items()
            ]
            forwards += [
                Forward(Guest(guest), Host(host))
                for guest, host in options.host_services.items()
            ]
            mode = {
                "none": NetworkMode.NONE,
                "mounts_only": NetworkMode.MOUNTS_ONLY,
                "full": NetworkMode.FULL,
            }[options.network_mode]
            _atomic_text(directory / "RUNNING", json.dumps({"pid": os.getpid()}))
            from .quicksand_runtime import sandbox_class

            self.vm = sandbox_class()(
                image=image,
                enable_display=options.enable_display,
                memory=options.memory,
                cpus=options.cpus,
                disk_size=options.disk_size,
                network_mode=mode,
                port_forwards=forwards,
                mounts=[
                    Mount(host=m.source, guest=m.target, readonly=m.read_only)
                    for m in options.mounts
                ],
                boot_timeout=options.boot_timeout,
            )
            await self.vm.start()
            logger.info("QuickSand started with accelerator %s", self.vm.accelerator)
        except BaseException:
            if self.vm is not None:
                await self.vm.stop()
                self.vm = None
                (directory / "RUNNING").unlink(missing_ok=True)
            self._lock.release()
            raise

    async def _prepare_backend_workspace(self):
        if not self.resumed:
            for index, command in enumerate(self.state.options.setup, 1):
                logger.info(
                    "QuickSand provisioning step %s/%s",
                    index,
                    len(self.state.options.setup),
                )
                result = await self.exec(command)
                if not result.ok():
                    raise RuntimeError(
                        "QuickSand setup failed: "
                        + result.stderr.decode(errors="replace")
                    )
            logger.info("QuickSand provisioning complete")
        for command in self.state.options.startup:
            result = await self.exec(command)
            if not result.ok():
                raise RuntimeError(
                    "QuickSand startup failed: "
                    + result.stderr.decode(errors="replace")
                )
        await self._exec_checked_nonzero("mkdir", "-p", self.state.manifest.root)

    async def _after_start(self):
        await self.persist_durable()
        interval = self.state.options.save_interval_seconds
        if interval:

            async def periodic():
                while True:
                    await asyncio.sleep(interval)
                    try:
                        await self.persist_durable()
                    except Exception:
                        logger.exception(
                            "QuickSand periodic save failed; previous generation retained"
                        )

            self._save_task = asyncio.create_task(periodic())

    async def _after_start_failed(self):
        # Do not erase the previous save when provisioning fails.
        await self._stop_vm()

    async def persist_durable(self):
        if self.vm is None or not self.vm.is_running:
            raise RuntimeError("Cannot save a stopped QuickSand VM")
        async with self._save_lock:
            from filelock import AsyncFileLock

            async with AsyncFileLock(str(Path(self.state.directory) / "snapshot.lock")):
                # SDK disk saves and parent-layer copies can continue in worker
                # threads after cancellation. Keep the lock until they finish,
                # before shutdown starts another save or closes the VM.
                saving = asyncio.create_task(self._save_generation())
                try:
                    await asyncio.shield(saving)
                except asyncio.CancelledError:
                    await saving
                    raise

    async def _save_generation(self):
        directory = Path(self.state.directory)
        name = "save-" + uuid.uuid4().hex
        self._guest_ready.clear()
        try:
            await self.vm.save(name, workspace=directory)
        finally:
            self._guest_ready.set()
        if self._loaded_generation is not None:
            await asyncio.to_thread(self._include_parent_overlays, directory / name)
        normalize_saved_overlay_order(directory / name)
        if len(list((directory / name / "overlays").glob("*.qcow2"))) > 32:
            await asyncio.to_thread(
                compact_saved_overlay_chain,
                directory / name,
                self.vm._overlay_manager._qemu_img,
            )
        _atomic_text(directory / name / "omni-state.json", self.state.model_dump_json())
        # QuickSand's overwrite removes the previous save before rename.
        # Unique generations + atomic pointer keep the previous save intact.
        _atomic_text(directory / "CURRENT", name)
        generations = sorted(directory.glob("save-*"), key=lambda p: p.stat().st_mtime)
        for old in generations[:-2]:
            if old.is_dir() and old != self._loaded_generation:
                shutil.rmtree(old)

    def _include_parent_overlays(self, generation):
        include_parent_overlays(self._loaded_generation, generation)

    async def suspend_durable(self):
        await self._cancel_saver()
        await self.pty_terminate_all()
        await self.persist_durable()  # A failed save must prevent destructive close.
        await self._stop_vm()

    async def _cancel_saver(self):
        if self._save_task:
            self._save_task.cancel()
            await asyncio.gather(self._save_task, return_exceptions=True)
            self._save_task = None

    async def _stop_vm(self):
        await self._cancel_saver()
        if self.vm:
            await self.vm.stop()
            self.vm = None
        if self._lock and self._lock.is_locked:
            (Path(self.state.directory) / "RUNNING").unlink(missing_ok=True)
            self._lock.release()

    async def remove_container(self):
        """Harness compatibility hook for explicit destruction of this VM."""
        await self.pty_terminate_all()
        await self._stop_vm()
        from filelock import FileLock

        directory = Path(self.state.directory)
        if not directory.exists():
            return
        with FileLock(str(directory / "owner.lock"), timeout=0):
            if (directory / "RUNNING").exists():
                raise RuntimeError(
                    "Refusing to delete an unclean or active QuickSand environment"
                )
            shutil.rmtree(directory)

    async def _shutdown_backend(self):
        if self.vm and self.vm.is_running:
            await self.suspend_durable()
        else:
            await self._stop_vm()

    async def running(self):
        return self.vm is not None and self.vm.is_running

    async def _command(self, command):
        env = (
            await self.state.manifest.environment.resolve()
            if self.state.manifest.environment
            else {}
        )
        prefix = " ".join(shlex.quote(f"{k}={v}") for k, v in (env or {}).items())
        script = f"export {prefix}; " if prefix else ""
        if self.runtime_environment_file:
            script += f". {shlex.quote(self.runtime_environment_file)}; "
        script += f"exec {shlex.join(str(c) for c in command)}"
        return script

    async def _execute_guest(self, command, *, timeout=3600, **kwargs):
        """Wait out checkpoints; retry only requests explicitly rejected before execution."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("QuickSand guest remained busy with a checkpoint")
            await asyncio.wait_for(self._guest_ready.wait(), timeout=remaining)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("QuickSand checkpoint exceeded the command timeout")
            result = await self.vm.execute(command, timeout=remaining, **kwargs)
            # The guest rejects this request before starting a process or consuming
            # stdin. Never retry a process failure, timeout, or ambiguous disconnect.
            if not (
                result.exit_code == -1
                and result.stderr.strip() == "Exclusive command in progress"
                and not result.stdout
            ):
                return result
            await asyncio.sleep(min(0.05, max(0, deadline - loop.time())))

    async def _exec_internal(self, *command, timeout=None):
        result = await self._execute_guest(
            await self._command(command), timeout=timeout or 3600, stdin=b""
        )
        return ExecResult(
            stdout=result.stdout.encode(),
            stderr=result.stderr.encode(),
            exit_code=result.exit_code,
        )

    async def read(self, path, *, user=None):
        path = await self._validate_path_access(path)
        result = await self.exec(
            "sh",
            "-lc",
            'if [ ! -e "$1" ]; then exit 44; elif [ ! -r "$1" ]; then exit 45; else base64 < "$1"; fi',
            "sh",
            str(path),
            shell=False,
            user=user,
        )
        if result.exit_code == 44:
            raise FileNotFoundError(str(path))
        if result.exit_code == 45:
            raise PermissionError(str(path))
        if not result.ok():
            raise OSError(result.stderr.decode(errors="replace"))
        return io.BytesIO(base64.b64decode(result.stdout))

    async def write(self, path, data, *, user=None):
        path = await self._validate_path_access(path, for_write=True)
        await self._check_write_with_exec(path, user=user)
        raw = data.read()
        encoded = base64.b64encode(raw).decode()
        # stdin keeps arbitrarily large binary data out of argv.
        command = self._prepare_exec_command(
            f"base64 -d > {shlex.quote(str(path))}", shell=True, user=user
        )
        result = await self._execute_guest(
            await self._command(command), stdin=encoded, timeout=3600
        )
        if result.exit_code:
            raise OSError(result.stderr)

    async def persist_workspace(self):
        result = await self.exec(
            f"tar -C {shlex.quote(self.state.manifest.root)} -czf - . | base64"
        )
        if not result.ok():
            raise RuntimeError("Could not archive QuickSand workspace")
        return io.BytesIO(base64.b64decode(result.stdout))

    async def hydrate_workspace(self, data):
        await self._extract_tar_archive(
            archive_path=Path("workspace.tar"),
            destination_root=Path(self.state.manifest.root),
            data=data,
        )

    async def _resolve_exposed_port(self, port):
        host = self.state.options.port_forwards.get(port)
        if host is None:
            raise ValueError(f"QuickSand port {port} has no host mapping")
        return ExposedPortEndpoint(host="127.0.0.1", port=host)

    async def pty_exec_start(
        self,
        *command,
        timeout=None,
        shell=True,
        user=None,
        tty=False,
        yield_time_s=None,
        max_output_tokens=None,
    ):
        if tty:
            return await self._tty_start(
                command, timeout, shell, user, yield_time_s, max_output_tokens
            )
        if len(self._jobs) >= 64:
            raise RuntimeError("Too many QuickSand background commands")
        from agents.sandbox.session.pty_types import allocate_pty_process_id

        key = allocate_pty_process_id(set(self._jobs))
        queue = asyncio.Queue()
        output = bytearray()

        async def stdin():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk

        def emit(text):
            output.extend(text.encode())
            if len(output) > 1024 * 1024:
                del output[: -1024 * 1024]

        prepared = self._prepare_exec_command(*command, shell=shell, user=user)
        task = asyncio.create_task(
            self._execute_guest(
                await self._command(prepared),
                timeout=timeout or 86400,
                stdin=stdin(),
                on_stdout=emit,
                on_stderr=emit,
            )
        )
        self._jobs[key] = (task, queue, output)
        return await self._poll(key, yield_time_s, max_output_tokens)

    async def _poll(self, key, delay, max_tokens):
        task, _, output = self._jobs[key]
        await asyncio.wait(
            {task}, timeout=max(0, min(delay if delay is not None else 1, 30))
        )
        text = output.decode(errors="replace")
        output.clear()
        count = None
        if max_tokens is not None:
            text, count = truncate_text_by_tokens(text, max_tokens)
        if task.done():
            del self._jobs[key]
            result = task.result()
            return PtyExecUpdate(None, text.encode(), result.exit_code, count)
        return PtyExecUpdate(key, text.encode(), None, count)

    async def pty_write_stdin(
        self, *, session_id, chars, yield_time_s=None, max_output_tokens=None
    ):
        if session_id not in self._jobs:
            raise ValueError("Unknown QuickSand process")
        task, queue, output = self._jobs[session_id]
        if session_id in self._tty_dirs:
            if chars:
                await self._tty_control(
                    session_id,
                    {
                        "op": "write",
                        "data": base64.b64encode(
                            chars.encode("utf-8", "surrogateescape")
                        ).decode(),
                    },
                )
            # A terminal stream owns output while attached; don't wait for
            # the long-lived shell to exit on every keypress.
            return await self._poll(
                session_id,
                0 if self._stream_consumers.get(session_id) else yield_time_s,
                max_output_tokens,
            )
        if chars == "\x03":
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            del self._jobs[session_id]
            return PtyExecUpdate(None, bytes(output), 130, None)
        if chars:
            await queue.put(None if chars == "\x04" else chars.encode())
        return await self._poll(session_id, yield_time_s, max_output_tokens)

    async def pty_terminate_all(self):
        for key in list(self._tty_dirs):
            try:
                await self._tty_control(key, {"op": "terminate"})
            except (OSError, RuntimeError):
                pass
        tasks = [job[0] for job in self._jobs.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._tty_dirs.clear()

    def supports_pty(self):
        return True

    async def _tty_request(self, directory, payload, operation="request"):
        helper = f"/tmp/.omni-pty-{self.state.session_id}.py"
        if not self._helper_ready:
            source = Path(__file__).with_name("quicksand_pty_guest.py").read_bytes()
            result = await self._execute_guest(
                f"umask 077; cat > {shlex.quote(helper)}",
                stdin=source,
            )
            if result.exit_code:
                raise RuntimeError("Cannot install QuickSand PTY helper")
            self._helper_ready = True
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        result = await self._execute_guest(
            shlex.join(["python3", helper, operation, directory, encoded]),
            stdin=b"",
            timeout=20,
        )
        if result.exit_code:
            raise RuntimeError("QuickSand PTY bridge failed: " + result.stderr)
        return json.loads(result.stdout)

    async def _tty_control(self, key, payload):
        return await self._tty_request(self._tty_dirs[key], payload)

    async def _tty_start(self, command, timeout, shell, user, delay, max_tokens):
        from agents.sandbox.session.pty_types import allocate_pty_process_id

        if len(self._jobs) >= 64:
            raise RuntimeError("Too many QuickSand background commands")
        key = allocate_pty_process_id(set(self._jobs))
        directory = f"/tmp/omni-pty-{uuid.uuid4().hex}"
        prepared = self._prepare_exec_command(*command, shell=shell, user=user)
        script = "export TERM=xterm-256color; " + await self._command(prepared)
        await self._tty_request(
            directory, {"command": script, "timeout": timeout or 86400}, "start"
        )
        self._tty_dirs[key] = directory
        output = bytearray()

        async def pump():
            try:
                while True:
                    result = await self._tty_request(directory, {"op": "read"})
                    chunk = base64.b64decode(result["output"])
                    if chunk:
                        consumers = self._stream_consumers.get(key, [])
                        if consumers:
                            for queue in consumers:
                                await queue.put(chunk)
                        else:
                            output.extend(chunk)
                            if len(output) > 1024 * 1024:
                                del output[: -1024 * 1024]
                    if result["exit_code"] is not None:
                        return SimpleNamespace(exit_code=result["exit_code"])
                    await asyncio.sleep(0.05)
            finally:
                self._tty_dirs.pop(key, None)
                for queue in self._stream_consumers.get(key, []):
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)

        task = asyncio.create_task(pump())
        self._jobs[key] = (task, None, output)
        return await self._poll(key, delay, max_tokens)

    async def pty_resize(self, process_id, *, cols, rows):
        if process_id in self._tty_dirs:
            await self._tty_control(
                process_id, {"op": "resize", "cols": cols, "rows": rows}
            )

    async def pty_stream_output(self, process_id):
        job = self._jobs.get(process_id)
        if job is None:
            return
        task, _, output = job
        queue = asyncio.Queue(maxsize=128)
        if output:
            queue.put_nowait(bytes(output))
            output.clear()
        consumers = self._stream_consumers.setdefault(process_id, [])
        consumers.append(queue)
        if task.done():
            queue.put_nowait(None)
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            consumers.remove(queue)
            if not consumers:
                self._stream_consumers.pop(process_id, None)
                if task.done():
                    self._jobs.pop(process_id, None)


class QuicksandClient(LifecycleSessionClientMixin, BaseSandboxClient):
    backend_id = "quicksand"
    supports_default_options = True

    def __init__(self, *, state_dir=None, recover_last_save=False, binds=None):
        from omniagents.core.paths import get_home_dir

        self.directory = (
            Path(state_dir or get_home_dir() / "quicksand").expanduser().resolve()
        )
        self.recover_last_save = recover_last_save
        self.binds = [QuicksandMount.model_validate(b) for b in (binds or [])]
        for mount in self.binds:
            if not Path(mount.source).is_absolute() or not Path(mount.source).is_dir():
                raise ValueError(
                    "QuickSand mount source must be an existing absolute directory"
                )
            if not mount.target.startswith("/") or ".." in Path(mount.target).parts:
                raise ValueError(
                    "QuickSand mount target must be an absolute guest path"
                )

    async def create(self, *, snapshot=None, manifest=None, options=None):
        if snapshot is not None and not isinstance(snapshot, NoopSnapshot):
            raise ValueError(
                "QuickSand uses full disk saves; workspace snapshots are not supported"
            )
        options = options or QuicksandOptions()
        if self.binds:
            options = options.model_copy(update={"mounts": self.binds})
        missing = set(options.exposed_ports) - options.port_forwards.keys()
        if options.desktop_vnc_port:
            if options.enable_display:
                raise ValueError(
                    "Choose either a native display or a guest VNC desktop"
                )
            missing |= {options.desktop_vnc_port} - options.port_forwards.keys()
        if missing:
            raise ValueError(
                f"QuickSand exposed ports need explicit port_forwards: {sorted(missing)}"
            )
        identity = uuid.uuid4()
        state = QuicksandState(
            session_id=identity,
            container_id=f"quicksand:{identity}",
            directory=str(self.directory / str(identity)),
            options=options,
            snapshot=NoopSnapshot(id=str(identity)),
            manifest=manifest or Manifest(root="/workspace"),
            exposed_ports=options.exposed_ports,
        )
        return self._wrap_session(QuicksandSession(state))

    async def resume(self, state):
        if not isinstance(state, QuicksandState):
            raise TypeError("Cannot resume another backend as QuickSand")
        directory = Path(state.directory).resolve()
        if directory.parent != self.directory or directory.name != str(
            state.session_id
        ):
            raise ValueError(
                "QuickSand state does not belong to the configured state directory"
            )
        saved = self.deserialize_session_state(
            json.loads((_generation(directory) / "omni-state.json").read_text())
        )
        if (
            saved.container_id != state.container_id
            or saved.session_id != state.session_id
            or saved.directory != state.directory
        ):
            raise ValueError("QuickSand saved identity mismatch")
        if self.binds:
            saved.options = saved.options.model_copy(update={"mounts": self.binds})
        return self._wrap_session(
            QuicksandSession(
                saved, resumed=True, recover_last_save=self.recover_last_save
            )
        )

    async def delete(self, session):
        from .lifecycle_session import resolve_backend_session

        await resolve_backend_session(session).remove_container()
        return session

    def deserialize_session_state(self, payload):
        return QuicksandState.model_validate(payload)
