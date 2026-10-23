"""The notebook API and the CLI run evaluations through one engine.

Each test runs the same small suite both ways, offline, with a deterministic
fake model (``tests/utils/fake_model.py``) behind every model name, and
asserts the results are identical - per-run outcomes, measures, metrics,
intervals, slices, stats and the manifest - apart from what differs on every
run (timestamps, session and trace ids, durations, file paths).

- ``EvalSuite.from_project(...).run()`` against ``omniagents eval suite run``
  on the same project: the notebook adds nothing, so even the history and
  token usage match.
- A hand-built ``EvalSuite`` run with an ``agents.Agent`` against the CLI
  running ``suite.to_config()`` with the equivalent project agent: two
  runtimes (the SDK ``Runner`` vs ``LocalAgent``), one engine.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from omniagents.core.evaluation import registry
from omniagents.testing import install_fake_model

TOOL_SOURCE = textwrap.dedent('''
    from omniagents import function_tool


    @function_tool
    def get_weather(city: str) -> str:
        """Weather for a city."""
        return f"sunny in {city}"
''')

TONE_MEASURE = textwrap.dedent("""
    from omniagents.core.evaluation import evaluation_measure, fail_reason


    @evaluation_measure
    def tone_agent(ctx):
        judge = ctx.judge_result("tone")
        if judge is None:
            return fail_reason("tone judge did not run")
        return judge.to_result()
""")

# The YAML twin of the notebook's Judge("tone", ...) below.
TONE_JUDGE = {
    "agent": {
        "model": "fake-judge",
        "instructions": "You are an evaluation judge. Answer Pass unless the response is wrong.",
        "output_type": "dict",
        "output_type_strict": True,
        "output_schema": {
            "properties": {
                "reasoning": {"type": "string"},
                "answer": {"type": "string", "enum": ["Pass", "Fail"]},
                "score": {"type": "number"},
            },
            "required": ["reasoning", "answer", "score"],
        },
    },
    "input": {"source": "final_assistant_text", "template": "{{ text }}"},
}

METRICS = [
    {"name": "grounded", "type": "pass_rate", "source_measure": "tool_hallucination"},
    {
        "name": "expectations_met",
        "type": "pass_rate",
        "source_measure": "expectations",
        "slice_by": ["tone", "tags"],
        "target": {"op": ">=", "value": 0.5},
    },
    {"name": "answered", "type": "answered_rate"},
    {
        "name": "tone",
        "type": "pass_rate",
        "source_measure": "tone_agent",
        "exclude_nonresponse": True,
    },
]

SCENARIOS = [
    {
        "name": "paris",
        "prompt": "what is the weather in paris",
        "tags": ["weather"],
        "metadata": {"tone": "34"},
        "expect": {"tool_called": "get_weather", "response_contains": "sunny"},
        "measures": ["tool_hallucination", "expectations"],
    },
    {
        "name": "quiet",
        "prompt": "say nothing",
        "tags": ["silent"],
        "metadata": {"tone": "56"},
        "expect": {"response_contains": "hello"},
        "measures": ["tool_hallucination", "expectations"],
    },
    {
        "name": "wrong",
        "prompt": "be wrong",
        "metadata": {"tone": "56"},
        "runs_per_prompt": 3,
        "measures": {"outcome": ["tone_agent"], "process": ["tool_hallucination"]},
    },
    {
        "name": "convo",
        "prompts": ["hello there", "and the weather in rome"],
        "tags": ["weather"],
        "expect": {"tool_sequence": ["get_weather"]},
        "measures": ["expectations"],
    },
    {"name": "unmeasured", "prompt": "just chatting"},
]


def _write_project(root: Path, evaluation: Dict[str, Any]) -> Path:
    (root / "agents" / "helper").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "evaluations").mkdir()
    (root / "project.yml").write_text(
        "product:\n  name: parity_demo\nagents:\n  entrypoint: helper\n"
        "paths:\n  tools: tools\n",
        encoding="utf-8",
    )
    (root / "agents/helper/agent.yml").write_text(
        "name: helper\nmodel: fake-model\ninstructions: You help.\n"
        "tools:\n- get_weather\n",
        encoding="utf-8",
    )
    (root / "tools/weather.py").write_text(TOOL_SOURCE, encoding="utf-8")
    settings = dict(evaluation)
    (root / "evaluations/scenarios").mkdir()
    (root / "evaluations/scenarios/main.yml").write_text(
        yaml.safe_dump({"scenarios": settings.pop("scenarios", [])}, sort_keys=False),
        encoding="utf-8",
    )
    for key, judge in (settings.pop("judges", None) or {}).items():
        (root / "evaluations/judges" / key).mkdir(parents=True)
        (root / "evaluations/judges" / key / "judge.yml").write_text(
            yaml.safe_dump(judge, sort_keys=False), encoding="utf-8"
        )
    (root / "evaluations/evaluation.yml").write_text(
        yaml.safe_dump({"evaluation": settings}, sort_keys=False), encoding="utf-8"
    )
    return root / "project.yml"


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """Fake model behind every ``fake*`` name; registries restored afterwards."""
    from omniagents.notebook import evaluation as nb

    install_fake_model(monkeypatch)
    monkeypatch.setattr(registry, "_MEASURES", dict(registry._MEASURES))
    monkeypatch.setattr(nb, "_JUDGES", dict(nb._JUDGES))
    monkeypatch.setenv("OMNIAGENTS_EVAL_TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _cli_run(project_yml: Path, out: Path, monkeypatch, *extra: str) -> Dict[str, Any]:
    """``omniagents eval suite run --project ... -o out.json``, as a user runs it."""
    from omniagents.cli import main as cli_main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "eval",
            "suite",
            "run",
            "--project",
            str(project_yml),
            "-o",
            str(out),
            "--reporter",
            "tap",
            "--color",
            "never",
            *extra,
        ],
    )
    try:
        cli_main()
    except SystemExit as exc:  # failing gating measures exit 1
        assert exc.code in (0, 1, None)
    return json.loads(out.read_text(encoding="utf-8"))


VOLATILE_RUN_KEYS = {
    "session_id",
    "trace_group_id",
    "transcript_path",
    "duration_seconds",
    "history",
}


def _run_view(run: Dict[str, Any], *, same_runtime: bool) -> Dict[str, Any]:
    view = {k: v for k, v in run.items() if k not in VOLATILE_RUN_KEYS}
    view["metadata"] = {
        k: v for k, v in (run.get("metadata") or {}).items() if k != "group_id"
    }
    if not same_runtime:
        # Two runtimes persist a conversation differently (the project agent's
        # session store vs the SDK's input list); everything scored from it
        # must still agree.
        view.pop("history_len")
        # A project's scenario carries the file it is written in; a
        # hand-built one has none.
        view["scenario_config"] = {
            k: v
            for k, v in (run.get("scenario_config") or {}).items()
            if k != "_source_path"
        }
    return view


def _manifest_view(manifest: Dict[str, Any], *, same_agent: bool) -> Dict[str, Any]:
    view = {k: v for k, v in manifest.items() if k != "created_at"}
    if not same_agent:
        for key in ("agent_yml", "agent_yml_sha"):
            view.pop(key)
    return view


def _assert_same(cli: Dict[str, Any], nb_results, *, same_runtime: bool) -> None:
    nb = nb_results.data
    assert [_run_view(r, same_runtime=same_runtime) for r in nb["runs"]] == [
        _run_view(r, same_runtime=same_runtime) for r in cli["runs"]
    ]
    assert nb["metrics"] == cli["metrics"]
    assert nb["summary"] == cli["summary"]
    assert nb["stats"] == cli["stats"]
    assert _manifest_view(nb["manifest"], same_agent=same_runtime) == _manifest_view(
        cli["manifest"], same_agent=same_runtime
    )
    # The notebook's own rates are the engine's, not a second implementation.
    summary = cli["summary"]
    assert nb_results.pass_rate == summary["value"]
    assert list(nb_results.pass_rate_interval) == summary["interval"]
    assert [r.passed for r in nb_results.results] == [r["passed"] for r in cli["runs"]]


def _check_expected_outcomes(data: Dict[str, Any]) -> None:
    """The suite exercises what it is meant to (guards against vacuous parity)."""
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for r in data["runs"]:
        by_name.setdefault(r["scenario"], []).append(r)
    assert set(by_name) == {"paris", "quiet", "wrong", "convo"}
    assert [r["trial"] for r in by_name["paris"]] == [0, 1]
    assert [r["trial"] for r in by_name["wrong"]] == [0, 1, 2]
    assert all(r["passed"] for r in by_name["paris"])
    assert by_name["paris"][0]["final_assistant_text"] == "It is sunny in Paris."
    # One tool call and one answer: two model requests, both counted.
    assert by_name["paris"][0]["tool_call_count"] == 1
    assert by_name["paris"][0]["usage"]["requests"] == 2
    assert not any(r["answered"] or r["passed"] for r in by_name["quiet"])
    assert all(r["metadata"]["tone"]["answer"] == "Fail" for r in by_name["wrong"])
    assert not any(r["passed"] for r in by_name["wrong"])
    assert all(r["passed"] and r["exchanges"] == 2 for r in by_name["convo"])
    metrics = {m["name"]: m for m in data["metrics"]}
    assert metrics["expectations_met"]["slices"]["tone"]["56"]["runs"] == 2
    assert metrics["expectations_met"]["slices"]["tags"]["weather"]["value"] == 1.0
    assert metrics["answered"]["non_responses"] == 2
    assert metrics["tone"]["value"] == 0.0 and metrics["tone"]["runs"] == 3
    assert data["stats"]["skipped_unmeasured"] == 1
    assert data["summary"]["runs"] == 9 and data["summary"]["passed_runs"] == 4


def test_project_suite_matches_cli(offline, monkeypatch):
    from omniagents.notebook import EvalSuite

    project = _write_project(
        offline,
        {
            "defaults": {"runs_per_prompt": 2, "max_turns": 6},
            "judges": {"tone": TONE_JUDGE},
            "scenarios": SCENARIOS,
            "metrics": METRICS,
        },
    )
    (offline / "evaluations/measures.py").write_text(TONE_MEASURE, encoding="utf-8")

    cli = _cli_run(project, offline / "cli.json", monkeypatch)
    suite = EvalSuite.from_project(project)
    results = asyncio.run(suite.run(show_progress=False))

    _check_expected_outcomes(cli)
    _assert_same(cli, results, same_runtime=True)
    assert cli["manifest"]["max_turns"] == 6 and cli["manifest"]["repeats"] == 2


def test_project_suite_selection_matches_cli(offline, monkeypatch):
    from omniagents.notebook import EvalSuite

    project = _write_project(
        offline,
        {"scenarios": SCENARIOS, "metrics": METRICS, "judges": {"tone": TONE_JUDGE}},
    )
    (offline / "evaluations/measures.py").write_text(TONE_MEASURE, encoding="utf-8")

    cli = _cli_run(
        project, offline / "cli.json", monkeypatch, "--select", "tag:weather"
    )
    suite = EvalSuite.from_project(project, select="tag:weather")
    results = asyncio.run(suite.run(show_progress=False))

    assert [r["scenario"] for r in cli["runs"]] == ["paris", "convo"]
    assert cli["manifest"]["select"] == "tag:weather"
    _assert_same(cli, results, same_runtime=True)


def test_hand_built_suite_with_sdk_agent_matches_cli(offline, monkeypatch):
    from agents import Agent, function_tool

    from omniagents.notebook import EvalSuite, Judge

    @function_tool
    def get_weather(city: str) -> str:
        """Weather for a city."""
        return f"sunny in {city}"

    agent = Agent(
        name="helper", instructions="You help.", model="fake-model", tools=[get_weather]
    )
    tone = Judge(
        "tone",
        instructions="You are an evaluation judge. Answer Pass unless the response is wrong.",
        model="fake-judge",
    )

    suite = EvalSuite("parity")
    suite.add(
        "paris",
        "what is the weather in paris",
        tags=["weather"],
        metadata={"tone": "34"},
        expect={"tool_called": "get_weather", "response_contains": "sunny"},
    )
    suite.add(
        "quiet",
        "say nothing",
        tags=["silent"],
        metadata={"tone": "56"},
        expect={"response_contains": "hello"},
    )
    suite.add("wrong", "be wrong", metadata={"tone": "56"}, judges=[tone])
    suite.add(
        "convo",
        prompts=["hello there", "and the weather in rome"],
        tags=["weather"],
        expect={"tool_sequence": ["get_weather"]},
    )

    results = asyncio.run(
        suite.run(agent, repeats=2, max_turns=10, show_progress=False)
    )

    # The CLI runs exactly what the notebook ran: its config, the project agent.
    config = suite.to_config(repeats=2, max_turns=10)
    config["metrics"] = METRICS
    project = _write_project(offline, config)
    cli = _cli_run(project, offline / "cli.json", monkeypatch)

    # Metrics come from the config, which the notebook run did not carry;
    # compare the per-run outcomes and the whole-run aggregates.
    nb = results.data
    assert [_run_view(r, same_runtime=False) for r in nb["runs"]] == [
        _run_view(r, same_runtime=False) for r in cli["runs"]
    ]
    assert nb["summary"] == cli["summary"]
    assert nb["stats"] == cli["stats"]
    assert _manifest_view(nb["manifest"], same_agent=False) == _manifest_view(
        cli["manifest"], same_agent=False
    )

    by_case = results.by_case()
    assert by_case["paris"]["passed"] == 2 and by_case["wrong"]["passed"] == 0
    assert by_case["quiet"]["answered"] == 0
    wrong = next(r for r in results.results if r.name == "wrong")
    assert (
        wrong.judges and wrong.judges[0].name == "tone" and not wrong.judges[0].passed
    )
    paris = next(r for r in results.results if r.name == "paris")
    assert paris.called("get_weather") and all(e.passed for e in paris.expectations)
    tones = results.by_field("tone")
    assert set(tones) == {"34", "56"} and tones["34"].pass_rate == 1.0
    assert results.by_field("tags")["weather"].summary["total"] == 4

    # The same notebook suite with the metrics attached reproduces the CLI's
    # metrics, intervals and slices exactly.
    suite.config = {"metrics": METRICS}
    with_metrics = asyncio.run(
        suite.run(agent, repeats=2, max_turns=10, show_progress=False)
    )
    assert with_metrics.metrics == cli["metrics"]
