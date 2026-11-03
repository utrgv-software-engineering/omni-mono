"""Instance-scoped persistent disk and desktop compatibility for QuickSand.

quick-sandbox 0.13 / quicksand-qemu 0.5.12 bundles Ubuntu QEMU 8.2.2 but
only copies accelerator modules and three firmware files. Native desktop mode
also needs its matching virtio display modules and VGA BIOS. Extract only those
files from pinned official packages; never install host packages or change the
SDK installation. Other runtime builds are left untouched.
"""

import hashlib
import io
import os
import socket
import time
import platform
import shutil
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path

_PACKAGES = (
    (
        "q/qemu/qemu-system-common_8.2.2+ds-0ubuntu1.16_amd64.deb",
        "1e0da48efe6c9ef8a6a356aa8c52a64b4ef08eb0c4b6e1a23e0c077c20f05924",
        {
            "hw-display-virtio-gpu.so",
            "hw-display-virtio-gpu-pci.so",
            "hw-display-virtio-vga.so",
        },
        "modules",
    ),
    (
        "s/seabios/seabios_1.16.3-2_all.deb",
        "cac4e59a66c834d19cae751c22ab3a0391c22f78010fd11aa2e3fe630e6bc0e0",
        {"vgabios-virtio.bin"},
        "firmware",
    ),
)


def _data_archive(data):
    """Read the data member of a Debian ar archive without system utilities."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("Invalid runtime package")
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset : offset + 60]
        size = int(header[48:58])
        offset += 60
        if header[:16].strip().rstrip(b"/") == b"data.tar.zst":
            return data[offset : offset + size]
        offset += size + size % 2
    raise ValueError("Runtime package has no data archive")


def desktop_runtime(runtime, cache_root=None):
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        return runtime
    version = subprocess.check_output(
        [str(runtime.qemu_binary), "--version"], text=True
    )
    if "8.2.2+ds-0ubuntu1.16" not in version:
        return runtime
    if (
        runtime.module_dir
        and (runtime.module_dir / "hw-display-virtio-vga.so").exists()
        and runtime.data_dir
        and (runtime.data_dir / "vgabios-virtio.bin").exists()
    ):
        return runtime
    from filelock import FileLock
    import httpx
    import zstandard

    from omniagents.core.paths import get_home_dir

    root = Path(cache_root) if cache_root else get_home_dir() / "runtimes"
    cache = root / "quicksand-desktop-8.2.2-ubuntu1.16-v1"
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(str(cache / "install.lock")):
        if not (cache / "READY").exists():
            for source, destination in (
                (runtime.module_dir, "modules"),
                (runtime.data_dir, "firmware"),
            ):
                if source:
                    shutil.copytree(source, cache / destination, dirs_exist_ok=True)
            for path, checksum, wanted, destination in _PACKAGES:
                response = httpx.get(
                    "https://archive.ubuntu.com/ubuntu/pool/main/" + path,
                    follow_redirects=True,
                    timeout=120,
                )
                response.raise_for_status()
                data = response.content
                if hashlib.sha256(data).hexdigest() != checksum:
                    raise ValueError("QuickSand desktop package checksum mismatch")
                found = set()
                with zstandard.ZstdDecompressor().stream_reader(
                    io.BytesIO(_data_archive(data))
                ) as stream:
                    with tarfile.open(fileobj=stream, mode="r|") as archive:
                        for member in archive:
                            name = Path(member.name).name
                            if name not in wanted or not member.isfile():
                                continue
                            target = cache / destination / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.extractfile(member).read())
                            found.add(name)
                if found != wanted:
                    raise ValueError(
                        "QuickSand desktop package is missing required files"
                    )
            (cache / "READY").write_text("1")
    return replace(runtime, module_dir=cache / "modules", data_dir=cache / "firmware")


def sandbox_class(*, disk_path=None):
    """Keep the compatibility hook scoped to our VM instances, not global SDK state."""
    from quicksand import Sandbox

    class DesktopSandbox(Sandbox):
        def _create_overlay(self):
            if disk_path is None:
                return super()._create_overlay()
            # Only sockets/logs are temporary. The disk is never owned by SDK
            # cleanup and is reused directly, without a new layer per boot.
            target = Path(disk_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                self._overlay_manager._prepare_restored_chain(
                    self._image.chain[1:], self._image.chain[0]
                )
                temporary = target.with_suffix(".partial")
                subprocess.run(
                    [
                        str(self._overlay_manager._qemu_img),
                        "convert",
                        "-O",
                        "qcow2",
                        str(self._image.chain[-1]),
                        str(temporary),
                    ],
                    check=True,
                    capture_output=True,
                )
                self._overlay_manager.resize_overlay(temporary, self.config.disk_size)
                with temporary.open("rb") as disk:
                    os.fsync(disk.fileno())
                temporary.replace(target)
            self._overlay_path = target

        def _build_vm_command(self):
            command = super()._build_vm_command()
            # Docker's default seccomp policy blocks io_uring. Allow deployments
            # to select QEMU's standard threaded I/O without relaxing seccomp.
            if os.getenv("OMNIAGENTS_QUICKSAND_DISK_AIO") == "threads":
                for index, argument in enumerate(command[:-1]):
                    if argument == "-drive":
                        command[index + 1] = command[index + 1].replace(
                            ",aio=io_uring", ",aio=threads"
                        )
            return command

        def _launch_process(self):
            # QEMU's SDK probes a free VNC port and releases it before launching.
            # Factories starting together otherwise both choose display :0.
            path = os.getenv("OMNIAGENTS_QUICKSAND_LAUNCH_LOCK")
            if not path:
                return super()._launch_process()
            from filelock import FileLock

            with FileLock(path, timeout=30):
                super()._launch_process()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        ports = [self._qmp_port]
                        if self.config.enable_display:
                            ports.append(self._vnc_port)
                        for port in ports:
                            with socket.create_connection(
                                ("127.0.0.1", port), timeout=0.1
                            ):
                                pass
                        return
                    except OSError:
                        if not self._process_manager.is_running:
                            return  # The SDK reports the launch error.
                        time.sleep(0.02)

        def _load_runtime(self):
            super()._load_runtime()
            if self.config.enable_display:
                self._runtime_info = desktop_runtime(self._runtime_info)

    return DesktopSandbox
