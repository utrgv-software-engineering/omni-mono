import os
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from omni_code.projects_runtime import ProjectsRuntime, get_fleet_store_path
from omni_code.tui_server import TuiRpcServer


def _tui_base_dir() -> Path:
    return Path(__file__).resolve().parent / "tui"


def _find_bundle(env_override: Optional[str] = None) -> Optional[Path]:
    if env_override:
        path = Path(env_override).expanduser().resolve()
        if path.exists() and path.is_file():
            return path
    bundle = _tui_base_dir() / "dist" / "index.js"
    if bundle.exists() and bundle.is_file():
        return bundle
    return None


def _build_bundle(debug: bool = False) -> Path:
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise RuntimeError("Node.js and npm are required to build the Omni Code Ink TUI")

    base_dir = _tui_base_dir()
    if not base_dir.exists():
        raise RuntimeError("Omni Code TUI sources not found")

    env = os.environ.copy()
    env.setdefault("NPM_CONFIG_AUDIT", "false")
    env.setdefault("NPM_CONFIG_FUND", "false")

    lockfile = base_dir / "package-lock.json"
    install_cmd = ["npm", "ci"] if lockfile.exists() else ["npm", "install"]
    stdout = None if debug else subprocess.PIPE
    stderr = None if debug else subprocess.PIPE

    try:
        subprocess.run(install_cmd, cwd=str(base_dir), check=True, env=env, text=True, stdout=stdout, stderr=stderr)
        subprocess.run(["npm", "run", "build"], cwd=str(base_dir), check=True, env=env, text=True, stdout=stdout, stderr=stderr)
    except subprocess.CalledProcessError as exc:
        message = f"Failed to build Omni Code Ink TUI (exit code {exc.returncode})"
        if not debug:
            tail = "\n".join(((exc.stdout or "") + "\n" + (exc.stderr or "")).splitlines()[-50:])
            if tail:
                message = f"{message}\n{tail}"
        raise RuntimeError(message) from exc

    bundle = base_dir / "dist" / "index.js"
    if not bundle.exists():
        raise RuntimeError("Omni Code Ink TUI build did not produce dist/index.js")
    return bundle


def launch_projects_tui(*, projects_config_path: Path, debug: bool = False) -> None:
    bundle = _find_bundle(os.environ.get("OMNI_CODE_TUI_PATH"))
    if bundle is None:
        bundle = _build_bundle(debug=debug)

    runtime = ProjectsRuntime(projects_config_path)
    server = TuiRpcServer(runtime)

    # Create pipes for RPC: server_read <- tui_write, tui_read <- server_write
    server_read_fd, tui_write_fd = os.pipe()
    tui_read_fd, server_write_fd = os.pipe()

    env = os.environ.copy()
    if debug:
        env["OMNI_DEBUG"] = "1"

    cmd = [
        "node",
        str(bundle),
        "--rpc-read-fd", str(tui_read_fd),
        "--rpc-write-fd", str(tui_write_fd),
    ]
    if debug:
        cmd.append("--debug")

    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            pass_fds=(tui_read_fd, tui_write_fd),
        )
    except FileNotFoundError as exc:
        for fd in (server_read_fd, server_write_fd, tui_read_fd, tui_write_fd):
            os.close(fd)
        raise RuntimeError("Node.js is required to run the Omni Code Ink TUI") from exc

    # Close the child's ends in the parent
    os.close(tui_read_fd)
    os.close(tui_write_fd)

    # Run the RPC server in a background thread
    import asyncio

    def run_server():
        try:
            loop = asyncio.new_event_loop()
            loop.set_exception_handler(lambda _loop, _ctx: None)
            loop.run_until_complete(server.serve(server_read_fd, server_write_fd))
        except Exception:
            pass

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    proc.wait()
    for fd in (server_read_fd, server_write_fd):
        try:
            os.close(fd)
        except OSError:
            pass
