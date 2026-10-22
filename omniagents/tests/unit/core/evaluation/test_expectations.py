import asyncio

from omniagents.core.eval.environment import (
    EnvironmentContext,
    EvalEnvironment,
    NoOpEnvironment,
)
from omniagents.core.evaluation import build_eval_context, run_measures_async
from omniagents.core.evaluation.expectations import has_expectations

HISTORY = [
    {"role": "user", "content": "weather?"},
    {
        "type": "function_call",
        "name": "get_weather",
        "call_id": "c1",
        "arguments": "{}",
    },
    {"type": "function_call_output", "call_id": "c1", "output": "sunny"},
    {"role": "assistant", "content": "It is Sunny."},
]


def _ctx(expect, environment=None, context=None):
    ctx = build_eval_context(history=HISTORY, metadata={"scenario": {"expect": expect}})
    ctx._environment = environment
    ctx._environment_context = context
    return ctx


def _run(ctx):
    (result,) = asyncio.run(run_measures_async(ctx, ["expectations"]))
    return result


def test_expectations_measure_checks_each_key():
    result = _run(
        _ctx(
            {
                "tool_called": "get_weather",
                "tool_not_called": ["delete_all"],
                "tool_sequence": ["get_weather"],
                "tool_called_times": {"get_weather": 1},
                "response_contains": "sunny",
                "response_not_contains": "rain",
                "expected_answer": "data for other measures",
            }
        )
    )
    assert result["passed"] is True
    assert len(result["checks"]) == 6


def test_expectations_measure_fails_with_reasons():
    result = _run(_ctx({"tool_called": "geocode", "response_contains": "sunny"}))
    assert result["passed"] is False
    assert "tool_called:geocode" in result["reason"]
    assert [c["passed"] for c in result["checks"]] == [False, True]


class _Env(EvalEnvironment):
    async def setup(self, scenario):
        return EnvironmentContext(session_id="s")

    async def teardown(self, context):
        pass

    async def verify(self, context, expected):
        return {"success": expected == {"row": 1}, "results": {"row": "ok"}}


def test_verify_needs_a_real_environment():
    noop = _run(
        _ctx({"verify": {"row": 1}}, NoOpEnvironment(), EnvironmentContext("s"))
    )
    assert noop["passed"] is False and "requires an EvalEnvironment" in noop["reason"]
    real = _run(_ctx({"verify": {"row": 1}}, _Env(), EnvironmentContext("s")))
    assert real["passed"] is True


def test_has_expectations_ignores_plain_data():
    assert has_expectations({"tool_called": "x"})
    assert not has_expectations({"expected_answer": "42"})
    assert not has_expectations(None)
