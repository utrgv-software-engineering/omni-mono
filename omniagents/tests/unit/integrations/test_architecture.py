import asyncio
from types import SimpleNamespace

import pytest

from omniagents.integrations.architecture import PERSONAL_SESSION, claim_intake
from omniagents.integrations.agent_runtimes import AgentRuntimes
from omniagents.integrations.intake import EventIntake, RunResult
from omniagents.integrations.store import EventStore
from omniagents.integrations.events import IncomingEvent


@pytest.mark.asyncio
async def test_each_automation_routes_to_its_own_agent(tmp_path, monkeypatch):
    from omniagents.integrations import self_improvement

    monkeypatch.setattr(
        self_improvement,
        "policy",
        lambda: {"enabled": True, "repository": "org/factory"},
    )
    for name, agent, provider, event in [
        ("mention", "", "slack", "app_mention"),
        ("review", "code_reviewer", "github", "pr_opened"),
        ("improve", "improver", "schedule", "cron_fired"),
    ]:
        directory = tmp_path / "automations" / name
        directory.mkdir(parents=True)
        assignment = f"agent: {agent}\n" if agent else ""
        (directory / "automation.md").write_text(
            f"---\n{assignment}triggers:\n  - provider: {provider}\n    event: {event}\n---\nHandle {name}.\n"
        )
    calls = []

    async def run(agent, session, prompt, progress):
        calls.append((agent, session, prompt))
        return RunResult(output="done")

    store = EventStore(tmp_path / "events.db")
    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="foreman",
        store=store,
        run=run,
    )
    for provider, event in [
        ("slack", "app_mention"),
        ("github", "pr_opened"),
        ("schedule", "cron_fired"),
    ]:
        incoming = IncomingEvent(
            provider=provider,
            event=event,
            delivery_id=provider,
            thread_key=provider,
            payload={"text": "request"},
        )
        assert await intake.accept(incoming)
        assert not await intake.accept(incoming)
    await intake.drain()
    assert len(calls) == 3
    assert {a for a, _, _ in calls} == {
        "foreman",
        "code_reviewer",
        "improver",
    }
    assert len({sid for _, sid, _ in calls}) == 3
    assert all("Source:" in prompt for _, _, prompt in calls)
    store.close()


@pytest.mark.asyncio
async def test_configured_entrypoint_reuses_primary_runtime():
    project = SimpleNamespace(
        agents_map={"coordinator": object()}, entrypoint="coordinator"
    )
    primary = object()
    runtime = AgentRuntimes(project, auth_token="t", primary_app=primary)
    assert await runtime.app("coordinator") is primary
    assert await runtime.app("missing") is None


def test_factory_has_one_intake_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    project = SimpleNamespace(product=SimpleNamespace(slug="factory"))
    handle = claim_intake(project)
    with pytest.raises(BlockingIOError):
        claim_intake(project)
    handle.close()


def test_workspace_maintenance_refuses_while_the_factory_runs(tmp_path, monkeypatch):
    from omniagents.integrations.personal_workspace import maintenance_lock

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    handle = claim_intake(SimpleNamespace(product=SimpleNamespace(slug="factory")))
    state = tmp_path / "integrations" / "factory.personal-sandbox.json"
    with pytest.raises(SystemExit, match="Stop the factory"):
        maintenance_lock(state)
    handle.close()
    maintenance_lock(state).close()


@pytest.mark.asyncio
async def test_personal_alias_uses_primary_runtime_without_second_service():
    from fastapi import FastAPI, WebSocket
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("shared")
        await socket.close()

    app.mount(
        "/agents",
        AgentRuntimes(
            SimpleNamespace(agents_map={}, entrypoint="generalist"),
            auth_token=None,
            primary_app=app,
        ),
    )
    with TestClient(app) as client:
        with client.websocket_connect("/agents/generalist/ws") as socket:
            assert socket.receive_text() == "shared"
        assert client.get("/agents/foreman/rest/x").status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("silent", [False, True])
async def test_automation_uses_real_queue_and_correlates_its_own_answer(
    tmp_path, monkeypatch, silent
):
    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.agents.service import AgentService
    from omniagents.execution import AgentHostResources
    from omniagents.integrations.personal import PersonalRunner
    from omniagents.core.session.submissions import submit_once

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    spec = AgentSpec(
        name="generalist",
        welcome_text="",
        get_agent_instructions=lambda: "",
        build_agent=None,
        project_slug="factory",
        agent_slug="generalist",
    )
    service = AgentService(spec, AgentHostResources.create("test-personal"))
    starts = []
    history = []
    turn_items = {}

    async def start_run(prompt, session_id, submission_id, **kwargs):
        session = service._session_manager.get(session_id)

        async def invoke():
            run_id = f"run-{len(starts)}"
            starts.append(prompt)
            session.active_run_id = run_id

            async def finish():
                await asyncio.sleep(0.01)
                history.append({"role": "assistant", "content": "Answer: " + prompt})
                turn_items[run_id] = (
                    []
                    if silent and prompt == "event"
                    else [
                        SimpleNamespace(
                            kind="agent_message", content={"text": "Answer: " + prompt}
                        )
                    ]
                )
                session.active_run_id = None
                await service._broadcast_session(
                    session,
                    "run_end",
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "end_reason": "completed",
                    },
                )

            session.current_task = asyncio.create_task(finish())
            return {"run_id": run_id, "session_id": session_id}

        return await submit_once(
            session,
            service._queue_store(session),
            submission_id,
            {"prompt": prompt},
            invoke,
        )

    async def get_history(session_id):
        return list(history)

    monkeypatch.setattr(service, "start_run", start_run)
    monkeypatch.setattr(service, "get_session_history", get_history)

    async def principal():
        return SimpleNamespace(user_id="token_user", roles=[])

    store = service._conversation_service().store
    monkeypatch.setattr(
        store, "get_turn", lambda *a: SimpleNamespace(created_at=1, completed_at=2)
    )
    monkeypatch.setattr(
        store, "list_items", lambda sid, *, turn_id, limit: turn_items.get(turn_id, [])
    )
    runner = PersonalRunner(service, principal, "generalist")
    await runner.resume(PERSONAL_SESSION)
    # A chat turn ahead of the event must not be mistaken for its result.
    await service.enqueue_message(PERSONAL_SESSION, "chat")
    result = await asyncio.wait_for(runner("generalist", PERSONAL_SESSION, "event"), 5)
    assert starts == ["chat", "event"]
    assert result.output == (None if silent else "Answer: event")
    assert result.history == (
        [] if silent else [{"role": "assistant", "content": "Answer: event"}]
    )
    assert result.error is None
    session = service._session_manager.get(PERSONAL_SESSION)
    assert session.user_id == "token_user"
    if session.drainer_task:
        await session.drainer_task


def test_shared_reports_exclude_other_turns(tmp_path):
    from omniagents.integrations.links import PullRequestLinks

    store = EventStore(tmp_path / "events.db")
    event = IncomingEvent(
        provider="slack", event="app_mention", delivery_id="e", payload={}
    )
    event_id = store.record(event)
    dispatch = store.start_dispatch(event_id, "mention", "generalist", PERSONAL_SESSION)
    unrelated = [
        {"type": "function_call", "name": "create_pull_request", "call_id": "old"},
        {
            "type": "function_call_output",
            "call_id": "old",
            "output": "https://github.com/acme/repo/pull/9",
        },
    ]
    store.record_turn(dispatch, [], 20, 30)
    links = PullRequestLinks(
        store, lambda *a: unrelated, lambda *a: [("worker", "old", unrelated)]
    )
    links.record(dispatch, "generalist", PERSONAL_SESSION, event)
    assert store.run_pull_requests(dispatch) == []
    for timestamp in [10, 25, 40]:
        store.record_usage(
            {
                "at": timestamp,
                "session_id": PERSONAL_SESSION,
                "model": "test",
                "agent": "generalist",
                "input_tokens": 1,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
    assert store.run_usage(dispatch)[0]["input_tokens"] == 1
    assert store.run_history(dispatch) == []
    store.close()


@pytest.mark.asyncio
async def test_every_automation_run_is_a_conversation_turn(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from omniagents.integrations import personal
    from omniagents.integrations.server import mount_integrations

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OMNIAGENTS_SANDBOX_PROFILE", raising=False)
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    root = tmp_path / "factory"
    (root / "agents" / "coordinator").mkdir(parents=True)
    (root / "agents" / "coordinator" / "agent.yml").write_text("name: coordinator\n")
    (root / "automations" / "nightly").mkdir(parents=True)
    (root / "automations" / "nightly" / "automation.md").write_text(
        "---\ntriggers:\n  - provider: schedule\n    event: cron_fired\n---\nReview.\n"
    )
    # No `runtime:` section: nothing opts into the conversation path.
    (root / "project.yml").write_text(
        "product:\n  name: turn-test\nagents:\n  entrypoint: coordinator\n"
        "memory:\n  url: http://memory.invalid\n"
    )
    turns = []

    async def turn(self, agent, session_id, prompt, progress=None):
        turns.append((self.agent, session_id))
        return RunResult(output="done")

    monkeypatch.setattr(personal.ConversationRunner, "__call__", turn)
    app = FastAPI()
    app.state.agent_service = SimpleNamespace(_release_gate=None)
    spec = SimpleNamespace(
        manifest_path=str(root / "project.yml"), agent_slug="coordinator"
    )
    intake = mount_integrations(app, spec, SimpleNamespace())
    assert intake is not None
    await intake.accept(
        IncomingEvent(
            provider="schedule", event="cron_fired", delivery_id="d", payload={}
        )
    )
    await intake.drain()
    assert [agent for agent, _ in turns] == ["coordinator"]
    intake.store.close()


@pytest.mark.asyncio
async def test_automation_steps_are_reported_and_only_its_own(tmp_path, monkeypatch):
    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.agents.service import AgentService
    from omniagents.execution import AgentHostResources
    from omniagents.integrations.personal import ConversationRunner
    from omniagents.core.session.submissions import submit_once

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    spec = AgentSpec(
        name="generalist",
        welcome_text="",
        get_agent_instructions=lambda: "",
        build_agent=None,
        project_slug="factory",
        agent_slug="generalist",
    )
    service = AgentService(spec, AgentHostResources.create("test-steps"))
    starts = []

    async def start_run(prompt, session_id, submission_id, **kwargs):
        session = service._session_manager.get(session_id)

        async def invoke():
            run_id = f"run-{len(starts)}"
            starts.append(prompt)
            session.active_run_id = run_id

            async def finish():
                await asyncio.sleep(0.01)
                await service._broadcast_session(
                    session,
                    "tool_called",
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "tool": prompt + "_tool",
                    },
                )
                await service._broadcast_session(
                    session,
                    "message_output",
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "content": "Did " + prompt,
                    },
                )
                session.active_run_id = None
                await service._broadcast_session(
                    session,
                    "run_end",
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "end_reason": "completed",
                    },
                )

            session.current_task = asyncio.create_task(finish())
            return {"run_id": run_id, "session_id": session_id}

        return await submit_once(
            session,
            service._queue_store(session),
            submission_id,
            {"prompt": prompt},
            invoke,
        )

    async def get_history(session_id):
        return []

    monkeypatch.setattr(service, "start_run", start_run)
    monkeypatch.setattr(service, "get_session_history", get_history)
    store = service._conversation_service().store
    monkeypatch.setattr(
        store, "get_turn", lambda *a: SimpleNamespace(created_at=1, completed_at=2)
    )
    monkeypatch.setattr(store, "list_items", lambda sid, *, turn_id, limit: [])

    async def principal():
        return SimpleNamespace(user_id="token_user", roles=[])

    reported = []

    async def progress(step):
        await asyncio.sleep(0.01)  # a slow report must not reorder or drop steps
        reported.append((step.kind, step.name or step.text))

    runner = ConversationRunner(service, principal, "generalist")
    await runner.resume("conversation")
    await service.enqueue_message("conversation", "chat")
    result = await asyncio.wait_for(
        runner("generalist", "conversation", "event", progress), 5
    )
    assert starts == ["chat", "event"]
    assert result.error is None
    assert reported == [("tool", "event_tool"), ("message", "Did event")]
    session = service._session_manager.get("conversation")
    if session.drainer_task:
        await session.drainer_task


@pytest.mark.asyncio
async def test_automations_view_shows_triggers_runs_and_events_it_skipped(
    tmp_path, monkeypatch
):
    import httpx
    from fastapi import FastAPI
    from omniagents.integrations import personal
    from omniagents.integrations.server import mount_integrations

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OMNIAGENTS_SANDBOX_PROFILE", raising=False)
    root = tmp_path / "factory"
    (root / "agents" / "coordinator").mkdir(parents=True)
    (root / "agents" / "coordinator" / "agent.yml").write_text("name: coordinator\n")
    for name, front in [
        ("nightly", "triggers:\n  - provider: schedule\n    event: cron_fired\n"),
        (
            "review",
            "agent: coordinator\ntriggers:\n  - provider: github\n    event: pull_request_opened\n"
            "    filter:\n      repos: ['*/api-service']\n",
        ),
    ]:
        (root / "automations" / name).mkdir(parents=True)
        (root / "automations" / name / "automation.md").write_text(
            f"---\n{front}---\nDo the {name} work.\n"
        )
    (root / "project.yml").write_text(
        "product:\n  name: view-test\nagents:\n  entrypoint: coordinator\n"
        "memory:\n  url: http://memory.invalid\n"
    )

    async def turn(self, agent, session_id, prompt, progress=None):
        return RunResult(output="done")

    monkeypatch.setattr(personal.ConversationRunner, "__call__", turn)
    from omniagents.rpc.auth import ConnectionAuthenticator

    app = FastAPI()
    app.state.agent_service = SimpleNamespace(_release_gate=None)
    app.state.connection_authenticator = ConnectionAuthenticator.from_spec(
        SimpleNamespace(security_config={}), auth_token="t"
    )
    spec = SimpleNamespace(
        manifest_path=str(root / "project.yml"), agent_slug="coordinator"
    )
    intake = mount_integrations(app, spec, SimpleNamespace())
    await intake.accept(
        IncomingEvent(
            provider="schedule", event="cron_fired", delivery_id="s", payload={}
        )
    )
    # A pull request in another repository: the review's filter leaves it out.
    await intake.accept(
        IncomingEvent(
            provider="github",
            event="pull_request_opened",
            delivery_id="g",
            thread_key="acme/webapp#3",
            payload={},
            filters={"repos": ["acme/webapp"]},
        )
    )
    await intake.drain()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        assert (await client.get("/integrations/automations")).status_code == 401
        data = (
            await client.get(
                "/integrations/automations", headers={"Authorization": "Bearer t"}
            )
        ).json()
    by_name = {a["name"]: a for a in data["automations"]}
    assert by_name["nightly"]["instruction"] == "Do the nightly work."
    assert by_name["nightly"]["agent"] == "coordinator"
    assert [r["status"] for r in by_name["nightly"]["runs"]] == ["completed"]
    assert by_name["review"]["triggers"][0]["filter"] == {"repos": ["*/api-service"]}
    assert by_name["review"]["runs"] == []
    assert [e["thread_key"] for e in by_name["review"]["skipped"]] == ["acme/webapp#3"]
    assert by_name["nightly"]["skipped"] == []
    assert by_name["nightly"]["enabled"] is True

    # Turned off, an automation's events start nothing and say why.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        auth = {"Authorization": "Bearer t"}
        off = await client.put(
            "/integrations/automations/nightly/enabled",
            json={"enabled": False},
            headers=auth,
        )
        assert off.json() == {"name": "nightly", "enabled": False}
        assert (
            await client.put(
                "/integrations/automations/nope/enabled",
                json={"enabled": False},
                headers=auth,
            )
        ).status_code == 404
        assert (
            await client.put(
                "/integrations/automations/nightly/enabled",
                json={"enabled": "no"},
                headers=auth,
            )
        ).status_code == 422
        await intake.accept(
            IncomingEvent(
                provider="schedule", event="cron_fired", delivery_id="s2", payload={}
            )
        )
        await intake.drain()
        nightly = next(
            a
            for a in (
                await client.get("/integrations/automations", headers=auth)
            ).json()["automations"]
            if a["name"] == "nightly"
        )
        assert nightly["enabled"] is False and len(nightly["runs"]) == 1
        assert nightly["skipped"][0]["detail"] == "turned off: nightly"
        await client.put(
            "/integrations/automations/nightly/enabled",
            json={"enabled": True},
            headers=auth,
        )
        await intake.accept(
            IncomingEvent(
                provider="schedule", event="cron_fired", delivery_id="s3", payload={}
            )
        )
        await intake.drain()
        nightly = next(
            a
            for a in (
                await client.get("/integrations/automations", headers=auth)
            ).json()["automations"]
            if a["name"] == "nightly"
        )
        assert len(nightly["runs"]) == 2
    intake.store.close()


@pytest.mark.asyncio
async def test_publishing_an_edit_moves_the_factory_and_its_runs(tmp_path, monkeypatch):
    """The Workplace saves an edit to the factory's repository and asks the
    factory to run it: the checkout moves, and the next run is recorded with it."""
    import subprocess

    import httpx
    from fastapi import FastAPI
    from omniagents.integrations import personal
    from omniagents.integrations.server import mount_integrations
    from omniagents.rpc.auth import ConnectionAuthenticator

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OMNIAGENTS_SANDBOX_PROFILE", raising=False)
    monkeypatch.delenv("FACTORY_VERSION", raising=False)
    source = tmp_path / "source"
    (source / "agents" / "coordinator").mkdir(parents=True)
    (source / "agents" / "coordinator" / "agent.yml").write_text(
        "name: coordinator\ninstructions_file: instructions.md\n"
    )
    (source / "agents" / "coordinator" / "instructions.md").write_text("Before.\n")
    (source / "automations" / "nightly").mkdir(parents=True)
    (source / "automations" / "nightly" / "automation.md").write_text(
        "---\ntriggers:\n  - provider: schedule\n    event: cron_fired\n---\nWork.\n"
    )
    (source / "project.yml").write_text(
        "product:\n  name: edit-test\nagents:\n  entrypoint: coordinator\n"
        "memory:\n  url: http://memory.invalid\n"
    )
    git(source, "init", "-q", "-b", "main")
    git(source, "add", "-A")
    git(source, "commit", "-qm", "Start")
    git(tmp_path, "clone", "-q", "--bare", str(source), str(tmp_path / "origin.git"))
    root = tmp_path / "live"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(root))
    (source / "agents" / "coordinator" / "instructions.md").write_text("After.\n")
    git(source, "commit", "-qam", "Edit coordinator instructions")
    git(source, "push", "-q", str(tmp_path / "origin.git"), "main")
    edit = git(source, "rev-parse", "HEAD")

    async def turn(self, agent, session_id, prompt, progress=None):
        return RunResult(output="done")

    monkeypatch.setattr(personal.ConversationRunner, "__call__", turn)
    app = FastAPI()
    app.state.agent_service = SimpleNamespace(
        _release_gate=None, active_run_ids=lambda: [], active_sessions=lambda: []
    )
    app.state.connection_authenticator = ConnectionAuthenticator.from_spec(
        SimpleNamespace(security_config={}), auth_token="t"
    )
    spec = SimpleNamespace(
        manifest_path=str(root / "project.yml"), agent_slug="coordinator"
    )
    intake = mount_integrations(app, spec, SimpleNamespace())
    auth = {"Authorization": "Bearer t"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        assert (await client.get("/integrations/definition")).status_code == 401
        tree = (await client.get("/integrations/definition", headers=auth)).json()
        assert tree["agents"][0]["instructions"] == "agents/coordinator/instructions.md"
        assert tree["uncommitted"] == []
        checked = await client.post(
            "/integrations/definition/check",
            json={"changes": [{"path": "project.yml", "content": "x"}]},
            headers=auth,
        )
        assert list(checked.json()["problems"]) == ["project.yml"]
        moved = await client.post(
            "/integrations/definition/advance", json={"commit": edit}, headers=auth
        )
        assert moved.json() == {"commit": edit, "reloaded": False}
        # A new agent in Chat: the running factory has it once it moves.
        added = (
            await client.post(
                "/integrations/definition/new-agent",
                json={"key": "designer", "description": "Designs.", "chat": True},
                headers=auth,
            )
        ).json()["changes"]
        for change in added:
            target = source / change["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change["content"])
        git(source, "add", "-A")
        git(source, "commit", "-qm", "Add agent Designer")
        git(source, "push", "-q", str(tmp_path / "origin.git"), "main")
        agent = git(source, "rev-parse", "HEAD")
        status = (await client.get("/integrations/architecture", headers=auth)).json()
        assert "designer" not in status["agents"]
        moved = await client.post(
            "/integrations/definition/advance", json={"commit": agent}, headers=auth
        )
        assert moved.json() == {"commit": agent, "reloaded": True}
        status = (await client.get("/integrations/architecture", headers=auth)).json()
        assert "designer" in status["agents"] and "designer" in status["chat_agents"]
        assert {
            "id": "designer",
            "name": "Designer",
            "description": "Designs.",
        } in status["agent_details"]
        edit = agent
        refused = await client.post(
            "/integrations/definition/advance", json={"commit": "nope"}, headers=auth
        )
        assert refused.status_code == 422
    assert (
        root / "agents" / "coordinator" / "instructions.md"
    ).read_text() == "After.\n"
    await intake.accept(
        IncomingEvent(
            provider="schedule", event="cron_fired", delivery_id="s", payload={}
        )
    )
    await intake.drain()
    (run,) = intake.store.runs_since(0)
    assert run["version"] == edit
