"""Event intake in server mode: the project's ``integrations:``, wired into the app.

``build_app`` calls :func:`mount_integrations`. A project that declares
integrations with credentials gets their sources on the server: webhook
routes for ``delivery: http`` and GitHub, outbound connections for
``delivery: socket``, and its automations' schedules
(:mod:`omniagents.integrations.schedule`), started with the server and
stopped with it. Each
event goes through :class:`~omniagents.integrations.intake.EventIntake`,
and each matched automation runs as a turn on its agent's durable
conversation (:class:`~omniagents.integrations.personal.ConversationRunner`),
unattended (no person answers its approval prompts; the approvals reviewer
the project configures decides them). Every agent's tools run on the
factory's one shared machine, brought up from the project's sandbox profile
with its repositories checked out and kept across restarts
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
import dataclasses
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
from .intake import EventIntake
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

    from omniagents.core.allowed_services import INTEGRATIONS_ENV, integration_allowed

    def _resolved_or_raw(entry):
        try:
            return _substitute_env_vars(dict(entry))
        except ValueError:  # a variable it needs is unset: its own check reports it
            return dict(entry)

    configs: List[Config] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if os.environ.get(INTEGRATIONS_ENV) and not integration_allowed(
            _resolved_or_raw(entry)
        ):
            logger.warning(
                "integrations: %r is not one of the allowed services; it is off", kind
            )
            continue
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
        if os.environ.get("OMNIAGENTS_EVENT_GATEWAY") == "1":
            entry["delivery"] = "http"
            entry["signing_secret"] = os.environ.get("SLACK_SIGNING_SECRET", "")
            entry["token"] = os.environ.get(
                "OMNIAGENTS_GATEWAY_SLACK_TOKEN", entry.get("token", "")
            )
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


def _left_out(name: str, kinds: set, event: Dict[str, Any]) -> bool:
    """Whether ``event`` was one of automation ``name``'s kinds that it did not run for."""
    if (event["provider"], event["event"]) not in kinds or name in event["automations"]:
        return False
    if event["provider"] == "schedule":
        # Each timer belongs to one automation: another automation's timer firing
        # is not one this left out, only its own while it was turned off.
        detail = event.get("detail") or ""
        return detail.startswith("turned off: ") and name in detail[
            len("turned off: ") :
        ].split(", ")
    return True


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
    from .architecture import PERSONAL_SESSION, claim_intake

    configs = parse_integrations(data.get("integrations"))
    from .schedule import ScheduleSource, project_schedules

    schedules = project_schedules(Path(manifest).parent)
    if (
        not configs
        and not schedules
        and not data.get("memory")
        and not os.environ.get("SHARED_FILES_HOST_PATH")
    ):
        return None

    from omniagents.core.paths import get_home_dir
    from omniagents.core.project.runtime import load_project
    from omniagents.core.sandbox.profiles import product_default_profile_path

    project = load_project(str(manifest))
    if not project.entrypoint:
        logger.warning("integrations: the project has no entrypoint agent; off")
        return None
    from omniagents.core.skills import default_skill_roots, set_skill_roots_provider

    # A factory's skills are its repository's and its repositories': never
    # the installation's unversioned global folder.
    set_skill_roots_provider(
        lambda workspace_root: default_skill_roots(workspace_root, include_global=False)
    )
    profile = product_default_profile_path(manifest)
    environments = None
    if profile is not None:
        environments = RunEnvironments(profile, parse_github(data.get("integrations")))
    else:
        logger.warning(
            "integrations: the project has no sandbox profile; "
            "its agents have no shared machine"
        )
    store = EventStore(get_home_dir() / "integrations" / f"{project.product.slug}.db")
    intake_lock = claim_intake(project)
    principal = _server_principal(app, auth_token)
    if environments:
        _attach_factory_workspace(app, project, resources, environments, principal)
    from .agent_runtimes import AgentRuntimes, AGENTS_PATH
    from .personal import ConversationRunner

    runtimes = AgentRuntimes(
        project,
        auth_token=auth_token,
        primary_app=app,
        primary_agent=spec.agent_slug,
        host_resources=resources,
    )

    async def run(agent, session_id, prompt, progress=None):
        """Every automation run is a turn on its agent's durable conversation."""
        runtime = await runtimes.app(agent)
        if runtime is None:
            raise ValueError(f"Unknown automation agent: {agent}")
        runtime.state.agent_service._release_gate = (
            app.state.agent_service._release_gate
        )
        return await ConversationRunner(runtime.state.agent_service, principal, agent)(
            agent, session_id, prompt, progress
        )

    # Presentation metadata belongs to the configured agents, not the host UI.
    def agent_details() -> List[Dict[str, str]]:
        details = []
        for agent_id, agent_path in project.agents_map.items():
            definition = yaml.safe_load(agent_path.read_text()) or {}
            details.append(
                {
                    "id": agent_id,
                    "name": definition.get("label")
                    or agent_id.replace("_", " ").replace("-", " ").title(),
                    "description": definition.get("description") or "",
                }
            )
        return details

    async def factory_status(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        service = app.state.agent_service
        busy = store.unsettled() + runtimes.busy()
        return JSONResponse(
            {
                "session_id": PERSONAL_SESSION,
                "routing_version": 1,
                "agents": sorted(project.agents_map),
                "chat_agent": project.chat_entrypoint,
                "chat_agents": project.chat_agents,
                "agent_details": agent_details(),
                "busy": busy,
                "version": os.environ.get("FACTORY_VERSION"),
                "release_protocol": 1,
                "event_gateway": os.environ.get("OMNIAGENTS_EVENT_GATEWAY") == "1",
                "release_paused": bool(
                    getattr(service, "_release_gate", None)
                    and service._release_gate.paused
                ),
                "stopped": factory_stop.current(),
            }
        )

    app.add_api_route("/integrations/architecture", factory_status, methods=["GET"])
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
    )
    app.state.event_intake = intake

    @app.get("/integrations/conversations")
    async def conversations(request: Request, session_id: str | None = None):
        if not await _authorized(request):
            return Response(status_code=401)
        return {"conversations": store.conversations(session_id)}

    @app.get("/integrations/automations")
    async def automations_view(request: Request, days: float = 7):
        """Each automation as a person reads it: what it does and what starts it,
        who runs it, its recent runs, and recent events of its kinds that it did
        not run for (a filter that excluded them, or no match at all)."""
        if not await _authorized(request):
            return Response(status_code=401)
        import time as clock

        from omniagents.core.automations import list_automations

        since = clock.time() - max(0.0, min(float(days), 90.0)) * 86400
        overrides = store.automation_routes()
        off = store.turned_off()
        from .self_improvement import allowed

        runs = await asyncio.to_thread(store.runs_since, since)
        events = await asyncio.to_thread(store.event_outcomes, since)
        out = []
        for a in list_automations(project.root):
            kinds = {(t.get("provider"), t.get("event")) for t in a.triggers}
            own = [r for r in runs if r["automation"] == a.name]
            # What stops it running at all, said so a person can fix it.
            blocked = None if await allowed(a) else "Self-improvement is off"
            out.append(
                {
                    "name": a.name,
                    "description": a.description,
                    "instruction": a.instruction,
                    "triggers": a.triggers,
                    "self_improvement": a.self_improvement,
                    "enabled": a.name not in off,
                    "agent": overrides.get(a.name, {}).get("agent")
                    or a.agent
                    or project.entrypoint,
                    "conversation": overrides.get(a.name, {}).get("conversation")
                    or a.conversation,
                    "runs": [
                        {
                            k: r.get(k)
                            for k in (
                                "id",
                                "agent",
                                "session_id",
                                "status",
                                "error",
                                "started_at",
                                "finished_at",
                                "provider",
                                "event",
                                "thread_key",
                                "grades",
                            )
                        }
                        for r in reversed(own)
                    ][:50],
                    "blocked": blocked,
                    "skipped": [e for e in events if _left_out(a.name, kinds, e)][:20],
                }
            )
        return {"automations": out, "since": since}

    @app.put("/integrations/automations/{name}/enabled")
    async def set_automation_enabled(name: str, request: Request):
        """Turn an automation on or off; off, its events start nothing."""
        if not await _authorized(request):
            return Response(status_code=401)
        from omniagents.core.automations import list_automations

        body = await request.json()
        if name not in {a.name for a in list_automations(project.root)}:
            return JSONResponse({"detail": "Unknown automation"}, status_code=404)
        if (
            not isinstance(body, dict)
            or set(body) != {"enabled"}
            or not isinstance(body["enabled"], bool)
        ):
            return JSONResponse(
                {"detail": "Expected {enabled: true|false}"}, status_code=422
            )
        store.set_automation_enabled(name, body["enabled"])
        return {"name": name, "enabled": body["enabled"]}

    @app.get("/integrations/automation-routes")
    async def automation_routes(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        from omniagents.core.automations import list_automations

        overrides = store.automation_routes()
        return {
            "agents": sorted(project.agents_map),
            "chat_agent": project.chat_entrypoint,
            "chat_agents": project.chat_agents,
            "automations": [
                {
                    "name": a.name,
                    "description": a.description,
                    "agent": overrides.get(a.name, {}).get("agent")
                    or a.agent
                    or project.entrypoint,
                    "conversation": overrides.get(a.name, {}).get("conversation")
                    or a.conversation,
                }
                for a in list_automations(project.root)
            ],
        }

    @app.put("/integrations/automation-routes/{name}")
    async def set_automation_route(name: str, request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        from omniagents.core.automations import list_automations

        body = await request.json()
        if name not in {a.name for a in list_automations(project.root)}:
            return JSONResponse({"detail": "Unknown automation"}, status_code=404)
        if (
            not isinstance(body, dict)
            or set(body) != {"agent", "conversation"}
            or body.get("agent") not in project.agents_map
            or body.get("conversation") not in {"auto", "thread", "new"}
        ):
            return JSONResponse(
                {"detail": "Choose an available agent and conversation grouping"},
                status_code=422,
            )
        store.set_automation_route(name, body)
        return {"ok": True}

    from omniagents.core.runtime.release_gate import ReleaseGate

    service = app.state.agent_service
    gate = ReleaseGate(
        lambda: (runtimes.busy() + len(intake._tasks) + store.unsettled())
    )
    pending_release = os.environ.get("FACTORY_RELEASE_PENDING")
    if pending_release and Path(pending_release).is_file():
        gate.owner = Path(pending_release).read_text().strip()
        gate.until = float("inf")  # Candidate cannot accept work before verification.
    service._release_gate = intake.release_gate = gate
    from omniagents.core.runtime import factory_stop

    factory_stop.set_state(store.stop_state())

    async def queued_conversations():
        """Every (agent, conversation, pending item ids) with queued work."""
        from omniagents.core.conversation.model import ThreadFilter
        from omniagents.core.conversation.store import get_conversation_store
        from omniagents.core.project.runtime import build_spec_from_project
        from omniagents.rpc.auth import ANONYMOUS_USER_ID

        identity = await principal()
        owner = identity.user_id if identity else ANONYMOUS_USER_ID
        for agent in project.agents_map:
            agent_spec = build_spec_from_project(project, agent)
            conversations = get_conversation_store(
                agent_spec.project_slug, agent_spec.agent_slug
            )
            after = None
            while True:
                threads = conversations.list_threads(
                    ThreadFilter(user_id=owner), limit=100, after=after
                )
                for thread in threads:
                    pending = conversations.mutate_queue(
                        thread.thread_id,
                        lambda state, receipt: (
                            [
                                entry["item"]["id"]
                                for entry in state.get("items", [])
                                if entry.get("item", {}).get("state") == "pending"
                            ],
                            None,
                        ),
                    )
                    if pending:
                        yield agent, thread.thread_id, pending
                if len(threads) < 100:
                    break
                last = threads[-1]
                after = (last.updated_at, last.thread_id)

    async def resume_conversations():
        if gate.paused or factory_stop.current() is not None:
            return
        async for agent, thread_id, _pending in queued_conversations():
            runtime = await runtimes.app(agent)
            await ConversationRunner(
                runtime.state.agent_service, principal, agent
            ).resume(thread_id)

    async def stop_factory(state: dict) -> dict:
        return await stop_all_work(state, store, intake, runtimes, queued_conversations)

    app.router.on_startup.append(resume_conversations)
    app.router.on_startup.append(intake.resume_received)

    async def release_lease(request: Request):
        import hmac

        expected = os.environ.get("OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN", "")
        supplied = request.headers.get("authorization", "")
        if not expected or not hmac.compare_digest(supplied, "Bearer " + expected):
            return Response(status_code=403)
        try:
            if request.method == "POST":
                return JSONResponse({"lease": gate.acquire(), "expires_in": 120})
            body = await request.json()
            gate.release(body.get("lease"))
            await resume_conversations()
            if pending_release:
                Path(pending_release).unlink(missing_ok=True)
            return JSONResponse({"ok": True})
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except (ValueError, AttributeError):
            return JSONResponse({"detail": "Invalid release lease"}, status_code=409)

    app.add_api_route(
        "/integrations/release-lease", release_lease, methods=["POST", "DELETE"]
    )

    async def stop_control(request: Request):
        """``POST`` stops the factory (``{stopped_by, stopped_at, reason}``),
        ``DELETE`` resumes it, ``GET`` says which; whoever runs the factory
        holds the control token."""
        import hmac

        expected = os.environ.get("OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN", "")
        supplied = request.headers.get("authorization", "")
        if not expected or not hmac.compare_digest(supplied, "Bearer " + expected):
            return Response(status_code=403)
        if request.method == "GET":
            return JSONResponse({"stopped": factory_stop.current()})
        if request.method == "DELETE":
            store.set_stop_state(None)
            factory_stop.set_state(None)
            logger.warning("integrations: factory resumed")
            return JSONResponse({"stopped": None})
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                {"detail": "Give the stop as an object"}, status_code=422
            )
        state = {
            key: body[key]
            for key in ("stopped_by", "stopped_at", "reason", "scope")
            if body.get(key) is not None
        }
        return JSONResponse(await stop_factory(state))

    app.add_api_route(
        "/integrations/stop", stop_control, methods=["GET", "POST", "DELETE"]
    )
    # The factory's version, from whoever runs it (the supervisor: the commit it promoted).
    intake.version = os.environ.get("FACTORY_VERSION") or None
    from omniagents.core.agents.handoffs import set_handler

    # Agents hand work to each other's own conversations through the intake.
    set_handler(intake.hand_off)

    def reload_agents() -> None:
        """Agents' settings, agents added or deleted, or the project's agents
        section changed by a published edit: the running project takes the new
        definitions, and each running agent's next run is built from its new
        settings (an agent not running yet is built on first use)."""
        from omniagents.core.project.runtime import build_spec_from_project

        fresh = load_project(str(manifest))
        for field in dataclasses.fields(project):
            setattr(project, field.name, getattr(fresh, field.name))
        running = dict(runtimes._apps)
        if runtimes.primary_app is not None and runtimes.primary_agent:
            running[runtimes.primary_agent] = runtimes.primary_app
        for key, runtime in running.items():
            if key not in project.agents_map:
                continue  # deleted: no new conversations reach it
            service = runtime.state.agent_service
            spec = build_spec_from_project(project, key)
            spec.project_slug = service.spec.project_slug
            spec.agent_slug = service.spec.agent_slug
            service.spec = spec
        logger.info(
            "integrations: agents reloaded: %s", ", ".join(sorted(project.agents_map))
        )

    _mount_definition_routes(
        app, project.root, intake, data.get("integrations"), reload_agents
    )
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
    from .shared import mount_shared_route

    mount_shared_route(app, intake, _authorized)
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
    _mount_runs_route(app, store, _price_list(project.root, data), project.root)
    # Each agent's sessions, for the shared chat UI to read (agent_runtimes.py).
    from .agent_runtimes import AGENTS_PATH, AgentRuntimes

    app.mount(AGENTS_PATH, runtimes)

    def session_started(agent_key: str, thread_id: str) -> Optional[float]:
        from omniagents.core.conversation.store import get_conversation_store

        spec = build_spec_from_project(project, agent_key)
        thread = get_conversation_store(spec.project_slug, spec.agent_slug).get_thread(
            thread_id
        )
        return thread.created_at if thread else None

    _mount_case_route(app, store, history, children, session_started, project.root)

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
    app.router.on_shutdown.append(intake_lock.close)
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


def _mount_definition_routes(
    app: Any,
    root: Path,
    intake: EventIntake,
    integrations: Any,
    reload_agents: Callable[[], None] = lambda: None,
) -> None:
    """What people edit in the factory (``GET /integrations/definition``),
    checking edits before they are saved (``POST .../check``), and moving
    the factory to the commit that saved them (``POST .../advance``)."""
    from . import definition

    lock = asyncio.Lock()

    @app.get("/integrations/definition")
    async def definition_tree(request: Request):
        if not await _authorized(request):
            return Response(status_code=401)
        tree = await asyncio.to_thread(definition.definition_tree, root)
        commit = await asyncio.to_thread(definition.live_commit, root)
        return {
            **tree,
            "commit": commit,
            "origin": await asyncio.to_thread(definition.origin, root),
            "uncommitted": (
                await asyncio.to_thread(definition.uncommitted, root) if commit else []
            ),
        }

    @app.post("/integrations/definition/check")
    async def check_definition(request: Request):
        """``{"changes": [{"path", "content" | null}]}``: each path's problems."""
        if not await _authorized(request):
            return Response(status_code=401)
        body = await request.json()
        changes = body.get("changes") if isinstance(body, dict) else None
        if not isinstance(changes, list) or not all(
            isinstance(c, dict) and isinstance(c.get("path"), str) for c in changes
        ):
            return JSONResponse(
                {"detail": "Expected {changes: [{path, content}]}"}, status_code=422
            )
        problems = await asyncio.to_thread(definition.check_changes, root, changes)
        return {"problems": problems}

    @app.post("/integrations/definition/new-agent")
    async def new_agent(request: Request):
        """``{key, description, template?, chat?}``: the files that add an agent."""
        if not await _authorized(request):
            return Response(status_code=401)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("key"), str):
            return JSONResponse(
                {"detail": "Expected {key, description}"}, status_code=422
            )
        try:
            changes = await asyncio.to_thread(
                definition.new_agent_changes,
                root,
                body["key"],
                description=str(body.get("description") or ""),
                template=body.get("template") or None,
                chat=bool(body.get("chat")),
            )
        except definition.DefinitionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        return {"changes": changes}

    @app.get("/integrations/definition/agents/{key}")
    async def agent_settings(key: str, request: Request):
        """An agent's settings as fields, what it inherits, and the choices."""
        if not await _authorized(request):
            return Response(status_code=401)
        try:
            return await asyncio.to_thread(definition.agent_settings, root, key)
        except definition.DefinitionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.post("/integrations/definition/agents/{key}/changes")
    async def agent_changes(key: str, request: Request):
        """``{settings}``: the files that give an agent those settings, or
        (``{"delete": true}``) that delete it."""
        if not await _authorized(request):
            return Response(status_code=401)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                {"detail": "Expected {settings} or {delete}"}, status_code=422
            )
        try:
            if body.get("delete") is True:
                changes = await asyncio.to_thread(
                    definition.delete_agent_changes, root, key
                )
            elif isinstance(body.get("settings"), dict):
                changes = await asyncio.to_thread(
                    definition.agent_changes, root, key, body["settings"]
                )
            else:
                return JSONResponse(
                    {"detail": "Expected {settings} or {delete}"}, status_code=422
                )
        except definition.DefinitionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        return {"changes": changes}

    @app.post("/integrations/definition/advance")
    async def advance_definition(request: Request):
        """``{"commit"}``: run that commit of the factory's repository from now on."""
        if not await _authorized(request):
            return Response(status_code=401)
        body = await request.json()
        commit = body.get("commit") if isinstance(body, dict) else None
        github = parse_github(integrations)
        token = github.resolved_token() if github else None
        async with lock:
            try:
                moved = await asyncio.to_thread(
                    definition.advance, root, str(commit or ""), token=token
                )
            except definition.DefinitionError as exc:
                return JSONResponse(
                    {"detail": str(exc), "paths": exc.paths}, status_code=exc.status
                )
            if moved["reload"]:
                await asyncio.to_thread(reload_agents)
        live = moved["commit"]
        # Runs from now on are recorded with it (the supervisor restarts on it too).
        os.environ["FACTORY_VERSION"] = live
        intake.version = live
        return {"commit": live, "reloaded": moved["reload"]}


def _mount_runs_route(
    app: Any, store: EventStore, prices: Any, root: Optional[Path] = None
) -> None:
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
                        "skills": _run_skills(store, run["id"], root),
                    }
                )
            return out

        return JSONResponse(
            {"runs": await asyncio.to_thread(read), "priced": prices is not None}
        )

    app.add_api_route(RUNS_PATH, runs, methods=["GET"])


def _run_skills(store: EventStore, dispatch_id: int, root: Optional[Path]) -> Dict:
    """The skills a run was offered and loaded, each with where it came from."""
    from .definition import skill_source

    used = store.run_skills(dispatch_id)
    return {
        part: [
            {
                "name": s["name"],
                "source": (
                    skill_source(root, s["path"]) if root else {"kind": "unknown"}
                ),
            }
            for s in used.get(part, [])
        ]
        for part in ("offered", "loaded")
    }


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
    root: Optional[Path] = None,
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
                "skills": _run_skills(store, dispatch_id, root),
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
    """The factory's shared machine: every agent's tools run on it, and it
    survives restarts (its identity is saved, so the next start reattaches)."""
    from omniagents.core.paths import get_home_dir

    # The filename predates the shared machine being unconditional; existing
    # factories and the backup/reset/restore commands use it.
    context = environments.sandbox(
        "factory",
        state_path=get_home_dir()
        / "integrations"
        / f"{project.product.slug}.personal-sandbox.json",
    )

    from .desktop import mount_desktop

    active_workspace = None
    mount_desktop(app, lambda: active_workspace, principal)

    async def start_workspace():
        nonlocal active_workspace
        sandbox = await context.__aenter__()
        active_workspace = sandbox
        owner = await principal()
        sandbox.register(resources, owner_user_id=owner.user_id if owner else None)

    async def stop_workspace():
        nonlocal active_workspace
        active_workspace = None
        await context.__aexit__(None, None, None)

    app.router.on_startup.insert(0, start_workspace)
    app.router.on_shutdown.append(stop_workspace)


async def stop_all_work(state, store, intake, runtimes, queued_conversations) -> dict:
    """The kill switch: end all AI work now, and start none until resumed.

    Admission, model and tool calls refuse from the moment the state is set;
    then every run, queued message and event in progress is cancelled and
    every sandbox command ended. Queued work is removed, not deferred, so
    resuming replays nothing."""
    from omniagents.core.runtime import factory_stop

    store.set_stop_state(state)
    factory_stop.set_state(state)
    events = intake.cancel_all()
    runs = 0
    for agent_service in runtimes.services():
        for run_id in list(agent_service.active_run_ids()):
            try:
                runs += bool(await agent_service.stop_run(run_id))
            except Exception:  # noqa: BLE001 - keep stopping the rest
                logger.exception("factory stop: run %s did not stop", run_id)
    queued = 0
    async for agent, thread_id, pending in queued_conversations():
        runtime = await runtimes.app(agent)
        for item_id in pending:
            result = await runtime.state.agent_service.cancel_queued_message(
                thread_id, item_id
            )
            queued += bool(result.get("ok"))
    sandboxes = await factory_stop.terminate_sandbox_commands()
    logger.warning(
        "integrations: factory stopped (%s): %d run(s), %d queued message(s), "
        "%d event(s) cancelled; commands ended in %d sandbox(es)",
        state.get("reason") or "no reason given",
        runs,
        queued,
        events,
        sandboxes,
    )
    return {
        "stopped": state,
        "runs": runs,
        "queued": queued,
        "events": events,
        "sandboxes": sandboxes,
    }


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
