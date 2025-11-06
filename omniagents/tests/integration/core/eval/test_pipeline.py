import asyncio
import sys
import textwrap
import types

from omniagents.core.eval import pipeline
from omniagents.core.evaluation import registry


def _configure_pipeline(monkeypatch, history_text="final"):
    def fake_load_spec(path):
        return types.SimpleNamespace(
            use_safe_agent=True,
            max_turns=1,
            available_tools=[],
            project_slug="proj",
            agent_slug="agent",
        )

    monkeypatch.setattr(pipeline, "load_agent_spec_from_yaml", fake_load_spec)

    class FakeRunResult:
        async def stream_events(self):
            yield {"delta": "chunk"}

    class FakeAgent:
        def __init__(self):
            self._session_id = "session"

        def run_streamed(self, msgs):
            return FakeRunResult()

        def get_last_run_end(self):
            return None

    async def fake_create_agent(spec):
        return FakeAgent()

    monkeypatch.setattr(pipeline, "create_unified_agent", fake_create_agent)
    monkeypatch.setattr(
        pipeline,
        "load_history",
        lambda session_id, project_slug, agent_slug: [
            {"role": "assistant", "content": history_text}
        ],
    )

    def fake_build_eval_context(*, history, tools, metadata, session=None, agent=None):
        return types.SimpleNamespace(history=history, tools=tools, metadata=metadata)

    monkeypatch.setattr(pipeline, "build_eval_context", fake_build_eval_context)
    monkeypatch.setattr(
        pipeline,
        "final_assistant_text",
        lambda history: history[-1]["content"] if history else "",
    )
    monkeypatch.setattr(
        pipeline, "compute_metrics", lambda runs, metrics_cfg: {"run_total": len(runs)}
    )
    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg: None)


def test_pipeline_executes_multiple_judges_per_scenario(tmp_path, monkeypatch):
    _configure_pipeline(monkeypatch)
    measure_map = {}

    def make_measure(expected_key):
        def measure(ctx):
            return {
                "name": f"{expected_key}_agent",
                "passed": expected_key in ctx.metadata,
                "judge_keys": sorted(ctx.metadata.keys()),
            }

        return measure

    measure_map["accuracy_agent"] = make_measure("accuracy")
    measure_map["completeness_agent"] = make_measure("completeness")
    monkeypatch.setattr(registry, "_MEASURES", measure_map, raising=False)

    judge_calls = []
    agents_mod = types.ModuleType("agents")

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns):
            key = agent.agent_slug.replace("judge_", "")
            judge_calls.append(key)
            return types.SimpleNamespace(
                final_output={"answer": "Pass", "reasoning": f"{key}-ok"}
            )

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)
    built_specs = []

    async def fake_build_agent(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        built_specs.append(spec)
        return types.SimpleNamespace(agent_slug=spec.agent_slug)

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_build_agent,
    )

    config_text = textwrap.dedent(
        """
        name: Multi Judge Agent
        evaluation:
          judges:
            accuracy:
              agent:
                name: Accuracy Judge
            completeness:
              agent:
                name: Completeness Judge
          scenarios:
            - id: multi
              name: multi
              prompt: "Rate this answer"
              measures:
                - accuracy_agent
                - completeness_agent
          metrics: []
        """
    )
    cfg_path = tmp_path / "agent.yaml"
    cfg_path.write_text(config_text)

    result = asyncio.run(pipeline.run_evaluation_async(cfg_path))
    assert judge_calls == ["accuracy", "completeness"]
    assert built_specs and {s.agent_slug for s in built_specs} == {
        "judge_accuracy",
        "judge_completeness",
    }

    run = result["runs"][0]
    assert sorted(run["metadata"].keys()) == ["accuracy", "completeness"]
    for measure in run["measures"]:
        assert measure["passed"] is True
        assert measure["judge_keys"] == ["accuracy", "completeness"]


def test_pipeline_judge_selection_modes(tmp_path, monkeypatch):
    _configure_pipeline(monkeypatch)
    measure_map = {}

    def make_measure(expected_key):
        def measure(ctx):
            return {
                "name": f"{expected_key}_agent",
                "passed": expected_key in ctx.metadata,
                "judge_keys": sorted(ctx.metadata.keys()),
            }

        return measure

    measure_map["quality_agent"] = make_measure("quality")
    measure_map["consistency_agent"] = make_measure("consistency")
    monkeypatch.setattr(registry, "_MEASURES", measure_map, raising=False)

    judge_calls = []
    agents_mod = types.ModuleType("agents")

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns):
            key = agent.agent_slug.replace("judge_", "")
            judge_calls.append(key)
            return types.SimpleNamespace(
                final_output={"answer": "Pass", "reasoning": key}
            )

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    async def fake_build_agent(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        return types.SimpleNamespace(agent_slug=spec.agent_slug)

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_build_agent,
    )

    config_text = textwrap.dedent(
        """
        name: Judge Selection Agent
        evaluation:
          judges:
            quality:
              agent:
                name: Quality Judge
            consistency:
              agent:
                name: Consistency Judge
            custom:
              agent:
                name: Custom Judge
          scenarios:
            - id: auto
              name: auto
              prompt: "Auto"
              measures:
                - quality_agent
                - consistency_agent
            - id: none
              name: none
              prompt: "None"
              judges: none
              measures:
                - quality_agent
            - id: explicit
              name: explicit
              prompt: "Explicit"
              judges:
                - custom
              measures:
                - quality_agent
          metrics: []
        """
    )
    cfg_path = tmp_path / "agent.yaml"
    cfg_path.write_text(config_text)

    result = asyncio.run(pipeline.run_evaluation_async(cfg_path))
    assert sorted(judge_calls) == ["consistency", "custom", "quality", "quality"]

    runs_by_id = {run["scenario_config"]["id"]: run for run in result["runs"]}

    auto_run = runs_by_id["auto"]
    assert sorted(auto_run["metadata"].keys()) == ["consistency", "quality"]
    for measure in auto_run["measures"]:
        assert measure["judge_keys"] == ["consistency", "quality"]

    none_run = runs_by_id["none"]
    assert none_run["metadata"] == {}
    for measure in none_run["measures"]:
        assert measure["judge_keys"] == []

    explicit_run = runs_by_id["explicit"]
    assert sorted(explicit_run["metadata"].keys()) == ["custom", "quality"]
    for measure in explicit_run["measures"]:
        assert measure["judge_keys"] == ["custom", "quality"]
