"""Persistent personal sandbox maintenance. Reset/restore require a stopped factory."""

from __future__ import annotations

import argparse
from contextlib import closing
import asyncio
import fcntl
import gzip
import json
import os
import re
import shlex
import shutil
import time
import uuid
from pathlib import Path


async def refresh_environment(session, github, *, run_as=None):
    """Refresh managed auth for every new shell/PTY without replacing the container."""
    from omniagents.core.sandbox.lifecycle_session import resolve_backend_session

    inner = resolve_backend_session(session)
    inner.runtime_environment_file = None
    token = github.resolved_token() or ""
    values = {
        "GIT_CONFIG_COUNT": "0",
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
        "GITHUB_MCP_TOKEN": token,
        **github.git_environment(),
    }
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", github.token_env):
        values[github.token_env] = token
    script = (
        "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values.items())
        + "\n"
    )
    path = "/tmp/.omni-factory-environment"
    command = f"umask 077; printf '%s' {shlex.quote(script)} > {path}.tmp && chmod 600 {path}.tmp"
    if run_as:
        command += f" && chown {shlex.quote(run_as)} {path}.tmp"
    command += f" && mv {path}.tmp {path}"
    result = await session.exec(command, shell=True)
    if result.exit_code:
        raise RuntimeError("Could not refresh persistent sandbox credentials")
    inner.runtime_environment_file = path
    # Remove the previous managed GitHub login before the normal harness installs
    # the current credential. This also revokes local use after disconnect.
    result = await session.exec(
        "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential reject; "
        "if command -v gh >/dev/null 2>&1; then GH_TOKEN= GITHUB_TOKEN= gh auth logout -h github.com </dev/null >/dev/null 2>&1 || true; fi",
        shell=True,
        user=run_as,
    )
    if result.exit_code:
        raise RuntimeError("Could not clear the previous sandbox GitHub credential")
    await asyncio.to_thread(
        inner._container.update, restart_policy={"Name": "unless-stopped"}
    )


def read_state(path):
    value = json.loads(Path(path).read_text())
    if (
        not value.get("container_id")
        or value["session_state"]["container_id"] != value["container_id"]
    ):
        raise ValueError("Invalid personal sandbox state")
    return value


def write_state(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out)
    temporary.chmod(0o600)
    temporary.replace(path)


def backup(state_path, destination, *, keep=3):
    """Save the entire writable filesystem/image; bind mounts have their own backup."""
    import docker

    state_path, destination = Path(state_path), Path(destination)
    record = read_state(state_path)
    target = destination / (
        time.strftime("personal-%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6]
    )
    partial = target.with_suffix(".partial")
    partial.mkdir(parents=True, mode=0o700)
    image = None
    try:
        with closing(docker.from_env()) as client:
            container = client.containers.get(record["container_id"])
            if (
                container.labels.get("com.omni.session-id")
                != record["session_state"]["session_id"]
            ):
                raise RuntimeError("Sandbox ownership does not match its saved state")
            image = container.commit(pause=True)
            with gzip.open(partial / "image.tar.gz", "wb", compresslevel=1) as out:
                for chunk in image.save():
                    out.write(chunk)
            write_state(partial / "state.json", record)
            metadata = {
                "image": image.id,
                "network": container.attrs["HostConfig"]["NetworkMode"],
                "mounts": container.attrs["Mounts"],
                "ports": container.attrs["HostConfig"]["PortBindings"],
            }
            write_state(partial / "container.json", metadata)
            client.images.remove(image.id)
            image = None
        partial.rename(target)
        for old in sorted(
            p
            for p in destination.glob("personal-*")
            if p.is_dir() and p.suffix != ".partial"
        )[:-keep]:
            shutil.rmtree(old)
        return target
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        if image:
            with closing(docker.from_env()) as client:
                client.images.remove(image.id)
        raise


def maintenance_lock(state_path):
    path = Path(state_path)
    architecture = path.with_name(
        path.name.removesuffix(".personal-sandbox.json") + ".architecture"
    )
    handle = architecture.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit(
            "Stop the factory before adopting, resetting, or restoring its workspace"
        ) from None
    return handle


async def adopt(state_path, container_id, project_path):
    """One-time migration of an existing resident container, without altering files."""
    import docker
    import yaml
    from agents.sandbox.sandboxes.docker import DockerSandboxSessionState
    from agents.sandbox.snapshot import NoopSnapshot
    from omniagents.core.runtime.serve_harness import _build_sandbox
    from omniagents.core.sandbox.profiles import (
        resolve_profile,
        product_default_profile_path,
        _manifest_env_values,
    )
    from .environment import parse_github

    if Path(state_path).exists():
        raise SystemExit("A personal workspace is already registered")
    project_path = Path(project_path)
    from omniagents.core.project.runtime import load_project

    load_project(str(project_path))
    github = parse_github(yaml.safe_load(project_path.read_text()).get("integrations"))
    profile = resolve_profile(
        workspace_dir=project_path.parent,
        default_profile_path=product_default_profile_path(project_path),
    )
    values = _manifest_env_values(profile.raw)
    if values is not None and github:
        values.update(github.git_environment())
    _, options, manifest = _build_sandbox(
        profile, sources=github.sources() if github else []
    )
    with closing(docker.from_env()) as client:
        container = client.containers.get(container_id)
        session_id = container.labels.get("com.omni.session-id")
        if not session_id:
            raise SystemExit("Container is not an OmniAgents sandbox")
        entries = {}
        for name, entry in manifest.entries.items():
            dest = str(Path(manifest.root) / name)
            if container.exec_run(["test", "-d", dest]).exit_code == 0:
                expected = (
                    next(
                        (
                            f"https://github.com/{repo}"
                            for repo in github.repositories
                            if repo.rsplit("/", 1)[-1] == name
                        ),
                        None,
                    )
                    if github
                    else None
                )
                origin = container.exec_run(
                    ["git", "-C", dest, "remote", "get-url", "origin"]
                )
                if expected and (
                    origin.exit_code
                    or origin.output.decode().strip().removesuffix(".git") != expected
                ):
                    raise RuntimeError(
                        f"Existing directory {name!r} does not match its selected repository"
                    )
                entries[name] = entry
        manifest = manifest.model_copy(update={"entries": entries})
        state = DockerSandboxSessionState(
            session_id=uuid.UUID(session_id),
            manifest=manifest,
            snapshot=NoopSnapshot(id=session_id),
            image=container.attrs["Config"]["Image"],
            container_id=container.id,
            exposed_ports=tuple(
                int(port.split("/")[0])
                for port in container.attrs["NetworkSettings"]["Ports"]
            ),
            workspace_root_ready=True,
        )
        write_state(
            state_path,
            {
                "version": 1,
                "backend": "docker",
                "container_id": container.id,
                "session_state": state.model_dump(mode="json"),
            },
        )
        container.update(restart_policy={"Name": "unless-stopped"})
        return container.id


def restore(state_path, source):
    import docker
    from docker.types import Mount

    source = Path(source)
    record = read_state(source / "state.json")
    metadata = json.loads((source / "container.json").read_text())
    if Path(state_path).exists():
        raise SystemExit("Reset the current workspace before restoring another")
    with closing(docker.from_env()) as client:
        with (source / "image.tar.gz").open("rb") as data:
            images = client.images.load(data)
        image = next(image for image in images if image.id == metadata["image"])
        mounts = [
            Mount(
                target=m["Destination"],
                source=m.get("Name") or m["Source"],
                type=m["Type"],
                read_only=not m["RW"],
            )
            for m in metadata["mounts"]
        ]
        ports = {key: ("127.0.0.1", None) for key in metadata["ports"]}
        container = client.containers.create(
            image.id,
            entrypoint=["tail"],
            command=["-f", "/dev/null"],
            detach=True,
            network=metadata["network"],
            mounts=mounts,
            ports=ports,
            labels={
                "com.omni.omni-code": "true",
                "com.omni.session-id": record["session_state"]["session_id"],
            },
            restart_policy={"Name": "unless-stopped"},
        )
        record["container_id"] = record["session_state"]["container_id"] = container.id
        record["session_state"]["image"] = image.id
        write_state(state_path, record)
        container.start()
        return container.id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["status", "backup", "reset", "restore", "adopt"]
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--to", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--container")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    if args.action == "status":
        print(read_state(args.state)["container_id"])
    elif args.action == "backup":
        if not args.to or args.keep < 1:
            parser.error("backup requires --to and --keep >= 1")
        print(backup(args.state, args.to, keep=args.keep))
    else:
        with maintenance_lock(args.state):
            if args.action == "adopt":
                if not args.container or not args.project:
                    parser.error("adopt requires --container and --project")
                print(asyncio.run(adopt(args.state, args.container, args.project)))
            elif args.action == "restore":
                if not args.source:
                    parser.error("restore requires --source")
                print(restore(args.state, args.source))
            else:
                if not args.confirm_reset or not args.to:
                    parser.error(
                        "reset requires --confirm-reset and --to for the mandatory backup"
                    )
                saved = backup(args.state, args.to, keep=args.keep)
                import docker

                with closing(docker.from_env()) as client:
                    client.containers.get(
                        read_state(args.state)["container_id"]
                    ).remove(force=True)
                args.state.unlink()
                print(
                    f"Workspace reset. Backup: {saved}. Start the factory to create a fresh workspace."
                )


if __name__ == "__main__":
    main()
