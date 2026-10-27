"""Notebook suites run through the engine: repeats, per-case rates, slices.

The agent is a real ``agents.Agent`` on the offline fake model; flakiness
comes from a measure that fails on chosen calls.
"""

import asyncio
from itertools import count

import pytest
from agents import Agent

from omniagents.core.evaluation import registry
from omniagents.notebook import EvalSuite, EvalSuiteResults, measure
from omniagents.testing import install_fake_model


@pytest.fixture
def agent(monkeypatch):
    install_fake_model(monkeypatch)
    monkeypatch.setattr(registry, "_MEASURES", dict(registry._MEASURES))
    return Agent(name="echo", instructions="Echo.", model="fake-model")


def _flaky(passes):
    """A measure passing on the calls listed in ``passes`` (per case name)."""
    calls = {name: count() for name in passes}

    @measure(name="flaky_check")
    def flaky_check(ctx):
        name = ctx.scenario["name"]
        n = next(calls[name])
        return {"passed": n in passes[name]}

    return flaky_check


def test_run_with_repeats_indexes_trials_and_rates_per_case(agent):
    suite = EvalSuite("s")
    suite.add("flaky", "q", metadata={"tone": "34"}, measures=["flaky_check"])
    suite.add(
        "broken", "say nothing", metadata={"tone": "56"}, measures=["flaky_check"]
    )
    _flaky({"flaky": {0, 2}, "broken": set()})

    results = asyncio.run(
        suite.run(agent, repeats=3, show_progress=False, concurrency=1)
    )
    assert sorted(r.trial for r in results.results if r.name == "flaky") == [0, 1, 2]
    by_case = results.by_case()
    assert by_case["flaky"]["passed"] == 2 and by_case["flaky"]["runs"] == 3
    assert by_case["broken"]["pass_rate"] == 0.0 and by_case["broken"]["answered"] == 0
    low, high = results.pass_rate_interval
    assert low < results.pass_rate < high
    assert results.answered_rate == pytest.approx(0.5)
    assert results.manifest["repeats"] == 3 and results.manifest["agent"] == "echo"


def test_by_field_slices_by_metadata_and_tags(agent):
    suite = EvalSuite("s")
    suite.add("a", "q", metadata={"tone": "12"}, tags=["dev"])
    suite.add("b", "be wrong", metadata={"tone": "56"}, tags=["dev", "malignant"])
    suite.add("c", "q", metadata={"tone": "56"}, tags=["test"])
    measure(name="not_wrong")(
        lambda ctx: {"passed": "wrong" not in (ctx.final_assistant_message.text or "")}
    )

    results = asyncio.run(suite.run(agent, measures=["not_wrong"], show_progress=False))
    by_tone = results.by_field("tone")
    assert set(by_tone) == {"12", "56"}
    assert by_tone["56"].pass_rate == 0.5
    assert isinstance(by_tone["56"], EvalSuiteResults)
    by_tag = results.by_field("tags")
    assert by_tag["dev"].summary["total"] == 2 and by_tag["malignant"].pass_rate == 0.0
    assert results.by_tag("dev").summary["total"] == 2
    assert results.summary["interval"][0] <= results.summary["pass_rate"]


def test_default_repeats_is_one(agent):
    suite = EvalSuite("s")
    suite.add("a", "q")
    results = asyncio.run(suite.run(agent, show_progress=False))
    (only,) = results.results
    assert only.trial == 0 and only.metadata is None
    assert only.passed and only.output == "You said: q"
    # The builtin measure a hand-built suite gets by default.
    assert [m.name for m in only.measures] == ["tool_hallucination"]


def test_failed_run_fails_the_case(agent):
    """A run the harness could not execute fails, it does not pass vacuously."""
    suite = EvalSuite("s")
    suite.add("a", "q", measures=["no_such_measure"])
    results = asyncio.run(suite.run(agent, show_progress=False))
    (only,) = results.results
    assert not only.passed
    assert "no_such_measure" in (only.error or "")
    assert results.stats["unknown_measures"] == {"no_such_measure": 1}
