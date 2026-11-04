"""Inspect repository Compose configuration and provision native PR previews."""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import quote

import yaml
from fastapi import HTTPException

from .coolify import compose, write_private
from .managed import php
from .previews import SCOPE, repository

CANDIDATES = (
    "compose.preview.yml",
    "compose.preview.yaml",
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}")


def inspect_compose(raw):
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise ValueError("The Compose file is not valid YAML") from None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("services"), dict)
        or not data["services"]
    ):
        raise ValueError("The Compose file must define services")
    services = []
    for name, service in data["services"].items():
        if not isinstance(service, dict):
            raise ValueError(f"Invalid service: {name}")
        if (
            service.get("ports")
            or service.get("privileged")
            or service.get("network_mode") == "host"
        ):
            raise ValueError(
                f"{name}: use expose for internal ports; previews must not publish host ports or use privileged/host networking"
            )
        if service.get("env_file") or service.get("container_name"):
            raise ValueError(
                f"{name}: use environment variables and let Coolify name preview containers"
            )
        for mount in service.get("volumes", []):
            source = (
                mount.get("source", "")
                if isinstance(mount, dict)
                else str(mount).split(":")[0]
            )
            if (
                isinstance(mount, dict) and mount.get("type") == "bind"
            ) or source.startswith(("/", ".", "~")):
                raise ValueError(
                    f"{name}: use named volumes instead of host directories"
                )
        ports = []
        for value in service.get("expose", []):
            port = str(value).split("/")[0]
            if port.isdigit() and 0 < int(port) < 65536:
                ports.append(int(port))
        services.append({"name": name, "ports": ports})
    for section in ("volumes", "networks"):
        if any(
            isinstance(v, dict) and (v.get("external") or v.get("name"))
            for v in (data.get(section) or {}).values()
        ):
            raise ValueError(
                f"Preview {section} must not reference shared external or fixed-name resources"
            )
    variables = sorted({m[0] for m in VARIABLE.findall(raw) if not m[1] or "?" in m[1]})
    return {
        "services": services,
        "variables": variables,
        "generated": [
            v for v in variables if v.endswith(("_PASSWORD", "_SECRET_KEY_BASE"))
        ],
        "login": login(data, variables),
    }


def login(data, variables):
    """The sample sign-in a Compose file declares for its previews:

        x-preview:
          login:
            email: demo@example.com
            password: PREVIEW_ADMIN_PASSWORD   # the variable holding it

    ``{"email", "variable"}``, or None when it declares none.
    """
    section = data.get("x-preview")
    value = section.get("login") if isinstance(section, dict) else None
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("email"), str)
        or value.get("password") not in variables
    ):
        raise ValueError(
            "x-preview.login needs an email and, as password, a variable the Compose file requires"
        )
    return {"email": value["email"], "variable": value["password"]}


def generate(name):
    """A fresh value for a generated preview variable."""
    return secrets.token_hex(64 if name.endswith("_SECRET_KEY_BASE") else 24)


def environment(values, preview):
    """Coolify's bulk environment payload: build and run time, one set each."""
    return {
        "data": [
            {
                "key": k,
                "value": v,
                "is_preview": preview,
                "is_buildtime": True,
                "is_runtime": True,
            }
            for k, v in values.items()
        ]
    }


def saved_setup(directory, application):
    """``(path, saved)`` for the application Workplace set previews up for, or None."""
    for path in directory.glob("preview-setup-*.json"):
        saved = json.loads(path.read_text())
        if saved.get("application") == application:
            return path, saved
    return None


async def read_compose(w, provider, repo, ref, path):
    """The text of ``path`` at ``ref`` (a branch or commit), or None."""
    if provider == "standin":
        r = w.github.repos[repo]
        blob = await asyncio.to_thread(r.bare.blob, ref, path)
        return blob[1].decode() if blob else None
    content = await asyncio.to_thread(
        w.repository_settings.github,
        f"/repos/{repo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}",
    )
    if content and content.get("encoding") == "base64":
        return base64.b64decode(content["content"]).decode()
    return None


class PreviewSetup:
    def __init__(self, installation, factory_id):
        self.i, self.factory_id = installation, factory_id
        self.w = installation.instances[factory_id][0]
        self.d = installation.deployments

    async def inspect(self, repo, path=""):
        settings = self.w.repository_settings.settings()
        if repo not in settings["repositories"]:
            raise ValueError("Save this repository selection before enabling previews")
        provider = settings["provider"]
        if path and (
            path.startswith("/")
            or ".." in path.split("/")
            or not re.fullmatch(r"[\w./-]+", path)
        ):
            raise ValueError("Use a repository-relative Compose path")
        if provider == "standin":
            r = self.w.github.repos[repo]
            branch = r.default_branch
            pulls = [
                self.w.github_json.pull(r, issue)
                for issue in list(r.issues.values())
                if issue.pull
            ]
            files = await asyncio.to_thread(r.bare.files, branch)
            selected = path or next((p for p in CANDIDATES if p in files), "")
            blob = (
                await asyncio.to_thread(r.bare.blob, branch, selected)
                if selected
                else None
            )
            raw = blob[1].decode() if blob else None
        else:
            meta = await asyncio.to_thread(
                self.w.repository_settings.github, f"/repos/{repo}"
            )
            branch = meta["default_branch"]
            pulls = await asyncio.to_thread(
                self.w.repository_settings.github,
                f"/repos/{repo}/pulls?state=open&base={quote(branch, safe='')}&per_page=100",
            )
            listing = await asyncio.to_thread(
                self.w.repository_settings.github,
                f"/repos/{repo}/contents?ref={quote(branch, safe='')}",
            )
            files = [v["path"] for v in listing]
            selected = path or next((p for p in CANDIDATES if p in files), "")
            raw = (
                await read_compose(self.w, provider, repo, branch, selected)
                if selected
                else None
            )
        if not raw:
            raise ValueError(
                "No Compose file found. Prepare a preview configuration using the factory-previews skill, then try again."
            )
        if len(raw) > 256_000:
            raise ValueError("Compose file exceeds 256 KB")
        return {
            "repository": repo,
            "provider": provider,
            "branch": branch,
            "path": selected,
            "revision": hashlib.sha256(raw.encode()).hexdigest(),
            # Open now, so not previewed unless chosen: same-repository PRs into the branch.
            "open_pulls": [
                {"number": p["number"], "title": p["title"]}
                for p in pulls
                if p["state"] == "open"
                and p["base"]["ref"] == branch
                and ((p.get("head") or {}).get("repo") or {})
                .get("full_name", "")
                .lower()
                == repo.lower()
            ],
            **inspect_compose(raw),
            "_raw": raw,
        }

    async def enable(self, request):
        plan = await self.inspect(request["repository"], request.get("path", ""))
        if (
            request.get("revision") != plan["revision"]
            or request.get("provider") != plan["provider"]
        ):
            raise ValueError(
                "Repository configuration changed. Inspect it again before enabling previews."
            )
        ports = request.get("services", {})
        names = {s["name"] for s in plan["services"]}
        if not ports or any(
            n not in names
            or not isinstance(p, int)
            or isinstance(p, bool)
            or not 0 < p < 65536
            for n, p in ports.items()
        ):
            raise ValueError(
                "Choose at least one public service and its internal HTTP port"
            )
        bridge = self.d.previews.get(self.factory_id)
        if not bridge or not bridge.token:
            self.d.configure(self.factory_id, True)
            await self.d.start(self.factory_id)
            for _ in range(120):
                bridge = self.d.previews.get(self.factory_id)
                if bridge and bridge.token:
                    break
                if self.d.errors.get(self.factory_id):
                    raise HTTPException(
                        503, "Coolify could not start; check its status in Factories"
                    )
                await asyncio.sleep(1)
            else:
                raise HTTPException(503, "Coolify is still starting. Retry shortly.")
        key = hashlib.sha256(
            f"{plan['provider']}:{plan['repository']}".encode()
        ).hexdigest()[:20]
        directory, _ = self.d.prepare(self.factory_id)
        state_path = directory / f"preview-setup-{key}.json"
        saved = json.loads(state_path.read_text()) if state_path.exists() else {}
        apps = await bridge.applications()
        matches = [
            a
            for a in apps
            if a["branch"] == plan["branch"]
            and repository(a["repository"], plan["provider"], self.w)
            == plan["repository"]
        ]
        if len(matches) > 1:
            raise ValueError(
                "Multiple Coolify applications match this repository. Configure the intended application in Coolify."
            )
        app = matches[0] if matches else None
        if app and saved.get("application") != app["uuid"]:
            if app["enabled"]:
                return {
                    "application": app["uuid"],
                    "status": "enabled",
                    "url": self.d.url(self.factory_id),
                }
            raise ValueError(
                "This repository already has a Coolify application. Configure it there rather than replacing its settings."
            )
        values = dict(saved.get("variables", {}))
        supplied = request.get("variables", {})
        if any(
            k not in plan["variables"] or not isinstance(v, str)
            for k, v in supplied.items()
        ):
            raise ValueError("Unexpected environment variable")
        values.update({k: v for k, v in supplied.items() if v})
        for name in plan["generated"]:
            values.setdefault(name, generate(name))
        missing = [v for v in plan["variables"] if not values.get(v)]
        if missing:
            raise ValueError("Provide preview values for: " + ", ".join(missing))
        projects = await bridge.api("GET", "/projects")
        project = next((p for p in projects if p["name"] == "Factory previews"), None)
        if not project:
            project = await bridge.api(
                "POST", "/projects", {"name": "Factory previews"}
            )
        servers = await bridge.api("GET", "/servers")
        server = next(s for s in servers if s["name"] == "localhost")
        if not app:
            if plan["provider"] == "standin":
                username = (self.w.factory_config.load().get("app") or {})["username"]
                identity = next(
                    a for a in self.w.apps.active() if a.username == username
                )
                token = self.w.github.users[identity.github_login].token
                url = self.w.base_url + "/github/" + plan["repository"] + ".git"
            else:
                token = await asyncio.to_thread(self.w.repository_settings.token)
                url = "https://github.com/" + plan["repository"] + ".git"
            scheme, address = url.split("://", 1)
            url = scheme + "://x:" + quote(token, safe="") + "@" + address
            app = await bridge.api(
                "POST",
                "/applications/public",
                {
                    "name": plan["repository"] + " previews",
                    "project_uuid": project["uuid"],
                    "environment_name": "production",
                    "server_uuid": server["uuid"],
                    "git_repository": url,
                    "git_branch": plan["branch"],
                    "build_pack": "dockercompose",
                    "docker_compose_location": "/" + plan["path"],
                    "instant_deploy": False,
                    "is_auto_deploy_enabled": False,
                    "is_preview_deployments_enabled": False,
                    "autogenerate_domain": False,
                },
            )
        elif app.get("build_pack") != "dockercompose":
            raise ValueError(
                "The existing Coolify application uses a different build pack; configure it in Coolify."
            )
        saved.update(
            application=app["uuid"],
            variables=values,
            provider=plan["provider"],
            repository=plan["repository"],
        )
        write_private(state_path, json.dumps(saved))
        include = request.get("include") or []
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in include):
            raise ValueError("Choose pull requests by number")
        # Previews begin with PRs opened from now on, plus the open ones chosen.
        scope = dict(self.i.store.docs(SCOPE)).get(app["uuid"]) or {}
        self.i.store.put(
            SCOPE,
            app["uuid"],
            {
                "factory": self.factory_id,
                "since": scope.get("since") or time.time(),
                "include": sorted(set(scope.get("include", [])) | set(include)),
            },
        )
        await refresh_git_credentials(bridge, self.w)
        path = "/applications/" + app["uuid"]
        await bridge.api(
            "PATCH",
            path,
            {"docker_compose_location": "/" + plan["path"], "use_build_secrets": True},
        )
        for preview in (False, True):
            if values:
                await bridge.api(
                    "PATCH", path + "/envs/bulk", environment(values, preview)
                )
        await compose(
            bridge.runner,
            "exec",
            "-T",
            "coolify",
            "php",
            "artisan",
            "tinker",
            '--execute=$a=App\\Models\\Application::where("uuid",'
            + php(app["uuid"])
            + ')->firstOrFail(); $a->loadComposeFile(); echo "LOADED";',
        )
        domains = [
            {"name": n, "domain": f"http://{key}-{n}.preview.invalid:{p}"}
            for n, p in ports.items()
        ]
        await bridge.api(
            "PATCH",
            path,
            {"docker_compose_domains": domains, "is_preview_deployments_enabled": True},
        )
        bridge.wake.set()
        return {
            "application": app["uuid"],
            "status": "enabled",
            "url": self.d.url(self.factory_id),
        }


async def refresh_git_credentials(bridge, w):
    """Keep device-login refreshes usable for future clones of managed apps."""
    if w.repository_settings.settings()["provider"] != "github":
        return
    directory, _ = bridge.d.prepare(bridge.factory_id)
    states = [json.loads(p.read_text()) for p in directory.glob("preview-setup-*.json")]
    states = [s for s in states if s.get("provider") == "github"]
    if not states:
        return
    token = await asyncio.to_thread(w.repository_settings.token)
    digest = hashlib.sha256(token.encode()).hexdigest()
    cached = getattr(bridge, "_git_credentials", {})
    for state in states:
        app = state["application"]
        if cached.get(app) != digest:
            # Coolify's public-create endpoint converts github.com URLs into
            # its anonymous GitHub source, discarding embedded credentials.
            # These managed apps use the Workplace connection directly.
            await compose(
                bridge.runner,
                "exec",
                "-T",
                "coolify",
                "php",
                "artisan",
                "tinker",
                '--execute=$a=App\\Models\\Application::where("uuid",'
                + php(app)
                + ")->firstOrFail(); $a->source_id=null; $a->source_type=null; $a->save();",
            )
            await bridge.api(
                "PATCH",
                "/applications/" + app,
                {
                    "git_repository": "https://x:"
                    + quote(token, safe="")
                    + "@github.com/"
                    + state["repository"]
                    + ".git"
                },
            )
            cached[app] = digest
    bridge._git_credentials = cached
