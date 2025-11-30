import asyncio
import contextlib
import json
import sys
import textwrap
import types

from omniagents.core.eval import pipeline
from omniagents.core.evaluation import registry


def test_run_evaluation_async_handles_failure_categories(monkeypatch, tmp_path):
    config_text = textwrap.dedent(
        """
        evaluation:
          defaults:
            runs_per_prompt: 2
          failure_categories:
            safety:
              measures:
                - safety_agent
          scenarios:
            - name: Alpha
              prompt: hi
              measures:
                - tool_hallucination
              failure_categories:
                - safety
            - name: Beta
              prompt: bye
          metrics:
            - name: pass_rate
        """
    )
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)

    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg: None)
    monkeypatch.setattr(
        pipeline, "compute_metrics", lambda runs, metrics_cfg: {"run_count": len(runs)}
    )

    registry_map = {
        "tool_hallucination": lambda ctx: {
            "name": "tool_hallucination",
            "passed": True,
        },
        "safety_agent": lambda ctx: {"name": "safety_agent", "passed": True},
    }
    monkeypatch.setattr(registry, "_MEASURES", registry_map)

    calls = []

    async def fake_run_scenario(
        agent_yaml, scenario, defaults, eval_cfg, workflow_name
    ):
        calls.append(
            {
                "name": scenario["name"],
                "measures": list(scenario.get("measures") or []),
                "failure_categories": list(scenario.get("failure_categories") or []),
            }
        )
        return {
            "scenario": scenario["name"],
            "prompt": scenario["prompt"],
            "session_id": f"session-{scenario['name']}",
            "final_assistant_text": "done",
            "measures": [
                {"name": m, "passed": True} for m in scenario.get("measures", [])
            ],
            "history_len": 1,
            "metadata": {"judge": {"answer": "pass"}},
        }

    monkeypatch.setattr(pipeline, "_run_scenario", fake_run_scenario)

    progress_events = []

    result = asyncio.run(
        pipeline.run_evaluation_async(cfg_path, progress_cb=progress_events.append)
    )

    assert len(calls) == 2
    assert calls[0]["measures"] == ["tool_hallucination", "safety_agent"]
    assert result["stats"]["total_scenarios"] == 2
    assert result["stats"]["skipped_unmeasured"] == 1
    assert result["stats"]["unknown_measures"] == {}
    assert result["metrics"] == {"run_count": 2}
    assert len(progress_events) == 3
    assert progress_events[0]["event"] == "start"
    assert progress_events[1]["index"] == 1 and progress_events[2]["index"] == 2
    assert result["runs"][0]["scenario_config"]["measures"] == [
        "tool_hallucination",
        "safety_agent",
    ]


def test_collect_judges_for_scenario_uses_measure_suffix():
    eval_cfg = {
        "judges": {
            "quality": {"agent": {"name": "Quality"}},
            "safety": {"agent": {"name": "Safety"}},
        }
    }
    scenario = {"measures": ["quality_agent", "tool_hallucination"]}
    judges = pipeline._collect_judges_for_scenario(eval_cfg, scenario)
    assert judges == [("quality", {"agent": {"name": "Quality"}})]


def test_run_judges_parses_coded_output(monkeypatch, tmp_path):
    import sys
    import types

    agents_mod = types.ModuleType("agents")
    prompts = []

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns):
            prompts.append(prompt)
            return types.SimpleNamespace(
                final_output='```json\n{"answer": "Pass", "reasoning": "ok"}\n```'
            )

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    built_specs = []

    async def fake_build_agent(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        built_specs.append(spec)
        return object()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build_agent
    )

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    instructions_path = cfg_dir / "instructions.txt"
    instructions_path.write_text("Judge instructions text", encoding="utf-8")
    template_path = cfg_dir / "template.md"
    template_path.write_text("Prompt: {{ text }}", encoding="utf-8")
    cfg_path = cfg_dir / "compiled.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    eval_cfg = {
        "judges": {
            "quality": {
                "agent": {
                    "name": "Quality",
                    "instructions_file": instructions_path.name,
                    "output_type": "dict",
                    "output_type_strict": True,
                    "output_schema": {"properties": {"answer": {"type": "string"}}},
                },
                "input": {"template_file": template_path.name},
            }
        }
    }

    scenario = {"judges": ["quality"], "measures": ["quality_agent"]}
    history = [{"role": "assistant", "content": "final answer"}]

    result = asyncio.run(pipeline._run_judges(cfg_path, eval_cfg, scenario, history))

    assert result["quality"]["answer"] == "Pass"
    assert "final answer" in prompts[0]
    assert (
        built_specs
        and built_specs[0].get_agent_instructions() == "Judge instructions text"
    )


def test_run_evaluation_async_progress_callback_exceptions(monkeypatch, tmp_path):
    config_text = textwrap.dedent(
        """
        evaluation:
          scenarios:
            - name: Solo
              prompt: hi
              measures:
                - m1
          metrics:
            - name: pass_rate
        """
    )
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)

    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg: None)
    monkeypatch.setattr(
        pipeline, "compute_metrics", lambda runs, metrics_cfg: {"count": len(runs)}
    )

    registry_map = {"m1": lambda ctx: {"name": "m1", "passed": True}}
    monkeypatch.setattr(registry, "_MEASURES", registry_map)

    async def fake_run_scenario(
        agent_yaml, scenario, defaults, eval_cfg, workflow_name
    ):
        return {
            "scenario": scenario["name"],
            "prompt": scenario["prompt"],
            "session_id": "session",
            "final_assistant_text": "text",
            "measures": [{"name": "m1", "passed": True}],
            "history_len": 0,
            "metadata": {},
        }

    monkeypatch.setattr(pipeline, "_run_scenario", fake_run_scenario)

    progress_calls = []

    def progress_cb(event):
        progress_calls.append(event)
        raise RuntimeError("boom")

    result = asyncio.run(
        pipeline.run_evaluation_async(cfg_path, progress_cb=progress_cb)
    )

    assert len(result["runs"]) == 1
    assert result["metrics"] == {"count": 1}
    assert len(progress_calls) == 2
    assert progress_calls[0]["event"] == "start"
    assert progress_calls[1]["event"] == "progress"


def test_load_local_measures_discovers_project_paths(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    compiled = cfg_dir / "compiled.yaml"
    compiled.write_text("{}", encoding="utf-8")

    agents_dir = tmp_path / "project" / "agents"
    agents_dir.mkdir(parents=True)
    agent_file = agents_dir / "main.yaml"
    agent_file.write_text("agent: {}")

    project_root = agents_dir.parent
    project_yaml = project_root / "project.yml"
    project_yaml.write_text(
        textwrap.dedent(
            """
            paths:
              evaluations:
                - custom_eval
            """
        )
    )

    eval_dir = project_root / "custom_eval"
    eval_dir.mkdir()
    measures_file = eval_dir / "measures.py"
    measures_file.write_text("MARKER = 'loaded'")

    manifest = {"agents": [{"file": str(agent_file)}]}
    (cfg_dir / "agents.json").write_text(json.dumps(manifest))

    module_name = f"eval_measures_{eval_dir.name}"
    sys.modules.pop(module_name, None)

    pipeline._load_local_measures(compiled)

    assert module_name in sys.modules
    assert getattr(sys.modules[module_name], "MARKER") == "loaded"

    sys.modules.pop(module_name, None)


def test_run_judges_records_failure(monkeypatch, tmp_path):
    agents_mod = types.ModuleType("agents")

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns):
            raise RuntimeError("judge boom")

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    async def fake_build_agent(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        return object()

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build_agent
    )

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "compiled.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    eval_cfg = {"judges": {"quality": {"agent": {"name": "Quality"}}}}
    scenario = {
        "judges": ["quality"],
        "measures": ["quality_agent"],
        "history_pref": True,
    }

    result = asyncio.run(
        pipeline._run_judges(
            cfg_path,
            {
                "judges": {
                    "quality": {
                        "agent": {"name": "Quality"},
                        "input": {"source": "history"},
                    }
                }
            },
            scenario,
            [{"role": "assistant", "content": "final"}],
        )
    )

    assert result == {"quality": {"error": "judge_failed"}}


def test_run_scenario_traces_and_records_agent_error(monkeypatch, tmp_path):
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        textwrap.dedent(
            """
            evaluation:
              synthetic_data_generation:
                dimensions:
                  - name: tone
                    values:
                      - name: serious
            """
        )
    )

    spec_holder = {}

    def fake_load_spec(path):
        spec = types.SimpleNamespace(
            use_safe_agent=True,
            max_turns=1,
            available_tools=["t"],
            project_slug="proj",
            agent_slug="agent",
        )
        spec_holder["spec"] = spec
        return spec

    monkeypatch.setattr(pipeline, "load_agent_spec_from_yaml", fake_load_spec)

    fake_agents = []

    class FakeRunResult:
        async def stream_events(self):
            yield {"delta": "chunk"}

    class FakeAgent:
        def __init__(self):
            self.calls = []
            self._session_id = "sess-123"

        def run_streamed(self, msgs):
            self.calls.append(msgs)
            return FakeRunResult()

        def get_last_run_end(self):
            return {"error": {"message": "bad", "type": "AgentError"}}

    async def fake_create_agent(spec):
        agent = FakeAgent()
        fake_agents.append(agent)
        return agent

    monkeypatch.setattr(pipeline, "create_unified_agent", fake_create_agent)
    monkeypatch.setattr(
        pipeline,
        "load_history",
        lambda session_id, project_slug, agent_slug: [
            {"role": "assistant", "content": "final"}
        ],
    )
    monkeypatch.setattr(pipeline, "final_assistant_text", lambda history: "final text")
    monkeypatch.setattr(
        pipeline,
        "build_eval_context",
        lambda history, tools, metadata: {
            "history": history,
            "tools": tools,
            "metadata": metadata,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "run_measures",
        lambda ctx, measures: [{"name": m, "passed": True} for m in measures],
    )

    async def fake_run_judges(cfg_path, eval_cfg, scenario, history):
        return {"quality": {"score": "ok"}}

    monkeypatch.setattr(pipeline, "_run_judges", fake_run_judges)

    trace_calls = []

    @contextlib.contextmanager
    def fake_trace(name, group_id=None, metadata=None):
        trace_calls.append({"name": name, "group_id": group_id, "metadata": metadata})
        yield

    agentio_mod = types.SimpleNamespace(trace=fake_trace)
    monkeypatch.setitem(sys.modules, "agentio", agentio_mod)

    scenario = {
        "id": "scenario-id",
        "name": "ScenarioOne",
        "prompt": "Do it",
        "measures": ["measure_a"],
        "dimensions": {"tone": "serious"},
    }
    defaults = {"max_turns": 4}
    eval_cfg = {}

    result = asyncio.run(
        pipeline._run_scenario(
            str(agent_yaml),
            scenario,
            defaults,
            eval_cfg,
            workflow_name="EvalFlow",
        )
    )

    assert spec_holder["spec"].max_turns == 4
    assert fake_agents and fake_agents[0].calls[0][0]["content"] == "Do it"
    assert result["scenario"] == "ScenarioOne"
    assert result["final_assistant_text"] == "final text"
    assert result["history_len"] == 1
    assert result["metadata"]["quality"] == {"score": "ok"}
    assert result["metadata"]["agent_error"]["error"]["message"] == "bad"
    assert any(m["name"] == "_agent_error" for m in result["measures"])
    assert trace_calls and trace_calls[0]["name"] == "EvalFlow"
    assert trace_calls[0]["group_id"].startswith("scenario-id-")
    assert trace_calls[0]["metadata"]["omniagents"]["synthetic"]["selection"][
        "mapping"
    ] == {"tone": "serious"}


def test_run_evaluation_with_agentio_tracing(monkeypatch, tmp_path):
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    async_calls = []

    async def fake_run_evaluation_async(
        compiled_yaml,
        workflow_name=None,
        include_unmeasured=False,
        progress_cb=None,
        concurrency=5,
    ):
        async_calls.append(
            {
                "compiled_yaml": compiled_yaml,
                "workflow_name": workflow_name,
                "include_unmeasured": include_unmeasured,
                "progress_cb": progress_cb,
                "concurrency": concurrency,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(pipeline, "run_evaluation_async", fake_run_evaluation_async)

    import asyncio as real_asyncio

    def fake_asyncio_run(coro):
        loop = real_asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(pipeline.asyncio, "run", fake_asyncio_run)

    init_calls = []

    def fake_agentio_init(project, name, replace_default, **settings):
        init_calls.append(
            {
                "project": project,
                "name": name,
                "replace_default": replace_default,
                "settings": settings,
            }
        )

    agentio_mod = types.SimpleNamespace(init=fake_agentio_init)
    monkeypatch.setitem(sys.modules, "agentio", agentio_mod)

    tracing_cfg = {
        "provider": "agentio",
        "project": "proj",
        "settings": {"region": "us"},
    }

    result = pipeline.run_evaluation(
        cfg_path,
        tracing_cfg=tracing_cfg,
        workflow_base_name="EvalBase",
        include_unmeasured=True,
    )

    assert result == {"ok": True}
    assert init_calls and init_calls[0]["project"] == "proj"
    workflow_name = init_calls[0]["name"]
    assert workflow_name.startswith("EvalBase_scenarios_")
    assert init_calls[0]["replace_default"] is True
    assert init_calls[0]["settings"] == {"region": "us"}
    assert async_calls and async_calls[0]["workflow_name"] == workflow_name
    assert async_calls[0]["include_unmeasured"] is True


def test_catalog_with_labels_derives_missing_entries():
    dimensions = [
        {
            "name": "tone_of_voice",
            "values": [
                {"name": "formal_tone"},
                {"name": "casual", "label": "Preferred Casual"},
            ],
        },
        {
            "name": "non_existent",
            "values": [{"name": "non_existent"}],
        },
    ]

    result = pipeline._catalog_with_labels(dimensions)

    assert result[0]["label"] == "Tone of voice"
    value_labels = [value["label"] for value in result[0]["values"]]
    assert value_labels == ["Formal tone", "Preferred Casual"]
    assert result[1]["label"] == "Non-existent"
    assert result[1]["values"][0]["label"] == "Non-existent"


def test_derive_judge_keys_from_measures_handles_mixed_values():
    measures = [
        "quality_agent",
        "custom_agent",
        "custom_agent",
        "speed_agent",
        "speed_agent_extra",
        None,
        {"name": "ignored"},
    ]

    result = pipeline._derive_judge_keys_from_measures(measures)

    assert result == ["quality", "custom", "speed"]
