"""Run a persistent Coolify stack in Docker or a dedicated QuickSand VM.

python -m omniagents_workplace.deployments.coolify --help
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tarfile
from pathlib import Path

import httpx
import yaml
from filelock import FileLock

VERSION = "4.5.0"
GUEST = Path("/opt/omni-coolify")
TARGET_DOCKERFILE = """FROM docker:28-cli
RUN apk add --no-cache openssh-server bash curl git jq procps
CMD ["sh", "-ec", "mkdir -p /run/sshd /root/.ssh; cp /install/target.pub /root/.ssh/authorized_keys; chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys; ssh-keygen -A; exec /usr/sbin/sshd -D -e -o PasswordAuthentication=no -o PermitRootLogin=prohibit-password"]
"""


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(content)


def prepare(data: Path, backend: str, port: int):
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.chmod(0o700)
    state_path = data / "settings.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if (state["backend"], state["port"]) != (backend, port):
            raise ValueError(
                "This data directory already belongs to a different backend/port"
            )
        return state
    root = GUEST if backend != "docker" else data
    name = "omni-coolify-" + secrets.token_hex(4)
    values = {
        "APP_ID": secrets.token_hex(16),
        "APP_NAME": "Coolify",
        "SESSION_COOKIE": name.replace("-", "_") + "_session",
        "APP_ENV": "production",
        "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "APP_URL": f"http://127.0.0.1:{port}",
        "DB_HOST": "postgres",
        "DB_USERNAME": "coolify",
        "DB_DATABASE": "coolify",
        "DB_PASSWORD": secrets.token_hex(32),
        "REDIS_HOST": "redis",
        "REDIS_PASSWORD": secrets.token_hex(32),
        "PUSHER_APP_ID": secrets.token_hex(16),
        "PUSHER_APP_KEY": secrets.token_hex(32),
        "PUSHER_APP_SECRET": secrets.token_hex(32),
        "PUSHER_BACKEND_PORT": "6001",
        "PUSHER_PORT": str(port + 1),
        "TERMINAL_PORT": str(port + 2),
        "ROOT_USERNAME": "Workplace Admin",
        "ROOT_USER_EMAIL": "admin@example.com",
        "ROOT_USER_PASSWORD": secrets.token_urlsafe(24) + "Aa1!",
        "AUTOUPDATE": "false",
        "SELF_HOSTED": "true",
        "SSH_MUX_ENABLED": "false",
        "BASE_CONFIG_PATH": str(root / "resources"),
        # Finish server configuration before background jobs can touch Docker.
        "HORIZON_ENABLED": "false",
        "SCHEDULER_ENABLED": "false",
    }
    write_private(
        data / ".env",
        "".join(
            f"{k}={json.dumps(v) if any(c.isspace() for c in v) else v}\n"
            for k, v in values.items()
        ),
    )
    # The enclosing host directory is private; the app's UID must read this bind mount.
    (data / ".env").chmod(0o644)
    write_private(
        data / "access.json",
        json.dumps(
            {
                "url": values["APP_URL"],
                "email": values["ROOT_USER_EMAIL"],
                "password": values["ROOT_USER_PASSWORD"],
            },
            indent=2,
        ),
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    write_private(
        data / "target",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode(),
    )
    write_private(
        data / "target.pub",
        key.public_key()
        .public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
        .decode()
        + " "
        + name
        + "\n",
    )
    (data / "Target.Dockerfile").write_text(TARGET_DOCKERFILE)
    (data / ".dockerignore").write_text("*\n!Target.Dockerfile\n")
    resources = ["ssh", "applications", "databases", "services", "backups", "images"]
    bind = "0.0.0.0" if backend != "docker" else "127.0.0.1"
    stack = {
        "name": name,
        "services": {
            "target": {
                "build": {"context": ".", "dockerfile": "Target.Dockerfile"},
                "restart": "unless-stopped",
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    f"{root}:/install:ro",
                    f"{root}/resources:{root}/resources",
                    "target-ssh:/etc/ssh",
                ],
            },
            "coolify": {
                "image": f"coollabsio/coolify:{VERSION}",
                "restart": "unless-stopped",
                "working_dir": "/var/www/html",
                "env_file": [".env"],
                "volumes": [
                    f"{root}/.env:/var/www/html/.env:ro",
                    *[
                        f"{root}/resources/{r}:/var/www/html/storage/app/{r}"
                        for r in resources
                    ],
                ],
                "ports": [
                    f"{bind}:{port}:8080",
                    f"{bind}:{port + 1}:6001",
                    f"{bind}:{port + 2}:6002",
                ],
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                },
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        "curl -fsS http://127.0.0.1:8080/api/health && curl -fsS http://127.0.0.1:6001/up",
                    ],
                    "interval": "5s",
                    "timeout": "5s",
                    "retries": 60,
                    "start_period": "60s",
                },
            },
            "postgres": {
                "image": "postgres:15-alpine",
                "restart": "unless-stopped",
                "environment": {
                    "POSTGRES_USER": "coolify",
                    "POSTGRES_DB": "coolify",
                    "POSTGRES_PASSWORD": "${DB_PASSWORD}",
                },
                "volumes": ["db:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U coolify -d coolify"],
                    "interval": "5s",
                    "timeout": "5s",
                    "retries": 30,
                },
            },
            "redis": {
                "image": "redis:7-alpine",
                "restart": "unless-stopped",
                "command": [
                    "redis-server",
                    "--save",
                    "20",
                    "1",
                    "--requirepass",
                    "${REDIS_PASSWORD}",
                ],
                "volumes": ["redis:/data"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "5s",
                    "timeout": "5s",
                    "retries": 30,
                },
            },
        },
        "volumes": {"db": {}, "redis": {}, "target-ssh": {}},
        "networks": {"default": {"name": name}},
    }
    (data / "compose.yml").write_text(yaml.safe_dump(stack, sort_keys=False))
    state = {"backend": backend, "port": port, "project": name, "version": VERSION}
    write_private(state_path, json.dumps(state, indent=2))
    return state


class Docker:
    def __init__(self, data):
        self.data = data

    async def run(self, argv, stdin=None):
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.data,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate(stdin)
        if process.returncode:
            raise RuntimeError(
                f"Command failed ({argv[0]}): {(err + out).decode()[-3000:]}"
            )
        return out.decode()


class Guest:
    def __init__(self, session):
        self.session = session

    async def run(self, argv, stdin=None):
        command = f"cd {GUEST} && " + shlex.join(argv)
        if stdin is not None:
            command = (
                "printf %s "
                + shlex.quote(base64.b64encode(stdin).decode())
                + " | base64 -d | ("
                + command
                + ")"
            )
        result = await self.session.exec(command, timeout=1800)
        if not result.ok():
            raise RuntimeError(
                (result.stderr + result.stdout).decode(errors="replace")[-3000:]
            )
        return result.stdout.decode()


async def compose(runner, *args):
    return await runner.run(["docker", "compose", "-f", "compose.yml", *args])


# Coolify 4.5.0 regenerates API-configured domains in the deployment job.
# Preserve them, as newer upstream versions do. Keep this compatibility patch
# with the pinned version until upgrading to a release containing that fix.
PREVIEW_DOMAIN_PATCH = r"""
$path='/var/www/html/app/Jobs/ApplicationDeploymentJob.php';
$source=file_get_contents($path);
$updated=$source;
foreach (['generate_preview_fqdn_compose'=>'docker_compose_domains', 'generate_preview_fqdn'=>'fqdn'] as $method=>$field) {
    $old='$this->preview->'.$method.'();';
    $new='if (blank($this->preview->'.$field.')) { '.$old.' }';
    if (str_contains($updated, $new)) { continue; }
    if (substr_count($updated, $old)!==1) { throw new RuntimeException('Unexpected Coolify preview domain implementation'); }
    $updated=str_replace($old, $new, $updated);
}
if ($updated!==$source) {
    if (file_put_contents($path, $updated)===false) { throw new RuntimeException('Cannot preserve Coolify preview domains'); }
    echo 'PATCHED';
}
"""


async def preserve_preview_domains(runner):
    result = await compose(
        runner,
        "exec",
        "-T",
        "--user",
        "0",
        "coolify",
        "php",
        "-r",
        PREVIEW_DOMAIN_PATCH,
    )
    if "PATCHED" in result:
        # Horizon gracefully replaces workers after their current jobs finish.
        await compose(
            runner, "exec", "-T", "coolify", "php", "artisan", "horizon:terminate"
        )


async def start_stack(runner, data, state):
    root = GUEST if state["backend"] != "docker" else data
    print("Preparing Coolify storage and SSH target", flush=True)
    if not (data / "configured").exists():
        await runner.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{root}:/install",
                "alpine:3.21",
                "sh",
                "-ec",
                (
                    "mkdir -p /install/resources/ssh/keys; cp /install/target /install/resources/ssh/keys/id.root@host.docker.internal; "
                    "mkdir -p /install/resources/applications /install/resources/databases /install/resources/services /install/resources/backups /install/resources/images; "
                    "chown -R 9999:9999 /install/resources; chmod 600 /install/resources/ssh/keys/id.root@host.docker.internal"
                ),
            ]
        )
    print("Starting Coolify, PostgreSQL, Redis, and SSH target", flush=True)
    await compose(runner, "up", "-d", "--build", "--wait", "--wait-timeout", "900")
    if not (data / "configured").exists():
        for _ in range(180):
            try:
                seeded = await compose(
                    runner,
                    "exec",
                    "-T",
                    "coolify",
                    "php",
                    "artisan",
                    "tinker",
                    "--execute=echo App\\Models\\Server::find(0) ? 'OMNI_SEEDED' : 'WAIT';",
                )
                if "OMNI_SEEDED" in seeded:
                    break
            except RuntimeError:
                pass
            await asyncio.sleep(5)
        else:
            raise RuntimeError("Coolify database seeding did not complete")
        # The normal upstream seed imports our unique key. Its default host is
        # deliberately unreachable until this setup assigns the SSH container.
        # A local login need not own an Internet mail domain. The upstream
        # root seeder requires DNS; create this generated account explicitly.
        php = "$u=App\\Models\\User::find(0); if (!$u) { $u=(new App\\Models\\User)->forceFill(['id'=>0,'name'=>env('ROOT_USERNAME'),'email'=>env('ROOT_USER_EMAIL'),'password'=>Illuminate\\Support\\Facades\\Hash::make(env('ROOT_USER_PASSWORD'))]); $u->save(); $u->teams()->syncWithoutDetaching([0=>['role'=>'owner']]); } "
        php += "$s=App\\Models\\Server::findOrFail(0); $s->ip='target'; $s->proxy->type='NONE'; $s->save(); $s->settings->is_sentinel_enabled=false; $s->settings->save(); "
        php += (
            "$d=App\\Models\\StandaloneDocker::findOrFail(0); $d->network="
            + json.dumps(state["project"])
            + "; $d->save(); "
        )
        php += (
            "$t=App\\Models\\Team::findOrFail(0); $t->show_boarding=false; $t->save(); "
        )
        php += "$i=App\\Models\\InstanceSettings::findOrFail(0); $i->is_registration_enabled=false; $i->is_auto_update_enabled=false; $i->save();"
        await compose(
            runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute=" + php,
        )
        env = (
            (data / ".env")
            .read_text()
            .replace("HORIZON_ENABLED=false", "HORIZON_ENABLED=true")
            .replace("SCHEDULER_ENABLED=false", "SCHEDULER_ENABLED=true")
        )
        (data / ".env").write_text(env)
        if state["backend"] != "docker":
            await runner.run(["sh", "-c", "cat > .env"], env.encode())
        await compose(
            runner,
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "900",
            "coolify",
        )
    await preserve_preview_domains(runner)
    # Exercise the exact SSH -> Docker path used by Coolify deployments.
    result = await compose(
        runner,
        "exec",
        "-T",
        "coolify",
        "php",
        "artisan",
        "tinker",
        "--execute=echo instant_remote_process(['docker version --format {{.Server.Version}}'], App\\Models\\Server::findOrFail(0));",
    )
    print("Coolify deployment target Docker " + result.strip(), flush=True)
    if state["backend"] == "docker":
        write_private(data / "configured", "yes\n")


async def serve_vm(data, state, *, stopped=None, configure=None, on_ready=None):
    from omniagents.core.sandbox.lifecycle_session import resolve_backend_session
    from omniagents.core.sandbox.quicksand import QuicksandClient, QuicksandOptions

    with FileLock(str(data / "vm.lock"), timeout=0):
        provisioning = not (data / "configured").exists()
        stop = stopped or asyncio.Event()
        loop = asyncio.get_running_loop()
        if stopped is None:
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
        client = QuicksandClient(state_dir=data / "vms")
        state_file = data / "vm.json"
        if state_file.exists():
            saved = client.deserialize_session_state(json.loads(state_file.read_text()))
            if state.get("preview_port"):
                saved.options.port_forwards[80] = state["preview_port"]
            session = await client.resume(saved)
        else:
            session = await client.create(
                options=QuicksandOptions(
                    memory="4G",
                    cpus=2,
                    disk_size="24G",
                    network_mode="full",
                    port_forwards={
                        **{
                            p: p
                            for p in (
                                state["port"],
                                state["port"] + 1,
                                state["port"] + 2,
                            )
                        },
                        **(
                            {80: state["preview_port"]}
                            if state.get("preview_port")
                            else {}
                        ),
                    },
                    host_services=state.get("host_services", {}),
                    save_interval_seconds=0,
                    setup=[
                        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2"
                    ],
                    startup=[
                        "mkdir -p /opt/omni-coolify; systemctl start docker && docker info >/dev/null"
                    ],
                )
            )
        print("Booting persistent QuickSand deployment VM", flush=True)
        await session.start()
        write_private(state_file, session.state.model_dump_json())
        runner = Guest(session)
        try:
            await runner.run(["sh", "-c", "cat > .env"], (data / ".env").read_bytes())
            if provisioning or configure:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                    for name in (
                        ".env",
                        "compose.yml",
                        "Target.Dockerfile",
                        ".dockerignore",
                        "target",
                        "target.pub",
                    ):
                        archive.add(data / name, arcname=name)
                await runner.run(["tar", "xzf", "-"], buffer.getvalue())
            await start_stack(runner, data, state)
            if configure:
                await configure(runner)
            if provisioning:
                # Save the initialized image once. Subsequent boots restore
                # it directly; orderly shutdown saves later changes.
                await compose(runner, "stop")
                await resolve_backend_session(session).persist_durable()
                write_private(data / "configured", "yes\n")
                await compose(runner, "up", "-d", "--wait", "--wait-timeout", "900")
            if on_ready:
                on_ready(runner)
            write_private(data / "ready", "yes\n")
            print(f"Ready: http://127.0.0.1:{state['port']}", flush=True)
            while not stop.is_set() and not (data / "stop").exists():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            (data / "ready").unlink(missing_ok=True)
            print("Stopping Coolify and saving its persistent VM", flush=True)
            await compose(runner, "stop")
            await resolve_backend_session(session).suspend_durable()
            (data / "stop").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "stop", "status", "serve-vm"])
    parser.add_argument("--backend", choices=["docker", "quicksand"], default="docker")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    data = (
        (args.data or Path.home() / ".local/share/omniagents/coolify" / args.backend)
        .expanduser()
        .resolve()
    )
    port = args.port or (7400 if args.backend == "docker" else 7500)
    if not 1024 <= port <= 65533:
        parser.error("port must be between 1024 and 65533")
    if args.action in {"up", "serve-vm"}:
        data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(data.parent / (data.name + ".setup.lock"))):
            state = prepare(data, args.backend, port)
    else:
        state = json.loads((data / "settings.json").read_text())
    if args.action == "serve-vm":
        asyncio.run(serve_vm(data, state))
    elif args.action == "up":
        if state["backend"] == "docker":
            asyncio.run(start_stack(Docker(data), data, state))
        else:
            lock = FileLock(str(data / "vm.lock"))
            try:
                lock.acquire(timeout=0)
            except TimeoutError:
                print("VM is already starting or running; see " + str(data / "vm.log"))
            else:
                (data / "stop").unlink(missing_ok=True)
                (data / "ready").unlink(missing_ok=True)
                with (data / "vm.log").open("a") as log:
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "omniagents_workplace.deployments.coolify",
                            "serve-vm",
                            "--backend",
                            "quicksand",
                            "--data",
                            str(data),
                            "--port",
                            str(port),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                lock.release()
                print("VM starting; progress: " + str(data / "vm.log"))
        print(f"URL: http://127.0.0.1:{port}\nCredentials: {data / 'access.json'}")
    elif args.action == "stop":
        if state["backend"] == "docker":
            asyncio.run(compose(Docker(data), "stop"))
        else:
            write_private(data / "stop", "yes\n")
            print(
                "Stopping and saving the VM; progress: " + str(data / "vm.log"),
                flush=True,
            )
            with FileLock(str(data / "vm.lock"), timeout=900):
                (data / "stop").unlink(missing_ok=True)
            print("VM stopped")
    else:
        marker = "ready" if state["backend"] == "quicksand" else "configured"
        if not (data / marker).exists():
            print("Coolify is stopped or still starting; see the startup log")
            sys.exit(1)
        try:
            response = httpx.get(
                f"http://127.0.0.1:{state['port']}/api/health", timeout=10
            )
            response.raise_for_status()
            print("Coolify healthy")
        except httpx.HTTPError:
            print("Coolify is stopped or still starting")
            sys.exit(1)


if __name__ == "__main__":
    main()
