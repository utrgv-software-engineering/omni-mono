"""Event intake in server mode: the project's ``integrations:``, wired into the app.

``build_app`` calls :func:`mount_integrations`. A project that declares
integrations with credentials gets their sources on the server: webhook
routes for ``delivery: http`` and GitHub, outbound connections for
``delivery: socket``, and its automations' schedules
(:mod:`omniagents.integrations.schedule`), started with the server and
stopped with it. Each
event goes through :class:`~omniagents.integrations.intake.EventIntake`,
and matched automations run their agent on the server's AgentHost, as an
unattended run (no person answers its approval prompts; the approvals
reviewer the project configures decides them), in a fresh sandbox from the
project's sandbox profile with its repositories checked out
(:mod:`omniagents.integrations.environment`).

``project.yml``::

    integrations:
      - type: github
        repositories: [acme/api-service]
        git_url: ${GITHUB_GIT_URL:-}
        account: acme-factory                # the factory's GitHub account
        webhook_secret: ${GITHUB_WEBHOOK_SECRET}
      - type: slack
        delivery: socket                     # or http
        app_token: ${SLACK_APP_TOKEN}        # socket: the xapp- token
        signing_secret: ${SLACK_SIGNING_SECRET}  # http
        api_url: ${SLACK_API_URL:-https://slack.com/api}

Secrets come from the environment; an integration whose variables are unset
is left off, with the reason logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import yaml
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .environment import RunEnvironments, parse_github
from .linear import (
    LINEAR_EVENTS_PATH,
    LinearConfig,
    LinearSessions,
    normalize_linear_event,
    verify_linear_request,
)
from .github import (
    GITHUB_EVENTS_PATH,
    GitHubWebhookConfig,
    normalize_github_event,
    verify_github_request,
)
from .intake import EventIntake, Progress, RunResult, RunStep
from .slack import (
    SLACK_EVENTS_PATH,
    ChannelNames,
    SlackConfig,
    SlackSocketMode,
    normalize_slack_event,
    verify_slack_request,
)
from .replay import REPLAY_PATH
from .schedule import RUN_SCHEDULE_PATH, SCHEDULES_PATH
from .store import EventStore

__all__ = ["RUNS_PATH", "mount_integrations", "parse_integrations"]

logger = logging.getLogger(__name__)

#: The factory's runs, with their usage, cost and pull requests (the workplace's Factory page).
RUNS_PATH = "/integrations/runs"


Config = Union[SlackConfig, GitHubWebhookConfig, LinearConfig]


def parse_integrations(entries: Any) -> List[Config]:
    """The event sources in a ``project.yml`` ``integrations:`` list."""
    from omniagents.core.config.loader import _substitute_env_vars

    configs: List[Config] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "github":
            from omniagents.core.project.github_settings import github_settings

            entry = github_settings(entry)
            # Its repositories are where runs work (parse_github); with a
            # webhook secret, its webhooks are events.
            try:
                entry = _substitute_env_vars(dict(entry))
            except ValueError as exc:
                logger.warning("integrations: github events are off: %s", exc)
                continue
            if entry.get("webhook_secret"):
                configs.append(
                    GitHubWebhookConfig(
                        webhook_secret=str(entry["webhook_secret"]),
                        account=str(entry.get("account") or ""),
                    )
                )
            else:
                logger.info(
                    "integrations: github has no webhook_secret; its events are off"
                )
            continue
        if kind == "linear":
            try:
                entry = _substitute_env_vars(dict(entry))
            except ValueError as exc:
                logger.warning("integrations: linear is off: %s", exc)
                continue
            if not entry.get("webhook_secret"):
                logger.info(
                    "integrations: linear has no webhook_secret; its agent sessions are off"
                )
                continue
            if not entry.get("access_token"):
                logger.warning(
                    "integrations: linear has no access_token; sessions get no acknowledgement or response"
                )
            configs.append(
                LinearConfig(
                    webhook_secret=str(entry["webhook_secret"]),
                    access_token=str(entry.get("access_token") or ""),
                    api_url=str(
                        entry.get("api_url") or "https://api.linear.app/graphql"
                    ),
                )
            )
            continue
        if kind != "slack":
            logger.info("integrations: %r events are not supported yet", kind)
            continue
        try:
            entry = _substitute_env_vars(dict(entry))
        except ValueError as exc:
            logger.warning("integrations: slack is off: %s", exc)
            continue
        app_token = entry.get("app_token") or None
        signing_secret = entry.get("signing_secret") or None
        delivery = entry.get("delivery") or ("socket" if app_token else "http")
        if delivery == "socket" and not app_token:
            logger.warning("integrations: slack socket delivery needs app_token")
            continue
        if delivery == "http" and not signing_secret:
            logger.warning("integrations: slack http delivery needs signing_secret")
            continue
        if delivery not in ("socket", "http"):
            logger.warning("integrations: slack delivery %r is unknown", delivery)
            continue
        configs.append(
            SlackConfig(
                delivery=delivery,
                app_token=app_token,
                signing_secret=signing_secret,
                api_url=str(entry.get("api_url") or "https://slack.com/api"),
                token=str(entry.get("token") or "") or None,
            )
        )
    return configs


def mount_integrations(
    app: Any, spec: Any, resources: Any, *, auth_token: Optional[str] = None
) -> Optional[EventIntake]:
    """Wire the project's integrations into ``app``; ``None`` when it has none."""
    manifest = getattr(spec, "manifest_path", None)
    if not manifest or not Path(manifest).is_file():
        return None
    try:
        data = yaml.safe_load(Path(manifest).read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    from .architecture import factory_architecture, PERSONAL_SESSION, claim_architecture

    architecture = factory_architecture()
    configs = parse_integrations(data.get("integrations"))
    from .schedule import ScheduleSource, project_schedules

    schedules = project_schedules(Path(manifest).parent)
    if not configs and not schedules and not data.get("memory") and not architecture:
        return None

    from omniagents.core.paths import get_home_dir
    from omniagents.core.project.runtime import load_project
    from omniagents.core.sandbox.profiles import product_default_profile_path

    project = load_project(str(manifest))
    if not project.entrypoint:
        logger.warning("integrations: the project has no entrypoint agent; off")
        return None
    profile = product_default_profile_path(manifest)
    environments = None
    if profile is not None:
        environments = RunEnvironments(profile, parse_github(data.get("integrations")))
    else:
        logger.warning(
            "integrations: the project has no sandbox profile; "
            "automation runs have no workspace"
        )
    store = EventStore(get_home_dir() / "integrations" / f"{project.product.slug}.db")
    architecture_lock = claim_architecture(project, store)
    principal = _server_principal(app, auth_token)
    persistent = architecture in ("personal", "specialist")
    if persistent and environments:
        _attach_factory_workspace(app, project, resources, environments, principal)
    run = _run_starter(
        project, resources, None if persistent else environments, principal
    )
    if architecture == "personal":
        from .personal import PersonalRunner

        run = PersonalRunner(app.state.agent_service, principal)
        app.router.on_startup.append(run.resume)

    async def architecture_status(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        service = app.state.agent_service
        busy = store.running() + len(service.active_run_ids())
        busy += sum(
            item.state == "pending"
            for session in service.active_sessions()
            for item in session.message_queue
        )
        return JSONResponse(
            {
                "architecture": architecture or "specialist",
                "session_id": PERSONAL_SESSION if architecture == "personal" else None,
                "busy": busy,
            }
        )

    app.add_api_route(
        "/integrations/architecture", architecture_status, methods=["GET"]
    )
    interrupted = store.mark_interrupted()
    if interrupted:
        logger.info(
            "integrations: %d run(s) a previous server left unfinished are marked interrupted",
            interrupted,
        )
    intake = EventIntake(
        project_root=project.root,
        entrypoint=project.entrypoint,
        store=store,
        run=run,
        shared_session=PERSONAL_SESSION if architecture == "personal" else None,
    )
    app.state.event_intake = intake
    # The factory's version, from whoever runs it (the supervisor: the commit it promoted).
    intake.version = os.environ.get("FACTORY_VERSION") or None
    from omniagents.core.project.runtime import build_spec_from_project
    from omniagents.core.session.history_db import load_history

    from .grading import LiveJudges, live_judges
    from .links import PullRequestLinks

    def history(agent_key: str, session_id: str) -> list:
        spec = build_spec_from_project(project, agent_key)
        return load_history(
            session_id, project_slug=spec.project_slug, agent_slug=spec.agent_slug
        )

    def children(agent_key: str, session_id: str) -> list:
        """The sessions of the agents the run called as tools: child threads in
        its agent's conversation store, each named by the agent it was."""
        from omniagents.core.conversation.store import get_conversation_store

        from .grading import thread_history

        spec = build_spec_from_project(project, agent_key)
        convo = get_conversation_store(spec.project_slug, spec.agent_slug)
        out = []
        for thread_id, _, _ in convo.descendant_thread_rows(session_id):
            thread = convo.get_thread(thread_id)
            child = (thread.metadata or {}).get("agent") if thread else None
            if child:
                items = convo.list_items(thread_id, limit=100000)
                out.append((str(child), thread_id, thread_history(items)))
        return out

    def extract_memory(dispatch_id, automation, agent, session_id, event, result):
        from omniagents.core.memory.extract import schedule

        spec = build_spec_from_project(project, agent)
        config = getattr(spec, "memory_config", None)
        if (
            config
            and config.get("extraction")
            and "run_end" in config["extraction"].get("triggers", ["run_end"])
        ):
            source = {
                "run_id": str(dispatch_id),
                "session_id": session_id,
                "automation": automation,
                "error": result.error,
                "grades": store.run_grades(dispatch_id),
            }
            schedule(
                config,
                (
                    result.history
                    if result.history is not None
                    else history(agent, session_id)
                ),
                source,
            )
            for child, thread, transcript in (
                children(agent, session_id) if result.history is None else []
            ):
                child_config = getattr(
                    build_spec_from_project(project, child), "memory_config", None
                )
                if (
                    child_config
                    and child_config.get("extraction")
                    and "run_end"
                    in child_config["extraction"].get("triggers", ["run_end"])
                ):
                    schedule(child_config, transcript, {**source, "session_id": thread})

    intake.extract_memory = extract_memory
    from .memory import mount_memory_route

    mount_memory_route(app, intake)
    from omniagents.core.memory.extract import shutdown as shutdown_memory

    app.router.on_shutdown.append(shutdown_memory)
    intake.links = PullRequestLinks(store, history, children)
    # What every model call uses, recorded as it happens, whatever tracing does.
    from omniagents.core.tracing import (
        add_permanent_processor,
        remove_permanent_processor,
    )
    from omniagents.core.usage import UsageRecorder

    recorder = UsageRecorder(store.record_usage)
    add_permanent_processor(recorder)
    app.router.on_shutdown.append(lambda: remove_permanent_processor(recorder))
    _mount_runs_route(app, store, _price_list(project.root, data))
    # Each agent's sessions, for the shared chat UI to read (agent_runtimes.py).
    from .agent_runtimes import AGENTS_PATH, AgentRuntimes

    app.mount(
        AGENTS_PATH,
        AgentRuntimes(
            project,
            auth_token=auth_token,
            primary_app=app,
            architecture=architecture,
            host_resources=resources if persistent else None,
        ),
    )

    def session_started(agent_key: str, thread_id: str) -> Optional[float]:
        from omniagents.core.conversation.store import get_conversation_store

        spec = build_spec_from_project(project, agent_key)
        thread = get_conversation_store(spec.project_slug, spec.agent_slug).get_thread(
            thread_id
        )
        return thread.created_at if thread else None

    _mount_case_route(app, store, history, children, session_started)

    judges = live_judges(project.root, data)
    if judges:
        from omniagents.core.eval.layout import evaluations_dir

        intake.judges = LiveJudges(
            judges,
            evaluations_dir(project.root, data),
            store,
            history,
            children=children,
        )

    for config in configs:
        if isinstance(config, GitHubWebhookConfig):
            _mount_github_route(app, intake, config)
        elif isinstance(config, LinearConfig):
            _mount_linear_route(app, intake, config)
        elif config.delivery == "http":
            _mount_slack_route(app, intake, config)
        else:
            socket = SlackSocketMode(config, intake.accept)
            app.router.on_startup.append(socket.start)
            app.router.on_shutdown.append(socket.stop)
    _mount_replay_route(app, intake)
    if schedules:
        improve = {key for key, cfg in judges.items() if cfg.get("self_improve")}
        source = ScheduleSource(
            project.root,
            intake.accept,
            runs=store.runs_since,
            improve=improve,
            unmatched=store.unmatched_since,
        )
        app.router.on_startup.append(source.start)
        app.router.on_shutdown.append(source.stop)
        _mount_run_schedule_route(app, source)
    app.router.on_shutdown.append(store.close)
    if architecture_lock:
        app.router.on_shutdown.append(architecture_lock.close)
    logger.info(
        "integrations: %s",
        ", ".join(
            [
                (
                    "github (webhooks)"
                    if isinstance(c, GitHubWebhookConfig)
                    else (
                        "linear (agent sessions)"
                        if isinstance(c, LinearConfig)
                        else f"slack ({c.delivery})"
                    )
                )
                for c in configs
            ]
            + ([f"schedules ({len(schedules)})"] if schedules else [])
            + ([f"judges on live runs ({', '.join(judges)})"] if judges else [])
        ),
    )
    return intake


def _price_list(root: Path, data: dict):
    """The project's model prices (``paths.pricing``), or ``None`` without them."""
    from omniagents.core.pricing import project_price_list

    try:
        prices = project_price_list(root)
    except (OSError, ValueError) as exc:
        logger.warning("integrations: the project's prices are unreadable: %s", exc)
        return None
    if prices is None:
        logger.info(
            "integrations: the project has no paths.pricing; runs' costs are unknown"
        )
    return prices


def _mount_runs_route(app: Any, store: EventStore, prices: Any) -> None:
    """``GET /integrations/runs?since=<epoch seconds>``: the factory's runs, newest
    last, each with what started it, how it went, its grades, what its model calls
    used and cost (API prices; a subscription's are API-equivalent), and its pull
    requests. With this server's token."""

    async def runs(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        try:
            since = float(request.query_params.get("since") or 0)
        except ValueError:
            return JSONResponse({"error": "since is epoch seconds"}, status_code=400)

        def read() -> list:
            out = []
            for run in store.runs_since(since):
                usage = []
                for row in store.run_usage(run["id"]):
                    cost = (
                        prices.cost(row["model"], row) if prices is not None else None
                    )
                    usage.append({**row, "cost": cost})
                priced = [u["cost"] for u in usage if u["cost"] is not None]
                out.append(
                    {
                        **{k: v for k, v in run.items() if k not in ("payload",)},
                        "payload": run["payload"],
                        "usage": usage,
                        # The total, when every model it used has a price.
                        "cost": (
                            sum(priced)
                            if usage and len(priced) == len(usage)
                            else (0.0 if not usage else None)
                        ),
                        "pull_requests": store.run_pull_requests(run["id"]),
                    }
                )
            return out

        return JSONResponse(
            {"runs": await asyncio.to_thread(read), "priced": prices is not None}
        )

    app.add_api_route(RUNS_PATH, runs, methods=["GET"])


def _first_user_text(history: list) -> Optional[str]:
    for item in history:
        if item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, list):
                return (
                    " ".join(
                        str(c.get("text") or "") for c in content if isinstance(c, dict)
                    )
                    or None
                )
            return str(content or "") or None
    return None


def _mount_case_route(
    app: Any,
    store: EventStore,
    history: Any,
    children: Any,
    session_started: Any = lambda agent, thread: None,
) -> None:
    """``GET /integrations/runs/{id}/case``: what a run was given, to make a benchmark
    task of it: its own input, and each session's (the agents it called as tools)."""

    async def case(dispatch_id: int, request: Request):
        if not await _authorized(request):
            return Response(status_code=401)

        def read():
            run = next((r for r in store.runs_since(0) if r["id"] == dispatch_id), None)
            if run is None:
                return None
            from .links import opened_pull_requests

            scoped = store.run_history(dispatch_id)
            from .architecture import PERSONAL_SESSION

            if run["session_id"] == PERSONAL_SESSION and scoped is None:
                scoped = []
            own = (
                scoped
                if scoped is not None
                else history(run["agent"], run["session_id"])
            )
            sessions = [
                {
                    "agent": run["agent"],
                    "session_id": run["session_id"],
                    "started_at": run["started_at"],
                    "prompt": store.run_prompt(dispatch_id) or _first_user_text(own),
                    "opened": [f"{r}#{n}" for r, n in opened_pull_requests(own)],
                }
            ]
            for agent, thread_id, child_history in (
                children(run["agent"], run["session_id"]) if scoped is None else []
            ):
                # When it began: what it was given then is the task (an issue triage made, say).
                sessions.append(
                    {
                        "agent": agent,
                        "session_id": thread_id,
                        "prompt": _first_user_text(child_history),
                        "started_at": session_started(run["agent"], thread_id)
                        or run["started_at"],
                        "opened": [
                            f"{r}#{n}" for r, n in opened_pull_requests(child_history)
                        ],
                    }
                )
            return {
                "id": dispatch_id,
                "automation": run["automation"],
                "agent": run["agent"],
                "started_at": run["started_at"],
                "provider": run["provider"],
                "event": run["event"],
                "payload": run["payload"],
                "version": run.get("version"),
                "pull_requests": store.run_pull_requests(dispatch_id),
                "sessions": sessions,
            }

        found = await asyncio.to_thread(read)
        if found is None:
            return JSONResponse({"error": f"no run {dispatch_id}"}, status_code=404)
        return JSONResponse(found)

    app.add_api_route(RUNS_PATH + "/{dispatch_id}/case", case, methods=["GET"])


async def _authorized(request: Request) -> bool:
    """The request carries this server's own token."""
    from omniagents.rpc.auth import AuthenticationFailed

    authenticator = getattr(request.app.state, "connection_authenticator", None)
    if authenticator is None:
        return False
    try:
        await authenticator.authenticate_http(request)
    except AuthenticationFailed:
        return False
    return True


def _mount_run_schedule_route(app: Any, source: Any) -> None:
    """``GET /integrations/schedules``: the project's schedules and when each fires next;
    ``POST /integrations/schedules/{name}/run``: fire one now (``run-schedule``)."""

    async def run_schedule(name: str, request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        try:
            fired = await source.fire_now(name)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        if not fired:
            return JSONResponse({"error": f"no schedule {name!r}"}, status_code=404)
        return JSONResponse({"fired": name})

    app.add_api_route(RUN_SCHEDULE_PATH, run_schedule, methods=["POST"])

    async def schedules(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        return JSONResponse({"schedules": source.listing()})

    app.add_api_route(SCHEDULES_PATH, schedules, methods=["GET"])


def _mount_replay_route(app: Any, intake: EventIntake) -> None:
    """``POST /integrations/replay``: another factory's events, run here again
    (``replay.py``); with this server's own token."""
    from .replay import event_from_json, replayed

    async def replay_events(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        try:
            body = await request.json()
            events = [event_from_json(e) for e in body.get("events") or []]
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        accepted = 0
        for event in events:
            if event.control is None and await intake.accept(replayed(event)):
                accepted += 1
        return JSONResponse({"accepted": accepted, "sent": len(events)})

    app.add_api_route(REPLAY_PATH, replay_events, methods=["POST"])


def _mount_slack_route(app: Any, intake: EventIntake, config: SlackConfig) -> None:
    names = ChannelNames(config)

    async def slack_events(request: Request):
        body = await request.body()
        if not verify_slack_request(config.signing_secret or "", request.headers, body):
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        if payload.get("type") == "url_verification":
            return JSONResponse({"challenge": payload.get("challenge")})
        event = normalize_slack_event(payload)
        if event is not None:
            await intake.accept(await names.named(event))
        return Response(status_code=200)

    app.add_api_route(SLACK_EVENTS_PATH, slack_events, methods=["POST"])


def _mount_github_route(
    app: Any, intake: EventIntake, config: GitHubWebhookConfig
) -> None:
    async def github_events(request: Request):
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify_github_request(config.webhook_secret, headers, body):
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        event = headers.get("x-github-event", "")
        delivery = headers.get("x-github-delivery", "")
        if event == "ping" or not delivery:
            return Response(status_code=204)
        for incoming in normalize_github_event(
            event, delivery, payload, config.account
        ):
            await intake.accept(incoming)
        return Response(status_code=202)

    app.add_api_route(GITHUB_EVENTS_PATH, github_events, methods=["POST"])


def _mount_linear_route(app: Any, intake: EventIntake, config: LinearConfig) -> None:
    sessions = LinearSessions(config)
    intake.on_step["linear"] = sessions.step
    intake.after_run["linear"] = sessions.finished
    pending: set = set()

    async def linear_events(request: Request):
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify_linear_request(config.webhook_secret, headers, body):
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        event = normalize_linear_event(headers.get("linear-delivery", ""), payload)
        if event is not None and await intake.accept(event) and event.control is None:
            # Linear wants an activity within 10 seconds; the run takes longer.
            task = asyncio.get_running_loop().create_task(sessions.acknowledge(event))
            pending.add(task)
            task.add_done_callback(pending.discard)
        return Response(status_code=200)

    app.add_api_route(LINEAR_EVENTS_PATH, linear_events, methods=["POST"])


def _server_principal(
    app: Any, auth_token: Optional[str]
) -> Callable[[], Awaitable[Any]]:
    """Who the integrations' runs act as: the principal the server's own token
    authenticates as. Their sessions (and the threads of the agents they call)
    are then owned, so that principal can read them back over the server's
    runtimes, which never let anyone read an unowned session. ``None`` where
    the server takes no token."""
    cached: Dict[str, Any] = {}

    async def principal() -> Any:
        authenticator = getattr(app.state, "connection_authenticator", None)
        if (
            not auth_token
            or authenticator is None
            or not authenticator.enforces_authorization
        ):
            return None
        if "identity" not in cached:
            cached["identity"] = await authenticator.authenticate_token(auth_token)
        return cached["identity"]

    return principal


def _attach_factory_workspace(app, project, resources, environments, principal):
    """Both architectures share one durable factory environment, not a conversation."""
    from omniagents.core.paths import get_home_dir

    # Keep the original filename so switching architectures adopts the same
    # installed toolchain and the existing backup/reset commands keep working.
    context = environments.sandbox(
        "factory",
        state_path=get_home_dir()
        / "integrations"
        / f"{project.product.slug}.personal-sandbox.json",
    )

    async def start_workspace():
        sandbox = await context.__aenter__()
        owner = await principal()
        sandbox.register(resources, owner_user_id=owner.user_id if owner else None)

    async def stop_workspace():
        await context.__aexit__(None, None, None)

    app.router.on_startup.insert(0, start_workspace)
    app.router.on_shutdown.append(stop_workspace)


def _run_starter(
    project: Any,
    resources: Any,
    environments: Optional[RunEnvironments],
    principal: Callable[[], Awaitable[Any]],
):
    from omniagents.core.agents.factory import create_unified_agent
    from omniagents.core.project.runtime import build_spec_from_project

    async def run_in(
        host_resources: Any,
        agent_key: str,
        session_id: str,
        prompt: str,
        progress: Optional[Progress],
    ) -> RunResult:
        from agents import ItemHelpers

        spec = build_spec_from_project(project, agent_key)
        agent = await create_unified_agent(
            spec, host_resources=host_resources, unattended=True, session_id=session_id
        )
        result = None
        try:
            owner = await principal()
            if owner is not None and callable(getattr(agent, "set_principal", None)):
                await agent.initialize()
                agent.set_principal(owner)
            result = agent.run_streamed([{"role": "user", "content": prompt}])
            last: Optional[str] = None
            async for event in result.stream_events():
                name = getattr(event, "name", None)
                step: Optional[RunStep] = None
                if name == "message_output_created":
                    text = ItemHelpers.text_message_output(event.item)
                    if text.strip():
                        last = text
                        step = RunStep("message", text=text)
                elif name == "tool_called":
                    called = (
                        event.item.to_input_item()
                        if hasattr(event.item, "to_input_item")
                        else {}
                    )
                    tool = called.get("name") if isinstance(called, dict) else None
                    step = RunStep("tool", name=tool or "a tool")
                if step is not None and progress is not None:
                    await progress(step)
            return RunResult(error=run_failure(agent.get_last_run_end()), output=last)
        except asyncio.CancelledError:
            # Stopped: the streamed run stops too, not just our reading of it.
            cancel = getattr(result, "cancel", None)
            if callable(cancel):
                cancel()
            raise
        finally:
            cleanup = getattr(agent, "cleanup", None)
            if callable(cleanup):
                await cleanup()

    async def run(
        agent_key: str,
        session_id: str,
        prompt: str,
        progress: Optional[Progress] = None,
    ) -> RunResult:
        if environments is None:
            return await run_in(resources, agent_key, session_id, prompt, progress)
        async with environments.open() as sandbox:
            return await run_in(sandbox, agent_key, session_id, prompt, progress)

    return run


def run_failure(end: Optional[dict]) -> Optional[str]:
    """Why a run did not complete, from its ``run_end``; ``None`` when it did."""
    if not end:
        return "the run ended without reporting how"
    reason = end.get("end_reason")
    if reason == "completed":
        return None
    error = end.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    return f"{reason}: {message}" if message else str(reason or "unknown")
