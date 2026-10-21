import asyncio

import pytest

from omniagents.notebook import evaluation as ev
from omniagents.notebook.evaluation import EvalResult, EvalSuite, EvalSuiteResults


def _fake_evaluate(outcomes):
    """A stand-in for evaluate() that returns scripted pass/fail per (case, call)."""
    calls = {}

    async def fake(agent, input=None, *, name=None, **kwargs):
        n = calls.get(name, 0)
        calls[name] = n + 1
        passed, text = outcomes[name][n % len(outcomes[name])]
        return EvalResult(
            input=input or "", output=text, passed=passed, measures=[], name=name
        )

    return fake, calls


def test_run_with_repeats_indexes_trials_and_rates_per_case(monkeypatch):
    suite = EvalSuite("s")
    suite.add("flaky", "q", metadata={"tone": "34"})
    suite.add("broken", "q", metadata={"tone": "56"})
    fake, calls = _fake_evaluate(
        {
            "flaky": [(True, "ok"), (False, "no"), (True, "ok")],
            "broken": [(False, ""), (False, ""), (False, "")],
        }
    )
    monkeypatch.setattr(ev, "evaluate", fake)

    results = asyncio.run(suite.run(agent=object(), repeats=3, show_progress=False))
    assert calls == {"flaky": 3, "broken": 3}
    assert sorted(r.trial for r in results.results if r.name == "flaky") == [0, 1, 2]
    by_case = results.by_case()
    assert by_case["flaky"]["passed"] == 2 and by_case["flaky"]["runs"] == 3
    assert by_case["broken"]["pass_rate"] == 0.0 and by_case["broken"]["answered"] == 0
    low, high = results.pass_rate_interval
    assert low < results.pass_rate < high
    assert results.answered_rate == pytest.approx(0.5)


def test_by_field_slices_by_metadata_and_tags(monkeypatch):
    suite = EvalSuite("s")
    suite.add("a", "q", metadata={"tone": "12"}, tags=["dev"])
    suite.add("b", "q", metadata={"tone": "56"}, tags=["dev", "malignant"])
    suite.add("c", "q", metadata={"tone": "56"}, tags=["test"])
    fake, _ = _fake_evaluate(
        {"a": [(True, "ok")], "b": [(False, "no")], "c": [(True, "ok")]}
    )
    monkeypatch.setattr(ev, "evaluate", fake)

    results = asyncio.run(suite.run(agent=object(), show_progress=False))
    by_tone = results.by_field("tone")
    assert set(by_tone) == {"12", "56"}
    assert by_tone["56"].pass_rate == 0.5
    assert isinstance(by_tone["56"], EvalSuiteResults)
    by_tag = results.by_field("tags")
    assert by_tag["dev"].summary["total"] == 2 and by_tag["malignant"].pass_rate == 0.0
    assert results.summary["interval"][0] <= results.summary["pass_rate"]


def test_default_repeats_is_one(monkeypatch):
    suite = EvalSuite("s")
    suite.add("a", "q")
    fake, calls = _fake_evaluate({"a": [(True, "ok")]})
    monkeypatch.setattr(ev, "evaluate", fake)
    results = asyncio.run(suite.run(agent=object(), show_progress=False))
    assert (
        calls == {"a": 1}
        and results.results[0].trial == 0
        and results.results[0].metadata is None
    )
