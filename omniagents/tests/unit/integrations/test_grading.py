from pathlib import Path

import pytest

from omniagents.integrations import EventIntake, EventStore, IncomingEvent
from omniagents.integrations.grading import LiveJudges, live_judges, verdict
from omniagents.integrations.intake import RunResult
from omniagents.integrations.schedule import (
    ScheduleSource,
    parse_duration,
    project_schedules,
    review_context,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path) -> None:
    _write(root, "project.yml", "name: acme\n")
    _write(root, "evaluations/evaluation.yml", "evaluation: {}\n")
    _write(
        root,
        "evaluations/judges/task_compliance/judge.yml",
        "agent: {model: gpt-test, instructions: Grade it.}\n"
        "input: {source: history, template: '{{ history_json }}'}\n"
        "agents: [implementer]\nlive: {sample: 100}\n",
    )
    _write(
        root,
        "evaluations/judges/offline_only/judge.yml",
        "agent: {model: gpt-test, instructions: Grade it.}\nagents: [implementer]\n",
    )
    _write(
        root,
        "automations/mentions/automation.md",
        "---\nagent: implementer\ntriggers:\n  - provider: slack\n    event: app_mention\n---\nDo it.\n",
    )


def _event(delivery="Ev1"):
    return IncomingEvent(
        provider="slack",
        event="app_mention",
        delivery_id=delivery,
        payload={"event": {"text": "add pagination"}},
        thread_key=f"T:{delivery}",
    )


def test_only_judges_with_live_grade_live_runs_and_each_draws_its_sample(tmp_path):
    _project(tmp_path)
    judges = live_judges(tmp_path, {})
    assert list(judges) == ["task_compliance"]
    store = EventStore(tmp_path / "events.db")
    draws = iter([0.99, 0.10])
    quarter = {"task_compliance": {**judges["task_compliance"], "live": {"sample": 25}}}
    live = LiveJudges(
        quarter,
        tmp_path / "evaluations",
        store,
        lambda a, s: [],
        draw=lambda: next(draws),
    )
    assert live.sampled("implementer") == []  # 99 is over the 25% sample
    assert [k for k, _ in live.sampled("implementer")] == ["task_compliance"]
    assert live.sampled("reviewer") == []  # not one of its agents


def test_a_judges_verdict():
    assert verdict(
        {"answer": "Fail", "reason": "partial", "reasoning": "No tests."}
    ) == (False, "partial", "No tests.")
    assert verdict({"answer": "Pass", "reason": "none", "reasoning": "ok"}) == (
        True,
        "none",
        "ok",
    )
    assert verdict({"error": "judge_failed"}) == (None, "judge_failed", None)


class _Runs:
    def __init__(self):
        self.calls = []

    async def __call__(self, agent, session_id, prompt, progress=None):
        self.calls.append((agent, session_id, prompt))
        return RunResult(output="Opened acme/api-service#40.")


@pytest.mark.asyncio
async def test_completed_runs_are_graded_and_the_nightly_review_reads_them(
    tmp_path, monkeypatch
):
    from omniagents.core.eval import pipeline

    _project(tmp_path)
    store = EventStore(tmp_path / "events.db")
    runs = _Runs()
    intake = EventIntake(
        project_root=tmp_path, entrypoint="foreman", store=store, run=runs
    )
    histories = {}
    judged = []

    async def run_judge(key, cfg, *, scenario, history, base_dir):
        judged.append((key, scenario, history, base_dir))
        return {
            "reasoning": "It opened the PR without the tests #12 asked for.",
            "answer": "Fail",
            "reason": "partial",
        }

    monkeypatch.setattr(pipeline, "run_judge", run_judge)

    def history(agent, session_id):
        return histories.setdefault(
            session_id,
            [
                {"role": "user", "content": "Do it."},
                {"role": "assistant", "content": "Opened #40."},
            ],
        )

    intake.judges = LiveJudges(
        live_judges(tmp_path, {}), tmp_path / "evaluations", store, history
    )
    assert await intake.accept(_event())
    await intake.drain()
    [(key, scenario, seen, base_dir)] = judged
    assert key == "task_compliance" and base_dir == tmp_path / "evaluations"
    assert scenario == {
        "name": "mentions",
        "agent": "implementer",
        "parent_agent": None,
        "event": {"event": {"text": "add pagination"}},
    }
    assert seen[-1]["content"] == "Opened #40."
    [run] = store.runs_since(0)
    assert run["output"] == "Opened acme/api-service#40."
    [grade] = run["grades"]
    assert grade == {
        "judge": "task_compliance",
        "agent": "implementer",
        "session_id": run["session_id"],
        "passed": False,
        "reason": "partial",
        "reasoning": "It opened the PR without the tests #12 asked for.",
    }

    # The nightly review: its firing carries the runs, and what failed in full.
    _write(
        tmp_path,
        "automations/nightly-review/automation.md",
        "---\nagent: maintainer\ntriggers:\n  - provider: schedule\n    event: cron_fired\n"
        '    schedule:\n      name: nightly-review\n      cron: "0 2 * * *"\n      review_runs: 24h\n'
        "---\nReview the day.\n",
    )
    [schedule] = [s for s in project_schedules(tmp_path) if s.name == "nightly-review"]
    assert schedule.review == 86400
    now = [run["started_at"] + 3600]
    fired = []

    async def accept(event):
        fired.append(event)
        return True

    source = ScheduleSource(
        tmp_path,
        accept,
        clock=lambda: now[0],
        runs=store.runs_since,
        improve={"task_compliance"},
    )
    source.refresh()
    now[0] += 86400
    await source.tick()
    [event] = fired
    assert "1 graded, 1 grade(s) Fail" in event.context
    assert (
        "mentions (implementer), from slack app_mention, completed; graded task_compliance Fail (partial)"
        in event.context
    )
    assert (
        "without the tests #12 asked for" in event.context
        and "add pagination" in event.context
    )
    assert "Opened acme/api-service#40." in event.context
    # A judge without self_improve: its grades are listed, its Fails not shown in full.
    assert "without the tests" not in review_context(store.runs_since(0), 0, set())


@pytest.mark.asyncio
async def test_failed_and_stopped_runs_are_not_graded(tmp_path, monkeypatch):
    from omniagents.core.eval import pipeline

    _project(tmp_path)
    store = EventStore(tmp_path / "events.db")

    async def failing(agent, session_id, prompt, progress=None):
        return RunResult(error="max_turns: out of turns")

    async def run_judge(*args, **kwargs):
        raise AssertionError("a failed run is not graded")

    monkeypatch.setattr(pipeline, "run_judge", run_judge)
    intake = EventIntake(
        project_root=tmp_path, entrypoint="foreman", store=store, run=failing
    )
    intake.judges = LiveJudges(
        live_judges(tmp_path, {}), tmp_path / "evaluations", store, lambda a, s: [{}]
    )
    await intake.accept(_event())
    await intake.drain()
    [run] = store.runs_since(0)
    assert (run["status"], run["grades"], run["output"]) == ("failed", [], None)
    # The review shows it, with its error.
    context = review_context(store.runs_since(0), 0, set())
    assert "1 not completed" in context and "Error: max_turns: out of turns" in context


def test_review_durations():
    assert [parse_duration(v) for v in ("90m", "24h", "7d")] == [5400, 86400, 604800]
    with pytest.raises(ValueError):
        parse_duration("tomorrow")
    assert (
        review_context([], 0, set())
        == "The factory has no runs since 1970-01-01T00:00:00Z."
    )


def test_the_review_lists_the_events_that_started_nothing(tmp_path):
    store = EventStore(tmp_path / "events.db")
    for n in range(2):
        event_id = store.record(
            IncomingEvent(
                provider="github",
                event="pull_request_ready",
                delivery_id=f"D{n}",
                payload={},
            )
        )
        store.set_status(event_id, "ignored", "no automation matches")
    [kind] = store.unmatched_since(0)
    assert kind == {"provider": "github", "event": "pull_request_ready", "n": 2}
    context = review_context(store.runs_since(0), 0, set(), store.unmatched_since(0))
    assert (
        "Events that started nothing" in context
        and "- github pull_request_ready: 2" in context
    )


@pytest.mark.asyncio
async def test_the_sessions_of_the_agents_a_run_called_are_graded_from_their_own_transcripts(
    tmp_path, monkeypatch
):
    from omniagents.core.eval import pipeline

    _project(tmp_path)
    store = EventStore(tmp_path / "events.db")
    seen = []

    async def run_judge(key, cfg, *, scenario, history, base_dir):
        seen.append((key, scenario["agent"], scenario["parent_agent"], history))
        return {"answer": "Pass", "reason": "none", "reasoning": "ok"}

    monkeypatch.setattr(pipeline, "run_judge", run_judge)
    implementer = [
        {"role": "user", "content": "Implement ENG-1"},
        {"type": "function_call", "name": "execute_bash", "arguments": "{}"},
    ]
    live = LiveJudges(
        live_judges(tmp_path, {}),
        tmp_path / "evaluations",
        store,
        lambda agent, session: [{"role": "user", "content": "foreman's"}],
        children=lambda agent, session: [
            ("triage", "sub_t", [{"role": "user", "content": "x"}]),
            ("implementer", "sub_i", implementer),
        ],
    )
    event_id = store.record(_event())
    dispatch = store.start_dispatch(event_id, "mentions", "foreman", "S1")
    assert await live.grade(dispatch, "mentions", "foreman", "S1", _event()) == 1
    # The implementer's judge, on the implementer's own session; triage and the foreman have none.
    assert seen == [("task_compliance", "implementer", "foreman", implementer)]
    [run] = store.runs_since(0)
    assert (
        run["grades"][0]["session_id"] == "sub_i"
        and run["grades"][0]["agent"] == "implementer"
    )
    assert "on implementer's session" in review_context(store.runs_since(0), 0, set())


def test_a_recorded_thread_reads_as_a_judges_history():
    from types import SimpleNamespace as N

    from omniagents.integrations.grading import thread_history

    items = [
        N(item_id="i1", kind="user_message", content={"text": "Implement ENG-1"}),
        N(item_id="i2", kind="reasoning", content={"summary": ""}),
        N(
            item_id="i3",
            kind="tool_call",
            content={
                "tool": "execute_bash",
                "input": '{"command": "pytest"}',
                "output": "17 passed",
            },
        ),
        N(item_id="i4", kind="agent_message", content={"text": "Opened PR #1."}),
    ]
    assert thread_history(items) == [
        {"role": "user", "content": "Implement ENG-1"},
        {
            "type": "function_call",
            "call_id": "i3",
            "name": "execute_bash",
            "arguments": '{"command": "pytest"}',
        },
        {"type": "function_call_output", "call_id": "i3", "output": "17 passed"},
        {"role": "assistant", "content": "Opened PR #1."},
    ]
