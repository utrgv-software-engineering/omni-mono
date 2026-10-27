import asyncio
from types import SimpleNamespace

import pytest

from omniagents.integrations.architecture import (
    factory_architecture,
    PERSONAL_SESSION,
    claim_architecture,
)
from omniagents.integrations.agent_runtimes import AgentRuntimes
from omniagents.integrations.intake import EventIntake, RunResult
from omniagents.integrations.store import EventStore
from omniagents.integrations.events import IncomingEvent


def test_invalid_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "both")
    with pytest.raises(ValueError):
        factory_architecture()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["personal", "specialist"])
async def test_only_selected_architecture_receives_every_event(
    tmp_path, mode, monkeypatch
):
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
        entrypoint="generalist" if mode == "personal" else "foreman",
        store=store,
        run=run,
        shared_session=PERSONAL_SESSION if mode == "personal" else None,
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
    if mode == "personal":
        assert {(a, s) for a, s, _ in calls} == {("generalist", PERSONAL_SESSION)}
        assert all("Source:" in prompt for _, _, prompt in calls)
    else:
        assert {a for a, _, _ in calls} == {"foreman", "code_reviewer", "improver"}
        assert len({s for _, s, _ in calls}) == 3
    store.close()


@pytest.mark.asyncio
async def test_inactive_agent_endpoints_are_not_served():
    project = SimpleNamespace(agents_map={"generalist": object(), "foreman": object()})
    primary = object()
    runtime = AgentRuntimes(
        project, auth_token="t", primary_app=primary, architecture="personal"
    )
    assert await runtime.app("generalist") is primary
    assert await runtime.app("foreman") is None
    runtime = AgentRuntimes(project, auth_token="t", architecture="specialist")
    assert await runtime.app("generalist") is None


def test_switch_refused_with_active_automation(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "personal")
    project = SimpleNamespace(product=SimpleNamespace(slug="factory"), agents_map={})
    with pytest.raises(RuntimeError, match="automation work is active"):
        claim_architecture(project, SimpleNamespace(running=lambda: 1))
    handle = claim_architecture(project, SimpleNamespace(running=lambda: 0))
    with pytest.raises(BlockingIOError):
        claim_architecture(project, SimpleNamespace(running=lambda: 0))
    handle.close()


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
            SimpleNamespace(agents_map={}),
            auth_token=None,
            primary_app=app,
            architecture="personal",
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
    runner = PersonalRunner(service, principal)
    await runner.resume()
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


@pytest.mark.parametrize("busy_kind", ["queue", "lease"])
def test_switch_refused_with_active_conversation(tmp_path, monkeypatch, busy_kind):
    import time
    import omniagents.core.project.runtime as runtime
    from omniagents.core.conversation.store import get_conversation_store
    from omniagents.core.conversation.model import Thread

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "specialist")
    project = SimpleNamespace(
        product=SimpleNamespace(slug="factory"), agents_map={"generalist": object()}
    )
    monkeypatch.setattr(
        runtime,
        "build_spec_from_project",
        lambda *a: SimpleNamespace(project_slug="factory", agent_slug="generalist"),
    )
    path = tmp_path / "integrations" / "factory.architecture"
    path.parent.mkdir()
    path.write_text("personal")
    store = get_conversation_store("factory", "generalist")
    store.upsert_thread(Thread(thread_id=PERSONAL_SESSION, user_id="token_user"))

    def busy(state, receipt):
        if busy_kind == "queue":
            state["items"] = [{"item": {"state": "pending"}, "owner": None}]
        else:
            state["runtime_owner"] = {"heartbeat": time.time()}
        return None, None

    store.mutate_queue(PERSONAL_SESSION, busy)
    with pytest.raises(RuntimeError, match="conversation work is active or queued"):
        claim_architecture(project, SimpleNamespace(running=lambda: 0))
    assert path.read_text() == "personal"


@pytest.mark.parametrize(
    "mode,expected", [("personal", "generalist"), ("specialist", "foreman")]
)
def test_mode_selects_entrypoint_even_with_legacy_overlay(
    tmp_path, monkeypatch, mode, expected
):
    from omniagents.core.project.runtime import load_project

    monkeypatch.setenv("FACTORY_ARCHITECTURE", mode)
    (tmp_path / "project.yml").write_text(
        "product:\n  name: mode-test\nagents:\n  entrypoint: generalist\n"
    )
    for name in ("generalist", "foreman"):
        directory = tmp_path / "agents" / name
        directory.mkdir(parents=True)
        (directory / "agent.yml").write_text(f"name: {name}\n")
    assert load_project(str(tmp_path / "project.yml")).entrypoint == expected


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
