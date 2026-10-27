"""Evaluations in the workplace: each scenario gets its own Slack and GitHub.

``evaluation.yml``::

    evaluation:
      environment:
        type: workplace
        fixtures_dir: fixtures/workspaces   # repositories' content
        github_dir: fixtures/github         # issues and pull requests
        seed: acme                          # the workplace each scenario starts from: a seed file, or acme (bundled)
        # app: acme-factory                 # the app the agents act as (default: the seed's first)

Each scenario starts the way an automation run does in production: a
workplace of its own (its people, Slack, and the scenario's repository on
GitHub), and a fresh sandbox from the project's sandbox profile with that
repository cloned at ``/workspace/<name>`` from the workplace's git. The
agents act as one of the seed's apps, with the credentials its workplace
made: they reach Slack, GitHub and Linear through their MCP servers, pointed
at the scenario's workplace (``SLACK_MCP_URL``, ``GITHUB_MCP_URL``,
``LINEAR_MCP_URL``, ``CANVAS_MCP_URL`` and their tokens), and the sandbox's git pushes with the
app's token, so what they do there is what they would do in production.

A scenario's ``fixture`` is the repository's content and its ``github``
file (``github_dir``) its issues and pull requests::

    repository: acme/api-service
    default_branch: main
    issues: [{number: 12, title: ..., author: priya, body: ...}]
    pull_requests:
      - {number: 33, title: ..., author: dana, head: dana/pagination,
         base_fixture: api_service}   # the base's content; the head is the scenario's fixture
      - {number: 34, ..., diff: <a unified diff applied to the fixture>}

After the run, ``ctx.get_env_data("github")``, ``("slack")`` and
``("linear")`` hold what is there: every issue and pull request with its
comments and reviews, every Slack message, and every Linear issue with its
comments.

The workplace listens on a port in ``7300``-``7399`` (``OMNIAGENTS_WORKPLACE_PORTS``
as ``first-last``), where the scenario's sandbox reaches it: on Docker's
bridge, or, in a container, at ``OMNIAGENTS_WORKPLACE_HOST`` (the container's
name, on the network its sandboxes join; the self-hosted runner sets it). On
Linux behind a firewall, allow that range from Docker's bridge (see the README).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment

from .server import Workplace, bundled_seed, create_app, load_seed

__all__ = ["WorkplaceEnvironment", "build"]


def _ports() -> range:
    raw = os.environ.get("OMNIAGENTS_WORKPLACE_PORTS", "7300-7399")
    first, _, last = raw.partition("-")
    return range(int(first), int(last or first) + 1)


def _listen(port: int, addresses: List[str]) -> Optional[List[socket.socket]]:
    sockets = []
    for n, address in enumerate(addresses):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((address, port))
        except OSError:
            sock.close()
            if n == 0:
                for s in sockets:
                    s.close()
                return None
            continue
        sock.listen(128)
        sockets.append(sock)
    return sockets


class _Server:
    """A workplace served on a thread of its own, for one scenario."""

    def __init__(
        self,
        workplace: Workplace,
        sockets: List[socket.socket],
        sandbox_addresses: List[str],
    ):
        import uvicorn

        self.workplace = workplace
        app = create_app(workplace, sandbox_addresses=sandbox_addresses)
        self.server = uvicorn.Server(
            uvicorn.Config(app, log_level="warning", loop="asyncio")
        )
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": sockets}, daemon=True
        )
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the scenario's workplace did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.workplace.close()
        shutil.rmtree(self.workplace.data_dir, ignore_errors=True)


class WorkplaceEnvironment(EvalEnvironment):
    def __init__(
        self,
        *,
        project_root: Path,
        fixtures_dir: Optional[Path],
        github_dir: Optional[Path],
        seed_path: Path,
        app: Optional[str] = None,
    ):
        from omniagents.core.sandbox.profiles import product_default_profile_path

        self.project_root = Path(project_root)
        self.fixtures_dir = fixtures_dir
        self.github_dir = github_dir
        self.seed_path = seed_path
        self.app = app
        manifest = self.project_root / "project.yml"
        self.profile = product_default_profile_path(manifest)
        if self.profile is None:
            raise ValueError(
                "environment (type: workplace): the project has no sandbox profile"
            )
        self._live: Dict[str, Dict[str, Any]] = {}

    # -- the scenario's workplace -----------------------------------------------

    def _seed(self, scenario: Mapping[str, Any]) -> Dict[str, Any]:
        seed = copy.deepcopy(load_seed(self.seed_path))
        for app in seed.get("apps") or []:
            # Nothing listens for an app's webhooks in an evaluation.
            app.pop("github", None)
            app.pop("linear", None)
        github = seed.setdefault("github", {})
        name = scenario.get("github")
        fixture = scenario.get("fixture")
        if not name:
            return seed
        if self.github_dir is None:
            raise ValueError(
                "scenario sets `github:` but the environment has no github_dir"
            )
        raw = (
            yaml.safe_load((self.github_dir / str(name)).read_text(encoding="utf-8"))
            or {}
        )
        full_name = str(raw["repository"])
        base_repo = next(
            (r for r in github.get("repositories") or [] if r.get("name") == full_name),
            {},
        )
        fixture_dir = self._fixture(fixture) if fixture else None
        pulls = []
        repo_from = fixture_dir
        for pr in raw.get("pull_requests") or []:
            pr = {k: v for k, v in pr.items() if k not in ("base_fixture", "files")}
            original = next(
                p for p in raw["pull_requests"] if p.get("number") == pr.get("number")
            )
            # The scenario's fixture is the pull request's head (the checkout a
            # reviewer looks at); its base is the named fixture, or the
            # fixture with the pull request's diff undone.
            if original.get("base_fixture"):
                repo_from = self._fixture(original["base_fixture"])
                pr["from"] = str(fixture_dir) if fixture_dir else None
            elif pr.get("diff") and fixture_dir:
                pr["from"] = str(fixture_dir)
                pr["base_from"] = str(fixture_dir)
                pr["base_unapply"] = pr.pop("diff")
            pulls.append(pr)
        repo = {
            **{k: v for k, v in base_repo.items() if k in ("description", "labels")},
            "name": full_name,
            "default_branch": raw.get("default_branch")
            or base_repo.get("default_branch")
            or _missing(f"{name}: name the repository's default_branch"),
            "from": str(repo_from) if repo_from else None,
            "issues": raw.get("issues") or [],
            "pull_requests": pulls,
        }
        github["repositories"] = [repo]
        return seed

    def _fixture(self, name: str) -> Path:
        if self.fixtures_dir is None:
            raise ValueError(
                "scenario sets `fixture:` but the environment has no fixtures_dir"
            )
        path = self.fixtures_dir / str(name)
        if not path.is_dir():
            raise FileNotFoundError(f"fixture not found: {path}")
        return path

    def _serve(self, seed: Dict[str, Any]) -> _Server:
        from .cli import docker_bridge_gateway

        # In a container (the self-hosted runner's jobs), the sandboxes it starts
        # are its siblings on its network, and reach it at its name.
        host = os.environ.get("OMNIAGENTS_WORKPLACE_HOST")
        if host:
            bridge = [socket.gethostbyname(host)]
        else:
            bridge = [a for a in [docker_bridge_gateway()] if a]
        addresses = ["127.0.0.1"] + bridge
        for port in _ports():
            sockets = _listen(port, addresses)
            if sockets is None:
                continue
            # Each scenario's workplace is its own, in a data directory that
            # goes when the scenario ends.
            data_dir = Path(tempfile.mkdtemp(prefix="workplace-scenario-"))
            workplace = Workplace(
                data_dir,
                base_url=f"http://127.0.0.1:{port}",
                seed=seed,
                seed_dir=self.seed_path.parent,
                sandbox_url=f"http://{host}:{port}" if host else None,
            )
            return _Server(workplace, sockets, bridge)
        raise RuntimeError(f"no free port for the scenario's workplace in {_ports()}")

    # -- EvalEnvironment ------------------------------------------------------------

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        from omniagents.integrations.environment import GitHubConfig, RunEnvironments

        session_id = str(uuid.uuid4())
        seed = self._seed(scenario)
        server = await asyncio.to_thread(self._serve, seed)
        workplace = server.workplace
        stack = contextlib.AsyncExitStack()
        live = {"server": server, "stack": stack}
        self._live[session_id] = live
        try:
            app = (
                workplace.apps.by_username(self.app)
                if self.app
                else next(iter(workplace.apps.active()), None)
            )
            if app is None:
                raise ValueError(
                    f"environment (type: workplace): the seed has no app {self.app or ''}".rstrip()
                )
            settings = workplace.apps.env(app)
            # A small memory fixture for tool-use scenarios. IDs remain dynamic;
            # measures read them from context rather than embedding them in prompts.
            memory_fixture = {}
            if fixture := scenario.get("memory_fixture"):
                store = f"{settings['MEMORY_AGENT_STORE']}/{fixture['role']}"
                workplace.memory._access(app.id, store, True)
                memory_fixture = {"store": store}
                if fixture.get("text"):
                    memory_fixture.update(
                        workplace.memory.remember(app.id, store, fixture["text"])
                    )
            repositories = tuple(
                r["name"] for r in (seed.get("github") or {}).get("repositories") or []
            )
            environments = RunEnvironments(
                self.profile,
                GitHubConfig(
                    repositories=repositories,
                    git_url=f"{workplace.sandbox_url}/github",
                    token=settings["GITHUB_MCP_TOKEN"],
                ),
            )
            live["sandbox"] = await stack.enter_async_context(
                environments.sandbox("evaluation")
            )
        except Exception:
            await self.teardown(EnvironmentContext(session_id=session_id))
            raise
        env = {
            k: v
            for k, v in settings.items()
            if k
            in (
                "SLACK_MCP_URL",
                "SLACK_MCP_TOKEN",
                "GITHUB_MCP_URL",
                "GITHUB_MCP_TOKEN",
                "LINEAR_MCP_URL",
                "LINEAR_MCP_TOKEN",
                "CANVAS_MCP_URL",
                "CANVAS_MCP_TOKEN",
                "MEMORY_MCP_URL",
                "MEMORY_MCP_TOKEN",
                "MEMORY_AGENT_STORE",
            )
        }
        return EnvironmentContext(
            session_id=session_id,
            data={
                "workplace_url": workplace.base_url,
                "workspace_root": live["sandbox"].root,
                "memory_fixture": memory_fixture,
                "_workplace_env": env,
            },
        )

    async def start_event_run(self, target, scenario, *, defaults, context):
        from .evaluation_events import FactoryEventRun

        return FactoryEventRun(target, scenario, defaults, self, context)

    def event_store_path(self, context):
        return (
            self._live[context.session_id]["server"].workplace.data_dir
            / "eval-events.sqlite"
        )

    async def prepare_event(self, context, step, index):
        from .evaluation_event_fixtures import prepare_event

        return await prepare_event(self, context, step, index)

    async def validate_event(self, context, step):
        from .evaluation_event_fixtures import validate_event

        return await validate_event(self, context, step)

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        return dict(context.get("_workplace_env") or {})

    async def register_environment(
        self, resources: Any, context: Optional[EnvironmentContext]
    ) -> Any:
        live = self._live[context.session_id]
        return live["sandbox"].register(resources)

    def run_command(
        self, context: EnvironmentContext, command: str, timeout: int = 120
    ) -> tuple:
        from omniagents.core.eval.container_routing import _decode, _run_coro_sync

        live = self._live.get(context.session_id) if context else None
        if not live:
            return (127, "", "no sandbox for this scenario")
        session = live["sandbox"].session

        async def run():
            return await asyncio.wait_for(session.exec(command), timeout)

        try:
            result = _run_coro_sync(run())
        except asyncio.TimeoutError:
            return (124, "", f"command timed out after {timeout}s")
        return (
            getattr(result, "exit_code", 1),
            _decode(getattr(result, "stdout", b"")),
            _decode(getattr(result, "stderr", b"")),
        )

    async def collect(self, context: Optional[EnvironmentContext]) -> None:
        live = self._live.get(context.session_id) if context else None
        if not live:
            return
        workplace: Workplace = live["server"].workplace
        context.data["github"] = _github_record(workplace)
        context.data["slack"] = _slack_record(workplace)
        context.data["linear"] = _linear_record(workplace)

    async def teardown(self, context: EnvironmentContext) -> None:
        live = self._live.pop(context.session_id, None) if context else None
        if not live:
            return
        try:
            await live["stack"].aclose()
        finally:
            await asyncio.to_thread(live["server"].stop)


def _github_record(workplace: Workplace) -> Dict[str, Any]:
    from .github.state import iso

    state = workplace.github
    repos = []
    for repo in state.repos.values():
        items = []
        for issue in sorted(repo.issues.values(), key=lambda i: i.number):
            item = {
                "number": issue.number,
                "kind": "pull_request" if issue.pull else "issue",
                "title": issue.title,
                "author": issue.user,
                "state": issue.state,
                "body": issue.body,
                "labels": list(issue.labels),
                "assignees": list(issue.assignees),
                "comments": [
                    {"author": c.user, "body": c.body, "at": iso(c.created_at)}
                    for c in issue.comments
                ],
            }
            if issue.pull:
                item.update(
                    head=issue.pull.head,
                    head_sha=state.head_sha(repo, issue),
                    base=issue.pull.base,
                    draft=issue.pull.draft,
                    merged=issue.pull.merged,
                    reviews=[
                        {"author": r.user, "state": r.state, "body": r.body}
                        for r in issue.pull.reviews
                    ],
                    review_comments=[
                        {
                            "author": c.user,
                            "path": c.path,
                            "line": c.line,
                            "body": c.body,
                        }
                        for t in issue.pull.threads
                        for c in t.comments
                    ],
                    files=[
                        f.filename
                        for f in repo.bare.changes(
                            state.base_sha(repo, issue), state.head_sha(repo, issue)
                        )
                    ],
                )
            items.append(item)
        repos.append(
            {
                "repository": repo.full_name,
                "branches": sorted(repo.bare.branches()),
                "main_sha": repo.bare.ref_sha(repo.default_branch),
                "items": items,
            }
        )
    return {"repositories": repos}


def _slack_record(workplace: Workplace) -> Dict[str, Any]:
    ws = workplace.slack
    return {
        "messages": [
            {
                "channel": ws.channels[m.channel].name or m.channel,
                "user": ws.users[m.user].name if m.user in ws.users else m.user,
                "text": m.text,
                "ts": m.ts,
                "thread_ts": m.thread_ts,
                "files": [ws.files[f].name for f in m.files if f in ws.files],
            }
            for m in ws.all_messages()
        ]
    }


def _linear_record(workplace: Workplace) -> Dict[str, Any]:
    s = workplace.linear
    issues = []
    for issue in sorted(s.issues.values(), key=lambda i: (i.team_id, i.number)):
        issues.append(
            {
                "identifier": issue.identifier,
                "title": issue.title,
                "description": issue.description,
                "state": s.state(issue).name,
                "labels": [s.labels[i].name for i in issue.label_ids if i in s.labels],
                "creator": (
                    s.users[issue.creator_id].display_name
                    if issue.creator_id in s.users
                    else None
                ),
                "attachments": [
                    {"title": a.title, "url": a.url} for a in issue.attachments
                ],
                "comments": [
                    {
                        "author": (
                            s.users[c.user_id].display_name
                            if c.user_id in s.users
                            else None
                        ),
                        "body": c.body,
                    }
                    for c in s.thread(issue)
                    if c.body
                ],
            }
        )
    return {"issues": issues}


def _missing(message: str):
    raise ValueError(message)


def build(
    config: Mapping[str, Any], *, project_root: Optional[Path], eval_dir: Optional[Path]
) -> EvalEnvironment:
    """The ``workplace`` evaluation environment type (an ``omniagents.eval_environments`` entry point)."""
    unknown = set(config) - {"fixtures_dir", "github_dir", "seed", "app"}
    if unknown:
        raise ValueError(
            f"environment (type: workplace): unknown keys {sorted(unknown)}"
        )
    base = eval_dir or project_root or Path.cwd()

    def path(key: str) -> Optional[Path]:
        value = config.get(key)
        if not value:
            return None
        p = Path(str(value)).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    if not config.get("seed"):
        raise ValueError(
            "environment (type: workplace): name the seed scenarios start from (seed: a file, or acme)"
        )
    seed = bundled_seed() if config["seed"] == "acme" else path("seed")
    root = project_root
    if root is None:
        root = next(
            (p for p in [base, *base.parents] if (p / "project.yml").is_file()), None
        )
    if root is None:
        raise ValueError(
            "environment (type: workplace): no project.yml above the evaluation"
        )
    return WorkplaceEnvironment(
        project_root=Path(root),
        fixtures_dir=path("fixtures_dir"),
        github_dir=path("github_dir"),
        seed_path=seed,
        app=str(config["app"]) if config.get("app") else None,
    )
