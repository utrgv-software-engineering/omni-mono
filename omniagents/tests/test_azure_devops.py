from pathlib import Path

import pytest
from omniagents.core.automations import Automation
from omniagents.integrations.azure_devops import normalize
from omniagents.integrations.events import IncomingEvent
from omniagents.integrations.store import EventStore


def payload(**changes):
    return {
        "connection_id": "one",
        "scope_epoch": "epoch",
        "organization": "school",
        "project_id": "project",
        "repository_id": "repo",
        "pull_request_id": "8",
        "resource_id": "threads:8",
        "event": "pull_request_thread_changed",
        "resource": [{"id": 4, "comments": [{"content": "review"}]}],
        **changes,
    }


def test_normalized_thread_continuity_and_isolation():
    first = normalize(payload(), "delivery1")
    second = normalize(payload(event="pull_request_completed"), "delivery2")
    assert first.thread_key == second.thread_key
    assert (
        first.thread_key
        != normalize(payload(connection_id="two"), "delivery3").thread_key
    )
    assert first.payload["resource"][0]["comments"][0]["content"] == "review"
    assert first.filters["repositories"] == ["repo"]
    with pytest.raises(ValueError):
        normalize(payload(event="unsupported"), "delivery")


def test_dispatch_intents_survive_restart_and_are_claimed_once(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    event = normalize(payload(), "delivery")
    event_id = store.record(event)
    automation = Automation(
        name="review", path=Path("automations/review"), instruction="Review the change"
    )
    store.prepare_dispatches(event_id, [automation])
    assert store.received_events() == []
    store.close()
    store = EventStore(path)
    [(saved_id, recovered, saved_automation)] = store.pending_dispatches()
    assert (
        saved_id == event_id
        and recovered.payload == event.payload
        and saved_automation == automation
    )
    dispatch = store.start_dispatch(event_id, "review", "agent", "session", intent=True)
    assert dispatch
    assert (
        store.start_dispatch(event_id, "review", "agent", "session", intent=True)
        is None
    )
    assert not store.pending_dispatches()
    store.close()
    store = EventStore(path)
    assert not store.pending_dispatches()
    store.close()


def test_dispatch_preparation_rolls_back_status_and_intents(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event_id = store.record(
        IncomingEvent(
            provider="azure-devops", event="push", delivery_id="once", payload={}
        )
    )
    automation = Automation(name="one", path=Path(), instruction="Inspect")
    broken = Automation(
        name="two", path=Path(), instruction="Inspect", triggers=[{"invalid": object()}]
    )
    with pytest.raises(TypeError):
        store.prepare_dispatches(event_id, [automation, broken])
    assert len(store.received_events()) == 1
    assert not store.pending_dispatches()
    store.close()


@pytest.mark.asyncio
async def test_queued_intent_respects_disable_while_waiting_for_capacity(
    tmp_path, monkeypatch
):
    import asyncio
    from omniagents.integrations.intake import EventIntake, RunResult
    from omniagents.core.runtime import factory_stop

    monkeypatch.setattr(factory_stop, "current", lambda: None)
    store = EventStore(tmp_path / "events.db")
    event = normalize(payload(), "waiting")
    event_id = store.record(event)
    automation = Automation(name="review", path=tmp_path, instruction="Review")
    store.prepare_dispatches(event_id, [automation])
    prepared = asyncio.Event()
    calls = []

    def routes():
        prepared.set()
        return {}

    async def run(*arguments, **options):
        calls.append(arguments)
        return RunResult(output="done")

    monkeypatch.setattr(store, "automation_routes", routes)
    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="agent",
        store=store,
        run=run,
        max_concurrent_runs=1,
    )
    await intake._slots.acquire()
    task = asyncio.create_task(
        intake._dispatch(event_id, event, automation, intent=True)
    )
    try:
        await asyncio.wait_for(prepared.wait(), 2)
        assert len(store.pending_dispatches()) == 1
        store.set_automation_enabled("review", False)
        intake._slots.release()
        await asyncio.wait_for(task, 2)
        assert not calls
        assert not store.pending_dispatches()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        store.close()


def test_push_and_ref_events_expose_branch_filters():
    push = normalize(
        payload(event="push", resource={"refUpdates": [{"name": "refs/heads/main"}]}),
        "push",
    )
    refs = normalize(
        payload(event="refs_changed", resource=[{"name": "refs/heads/release"}]), "refs"
    )
    assert push.filters["branches"] == ["refs/heads/main"]
    assert refs.filters["branches"] == ["refs/heads/release"]


@pytest.mark.asyncio
async def test_delivery_endpoint_authenticates_and_deduplicates(tmp_path):
    import httpx
    from fastapi import FastAPI
    from omniagents.integrations.azure_devops import mount_azure_devops_route

    store = EventStore(tmp_path / "events.db")

    class Intake:
        async def accept(self, event):
            return store.record(event) is not None

    async def authorized(request):
        return request.headers.get("authorization") == "Bearer test-runtime"

    app = FastAPI()
    mount_azure_devops_route(app, Intake(), authorized)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://runtime"
        ) as client:
            path = "/integrations/azure-devops/events"
            assert (await client.post(path, json=payload())).status_code == 401
            headers = {"Authorization": "Bearer test-runtime", "x-ado-delivery": "once"}
            assert (
                await client.post(path, headers=headers, json=payload())
            ).json() == {"accepted": True}
            assert (
                await client.post(path, headers=headers, json=payload())
            ).json() == {"accepted": False}
            assert (
                await client.post(path, headers=headers, json=payload(event="unknown"))
            ).status_code == 422
    finally:
        store.close()
