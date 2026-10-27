from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents.integrations import EventIntake, EventStore, IncomingEvent
from omniagents.integrations.grading import LiveJudges
from omniagents.integrations.intake import RunResult
from omniagents.integrations.replay import (
    compare,
    event_json,
    format_comparison,
    replayed,
)
from omniagents.integrations.server import _mount_replay_route


def _automation(root: Path) -> None:
    path = root / "automations" / "triage-reactions" / "automation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nagent: triage\ntriggers:\n  - provider: slack\n    event: reaction_added\n"
        "    filter:\n      emojis: [ticket]\n---\nTriage it.\n",
        encoding="utf-8",
    )


def _event():
    return IncomingEvent(
        provider="slack",
        event="reaction_added",
        delivery_id="Ev9",
        thread_key="T1:C1:1.0",
        payload={"event": {"reaction": "ticket"}},
        filters={"emojis": ["ticket"]},
        context="From #eng.",
    )


class _Runs:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def __call__(self, agent, session_id, prompt, progress=None):
        self.calls.append((agent, session_id, prompt))
        return RunResult(output=self.output)


def _intake(root, store, runs):
    return EventIntake(project_root=root, entrypoint="foreman", store=store, run=runs)


@pytest.mark.asyncio
async def test_a_replayed_event_runs_again_in_a_conversation_of_its_own_and_is_graded(
    tmp_path, monkeypatch
):
    from omniagents.core.eval import pipeline

    _automation(tmp_path)
    production = EventStore(tmp_path / "production.db")
    prod_intake = _intake(tmp_path, production, _Runs("Filed ENG-12."))
    await prod_intake.accept(_event())
    await prod_intake.drain()
    first = production.runs_since(0)[0]
    production.record_grade(
        first["id"],
        "triage_quality",
        False,
        "vague",
        "No repro.",
        session_id=first["session_id"],
        agent="triage",
    )
    # Kept as it came: its filters and context replay with it.
    [kept] = production.events_since(0)
    assert (kept.filters, kept.context) == ({"emojis": ["ticket"]}, "From #eng.")

    verdicts = iter([{"answer": "Pass", "reason": "none", "reasoning": "Clear."}])

    async def run_judge(key, cfg, **kwargs):
        return next(verdicts)

    monkeypatch.setattr(pipeline, "run_judge", run_judge)
    staging = EventStore(tmp_path / "staging.db")
    runs = _Runs("Filed ENG-12 with steps to reproduce.")
    intake = _intake(tmp_path, staging, runs)
    # A 0% sample: replayed runs are graded all the same.
    intake.judges = LiveJudges(
        {"triage_quality": {"agents": ["triage"], "live": {"sample": 0}}},
        tmp_path,
        staging,
        lambda a, s: [{"role": "user", "content": "x"}],
    )
    again = replayed(kept)
    assert (again.delivery_id, again.thread_key) == ("replay:Ev9", "replay:T1:C1:1.0")
    assert await intake.accept(again)
    assert not await intake.accept(replayed(kept))  # once
    await intake.drain()
    [(agent, _, prompt)] = runs.calls
    assert agent == "triage" and "From #eng." in prompt

    rows = compare(production, staging, 0)
    [row] = rows
    assert row["baseline"]["output"] == "Filed ENG-12."
    text = format_comparison(rows)
    assert "baseline:  completed, triage_quality Fail" in text
    assert "candidate: completed, triage_quality Pass" in text
    assert "triage_quality: baseline 0/1 Pass; candidate 1/1 Pass" in text


class _Authenticator:
    async def authenticate_http(self, request):
        from omniagents.rpc.auth import AuthenticationFailed

        if request.headers.get("authorization") != "Bearer secret":
            raise AuthenticationFailed("no")
        return object()


def test_the_replay_route_takes_the_servers_token(tmp_path):
    _automation(tmp_path)
    store = EventStore(tmp_path / "staging.db")
    runs = _Runs("ok")
    intake = _intake(tmp_path, store, runs)
    app = FastAPI()
    app.state.connection_authenticator = _Authenticator()
    _mount_replay_route(app, intake)
    client = TestClient(app)
    body = {"events": [event_json(_event())]}
    assert client.post("/integrations/replay", json=body).status_code == 401
    assert (
        client.post(
            "/integrations/replay",
            json={"events": [{"bogus": 1}]},
            headers={"Authorization": "Bearer secret"},
        ).status_code
        == 400
    )
    response = client.post(
        "/integrations/replay", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert response.json() == {"accepted": 1, "sent": 1}
    [record] = store.recent()
    assert record["delivery_id"] == "replay:Ev9"


def test_what_the_candidate_did_worse_fails_the_check():
    from omniagents.integrations.replay import pass_rates, regressions

    def run(status, *grades, id=1):
        return {
            "id": id,
            "automation": "mentions",
            "agent": "triage",
            "provider": "slack",
            "event": "x",
            "status": status,
            "grades": [{"judge": j, "passed": p} for j, p in grades],
        }

    rows = [
        {
            "baseline": run("completed", ("triage_quality", True)),
            "candidate": run("completed", ("triage_quality", False)),
        },
        {"baseline": run("completed"), "candidate": run("failed", id=2)},
        {
            "baseline": None,
            "candidate": run("completed", ("triage_quality", True), id=3),
        },
    ]
    assert pass_rates(rows) == {
        "triage_quality": {"baseline": (1, 1), "candidate": (1, 2)}
    }
    assert regressions(rows) == [
        "triage_quality: 1/2 Pass, down from 1/1",
        "run 2 (mentions): failed, where the original completed",
    ]
    assert regressions(rows[2:]) == []


def test_waiting_counts_what_is_still_to_come(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event_id = store.record(_event())
    assert store.unsettled() == 1  # not matched yet
    store.set_status(event_id, "dispatched")
    dispatch = store.start_dispatch(event_id, "triage-reactions", "triage", "s")
    assert store.unsettled() == 1  # running
    store.finish_dispatch(dispatch, "completed")
    assert store.unsettled() == 0
