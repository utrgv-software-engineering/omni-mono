from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from omniagents.core.automations import list_automations
from omniagents.integrations import EventIntake, EventStore
from omniagents.integrations.intake import RunResult
from omniagents.integrations.matching import matching_automations
from omniagents.integrations.schedule import (
    ScheduleSource,
    fired,
    next_fire,
    project_schedules,
)

CHICAGO = ZoneInfo("America/Chicago")


def _at(*args, tz=CHICAGO):
    return datetime(*args, tzinfo=tz).timestamp()


def _automation(root, name, cron, extra=""):
    path = root / "automations" / name / "automation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nagent: maintainer\ntriggers:\n  - provider: schedule\n    event: cron_fired\n"
        f'    schedule:\n      name: {name}\n      cron: "{cron}"\n{extra}---\nRun the {name}.\n',
        encoding="utf-8",
    )


def test_next_fire_follows_the_cron_in_its_timezone():
    monday_9 = _at(2026, 10, 26, 9, 0)
    # Saturday 24 Oct 2026, noon: the next Monday at 9:00, Chicago time.
    assert (
        next_fire("0 9 * * 1", _at(2026, 10, 24, 12, 0), "America/Chicago") == monday_9
    )
    # Exactly at a firing, the next one is a week on.
    assert next_fire("0 9 * * 1", monday_9, "America/Chicago") == _at(2026, 11, 2, 9, 0)
    assert next_fire("*/15 * * * *", _at(2026, 10, 24, 12, 7), "UTC") == _at(
        2026, 10, 24, 17, 15, tz=ZoneInfo("UTC")
    )


def test_project_schedules_reads_automations_and_leaves_out_broken_ones(tmp_path):
    _automation(
        tmp_path,
        "weekly-dependency-audit",
        "0 9 * * 1",
        "      timezone: America/Chicago\n",
    )
    _automation(tmp_path, "broken", "every monday")
    [schedule] = project_schedules(tmp_path)
    assert (schedule.name, schedule.cron, schedule.timezone) == (
        "weekly-dependency-audit",
        "0 9 * * 1",
        "America/Chicago",
    )


def test_a_firing_starts_its_own_automation_only(tmp_path):
    _automation(tmp_path, "weekly-dependency-audit", "0 9 * * 1")
    _automation(tmp_path, "nightly-docs", "0 2 * * *")
    [audit] = [
        s for s in project_schedules(tmp_path) if s.name == "weekly-dependency-audit"
    ]
    event = fired(audit, _at(2026, 10, 26, 9, 0))
    assert [
        a.name for a in matching_automations(list_automations(tmp_path), event)
    ] == ["weekly-dependency-audit"]
    assert (
        event.delivery_id
        == "weekly-dependency-audit:weekly-dependency-audit:2026-10-26T14:00:00Z"
    )
    assert event.thread_key is None


@pytest.mark.asyncio
async def test_the_source_fires_on_time_once_and_follows_edits(tmp_path):
    _automation(
        tmp_path,
        "weekly-dependency-audit",
        "0 9 * * 1",
        "      timezone: America/Chicago\n",
    )
    now = [_at(2026, 10, 26, 8, 59)]
    runs = []

    async def run(agent, session_id, prompt, progress=None):
        runs.append((agent, prompt))
        return RunResult()

    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="foreman",
        store=EventStore(tmp_path / "e.db"),
        run=run,
    )
    source = ScheduleSource(tmp_path, intake.accept, clock=lambda: now[0])
    source.refresh()
    await source.tick()
    assert runs == [] and source.seconds_to_next() == 30.0
    now[0] = _at(2026, 10, 26, 9, 0, 20)
    await source.tick()
    await source.tick()
    await intake.drain()
    [(agent, prompt)] = runs
    assert agent == "maintainer" and "Run the weekly-dependency-audit." in prompt
    # A second source (a restart) in the same minute does not fire it again.
    again = ScheduleSource(tmp_path, intake.accept, clock=lambda: now[0])
    again._next = dict(source._next)
    again._next = {k: (s, _at(2026, 10, 26, 9, 0)) for k, (s, _) in again._next.items()}
    await again.tick()
    await intake.drain()
    assert len(runs) == 1
    # Removing the automation stops its schedule.
    (tmp_path / "automations" / "weekly-dependency-audit" / "automation.md").unlink()
    source.refresh()
    assert source._next == {}


def test_a_schedule_fires_now_on_demand_with_the_servers_token(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from omniagents.integrations.server import _mount_run_schedule_route

    _automation(tmp_path, "nightly-review", "0 2 * * *", "      review_runs: 24h\n")
    store = EventStore(tmp_path / "events.db")
    fired_events = []

    async def accept(event):
        fired_events.append(event)
        return True

    source = ScheduleSource(tmp_path, accept, runs=store.runs_since)

    class Authenticator:
        async def authenticate_http(self, request):
            from omniagents.rpc.auth import AuthenticationFailed

            if request.headers.get("authorization") != "Bearer secret":
                raise AuthenticationFailed("no")

    app = FastAPI()
    app.state.connection_authenticator = Authenticator()
    _mount_run_schedule_route(app, source)
    client = TestClient(app)
    assert client.post("/integrations/schedules/nightly-review/run").status_code == 401
    ok = {"Authorization": "Bearer secret"}
    [listed] = client.get("/integrations/schedules", headers=ok).json()["schedules"]
    assert (listed["name"], listed["automation"], listed["review_runs"]) == (
        "nightly-review",
        "nightly-review",
        86400,
    )
    assert (
        client.post("/integrations/schedules/nope/run", headers=ok).status_code == 404
    )
    assert client.post(
        "/integrations/schedules/nightly-review/run", headers=ok
    ).json() == {"fired": "nightly-review"}
    assert (
        client.post(
            "/integrations/schedules/nightly-review/run", headers=ok
        ).status_code
        == 200
    )
    first, second = fired_events
    # Each its own delivery, starting the schedule's automation, with its review.
    assert first.delivery_id != second.delivery_id and first.filters == {
        "schedules": ["nightly-review"]
    }
    assert first.context.startswith("The factory has no runs since")
