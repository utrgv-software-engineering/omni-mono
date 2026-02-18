import argparse
import os
import shutil
import socket
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, List, Optional

from omni_code._version import __version__
from omni_code.config import get_config_dir


CONTAINER_PORT = 7681


def _run_process(args: List[str], *, check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def _run_local_process(
    args: List[str], *, check: bool = True, env: Optional[dict[str, str]] = None
) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, env=env)


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _sandbox_image_tag() -> str:
    return f"omni-code-sandbox:{__version__}"


def _sandbox_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "sandbox"


def _docker_image_exists(tag: str) -> bool:
    result = _run_process(["docker", "image", "inspect", tag], check=False, capture_output=True)
    return result.returncode == 0


def _docker_volume_exists(name: str) -> bool:
    result = _run_process(["docker", "volume", "inspect", name], check=False, capture_output=True)
    return result.returncode == 0


def _ensure_docker_volume(name: str) -> None:
    if _docker_volume_exists(name):
        return
    _run_process(["docker", "volume", "create", name])


def _parse_persist_volume(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("Expected format: <volume_name>:<container_path>")
    name, container_path = value.split(":", 1)
    name = name.strip()
    container_path = container_path.strip()
    if not name:
        raise argparse.ArgumentTypeError("Volume name cannot be empty")
    if not container_path.startswith("/"):
        raise argparse.ArgumentTypeError("Container path must be absolute")
    return name, container_path


def _build_sandbox_image(tag: str, dockerfile: Optional[str] = None) -> None:
    cmd = ["docker", "build", "--no-cache", "-t", tag]
    if dockerfile:
        cmd.extend(["-f", dockerfile])
    cmd.append(str(_sandbox_assets_dir()))
    _run_process(cmd)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        handle.listen(1)
        return int(handle.getsockname()[1])


def _assert_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            handle.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"Port {port} is already in use")


def _resolve_env_file(explicit_path: Optional[str], workspace: Path) -> Optional[Path]:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Env file not found: {path}")
        return path

    candidate = workspace / ".env"
    if candidate.exists():
        return candidate

    return None


def _apply_env_file_to_process(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        from dotenv import dotenv_values
    except Exception:
        return

    values = dotenv_values(str(path))
    for key, value in values.items():
        if not key:
            continue
        if key in os.environ:
            continue
        if value is None:
            continue
        os.environ[key] = str(value)


def _iter_passthrough_env_keys() -> Iterable[str]:
    return [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "SERPAPI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ]


def _host_uid_gid() -> tuple[int, int]:
    uid = 1000
    gid = 1000
    if hasattr(os, "getuid"):
        uid = int(os.getuid())
    if hasattr(os, "getgid"):
        gid = int(os.getgid())
    return uid, gid


def _resolve_host_omniagents_home(config_dir: Path) -> Path:
    override = os.getenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME")
    if override:
        resolved = Path(override).expanduser().resolve()
    else:
        resolved = (config_dir / "sandbox" / "omniagents").resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _run_sandbox(
    *,
    mode: str,
    workspace: Path,
    host_port: int,
    env_file: Optional[Path],
    auth_token: Optional[str],
    rebuild: bool,
    detach: bool,
    container_name: Optional[str],
    print_url: bool,
    persist_volumes: list[tuple[str, str]],
    image: Optional[str] = None,
    dockerfile: Optional[str] = None,
) -> Optional[str]:
    if not _docker_available():
        raise RuntimeError("Docker not found on PATH. Install Docker Desktop or docker-ce.")

    if image:
        tag = image
    else:
        tag = _sandbox_image_tag()
        if rebuild or not _docker_image_exists(tag):
            _build_sandbox_image(tag, dockerfile=dockerfile)

    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    uid, gid = _host_uid_gid()
    host_omniagents_home = _resolve_host_omniagents_home(config_dir)

    args: List[str] = [
        "docker",
        "run",
        "--rm",
        "-p",
        f"{host_port}:{CONTAINER_PORT}",
        "-v",
        f"{str(workspace)}:/home/user/workspace",
        "-v",
        f"{str(config_dir)}:/home/user/.config/omni_code",
        "-v",
        f"{str(host_omniagents_home)}:/home/user/.omniagents",
        "-w",
        "/home/user/workspace",
        "-e",
        f"LOCAL_UID={uid}",
        "-e",
        f"LOCAL_GID={gid}",
        "-e",
        "HOME=/home/user",
        "-e",
        "OMNIAGENTS_HOME=/home/user/.omniagents",
        "-e",
        "XDG_CONFIG_HOME=/home/user/.config",
    ]

    workspace_project = workspace / "omni_code" / "project.yml"
    container_project_path = (
        "/home/user/workspace/omni_code/project.yml" if workspace_project.exists() else None
    )

    if detach:
        args.insert(3, "-d")

    if container_name:
        args.extend(["--name", container_name])

    if env_file is not None:
        args.extend(["--env-file", str(env_file)])

    for volume_name, container_path in persist_volumes:
        _ensure_docker_volume(volume_name)
        args.extend(["-v", f"{volume_name}:{container_path}"])

    for key in _iter_passthrough_env_keys():
        if os.getenv(key):
            args.extend(["-e", key])

    container_cmd: List[str] = ["omni"]
    if container_project_path:
        container_cmd.extend(["--project", container_project_path])
    container_cmd.extend(
        [
            "--mode",
            mode,
            *(
                ["--auth-token", auth_token]
                if auth_token and mode == "server"
                else []
            ),
            "--host",
            "0.0.0.0",
            "--port",
            str(CONTAINER_PORT),
        ]
    )
    args.extend([tag, *container_cmd])

    url = f"http://localhost:{host_port}"
    if print_url:
        print(url)
    else:
        print(f"Omni Code sandbox: {url}")

    if detach:
        result = _run_process(args, capture_output=True)
        container_id = (result.stdout or "").strip()
        if container_id and not print_url:
            print(f"Container: {container_id}")
        return container_id or None

    _run_process(args)
    return None


def _format_ui_url(ui_host: str, ui_port: int) -> str:
    display_host = ui_host
    if ui_host in {"127.0.0.1", "0.0.0.0"}:
        display_host = "localhost"
    return f"http://{display_host}:{ui_port}"


def _ensure_websockets_unlimited() -> None:
    try:
        import websockets
    except Exception:
        return

    if getattr(websockets, "_omni_code_unlimited", False):
        return

    original_connect = websockets.connect

    def patched_connect(*args, **kwargs):
        kwargs.setdefault("max_size", None)
        return original_connect(*args, **kwargs)

    websockets.connect = patched_connect
    websockets._omni_code_unlimited = True




def _run_local_ui(
    *,
    ws_url: str,
    ui_host: str,
    ui_port: int,
    auth_token: Optional[str],
    auth_token_env: Optional[str],
) -> None:
    from omniagents.core.runtime.runner import Runner

    _ensure_websockets_unlimited()

    previous_token = None
    if auth_token and auth_token_env:
        previous_token = os.environ.get(auth_token_env)
        os.environ[auth_token_env] = str(auth_token)

    try:
        runner = Runner.from_remote(
            url=ws_url,
            name="Omni Code Sandbox",
            welcome_text="Connected to sandboxed agent.",
            token_env_var=auth_token_env,
        )
        web_args = SimpleNamespace(
            host=ui_host,
            port=ui_port,
            debug=False,
            dev=False,
            dev_host=None,
            dev_port=5173,
            ssl_cert=None,
            ssl_key=None,
            approvals=None,
            on_reject=None,
        )
        runner.run(mode="web", args=web_args, auto_open=False)
    finally:
        if auth_token and auth_token_env:
            if previous_token is None:
                os.environ.pop(auth_token_env, None)
            else:
                os.environ[auth_token_env] = previous_token


def _stop_container(container_ref: str) -> None:
    with suppress(Exception):
        _run_process(["docker", "stop", container_ref], check=False, capture_output=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="omni sandbox",
        description="Run Omni Code inside a Docker sandbox",
    )
    parser.add_argument(
        "--mode",
        choices=["web", "server"],
        default="server",
        help="Runner mode inside the container",
    )
    parser.add_argument(
        "--ui",
        choices=["local", "container", "none"],
        default="local",
        help="Where to run the web UI: local (default), container, or none",
    )
    parser.add_argument(
        "--ui-host",
        default="0.0.0.0",
        help="Host interface to bind the local web UI (local UI only)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=0,
        help="Port to bind the local web UI (local UI only; 0 to auto-pick)",
    )
    parser.add_argument(
        "--auth-token",
        dest="auth_token",
        default=None,
        help="Bearer token required for clients connecting to the sandbox server (server mode only)",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Host workspace directory to mount into the sandbox",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7681,
        help="Host port to bind (use 0 to auto-pick)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Run the sandbox in the background and exit",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Optional Docker container name",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print only the URL to stdout",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to an env file to pass to Docker (default: <workspace>/.env if present)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuilding the sandbox image",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Use a custom Docker image instead of the default sandbox image",
    )
    parser.add_argument(
        "--dockerfile",
        default=None,
        help="Path to a custom Dockerfile to build the sandbox image from",
    )

    parser.add_argument(
        "--omniagents-home",
        default=None,
        help=(
            "Host directory to persist OmniAgents state for the sandbox (sessions/traces). "
            "Defaults to <omni config dir>/sandbox/omniagents"
        ),
    )

    parser.add_argument(
        "--persist-volume",
        action="append",
        default=[],
        type=_parse_persist_volume,
        help="Mount a persistent Docker volume at a container path (repeatable): <volume_name>:<container_path>",
    )

    args = parser.parse_args(argv)

    effective_mode = str(args.mode)
    if args.ui == "local":
        effective_mode = "server"
    elif args.ui == "container":
        effective_mode = "web"

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(f"Workspace directory not found: {workspace}")
        sys.exit(1)

    try:
        env_file = _resolve_env_file(args.env_file, workspace)
        _apply_env_file_to_process(env_file)

        from omni_code.models import has_models_configured

        if not has_models_configured():
            raise RuntimeError(
                "No model credentials configured. Set OPENAI_API_KEY (or provide a .env) "
                "or run `omni model setup` to create ~/.config/omni_code/models.json."
            )

        host_port = int(args.port)
        if host_port == 0:
            host_port = _pick_free_port()
        else:
            _assert_port_available(host_port)

        if args.print_url and args.ui == "local":
            raise RuntimeError("--print-url is only supported with --ui container/none")

        docker_detach = bool(args.detach) or args.ui == "local"

        if args.omniagents_home:
            os.environ["OMNI_CODE_SANDBOX_OMNIAGENTS_HOME"] = str(args.omniagents_home)

        container_id = _run_sandbox(
            mode=effective_mode,
            workspace=workspace,
            host_port=host_port,
            env_file=env_file,
            auth_token=str(args.auth_token) if args.auth_token else None,
            rebuild=bool(args.rebuild),
            detach=docker_detach,
            container_name=(str(args.name) if args.name else None),
            print_url=bool(args.print_url),
            persist_volumes=list(args.persist_volume),
            image=str(args.image) if args.image else None,
            dockerfile=str(args.dockerfile) if args.dockerfile else None,
        )

        if args.ui != "local":
            return

        ui_port = int(args.ui_port)
        if ui_port == 0:
            ui_port = _pick_free_port()
        else:
            _assert_port_available(ui_port)

        container_ref = str(args.name) if args.name else (container_id or "")
        cleanup = not bool(args.detach) and bool(container_ref)
        try:
            ws_url = f"ws://localhost:{host_port}/ws"
            print(f"Omni Code web UI: {_format_ui_url(str(args.ui_host), ui_port)}")
            _run_local_ui(
                ws_url=ws_url,
                ui_host=str(args.ui_host),
                ui_port=ui_port,
                auth_token=str(args.auth_token) if args.auth_token else None,
                auth_token_env=("OMNI_SANDBOX_REMOTE_TOKEN" if args.auth_token else None),
            )
        finally:
            if cleanup:
                _stop_container(container_ref)
    except RuntimeError as e:
        print(str(e))
        sys.exit(1)
