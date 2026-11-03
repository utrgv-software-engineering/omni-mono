"""Reconcile selected repositories' PRs with Coolify's native previews.

Coolify owns builds and cleanup. This bridge owns only the previews it creates;
SQLite records make successful dispatches survive Workplace restarts.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from .coolify import compose

log = logging.getLogger(__name__)
KIND = "deployment.preview"
# Which pull requests an application previews, set when previews are enabled:
# {"factory", "since": epoch, "include": [numbers]}, keyed by application uuid.
# Those opened since then, and the open ones chosen then; none means every one.
SCOPE = "deployment.preview.scope"


def opened_at(pr):
    """When a pull request was opened, in epoch seconds (0 when unknown)."""
    try:
        return datetime.fromisoformat(
            pr["created_at"].replace("Z", "+00:00")
        ).timestamp()
    except (KeyError, AttributeError, ValueError):
        return 0.0


def repository(value, provider, workplace):
    value = value.strip().removesuffix(".git")
    if provider == "github":
        value = re.sub(r"^git@github\.com:", "", value)
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.hostname != "github.com":
                return None
            value = parsed.path.strip("/")
    else:
        bases = (workplace.base_url, workplace.internal_url, workplace.sandbox_url)
        parsed = urlsplit(value)
        for base in bases:
            expected = urlsplit(base)
            prefix = expected.path.rstrip("/") + "/github/"
            if (parsed.scheme, parsed.hostname, parsed.port) == (
                expected.scheme,
                expected.hostname,
                expected.port,
            ) and parsed.path.startswith(prefix):
                value = parsed.path[len(prefix) :]
                break
        else:
            return None
    return value if re.fullmatch(r"[\w.-]+/[\w.-]+", value) else None


class Previews:
    def __init__(self, deployments, factory_id, runner):
        self.d = deployments
        self.i = deployments.installation
        self.factory_id = factory_id
        self.runner = runner
        self.store = self.i.store
        self.token = None
        self.wake = asyncio.Event()

    async def initialize(self):
        output = await compose(
            self.runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute="
            + "$u=App\\Models\\User::findOrFail(0); $u->tokens()->where('name','Workplace previews')->delete(); "
            "$t=$u->createToken('Workplace previews',['read','write','deploy'],null,0); "
            "$s=App\\Models\\InstanceSettings::findOrFail(0); $s->is_api_enabled=true; $s->save(); "
            "echo 'WORKPLACE_TOKEN:'.$t->plainTextToken;",
        )
        self.token = output.split("WORKPLACE_TOKEN:", 1)[1].strip()

    async def api(self, method, path, body=None):
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.request(
                method,
                self.d.upstream(self.factory_id) + "/api/v1" + path,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Accept": "application/json",
                },
                json=body,
            )
        if response.status_code == 404 and method in {"DELETE", "GET"}:
            return None
        if response.is_error:
            # Never include headers, tokens, or native application's secret fields.
            raise RuntimeError(
                f"Coolify {method} {path}: HTTP {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    async def applications(self):
        output = await compose(
            self.runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute="
            + "echo 'WORKPLACE_APPS:'.App\\Models\\Application::with('settings','destination')->get()->filter(fn($a)=>$a->team()?->id===0)->map(fn($a)=>['uuid'=>$a->uuid,'repository'=>$a->git_repository,'branch'=>$a->git_branch,'enabled'=>(bool)$a->settings->is_preview_deployments_enabled,'build_pack'=>$a->build_pack,'server_id'=>$a->destination?->server_id])->values()->toJson();",
        )
        return json.loads(output.split("WORKPLACE_APPS:", 1)[1])

    async def ensure_proxy(self):
        # Only the factory's own deployment server, never arbitrary remote servers.
        await compose(
            self.runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            "--execute="
            + "$s=App\\Models\\Server::findOrFail(0); if ($s->proxyType()==='NONE') { $s->proxy->type='TRAEFIK'; $s->save(); } "
            "if ($s->proxyType()!=='TRAEFIK') { throw new Exception('Workplace previews require the managed Traefik proxy'); } "
            "if (App\\Actions\\Proxy\\CheckProxy::run($s)) { App\\Actions\\Proxy\\StartProxy::run($s,async:false); }",
        )

    def records(self):
        return {
            key: value
            for key, value in self.store.docs(KIND)
            if value["factory"] == self.factory_id
        }

    async def pulls(self, w, provider, repo):
        if provider == "standin":
            from ..github.repos import git

            r = w.github.repos.get(repo)
            if r is None:
                raise RuntimeError("Selected stand-in repository no longer exists")
            values = []
            for issue in list(r.issues.values()):
                if not issue.pull:
                    continue
                value = w.github_json.pull(r, issue)
                if value["state"] == "open":
                    git(
                        "update-ref",
                        f"refs/pull/{issue.number}/head",
                        value["head"]["sha"],
                        cwd=r.bare.path,
                    )
                values.append(value)
            return values
        values = []
        page = 1
        while True:
            batch = await asyncio.to_thread(
                w.repository_settings.github,
                f"/repos/{repo}/pulls?state=open&per_page=100&page={page}",
            )
            values.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        # Absence alone never authorizes deletion (pagination, visibility, etc.).
        open_numbers = {p["number"] for p in values}
        tracked = {
            r["number"]
            for r in self.records().values()
            if r["provider"] == provider
            and r["repository"] == repo
            and r["state"] != "closed"
        }
        for number in tracked - open_numbers:
            values.append(
                await asyncio.to_thread(
                    w.repository_settings.github, f"/repos/{repo}/pulls/{number}"
                )
            )
        return values

    async def reconcile(self):
        w = self.i.instances[self.factory_id][0]
        settings = w.repository_settings.settings()
        provider = settings["provider"]
        from .setup import refresh_git_credentials

        if hasattr(self.d, "prepare"):
            await refresh_git_credentials(self, w)
        apps = await self.applications()
        current = {app["uuid"]: app for app in apps}
        # Disabling an app, deselecting a repo, or changing provider removes only
        # bridge-owned previews; unrelated native previews remain untouched.
        for key, record in self.records().items():
            app = current.get(record["application"])
            if record["state"] != "closed" and (
                not app
                or not app["enabled"]
                or record["provider"] != provider
                or record["repository"] not in settings["repositories"]
                or repository(app["repository"], provider, w) != record["repository"]
            ):
                await self.remove(key, record)
        cache = {}
        for app in apps:
            repo = repository(app["repository"], provider, w)
            if not app["enabled"] or repo not in settings["repositories"]:
                continue
            if app["server_id"] != 0:
                continue
            if repo not in cache:
                cache[repo] = await self.pulls(w, provider, repo)
            for pr in cache[repo]:
                # Use authoritative current state, not webhook order.
                await self.apply(app, repo, provider, pr)

    async def apply(self, app, repo, provider, pr):
        number = pr["number"]
        if not isinstance(number, int) or number < 1:
            return
        key = hashlib.sha256(
            f"{self.factory_id}:{app['uuid']}:{provider}:{repo}:{number}".encode()
        ).hexdigest()[:32]
        old = self.records().get(key)
        if pr["state"] == "closed" or pr["base"]["ref"] != app["branch"]:
            if old and old["state"] != "closed":
                await self.remove(key, old)
            return
        # Never execute untrusted forks automatically with a factory's build credentials.
        if (pr.get("head", {}).get("repo") or {}).get(
            "full_name", ""
        ).lower() != repo.lower():
            return
        sha = pr["head"]["sha"]
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            return
        scope = dict(self.store.docs(SCOPE)).get(app["uuid"])
        if (
            old is None
            and scope
            and number not in scope.get("include", [])
            and opened_at(pr) < scope["since"]
        ):
            return  # Opened before previews were enabled, and not chosen then.
        if old and old["sha"] == sha and old["state"] == "queued":
            if old.get("deployment") and old.get("build_status") not in {
                "finished",
                "failed",
                "cancelled-by-user",
            }:
                status = await self.api("GET", "/deployments/" + old["deployment"])
                if status:
                    old["build_status"] = status.get("status")
                    self.store.put(KIND, key, old)
            return
        record = {
            "factory": self.factory_id,
            "application": app["uuid"],
            "provider": provider,
            "repository": repo,
            "number": number,
            "sha": sha,
            "state": "pending",
            "urls": [],
            "hosts": [],
            "updated": time.time(),
        }
        if (
            old is None
            and await self.api("GET", f"/applications/{app['uuid']}/previews/{number}")
            is not None
        ):
            return  # An existing native preview is not ours to overwrite or remove.
        self.store.put(KIND, key, record)
        try:
            await self.ensure_proxy()
            path = f"/applications/{app['uuid']}/previews"
            body = {
                "pull_request_id": number,
                "git_type": "github",
                "commit": sha,
                "pull_request_html_url": pr.get("html_url"),
                "instant_deploy": False,
            }
            created = await self.api("POST", path, body)
            preview = created["preview"]
            services = [
                service
                for service in (preview.get("docker_compose_domains") or [])
                if service.get("domain")
            ]
            if app.get("build_pack") == "dockercompose" and not services:
                raise RuntimeError(
                    "Configure a web service domain in the Coolify Compose application before enabling previews"
                )
            domains = []
            for service in services or [{"name": ""}]:
                suffix = hashlib.sha256(
                    f"{key}:{service['name']}".encode()
                ).hexdigest()[:32]
                public = self.d.preview_url(suffix)
                record["urls"].append(public)
                record["hosts"].append(urlsplit(public).netloc)
                domains.append(
                    {**service, "domain": "http://" + urlsplit(public).hostname}
                )
            patch = (
                {"docker_compose_domains": domains}
                if services
                else {"domains": domains[0]["domain"]}
            )
            await self.api("PATCH", path + "/" + str(number), patch)
            self.store.put(KIND, key, record)
            result = await self.api("POST", path, {**body, "instant_deploy": True})
            record.update(
                state="queued",
                build_status="queued",
                deployment=result.get("deployment_uuid"),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - durable retry with visible error
            record.update(state="error", error=str(exc)[:500])
        self.store.put(KIND, key, record)

    async def remove(self, key, record):
        # Remove routing before teardown so a closed preview is immediately inaccessible.
        record = {**record, "state": "closing", "hosts": [], "urls": []}
        self.store.put(KIND, key, record)
        try:
            await self.api(
                "DELETE",
                f"/applications/{record['application']}/previews/{record['number']}",
            )
            record.update(state="closed", error=None)
        except Exception as exc:  # noqa: BLE001 - retry on the next reconciliation
            record.update(error=str(exc)[:500])
        self.store.put(KIND, key, record)

    async def run(self, stopped):
        while not stopped.is_set():
            self.wake.clear()
            try:
                if self.token is None:
                    await self.initialize()
                try:
                    await self.reconcile()
                finally:
                    from .comments import publish

                    await publish(self)
                self.d.preview_errors.pop(self.factory_id, None)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one outage must not stop reconciliation
                self.d.preview_errors[self.factory_id] = str(exc)[:500]
                log.warning(
                    "Factory %s preview reconciliation failed: %s", self.factory_id, exc
                )
            tasks = [
                asyncio.create_task(stopped.wait()),
                asyncio.create_task(self.wake.wait()),
            ]
            try:
                await asyncio.wait(
                    tasks, timeout=30, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
