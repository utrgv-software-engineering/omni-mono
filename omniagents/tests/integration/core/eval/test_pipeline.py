import asyncio
import sys
import textwrap
import types

from omniagents.core.eval import engine, pipeline, targets
from omniagents.core.evaluation import registry


class _Streamed:
    """A streamed run over a fake ``Runner.run``: no events, then its final output."""

    def __init__(self, pending):
        self._pending = pending
        self.final_output = None

    async def stream_events(self):
        self.final_output = (await self._pending).final_output
        return
        yield

    def cancel(self):
        pass


def _configure_pipeline(monkeypatch, history_text="final"):
    def fake_load_spec(path, **kw):
        return types.SimpleNamespace(
            use_safe_agent=True,
            max_turns=1,
            available_tools=[],
            project_slug="proj",
            agent_slug="agent",
            # run_scenario anchors workspace_root onto spec.context under
            # strict mode; real AgentSpec defaults these to None.
            context=None,
            variables=None,
        )

    monkeypatch.setattr(targets, "load_agent_spec_from_yaml", fake_load_spec)

    class FakeRunResult:
        async def stream_events(self):
            yield {"delta": "chunk"}

    class FakeAgent:
        def __init__(self):
            self._session_id = "session"

        def run_streamed(self, msgs, run_config=None):
            return FakeRunResult()

        def get_last_run_end(self):
            return None

    async def fake_create_agent(spec, **kwargs):
        return FakeAgent()

    monkeypatch.setattr(targets, "create_unified_agent", fake_create_agent)
    monkeypatch.setattr(
        targets,
        "load_history",
        lambda session_id, project_slug, agent_slug: [
            {"role": "assistant", "content": history_text}
        ],
    )

    def fake_build_eval_context(*, history, tools, metadata, session=None, agent=None):
        return types.SimpleNamespace(history=history, tools=tools, metadata=metadata)

    monkeypatch.setattr(engine, "build_eval_context", fake_build_eval_context)
    monkeypatch.setattr(
        engine,
        "final_assistant_text",
        lambda history: history[-1]["content"] if history else "",
    )
    monkeypatch.setattr(
        engine,
        "compute_metrics",
        lambda runs, metrics_cfg, **kw: {"run_total": len(runs)},
    )
    monkeypatch.setattr(pipeline, "load_project_measures", lambda cfg, **kw: None)


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
        async def run(agent, prompt, max_turns, context=None):
            key = agent.agent_slug.replace("judge_", "")
            judge_calls.append(key)
            return types.SimpleNamespace(
                final_output={"answer": "Pass", "reasoning": f"{key}-ok"}
            )

        @staticmethod
        def run_streamed(agent, prompt, max_turns=1, context=None, **_kwargs):
            # Judges run streamed (the Codex backend accepts only streaming).
            return _Streamed(Runner.run(agent, prompt, max_turns, context=context))

    class RunConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Agent:
        pass

    agents_mod.Runner = Runner
    agents_mod.RunConfig = RunConfig
    agents_mod.Agent = Agent
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

    config_text = textwrap.dedent("""
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
        """)
    cfg_path = tmp_path / "agent.yaml"
    cfg_path.write_text(config_text)

    result = asyncio.run(pipeline.run_evaluation_async(cfg_path))
    assert judge_calls == ["accuracy", "completeness"]
    assert built_specs and {s.agent_slug for s in built_specs} == {
        "judge_accuracy",
        "judge_completeness",
    }

    run = result["runs"][0]
    assert sorted(run["metadata"].keys()) == [
        "accuracy",
        "completeness",
        "environment_context",
    ]
    for measure in run["measures"]:
        assert measure["passed"] is True
        # ctx.metadata always includes "scenario" so measures can read ctx.expect/ctx.scenario
        assert measure["judge_keys"] == [
            "accuracy",
            "completeness",
            "environment_context",
            "scenario",
        ]


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
        async def run(agent, prompt, max_turns, context=None):
            key = agent.agent_slug.replace("judge_", "")
            judge_calls.append(key)
            return types.SimpleNamespace(
                final_output={"answer": "Pass", "reasoning": key}
            )

        @staticmethod
        def run_streamed(agent, prompt, max_turns=1, context=None, **_kwargs):
            # Judges run streamed (the Codex backend accepts only streaming).
            return _Streamed(Runner.run(agent, prompt, max_turns, context=context))

    class RunConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Agent:
        pass

    agents_mod.Runner = Runner
    agents_mod.RunConfig = RunConfig
    agents_mod.Agent = Agent
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    async def fake_build_agent(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        return types.SimpleNamespace(agent_slug=spec.agent_slug)

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent",
        fake_build_agent,
    )

    config_text = textwrap.dedent("""
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
        """)
    cfg_path = tmp_path / "agent.yaml"
    cfg_path.write_text(config_text)

    result = asyncio.run(pipeline.run_evaluation_async(cfg_path))
    assert sorted(judge_calls) == ["consistency", "custom", "quality", "quality"]

    runs_by_id = {run["scenario_config"]["id"]: run for run in result["runs"]}

    # ctx.metadata always includes "scenario" so measures can read ctx.expect/ctx.scenario,
    # but the run's "metadata" field still excludes it (keeps backward-compat with consumers).
    auto_run = runs_by_id["auto"]
    env = "environment_context"
    assert sorted(auto_run["metadata"].keys()) == ["consistency", env, "quality"]
    for measure in auto_run["measures"]:
        assert measure["judge_keys"] == ["consistency", env, "quality", "scenario"]

    none_run = runs_by_id["none"]
    assert none_run["metadata"] == {env: {}}
    for measure in none_run["measures"]:
        assert measure["judge_keys"] == [env, "scenario"]

    explicit_run = runs_by_id["explicit"]
    assert sorted(explicit_run["metadata"].keys()) == ["custom", env, "quality"]
    for measure in explicit_run["measures"]:
        assert measure["judge_keys"] == ["custom", env, "quality", "scenario"]
