import asyncio
from pathlib import Path

import pytest

from omniagents.core.automations import Automation
from omniagents.integrations import EventIntake, EventStore, IncomingEvent
from omniagents.integrations.intake import RunResult
from omniagents.integrations.matching import matching_automations, trigger_matches


def _event(event="app_mention", delivery="Ev1", thread="T1:C1:1.0", **filters):
    return IncomingEvent(
        provider="slack",
        event=event,
        delivery_id=delivery,
        payload={"event": {"text": "hi"}},
        thread_key=thread,
        filters={k: list(v) for k, v in filters.items()},
    )


def _write_automation(root: Path, name: str, frontmatter: str, body="Handle it."):
    path = root / "automations" / name / "automation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


# -- matching ---------------------------------------------------------------


def test_trigger_needs_provider_and_event():
    event = _event()
    assert trigger_matches({"provider": "slack", "event": "app_mention"}, event)
    assert not trigger_matches({"provider": "github", "event": "app_mention"}, event)
    assert not trigger_matches({"provider": "slack", "event": "message_dm"}, event)


def test_filter_keys_combine_with_and_and_lists_match_any():
    event = _event(channels=["C1"], users=["U1"])
    base = {"provider": "slack", "event": "app_mention"}
    assert trigger_matches({**base, "filter": {"channels": ["C9", "C1"]}}, event)
    assert trigger_matches(
        {**base, "filter": {"channels": "C1", "users": ["U1"]}}, event
    )
    assert not trigger_matches(
        {**base, "filter": {"channels": ["C1"], "users": ["U2"]}}, event
    )


def test_filter_on_a_value_the_event_lacks_does_not_match_except_not_in():
    event = _event(channels=["C1"])
    base = {"provider": "slack", "event": "app_mention"}
    assert not trigger_matches({**base, "filter": {"emojis": ["ticket"]}}, event)
    assert trigger_matches({**base, "filter": {"emojis": {"not_in": ["x"]}}}, event)
    assert not trigger_matches(
        {**base, "filter": {"channels": {"not_in": ["C1"]}}}, event
    )
    assert trigger_matches({**base, "filter": {"channels": {"in": ["C1"]}}}, event)


def test_matching_automations_any_trigger(tmp_path):
    a = Automation(
        name="a",
        path=tmp_path,
        instruction="x",
        triggers=[
            {"provider": "github", "event": "issues_opened"},
            {"provider": "slack", "event": "app_mention"},
        ],
    )
    b = Automation(name="b", path=tmp_path, instruction="x", triggers=[])
    assert matching_automations([a, b], _event()) == [a]


# -- store ------------------------------------------------------------------


def test_store_drops_redeliveries(tmp_path):
    store = EventStore(tmp_path / "events.db")
    assert store.record(_event(delivery="Ev1")) is not None
    assert store.record(_event(delivery="Ev1")) is None
    assert store.record(_event(delivery="Ev2")) is not None
    assert [e["delivery_id"] for e in store.recent()] == ["Ev2", "Ev1"]


def test_store_threads_are_per_agent(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.remember_thread("slack", "k", "foreman", "s1")
    store.remember_thread("slack", "k", "foreman", "s2")  # first one stays
    assert store.thread_session("slack", "k", "foreman") == "s1"
    assert store.thread_session("slack", "k", "maintainer") is None


def test_store_survives_reopening(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    store.record(_event(delivery="Ev1"))
    store.remember_thread("slack", "k", "foreman", "s1")
    store.close()
    again = EventStore(path)
    assert again.record(_event(delivery="Ev1")) is None
    assert again.thread_session("slack", "k", "foreman") == "s1"


# -- intake -----------------------------------------------------------------


class _Runs:
    def __init__(self, error=None, delay=0.0):
        self.calls = []
        self.error = error
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def __call__(self, agent, session_id, prompt, progress=None):
        self.calls.append((agent, session_id, prompt))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        if isinstance(self.error, Exception):
            raise self.error
        return RunResult(error=self.error, output="done")


def _intake(tmp_path, runs, **kwargs):
    return EventIntake(
        project_root=tmp_path,
        entrypoint="foreman",
        store=EventStore(tmp_path / "events.db"),
        run=runs,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_intake_runs_the_matched_automation_with_its_instruction(tmp_path):
    _write_automation(
        tmp_path,
        "mentions",
        "triggers:\n  - provider: slack\n    event: app_mention",
        body="Reply in the thread.",
    )
    runs = _Runs()
    intake = _intake(tmp_path, runs)
    assert await intake.accept(_event())
    await intake.drain()
    [(agent, session_id, prompt)] = runs.calls
    assert agent == "foreman"
    assert prompt.startswith("Reply in the thread.")
    assert '"text": "hi"' in prompt
    [record] = intake.store.recent()
    assert record["status"] == "dispatched"
    assert record["dispatches"] == [
        {
            "automation": "mentions",
            "agent": "foreman",
            "session_id": session_id,
            "status": "completed",
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_intake_redelivery_does_not_run_twice(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    runs = _Runs()
    intake = _intake(tmp_path, runs)
    assert await intake.accept(_event(delivery="Ev1"))
    assert not await intake.accept(_event(delivery="Ev1"))
    await intake.drain()
    assert len(runs.calls) == 1


@pytest.mark.asyncio
async def test_intake_thread_continues_its_conversation(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    runs = _Runs()
    intake = _intake(tmp_path, runs)
    await intake.accept(_event(delivery="Ev1", thread="T:C:1"))
    await intake.accept(_event(delivery="Ev2", thread="T:C:1"))
    await intake.accept(_event(delivery="Ev3", thread="T:C:2"))
    await intake.accept(_event(delivery="Ev4", thread=None))
    await intake.drain()
    sessions = {
        e["delivery_id"]: e["dispatches"][0]["session_id"]
        for e in intake.store.recent()
    }
    assert sessions["Ev1"] == sessions["Ev2"]
    assert len({sessions["Ev1"], sessions["Ev3"], sessions["Ev4"]}) == 3


@pytest.mark.asyncio
async def test_intake_named_agent_and_unmatched_events(tmp_path):
    _write_automation(
        tmp_path,
        "reactions",
        "agent: maintainer\ntriggers:\n  - provider: slack\n    event: reaction_added\n"
        "    filter:\n      emojis: [ticket]",
    )
    runs = _Runs()
    intake = _intake(tmp_path, runs)
    await intake.accept(
        _event(event="reaction_added", delivery="Ev1", emojis=["ticket"])
    )
    await intake.accept(_event(event="reaction_added", delivery="Ev2", emojis=["eyes"]))
    await intake.drain()
    assert [call[0] for call in runs.calls] == ["maintainer"]
    statuses = {
        e["delivery_id"]: (e["status"], e["detail"]) for e in intake.store.recent()
    }
    assert statuses["Ev2"] == ("ignored", "no automation matches")


@pytest.mark.asyncio
async def test_intake_records_run_failures(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    intake = _intake(tmp_path, _Runs(error="model refused"))
    await intake.accept(_event(delivery="Ev1"))
    await intake.drain()
    intake_raising = _intake(tmp_path / "b", _Runs(error=RuntimeError("boom")))
    _write_automation(
        tmp_path / "b", "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    await intake_raising.accept(_event(delivery="Ev1"))
    await intake_raising.drain()
    [d1] = intake.store.recent()[0]["dispatches"]
    [d2] = intake_raising.store.recent()[0]["dispatches"]
    assert (d1["status"], d1["error"]) == ("failed", "model refused")
    assert (d2["status"], d2["error"]) == ("failed", "RuntimeError: boom")


@pytest.mark.asyncio
async def test_intake_malformed_automation_fails_the_event(tmp_path):
    path = tmp_path / "automations" / "bad" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text("no frontmatter", encoding="utf-8")
    intake = _intake(tmp_path, _Runs())
    await intake.accept(_event())
    await intake.drain()
    assert intake.store.recent()[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_intake_one_run_per_conversation_and_a_concurrency_cap(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    same_thread = _Runs(delay=0.02)
    intake = _intake(tmp_path, same_thread)
    for i in range(3):
        await intake.accept(_event(delivery=f"Ev{i}", thread="T:C:1"))
    await intake.drain()
    assert same_thread.max_active == 1

    capped = _Runs(delay=0.02)
    root = tmp_path / "capped"
    _write_automation(
        root, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    intake = _intake(root, capped, max_concurrent_runs=2)
    for i in range(5):
        await intake.accept(_event(delivery=f"Ev{i}", thread=f"T:C:{i}"))
    await intake.drain()
    assert capped.max_active == 2
    assert len(capped.calls) == 5


@pytest.mark.asyncio
async def test_a_stop_cancels_its_conversations_run_and_reports_it_stopped(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    started = asyncio.Event()
    cancelled = []

    async def run(agent, session_id, prompt, progress=None):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(session_id)
            raise
        return RunResult(output="never")

    reported = []

    async def after(event, result):
        reported.append(result)

    intake = _intake(tmp_path, run)
    intake.after_run["slack"] = after
    await intake.accept(_event(delivery="Ev1", thread="T:C:1"))
    await asyncio.wait_for(started.wait(), 5)
    stop = IncomingEvent(
        provider="slack",
        event="stopped",
        delivery_id="Ev2",
        payload={},
        thread_key="T:C:1",
        control="stop",
    )
    assert await intake.accept(stop)
    await intake.drain()
    assert len(cancelled) == 1 and reported[0].stopped
    records = {e["delivery_id"]: e for e in intake.store.recent()}
    assert records["Ev1"]["dispatches"][0]["status"] == "stopped"
    assert records["Ev2"]["status"] == "stopped"
    # A stop with nothing running changes nothing.
    again = IncomingEvent(
        provider="slack",
        event="stopped",
        delivery_id="Ev3",
        payload={},
        thread_key="T:C:1",
        control="stop",
    )
    assert await intake.accept(again)
    assert {e["delivery_id"]: e["status"] for e in intake.store.recent()}[
        "Ev3"
    ] == "ignored"


def test_a_new_server_marks_its_predecessors_runs_interrupted(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event_id = store.record(_event(delivery="Ev1"))
    store.start_dispatch(event_id, "m", "foreman", "s1")
    assert store.running() == 1
    again = EventStore(tmp_path / "events.db")
    assert again.mark_interrupted() == 1 and again.running() == 0
    assert again.recent()[0]["dispatches"][0]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_memory_extraction_receives_failed_runs_after_reporting(tmp_path):
    _write_automation(
        tmp_path, "m", "triggers:\n  - provider: slack\n    event: app_mention"
    )
    intake = _intake(tmp_path, _Runs(error="model refused"))
    extracted = []
    intake.extract_memory = lambda *args: extracted.append(args)
    await intake.accept(_event())
    await intake.drain()
    assert len(extracted) == 1
    assert extracted[0][1:3] == ("m", "foreman")
    assert extracted[0][-1].error == "model refused"
