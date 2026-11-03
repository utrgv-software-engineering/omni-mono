import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
import httpx
import pytest

from omniagents.integrations import EventIntake, EventStore
from omniagents.integrations.intake import RunResult
from omniagents.integrations.shared import mount_shared_route


@pytest.mark.asyncio
@pytest.mark.parametrize("personal", [False, True])
async def test_shared_workflow_uses_durable_intake_and_architecture(tmp_path, personal):
    calls = []

    async def run(agent, session, prompt, progress):
        calls.append((agent, session, prompt))
        return RunResult(output="Done")

    store = EventStore(tmp_path / "events.db")
    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="generalist" if personal else "triage",
        store=store,
        run=run,
    )
    app = FastAPI()

    async def authorized(request):
        return request.headers.get("authorization") == "Bearer host-secret"

    mount_shared_route(app, intake, authorized)
    event = {
        "id": "batch-one",
        "event": "shared.files_uploaded",
        "origin": "person",
        "entries": [
            {"path": "Invoices/one.pdf", "revision": "abc"},
            {"path": "Invoices/two.pdf", "revision": "def"},
        ],
        "workflow": {
            "id": "invoices",
            "revision": 1,
            "name": "Read invoices",
            "instruction": "Summarize the invoice files.",
            "enabled": True,
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/integrations/shared/events", json=event)
        ).status_code == 401
        first = await client.post(
            "/integrations/shared/events",
            json=event,
            headers={"Authorization": "Bearer host-secret"},
        )
        assert first.json() == {"accepted": True}
        assert (
            await client.post(
                "/integrations/shared/events",
                json=event,
                headers={"Authorization": "Bearer host-secret"},
            )
        ).json() == {"accepted": False}
        await intake.drain()
        assert len(calls) == 1
        assert calls[0][0] == ("generalist" if personal else "triage")
        assert calls[0][1] != "factory-personal"
        assert "Invoices/one.pdf" in calls[0][2] and "Invoices/two.pdf" in calls[0][2]
        assert "/workspace/Shared" in calls[0][2]
        event["id"] = "agent-change"
        event["origin"] = "agent"
        assert (
            await client.post(
                "/integrations/shared/events",
                json=event,
                headers={"Authorization": "Bearer host-secret"},
            )
        ).status_code == 422
        event["workflow"]["include_agent"] = True
        assert (
            await client.post(
                "/integrations/shared/events",
                json=event,
                headers={"Authorization": "Bearer host-secret"},
            )
        ).status_code == 200
        await intake.drain()
        assert len(calls) == 2
        assert calls[0][1] != calls[1][1]
    store.close()


@pytest.mark.asyncio
async def test_shared_accepted_event_resumes_after_restart(tmp_path):
    from omniagents.integrations.events import IncomingEvent

    path = tmp_path / "events.db"
    store = EventStore(path)
    payload = {
        "id": "persisted",
        "event": "shared.files_uploaded",
        "origin": "person",
        "entries": [],
        "workflow": {
            "id": "rule",
            "revision": 1,
            "name": "Review",
            "instruction": "Review the files",
            "enabled": True,
        },
    }
    store.record(
        IncomingEvent(
            provider="shared",
            event=payload["event"],
            delivery_id="persisted:rule",
            payload=payload,
        )
    )
    store.close()
    store = EventStore(path)
    called = []

    async def run(*args):
        called.append(args)
        return RunResult(output="Recovered")

    intake = EventIntake(
        project_root=tmp_path, entrypoint="triage", store=store, run=run
    )

    async def authorized(request):
        return True

    mount_shared_route(FastAPI(), intake, authorized)
    await intake.resume_received()
    await intake.drain()
    assert len(called) == 1 and "Review the files" in called[0][2]
    await intake.resume_received()
    await intake.drain()
    assert len(called) == 1
    store.close()


def test_shared_capability_has_registered_tools():
    from omniagents.core.capabilities.parser import discover_capabilities_in_dir
    from omniagents.core.tools.discovery import discover_functions_in_dir

    root = Path(__file__).resolve().parents[3] / "omniagents/builtin"
    caps = discover_capabilities_in_dir(
        str(root / "capabilities"),
        tool_registry=discover_functions_in_dir(str(root / "tools")),
    )
    assert "shared" in caps
    assert [t.name for t in caps["shared"].tools()] == [
        t.name for t in caps["filesystem"].tools()
    ]
