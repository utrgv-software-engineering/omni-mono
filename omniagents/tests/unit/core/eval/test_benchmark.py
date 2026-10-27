import pytest

from omniagents.core.eval.benchmark import frontier, load_matrix, run_matrix
from omniagents.core.pricing import Price, PriceList


def test_a_matrix_runs_each_configuration_and_finds_the_frontier(monkeypatch, tmp_path):
    import omniagents.core.tracing as tracing
    import omniagents.core.usage as usage

    sinks = []

    class Recorder:
        def __init__(self, sink):
            sinks.append(sink)

    monkeypatch.setattr(usage, "UsageRecorder", Recorder)
    monkeypatch.setattr(tracing, "add_permanent_processor", lambda p: None)
    monkeypatch.setattr(tracing, "remove_permanent_processor", lambda p: None)
    outcomes = {"big": [True, True], "small": [True, False], "mid": [True, False]}
    tokens = {"big": 1_000_000, "small": 100_000, "mid": 900_000}

    def once():
        from omniagents.core.agents.overrides import current_overrides

        model = current_overrides()["model"]
        name = {"m-big": "big", "m-small": "small", "m-mid": "mid"}[model]
        sinks[-1](
            {
                "model": model,
                "input_tokens": tokens[name],
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
        return {
            "path": f"/runs/{name}",
            "runs": [
                {"scenario": f"task{i}", "measures": [{"passed": ok}]}
                for i, ok in enumerate(outcomes[name])
            ],
        }

    matrix_file = tmp_path / "configs.yml"
    matrix_file.write_text(
        "configs:\n  - {name: big, model: m-big}\n  - {name: small, model: m-small, reasoning: low}\n"
        "  - {name: mid, model: m-mid}\nruns: 1\n"
    )
    prices = PriceList(
        {
            m: Price(input=1.0, cached_input=0.1, output=1.0)
            for m in ("m-big", "m-small", "m-mid")
        }
    )
    result = run_matrix(once, load_matrix(matrix_file), prices=prices, suite="api")
    by = {r["name"]: r for r in result["configs"]}
    assert (by["big"]["correctness"], by["big"]["cost_per_task"]) == (
        1.0,
        pytest.approx(0.5),
    )
    assert (by["small"]["correctness"], by["small"]["cost_per_task"]) == (
        0.5,
        pytest.approx(0.05),
    )
    assert by["small"]["reasoning"] == "low" and by["small"]["by_task"]["task1"] == {
        "runs": 1,
        "passed": 0,
    }
    # mid is as correct as small and dearer: not on the frontier.
    assert result["frontier"] == ["big", "small"]


def test_a_matrix_file_is_checked(tmp_path):
    path = tmp_path / "configs.yml"
    path.write_text("configs:\n  - {name: x}\n")
    with pytest.raises(ValueError, match="name and a model"):
        load_matrix(path)
    assert frontier([{"name": "a", "cost_per_task": None, "correctness": 1.0}]) == []


@pytest.mark.asyncio
async def test_every_agent_built_under_an_override_takes_its_model():
    from types import SimpleNamespace as N

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.agents.overrides import overriding
    from omniagents.core.agents.specs import AgentSpec

    spec = AgentSpec(
        name="worker",
        welcome_text="",
        get_agent_instructions=lambda: "x",
        build_agent=None,
        model_name="its-own",
        use_safe_agent=False,
    )
    with overriding(model="the-benchmarks", reasoning="high"):
        agent = await _default_build_agent(
            settings={}, mcp_servers=None, spec=spec, session=None
        )
    assert agent.model == "the-benchmarks"
    assert agent.model_settings.reasoning.effort == "high"
    plain = await _default_build_agent(
        settings={}, mcp_servers=None, spec=spec, session=None
    )
    assert plain.model == "its-own"
