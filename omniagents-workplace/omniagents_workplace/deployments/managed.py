"""One independently persistent deployment environment per factory."""

import asyncio
import base64
import json
import os
import logging
import secrets
import socket
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

from .coolify import (
    GUEST,
    Docker,
    compose,
    prepare,
    serve_vm,
    start_stack,
    write_private,
)
from .identity import DeploymentIdentity


def php(value):
    # PHP data literals must not interpolate dollar signs or user input.
    encoded = base64.b64encode(json.dumps(value).encode()).decode()
    return f"json_decode(base64_decode('{encoded}'), true)"


# Out of memory, the kernel kills deployments' processes (builds, previews)
# before the factory's own: an agent's computer, the Workplace. Everything in
# the daemon descends from these processes and inherits the score.
EXPENDABLE = (
    "for p in /proc/[0-9]*; do echo 500 > $p/oom_score_adj; done 2>/dev/null; true"
)


class Engine:
    def __init__(self, host, name):
        self.host, self.name = host, name

    async def run(self, argv, stdin=None):
        return await self.host.run(
            ["docker", "exec", "-i", "-w", str(GUEST), self.name, *argv], stdin
        )


class Deployments:
    def __init__(self, installation):
        self.installation = installation
        self.identity = DeploymentIdentity(installation)
        self.tasks, self.stops, self.runners, self.errors = {}, {}, {}, {}
        self.previews, self.preview_errors = {}, {}
        self.mcp_tokens, self.mcp_locks = {}, {}

    def directory(self, factory_id):
        return (
            self.installation.directory
            / "factories"
            / factory_id
            / "deployments"
            / "coolify"
        )

    def url(self, factory_id):
        base = urlsplit(self.installation.base_url)
        hostname = base.hostname
        if hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
            hostname = "localhost"
        host = "coolify-" + factory_id + "." + hostname
        if base.port:
            host += ":" + str(base.port)
        return urlunsplit((base.scheme, host, "", "", ""))

    def preview_url(self, key):
        base = urlsplit(self.installation.base_url)
        hostname = base.hostname
        if hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
            hostname = "localhost"
        host = "preview-" + key + "." + hostname
        if base.port:
            host += ":" + str(base.port)
        return urlunsplit((base.scheme, host, "", "", ""))

    def preview_host(self, host):
        for _, record in self.installation.store.docs("deployment.preview"):
            config = (self.installation.records.get(record["factory"]) or {}).get(
                "deployment"
            ) or {}
            if (
                config.get("enabled")
                and record["state"] in {"pending", "queued", "error"}
                and host in record.get("hosts", [])
            ):
                return record["factory"]
        return None

    def backchannel(self, factory_id):
        port = urlsplit(self.installation.internal_url).port or 80
        return f"http://coolify-{factory_id}.internal:{port}"

    def backend(self):
        return self.installation.deployment_backend or (
            "docker" if self.installation.docker_image else "quicksand"
        )

    def view(self, factory_id):
        config = self.installation.records[factory_id].get("deployment") or {}
        task = self.tasks.get(factory_id)
        return {
            "enabled": bool(config.get("enabled")),
            "backend": config.get("backend", self.backend()),
            "running": bool(task and not task.done()),
            "ready": factory_id in self.runners,
            "error": self.errors.get(factory_id),
            "url": self.url(factory_id),
            "preview_error": self.preview_errors.get(factory_id),
            "previews": [
                {
                    k: r.get(k)
                    for k in (
                        "repository",
                        "number",
                        "state",
                        "build_status",
                        "urls",
                        "error",
                    )
                }
                for _, r in self.installation.store.docs("deployment.preview")
                if r["factory"] == factory_id and r["state"] != "closed"
            ],
        }

    def configure(self, factory_id, enabled):
        i = self.installation
        with i.lock:
            record = i.records[factory_id]
            config = record.get("deployment")
            if config is None:
                used = {
                    p
                    for r in i.records.values()
                    for p in range(
                        (r.get("deployment") or {}).get("port", 0),
                        (r.get("deployment") or {}).get("port", 0) + 3,
                    )
                }
                for _ in range(100):
                    port = secrets.randbelow(12000) * 3 + 20000
                    if any(port + n in used for n in range(3)):
                        continue
                    sockets = []
                    try:
                        for n in range(3):
                            sock = socket.socket()
                            sockets.append(sock)
                            sock.bind(("127.0.0.1", port + n))
                        break
                    except OSError:
                        continue
                    finally:
                        for sock in sockets:
                            sock.close()
                else:
                    raise ValueError("No deployment ports available")
                config = {
                    "backend": self.backend(),
                    "port": port,
                    "secret": secrets.token_urlsafe(32),
                }
            if not config.get("preview_port"):
                used = {
                    p
                    for r in i.records.values()
                    for p in (
                        *range(
                            (r.get("deployment") or {}).get("port", 0),
                            (r.get("deployment") or {}).get("port", 0) + 3,
                        ),
                        (r.get("deployment") or {}).get("preview_port", 0),
                    )
                }
                while True:
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1", 0))
                        port = listener.getsockname()[1]
                    if port not in used:
                        break
                config = {**config, "preview_port": port}
            i.save({**record, "deployment": {**config, "enabled": enabled}})

    async def start(self, factory_id):
        if factory_id in self.tasks and not self.tasks[factory_id].done():
            return
        self.configure(factory_id, True)
        self.errors.pop(factory_id, None)
        self.stops[factory_id] = asyncio.Event()
        self.tasks[factory_id] = asyncio.create_task(self.run(factory_id))

    async def stop(self, factory_id):
        if factory_id in self.stops:
            self.stops[factory_id].set()
        if factory_id in self.tasks:
            await self.tasks[factory_id]

    async def close(self):
        for stop in self.stops.values():
            stop.set()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    def prepare(self, factory_id):
        i = self.installation
        config = i.records[factory_id]["deployment"]
        data = self.directory(factory_id)
        state = prepare(
            data,
            "docker-vm" if config["backend"] == "docker" else "quicksand",
            config["port"],
        )
        state["preview_port"] = config["preview_port"]
        public = urlsplit(self.url(factory_id))
        state["host_services"] = {
            urlsplit(i.internal_url).port or 80: urlsplit(i.internal_url).port or 80
        }
        values = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in (data / ".env").read_text().splitlines()
            if "=" in line
        }
        values["SESSION_COOKIE"] = values["SESSION_COOKIE"].removeprefix("__Host-")
        if public.scheme == "https":
            values["SESSION_COOKIE"] = "__Host-" + values["SESSION_COOKIE"]
        values["SESSION_SECURE_COOKIE"] = (
            "true" if public.scheme == "https" else "false"
        )
        values.update(
            APP_URL=self.url(factory_id),
            PUSHER_PORT=str(public.port or (443 if public.scheme == "https" else 80)),
            TERMINAL_PORT=str(public.port or (443 if public.scheme == "https" else 80)),
            TERMINAL_HOST=public.hostname,
            TERMINAL_PROTOCOL="wss" if public.scheme == "https" else "ws",
        )
        (data / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        stack = yaml.safe_load((data / "compose.yml").read_text())
        if config["backend"] == "quicksand":
            address = "10.0.2.100"
        elif i.docker_network:
            address = socket.gethostbyname(urlsplit(i.internal_url).hostname)
        else:
            from ..cli import docker_bridge_gateway

            address = docker_bridge_gateway()
            if not address:
                raise ValueError("Docker host gateway is unavailable")
        stack["services"]["coolify"]["extra_hosts"] = [
            f"coolify-{factory_id}.internal:{address}"
        ]
        (data / "compose.yml").write_text(yaml.safe_dump(stack, sort_keys=False))
        return data, state

    async def configure_login(self, factory_id, runner):
        config = self.installation.records[factory_id]["deployment"]
        base = self.url(factory_id)
        settings = {
            "provider": "authentik",
            "client_id": factory_id,
            "client_secret": config["secret"],
            "base_url": self.backchannel(factory_id) + "/workplace-auth",
            "redirect_uri": base + "/auth/authentik/callback",
            "custom_label": "Workplace",
            "enabled": True,
        }
        await compose(
            runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute="
            + "$v="
            + php(settings)
            + "; App\\Models\\OauthSetting::updateOrCreate(['provider'=>'authentik'],$v);",
        )

    async def provision_user(self, factory_id, account):
        runner = self.runners.get(factory_id)
        if not runner:
            raise ValueError("Coolify is still starting")
        data = {
            "id": account.id,
            "name": account.name or account.username,
            "email": account.id + "@workplace.invalid",
            "issuer": self.backchannel(factory_id) + "/workplace-auth",
        }
        code = (
            "$v="
            + php(data)
            + "; $key=['provider'=>'authentik','issuer'=>$v['issuer'],'provider_user_id'=>$v['id']]; "
        )
        code += "$identity=App\\Models\\OauthIdentity::where($key)->first(); if ($identity) { $u=$identity->user; } else { $u=new App\\Models\\User; $u->forceFill(['name'=>$v['name'],'email'=>$v['email'],'password'=>null]); $u->save(); App\\Models\\OauthIdentity::create(array_merge($key,['user_id'=>$u->id,'email'=>$v['email'],'raw_claims'=>[]])); } "
        code += "$u->forceFill(['name'=>$v['name']])->save(); $u->teams()->syncWithoutDetaching([0=>['role'=>'admin']]); $u->teams()->where('teams.id','!=',0)->get()->each(function($t)use($u){$u->teams()->detach($t->id);});"
        await compose(
            runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute=" + code,
        )

    async def refresh_agent_mcp(self, factory_id):
        # Retire live MCP sets without interrupting runs holding those sets.
        # The next turn reconnects, including a previously unavailable Coolify.
        from ..factory_models import FactoryRpc

        try:
            w = self.installation.instances[factory_id][0]
            cfg = w.factory_config.load()["factory"]
            process = self.installation.processes.get(factory_id)
            if process and process.status()["running"]:
                await FactoryRpc(
                    cfg["url"], cfg.get("admin_auth_token") or cfg["auth_token"]
                ).call("mcp_reload_server", {})
        except Exception:
            logging.getLogger(__name__).warning(
                "Factory %s MCP refresh unavailable; new sessions will connect normally",
                factory_id,
            )

    async def run(self, factory_id):
        runner = host = None
        owned = False
        preview_task = None
        name = "omni-deploy-" + factory_id
        try:
            data, state = self.prepare(factory_id)
            config = self.installation.records[factory_id]["deployment"]

            async def configure(r):
                await self.configure_login(factory_id, r)

            def ready(r):
                nonlocal preview_task
                from .previews import Previews

                self.runners[factory_id] = r
                bridge = Previews(self, factory_id, r)
                self.previews[factory_id] = bridge
                asyncio.create_task(self.refresh_agent_mcp(factory_id))
                preview_task = asyncio.create_task(bridge.run(self.stops[factory_id]))

            if config["backend"] == "quicksand":
                await serve_vm(
                    data,
                    state,
                    stopped=self.stops[factory_id],
                    configure=configure,
                    on_ready=ready,
                )
                return
            host = Docker(data)
            i = self.installation
            source = (
                str(Path(i.docker_host_data) / data.relative_to(i.directory))
                if i.docker_host_data
                else str(data)
            )
            try:
                existing = json.loads(await host.run(["docker", "inspect", name]))[0]
                if (existing.get("Config", {}).get("Labels") or {}).get(
                    "omniagents.deployment"
                ) != factory_id:
                    raise ValueError("Deployment container belongs to another owner")
                if existing.get("Config", {}).get("Entrypoint") == ["dockerd"] or (
                    not i.docker_network
                    and not (
                        existing.get("HostConfig", {}).get("PortBindings") or {}
                    ).get("80/tcp")
                ):
                    # Only replace our launcher, retaining its bind-mounted engine and data.
                    await host.run(["docker", "stop", "--time", "60", name])
                    await host.run(["docker", "rm", name])
                    raise RuntimeError("Recreate owned daemon with preview forwarding")
            except RuntimeError:
                argv = [
                    "docker",
                    "create",
                    "--name",
                    name,
                    "--label",
                    "omniagents.deployment=" + factory_id,
                    "--privileged",
                    "--restart",
                    "unless-stopped",
                    "--mount",
                    f"type=bind,src={source},dst={GUEST}",
                    "--mount",
                    f"type=bind,src={source}/engine,dst=/var/lib/docker",
                ]
                (data / "engine").mkdir(exist_ok=True)
                # The engine pulls images through the factory's egress proxy.
                proxy = i.instances[factory_id][0].egress.runtime_environment([])
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
                    if key in proxy:
                        argv += ["--env", f"{key}={proxy[key]}"]
                if i.docker_network:
                    argv += ["--network", i.docker_network]
                else:
                    argv += ["-p", f"127.0.0.1:{config['preview_port']}:80"]
                    for n in range(3):
                        argv += [
                            "-p",
                            f"127.0.0.1:{state['port'] + n}:{state['port'] + n}",
                        ]
                argv += [
                    "docker:28-dind",
                    "dockerd",
                    "--host=unix:///var/run/docker.sock",
                    "--bip=172.29.0.1/16",
                    "--default-address-pool=base=172.28.0.0/16,size=24",
                ]
                await host.run(argv)
                proxy_network = os.environ.get("WORKPLACE_EGRESS_NETWORK")
                if proxy and proxy_network:
                    await host.run(
                        ["docker", "network", "connect", proxy_network, name]
                    )
            else:
                if (existing.get("Config", {}).get("Labels") or {}).get(
                    "omniagents.deployment"
                ) != factory_id:
                    raise ValueError("Deployment container belongs to another owner")
            owned = True
            await host.run(["docker", "start", name])
            runner = Engine(host, name)
            for _ in range(90):
                try:
                    await runner.run(["docker", "info"])
                    break
                except RuntimeError:
                    await asyncio.sleep(1)
            else:
                raise ValueError("Deployment Docker daemon did not start")
            await runner.run(["sh", "-c", EXPENDABLE])
            await start_stack(runner, data, state)
            await configure(runner)
            write_private(data / "configured", "yes\n")
            ready(runner)
            await self.stops[factory_id].wait()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - report background service failures to the UI
            self.errors[factory_id] = str(exc)[-1500:]
        finally:
            if preview_task:
                preview_task.cancel()
                await asyncio.gather(preview_task, return_exceptions=True)
            self.previews.pop(factory_id, None)
            self.mcp_tokens.pop(factory_id, None)
            self.runners.pop(factory_id, None)
            if host and owned:
                if runner:
                    try:
                        await compose(runner, "stop")
                    except (RuntimeError, OSError):
                        logging.getLogger(__name__).exception(
                            "Could not stop Coolify stack %s", factory_id
                        )
                try:
                    await host.run(["docker", "stop", "--time", "60", name])
                except (RuntimeError, OSError):
                    logging.getLogger(__name__).exception(
                        "Could not stop deployment daemon %s", factory_id
                    )

    def preview_upstream(self, factory_id):
        config = self.installation.records[factory_id]["deployment"]
        if self.installation.docker_network and config["backend"] == "docker":
            return f"http://omni-deploy-{factory_id}:80"
        return f"http://127.0.0.1:{config['preview_port']}"

    def upstream(self, factory_id, offset=0):
        port = self.installation.records[factory_id]["deployment"]["port"] + offset
        host = (
            "omni-deploy-" + factory_id
            if self.installation.docker_network
            and self.installation.records[factory_id]["deployment"]["backend"]
            == "docker"
            else "127.0.0.1"
        )
        return f"http://{host}:{port}"
