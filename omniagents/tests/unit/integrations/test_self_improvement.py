import asyncio
from pathlib import Path

import pytest

from omniagents.core.automations import Automation, load_automation
from omniagents.integrations import self_improvement as gate
from omniagents.integrations.intake import EventIntake, RunResult
from omniagents.integrations.events import IncomingEvent
from omniagents.integrations.store import EventStore
from omniagents.integrations.schedule import ScheduleSource


@pytest.fixture(autouse=True)
def no_opt_in(monkeypatch):
    for name in (
        "FACTORY_SETTINGS_URL",
        "FACTORY_SELF_IMPROVEMENT",
        "FACTORY_LINKED_REPOSITORY",
    ):
        monkeypatch.delenv(name, raising=False)


def automation(agent="improver", **kwargs):
    return Automation("review", Path("review"), "Review runs", agent=agent, **kwargs)


@pytest.mark.asyncio
async def test_disabled_by_default_and_linking_is_not_enabling(monkeypatch):
    assert not await gate.allowed(automation())
    assert await gate.allowed(automation("generalist"))
    assert not await gate.allowed(automation("generalist", self_improvement=True))
    monkeypatch.setenv("FACTORY_LINKED_REPOSITORY", "org/factory")
    assert not await gate.allowed(automation())
    monkeypatch.setenv("FACTORY_SELF_IMPROVEMENT", "true")
    assert await gate.allowed(automation())


def test_unavailable_policy_fails_closed(monkeypatch):
    monkeypatch.setenv("FACTORY_SETTINGS_URL", "http://invalid.test")
    monkeypatch.setenv("FACTORY_SELF_IMPROVEMENT", "true")
    monkeypatch.setenv("FACTORY_LINKED_REPOSITORY", "org/factory")

    def failed(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(gate, "urlopen", failed)
    assert gate.policy() == {}


@pytest.mark.asyncio
async def test_cancel_improvement_when_disabled_without_stopping_ordinary_work(
    monkeypatch,
):
    value = {"enabled": True, "repository": "org/factory"}
    monkeypatch.setattr(gate, "policy", lambda: value.copy())
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def run(agent, sid, prompt, progress):
        assert "org/factory" in prompt
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(
        gate.guarded_run(run, automation(), "generalist", "session", "Review", None)
    )
    await asyncio.wait_for(started.wait(), 2)
    value["enabled"] = False
    result = await asyncio.wait_for(task, 4)
    assert result.stopped and cancelled.is_set()

    async def normal(*args):
        return RunResult(output="normal work continues")

    assert (
        await gate.guarded_run(
            normal, automation("worker"), "worker", "s", "task", None
        )
    ).output


@pytest.mark.asyncio
async def test_gate_checks_again_before_queued_dispatch(monkeypatch):
    monkeypatch.setattr(
        gate, "policy", lambda: {"enabled": False, "repository": "org/factory"}
    )

    async def must_not_start(*args):
        pytest.fail("disabled work started")

    assert (
        await gate.guarded_run(
            must_not_start, automation(), "generalist", "s", "Review", None
        )
    ).stopped


def write_review(root):
    path = root / "automations" / "review" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        '---\nagent: generalist\nself_improvement: true\ntriggers:\n  - provider: schedule\n    event: cron_fired\n    schedule:\n      name: review\n      cron: "* * * * *"\n---\nReview runs.\n'
    )


@pytest.mark.asyncio
async def test_scheduled_and_manual_firings_are_gated(tmp_path, monkeypatch):
    write_review(tmp_path)
    assert load_automation(tmp_path, "review").self_improvement
    received = []

    async def accept(event):
        received.append(event)
        return True

    clock = [0.0]
    source = ScheduleSource(tmp_path, accept, clock=lambda: clock[0])
    source.refresh()
    clock[0] = 60.0
    await source.tick()
    assert not received
    with pytest.raises(PermissionError):
        await source.fire_now("review")
    monkeypatch.setattr(
        gate, "policy", lambda: {"enabled": True, "repository": "org/factory"}
    )
    assert await source.fire_now("review")
    assert len(received) == 1


@pytest.mark.asyncio
async def test_replayed_event_cannot_bypass_dispatch_gate(tmp_path):
    write_review(tmp_path)
    store = EventStore(tmp_path / "events.db")

    async def run(*args):
        pytest.fail("disabled replay reached agent")

    intake = EventIntake(
        project_root=tmp_path, entrypoint="generalist", store=store, run=run
    )
    event = IncomingEvent(
        provider="schedule",
        event="cron_fired",
        delivery_id="replayed",
        payload={},
        filters={"schedules": ["review"]},
    )
    try:
        await intake.accept(event)
        await intake.drain()
        assert store.running() == 0
    finally:
        store.close()


def test_pending_provider_change_cannot_start_improvement(monkeypatch):
    import io
    import json

    monkeypatch.setenv(
        "FACTORY_SETTINGS_URL", "http://workplace/factory/repository-policy"
    )
    monkeypatch.setenv("FACTORY_REPOSITORY_REVISION", "old")
    monkeypatch.setattr(
        gate,
        "urlopen",
        lambda *a, **kw: io.BytesIO(
            json.dumps(
                {"enabled": True, "repository": "org/factory", "revision": "new"}
            ).encode()
        ),
    )
    assert not gate.policy()["enabled"]
    monkeypatch.setenv("FACTORY_REPOSITORY_REVISION", "new")
    assert gate.policy()["enabled"]


def test_workspace_repository_override_is_shared_by_clones_and_prompts(monkeypatch):
    from types import SimpleNamespace
    from omniagents.integrations.environment import parse_github
    from omniagents.core.project.values import project_values

    entries = [
        {
            "type": "github",
            "repositories": ["acme/old"],
            "account": "old",
            "git_url": "http://standin",
        }
    ]
    monkeypatch.setenv("GITHUB_REPOSITORIES", '["org/new"]')
    monkeypatch.setenv("GITHUB_ACCOUNT", "connected-user")
    monkeypatch.setenv("GITHUB_GIT_URL", "")
    github = parse_github(entries)
    assert github.repositories == ("org/new",) and github.git_url is None
    values = project_values(
        {"integrations": entries}, SimpleNamespace(name="n", label="l", slug="s")
    )
    assert values["github"]["repositories"] == ["org/new"]
    assert values["github"]["account"] == "connected-user"
