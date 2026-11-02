"""Installation registry and isolated Workplace instances. No active-factory global."""

import asyncio
import contextlib
import os
import re
import secrets
import shutil
import threading
import uuid
from pathlib import Path

import yaml

from .accounts import Accounts, AccountError
from .config import FactoryConfig
from .factory_process import FactoryProcess
from .server import Workplace, create_app
from .store import Store


class FactoryAccounts:
    """Resolve shared sign-ins to this factory's local identity and role."""

    def __init__(self, installation, factory_id, local):
        self.installation, self.factory_id, self.local = installation, factory_id, local

    def __getattr__(self, name):
        return getattr(self.local, name)

    def signed_in(self, token):
        identity = self.installation.accounts.signed_in(token)
        if identity is None:
            return None
        member = self.installation.membership(self.factory_id, identity.id)
        if member is None:
            return None
        account = self.local.accounts.get(member.get("local_id"))
        return account if account and not account.disabled else None

    def change_password(self, account, current, new):
        identity = self.installation.accounts.by_username(account.username)
        return self.installation.accounts.change_password(identity, current, new)

    def sign_out(self, token):
        self.installation.accounts.sign_out(token)


class Installation:
    def __init__(
        self,
        directory,
        *,
        base_url,
        internal_url=None,
        sandbox_url=None,
        environment="development",
        runner_image=None,
        docker_image=None,
        docker_network=None,
        docker_host_data=None,
        workflow_image=None,
        template=None,
        sandbox_port=None,
        deployment_backend=None
    ):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base_url = base_url.rstrip("/")
        self.internal_url = (internal_url or base_url).rstrip("/")
        self.sandbox_url = (sandbox_url or base_url).rstrip("/")
        self.environment = environment
        self.runner_image = runner_image
        self.docker_image, self.docker_network = docker_image, docker_network
        self.docker_host_data, self.workflow_image = docker_host_data, workflow_image
        self.template = Path(template).resolve() if template else None
        self.sandbox_port = sandbox_port
        self.deployment_backend = deployment_backend
        if docker_image and not (docker_network and docker_host_data):
            raise ValueError("Docker hosting requires its network and host data path")
        self.store = Store(self.directory / "installation.db")
        self.accounts = Accounts(
            self.store,
            min_password=15 if environment in {"production", "staging"} else 8,
        )
        self.records = dict(self.store.docs("factory"))
        self.members = dict(self.store.docs("membership"))
        self.instances = {}
        self.processes = {}
        self.errors = {}
        self.setup_token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.stack = None
        self.mount_lock = asyncio.Lock()
        from .deployments.managed import Deployments

        self.deployments = Deployments(self)

    def adopt(
        self,
        data,
        config,
        *,
        project,
        architecture,
        runtime_container,
        environ=None,
        workflow=None
    ):
        """Register an offline existing factory without moving its service/runtime data."""
        if self.records or self.accounts.people():
            raise ValueError("Adoption requires an empty installation registry")
        source = Store(Path(data) / "workplace.db")
        try:
            factory_id = uuid.uuid4().hex
            directory = self.directory / "factories" / factory_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            # Keep operational settings, not demo seeds or plaintext login passwords.
            kept = {
                k: config[k]
                for k in ("organization", "factory", "app", "env_file", "memory")
                if k in config
            }
            kept["factory"] = dict(kept["factory"])
            kept["factory"].pop("repository", None)
            path = directory / "workplace.yml"
            path.write_text(yaml.safe_dump(kept, sort_keys=False))
            path.chmod(0o600)
            record = {
                "id": factory_id,
                "name": config["organization"]["name"],
                "project": str(project),
                "architecture": architecture,
                "autostart": True,
                "adopted": True,
                "runtime_container": runtime_container,
                "workplace_dir": str(Path(data).resolve()),
                "environ": environ or {},
                "workflow": workflow,
            }
            with self.store.transaction():
                for kind in (
                    "account",
                    "account.session",
                    "account.invite",
                    "account.failures",
                ):
                    for key, value in source.docs(kind):
                        self.store.put(kind, key, value)
                self.save(record)
                for key, value in source.docs("account"):
                    if not value.get("disabled"):
                        membership = {
                            "factory_id": factory_id,
                            "identity_id": key,
                            "local_id": key,
                            "role": "admin" if value.get("admin") else "member",
                        }
                        self.members[factory_id + ":" + key] = membership
                        self.store.put("membership", factory_id + ":" + key, membership)
                self.store.set_meta("installation.legacy_factory", factory_id)
            self.accounts = Accounts(
                self.store,
                min_password=15 if self.environment in {"production", "staging"} else 8,
            )
            return record
        finally:
            source.close()

    @staticmethod
    def product_config_name(project):
        product = (yaml.safe_load((project / "project.yml").read_text()) or {}).get(
            "product"
        ) or {}
        source_name = product.get("name") or product.get("slug", "omniagents")
        slug = product.get("slug") or re.sub(
            r"[-_]+", "_", re.sub(r"[^a-z0-9\-_]+", "_", source_name.strip().lower())
        ).strip("_")
        name = product.get("config_dir") or slug
        if os.name == "nt":
            label = product.get("label") or " ".join(
                p[:1].upper() + p[1:] for p in re.split(r"[-_]+", source_name) if p
            )
            name = product.get("windows_config_dir") or label.replace(" ", "")
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Product config_dir must be a directory name")
        return name

    def membership(self, factory_id, identity_id):
        return self.members.get(factory_id + ":" + identity_id)

    def record(self, factory_id):
        return self.records.get(factory_id)

    def save(self, record):
        self.records[record["id"]] = record
        self.store.put("factory", record["id"], record)

    def add(self, *, name, project, architecture, owner):
        name = name.strip()
        factory_id = uuid.uuid4().hex
        if not project:
            if not self.template or not (self.template / "project.yml").is_file():
                raise ValueError("Choose a factory project directory")
            project = self.directory / "factories" / factory_id / "project"
            shutil.copytree(
                self.template,
                project,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".env",
                    ".venv",
                    ".workplace",
                    "config",
                    "deploy",
                    "target",
                    "__pycache__",
                ),
            )
        project = Path(project).expanduser().resolve()
        if self.docker_image and not project.is_relative_to(
            self.directory / "factories"
        ):
            raise ValueError(
                "Hosted project directories must be inside the installation's factories directory"
            )
        if not name or len(name) > 100:
            raise ValueError("Name the factory (at most 100 characters)")
        if architecture not in {"personal", "specialist"}:
            raise ValueError("Choose personal or specialist")
        if not (project / "project.yml").is_file():
            raise ValueError(
                "Choose a factory project directory containing project.yml"
            )
        with self.lock:
            for existing in self.records.values():
                other = Path(existing["project"])
                if (
                    project == other
                    or project in other.parents
                    or other in project.parents
                ):
                    raise ValueError(
                        "Each factory needs a separate, non-overlapping project directory"
                    )
            record = {
                "id": factory_id,
                "name": name,
                "project": str(project),
                "architecture": architecture,
                "autostart": False,
            }
            directory = self.directory / "factories" / factory_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            config = {
                "organization": {
                    "name": name,
                    "slug": "f-" + factory_id[:12],
                    "tz": "UTC",
                },
                "factory": {
                    "url": "http://127.0.0.1:1",
                    "auth_token": secrets.token_urlsafe(32),
                    "admin_auth_token": secrets.token_urlsafe(32),
                    "config_dir": str(
                        directory / "config" / self.product_config_name(project)
                    ),
                },
                "app": {
                    "name": name,
                    "username": "factory-agent",
                    "slack_delivery": "socket",
                    "github_webhook": "http://127.0.0.1:1/integrations/github/events",
                    "linear_webhook": "http://127.0.0.1:1/integrations/linear/events",
                    "memory_webhook": "http://127.0.0.1:1/integrations/memory/events",
                },
                "memory": {"grants": {"team": "write"}},
            }
            path = directory / "workplace.yml"
            path.write_text(yaml.safe_dump(config, sort_keys=False))
            path.chmod(0o600)
            self.save(record)
            self.grant(factory_id, owner.id, "admin")
            return record

    def grant(self, factory_id, identity_id, role):
        if factory_id not in self.records or identity_id not in self.accounts.accounts:
            raise ValueError("Unknown factory or account")
        if role not in {"admin", "member", None}:
            raise ValueError("Role must be admin or member")
        with self.lock:
            key = factory_id + ":" + identity_id
            previous = self.members.get(key)
            if previous and previous["role"] == "admin" and role != "admin":
                admins = [
                    m
                    for m in self.members.values()
                    if m["factory_id"] == factory_id and m["role"] == "admin"
                ]
                if len(admins) == 1:
                    raise ValueError("Keep at least one factory administrator")
            if role is None:
                self.members.pop(key, None)
                self.store.delete("membership", key)
                if factory_id in self.instances and previous:
                    w, _ = self.instances[factory_id]
                    account = w.accounts.accounts.get(previous.get("local_id"))
                    if account:
                        w.update_person(account, disabled=True)
            else:
                member = {
                    **(previous or {}),
                    "factory_id": factory_id,
                    "identity_id": identity_id,
                    "role": role,
                }
                self.members[key] = member
                self.store.put("membership", key, member)
                if factory_id in self.instances:
                    self.sync_member(self.instances[factory_id][0], member)

    def sync_member(self, workplace, member):
        identity = self.accounts.accounts[member["identity_id"]]
        account = workplace.accounts.by_username(identity.username)
        if account is None:
            account = workplace.add_person(
                username=identity.username,
                name=identity.name,
                email=identity.email,
                admin=member["role"] == "admin",
            )
        else:
            workplace.update_person(
                account,
                name=identity.name,
                email=identity.email,
                admin=member["role"] == "admin",
                disabled=identity.disabled,
            )
        member["local_id"] = account.id
        self.store.put("membership", member["factory_id"] + ":" + identity.id, member)

    async def mount(self, factory_id):
        async with self.mount_lock:
            if factory_id in self.instances:
                return self.instances[factory_id]
            record = self.records[factory_id]
            prefix = "/factories/" + factory_id
            directory = self.directory / "factories" / factory_id
            environ = {
                "FACTORY_ARCHITECTURE": record["architecture"],
                "EVALUATIONS_ARCHIVE": str(directory / "evaluations"),
            }
            environ.update(record.get("environ", {}))
            environ["COOLIFY_MCP_URL"] = self.internal_url + prefix + "/coolify/mcp"
            if self.docker_host_data and not record.get("workplace_dir"):
                environ["LIBRARY_FILES_HOST_PATH"] = str(
                    Path(self.docker_host_data)
                    / "factories"
                    / factory_id
                    / "workplace"
                    / "library"
                )
            if environ.get("LIBRARY_FILES_HOST_PATH"):
                environ["SHARED_FILES_HOST_PATH"] = str(
                    Path(environ["LIBRARY_FILES_HOST_PATH"]).parent / "shared"
                )
            w = Workplace(
                Path(record.get("workplace_dir") or directory / "workplace"),
                base_url=self.base_url + prefix,
                internal_url=self.internal_url + prefix,
                sandbox_url=self.sandbox_url + prefix,
                environment=self.environment,
                run_workflows=False,
                environ=environ,
            )
            try:
                for member in list(self.members.values()):
                    if member["factory_id"] == factory_id:
                        self.sync_member(w, member)
                w.accounts = FactoryAccounts(self, factory_id, w.accounts)
                w.unclaimed = lambda: None
                w.factory_config = FactoryConfig(
                    directory / "workplace.yml", None, environ=environ
                )
                if record.get("adopted"):
                    # Existing state is authoritative. Never reapply seed/config data.
                    w.factory_config.write_environment(w)
                else:
                    w.factory_config.apply(w)
                    from .repository_settings import KEY

                    if w.store.meta(KEY) is None:
                        w.repository_settings.activate(
                            {
                                "provider": "standin",
                                "repositories": [],
                                "factory_repository": None,
                                "self_improvement": False,
                            }
                        )
                w.installation_factory_id = factory_id
                loop = asyncio.get_running_loop()

                def preview_changed(*_args):
                    bridge = self.deployments.previews.get(factory_id)
                    if bridge:
                        loop.call_soon_threadsafe(bridge.wake.set)

                w.preview_changed = preview_changed
                w.github.subscribe(
                    lambda kind, _data: (
                        preview_changed() if kind.startswith("pull_request_") else None
                    )
                )
                if self.docker_image or record.get("runtime_container"):
                    from .docker_factory import DockerFactoryProcess

                    process = DockerFactoryProcess(
                        directory, Path(record["project"]), self, record
                    )
                else:
                    process = FactoryProcess(directory, Path(record["project"]))
                if self.workflow_image:
                    from .github.actions import ActionsRunner
                    from .github.spaces import ContainerSettings

                    own = record.get("workflow") or {
                        "image": self.docker_image,
                        "network": self.docker_network,
                        "docker_socket": True,
                        "volumes": [
                            str(
                                Path(self.docker_host_data)
                                / "factories"
                                / factory_id
                                / "credentials"
                                / "codex"
                            )
                            + ":/secrets/codex"
                        ],
                        "env": {"OMNIAGENTS_CODEX_HOME": "/secrets/codex"},
                    }
                    w.github.actions = ActionsRunner(
                        w.github,
                        lambda: w.internal_url,
                        container=ContainerSettings(image=self.workflow_image),
                        self_hosted=ContainerSettings(**own),
                        instance=w.instance,
                    )
                if self.runner_image:
                    from urllib.parse import urlsplit
                    from .github.actions import ActionsRunner
                    from .github.quicksand_space import QuicksandSettings

                    profile = process.prepare_profile(
                        urlsplit(self.internal_url).port or 80
                    )
                    cfg = w.factory_config.load()["factory"]
                    runtime_env = process.environment(
                        w,
                        record["architecture"],
                        0,
                        cfg["auth_token"],
                        cfg["admin_auth_token"],
                        profile,
                    )
                    w.github.actions = ActionsRunner(
                        w.github,
                        lambda: w.internal_url,
                        instance=w.instance,
                        quicksand=QuicksandSettings(
                            str(Path(self.runner_image).resolve())
                        ),
                        agent_profile=profile,
                        agent_environment=runtime_env,
                    )
                app = create_app(w, static_dir=getattr(self, "static_dir", None))
                if self.stack is not None:
                    await self.stack.enter_async_context(self.child_lifespan(app))
                self.instances[factory_id] = (w, app)
                self.processes[factory_id] = process
                self.errors.pop(factory_id, None)
                return w, app
            except BaseException:
                w.close()
                raise

    async def start(self, factory_id):
        from urllib.parse import urlsplit

        w, _ = await self.mount(factory_id)
        port = urlsplit(self.internal_url).port or 80
        await self.processes[factory_id].start(
            w, self.records[factory_id], port, self.runner_image
        )
        self.save({**self.records[factory_id], "autostart": True})

    async def stop(self, factory_id):
        if factory_id in self.processes:
            await self.processes[factory_id].stop()
        self.save({**self.records[factory_id], "autostart": False})

    def view(self, record, identity):
        membership = self.membership(record["id"], identity.id)
        process = self.processes.get(record["id"])
        return {
            **{key: record[key] for key in ("id", "name", "architecture")},
            "project": record["project"] if identity.admin else None,
            "role": membership["role"] if membership else None,
            "deployment": (
                self.deployments.view(record["id"])
                if membership and membership["role"] == "admin"
                else None
            ),
            "runtime": (
                process.status()
                if process
                else {"running": False, "error": self.errors.get(record["id"])}
            ),
        }

    @staticmethod
    @contextlib.asynccontextmanager
    async def child_lifespan(app):
        # MCP's AnyIO cancel scopes must enter and exit in the same task, even
        # when a factory is created by an HTTP request after host startup.
        ready = asyncio.get_running_loop().create_future()
        stopped = asyncio.Event()

        async def run():
            try:
                async with app.router.lifespan_context(app):
                    ready.set_result(None)
                    await stopped.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                raise

        task = asyncio.create_task(run())
        try:
            await ready
            yield
        finally:
            stopped.set()
            await task

    @contextlib.asynccontextmanager
    async def lifespan(self, _app):
        from filelock import FileLock

        lock = FileLock(str(self.directory / "installation.lock"))
        lock.acquire(timeout=0)
        try:
            async with contextlib.AsyncExitStack() as stack:
                self.stack = stack
                for factory_id in self.records:
                    try:
                        await self.mount(factory_id)
                        if self.records[factory_id].get("autostart"):
                            await self.start(factory_id)
                    except Exception as exc:
                        self.errors[factory_id] = str(exc)
                for factory_id, record in self.records.items():
                    if (record.get("deployment") or {}).get("enabled"):
                        await self.deployments.start(factory_id)
                try:
                    yield
                finally:
                    await self.deployments.close()
                    await asyncio.gather(*(p.stop() for p in self.processes.values()))
        finally:
            self.stack = None
            for w, _ in self.instances.values():
                w.close()
            self.instances.clear()
            self.store.close()
            lock.release()
