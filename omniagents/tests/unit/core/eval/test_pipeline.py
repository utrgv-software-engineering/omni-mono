import asyncio
import contextlib
import json
import sys
import textwrap
import types

from omniagents.core.eval import pipeline
from omniagents.core.evaluation import registry


def test_run_evaluation_async_handles_failure_categories(monkeypatch, tmp_path):
    config_text = textwrap.dedent("""
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
        """)
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)

    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg, **kw: None)
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
        agent_yaml, scenario, defaults, eval_cfg, workflow_name, **kw
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
        async def run(agent, prompt, max_turns, context=None, **_kwargs):
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
    config_text = textwrap.dedent("""
        evaluation:
          scenarios:
            - name: Solo
              prompt: hi
              measures:
                - m1
          metrics:
            - name: pass_rate
        """)
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)

    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg, **kw: None)
    monkeypatch.setattr(
        pipeline, "compute_metrics", lambda runs, metrics_cfg: {"count": len(runs)}
    )

    registry_map = {"m1": lambda ctx: {"name": "m1", "passed": True}}
    monkeypatch.setattr(registry, "_MEASURES", registry_map)

    async def fake_run_scenario(
        agent_yaml, scenario, defaults, eval_cfg, workflow_name, **kw
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
    project_yaml.write_text(textwrap.dedent("""
            paths:
              evaluations:
                - custom_eval
            """))

    eval_dir = project_root / "custom_eval"
    eval_dir.mkdir()
    measures_file = eval_dir / "measures.py"
    measures_file.write_text("MARKER = 'loaded'")

    module_name = f"eval_measures_{eval_dir.name}"
    sys.modules.pop(module_name, None)

    pipeline._load_local_measures(compiled, project_root=project_root)

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
    agent_yaml.write_text(textwrap.dedent("""
            evaluation:
              synthetic_data_generation:
                dimensions:
                  - name: tone
                    values:
                      - name: serious
            """))

    spec_holder = {}

    def fake_load_spec(path, **kw):
        spec = types.SimpleNamespace(
            use_safe_agent=True,
            max_turns=1,
            available_tools=["t"],
            project_slug="proj",
            agent_slug="agent",
            # run_scenario anchors workspace_root onto spec.context under
            # strict mode; real AgentSpec defaults this to None.
            context=None,
            variables=None,
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

    async def fake_run_judges(cfg_path, eval_cfg, scenario, history, **kw):
        return {"quality": {"score": "ok"}}

    monkeypatch.setattr(pipeline, "_run_judges", fake_run_judges)

    trace_calls = []

    @contextlib.contextmanager
    def fake_trace(name, group_id=None, metadata=None):
        trace_calls.append({"name": name, "group_id": group_id, "metadata": metadata})
        yield

    tracing_mod = types.SimpleNamespace(trace=fake_trace)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", tracing_mod)

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
    # Telemetry fields
    assert "duration_seconds" in result
    assert isinstance(result["duration_seconds"], float)
    assert result["duration_seconds"] >= 0
    assert "tool_call_count" in result
    assert isinstance(result["tool_call_count"], int)
    assert "usage" in result
    assert "total_tokens" in result["usage"]
    assert "error_count" in result
    assert isinstance(result["error_count"], int)
    assert trace_calls and trace_calls[0]["name"] == "EvalFlow"
    assert trace_calls[0]["group_id"].startswith("scenario-id-")
    assert trace_calls[0]["metadata"]["omniagents"]["synthetic"]["selection"][
        "mapping"
    ] == {"tone": "serious"}


def test_run_evaluation_with_studio_tracing(monkeypatch, tmp_path):
    """Test that run_evaluation works with studio tracing configured.

    Note: Testing that tracing.init is actually called is difficult due to
    module caching. This test verifies the function runs successfully with
    tracing config but doesn't verify the internal init call.
    """
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    async_calls = []

    async def fake_run_evaluation_async(
        compiled_yaml,
        workflow_name=None,
        include_unmeasured=False,
        progress_cb=None,
        concurrency=5,
        **kw,
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

    tracing_cfg = {
        "provider": "studio",
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
    # Verify that run_evaluation_async was called with a workflow name
    assert async_calls
    assert async_calls[0]["include_unmeasured"] is True
    # Workflow name should be set when tracing is configured
    workflow_name = async_calls[0]["workflow_name"]
    if workflow_name:  # May be None if tracing import failed
        assert workflow_name.startswith("EvalBase_scenarios_")


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


def test_run_evaluation_with_environment_returns_error_on_failed_build(
    monkeypatch, tmp_path
):
    """A declared environment that fails to build is captured in the result."""
    from omniagents.core.eval import multi_turn

    # Config with an unknown environment type and no scenarios so no
    # actual agent runs.
    config_text = textwrap.dedent("""
        evaluation:
          environment:
            type: nonexistent_type
          scenarios: []
        """)
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)

    # _load_local_measures is imported from .pipeline inside the function
    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg, **kw: None)

    result = asyncio.run(
        multi_turn.run_evaluation_with_environment_async(
            cfg_path, include_unmeasured=True
        )
    )

    # Verify the error is captured even with no scenarios
    assert "environment_error" in result
    assert "nonexistent_type" in result["environment_error"]
    assert "warnings" in result
    assert len(result["warnings"]) == 1
    assert "Environment loading failed" in result["warnings"][0]
    # Verify runs are empty since we have no scenarios
    assert result["runs"] == []


def test_run_evaluation_async_indexes_repeats_and_records_answered(monkeypatch, tmp_path):
    config_text = textwrap.dedent("""
        evaluation:
          defaults:
            runs_per_prompt: 3
            slice_by: [expect.tone]
          scenarios:
            - name: Alpha
              prompt: hi
              expect: {tone: "12"}
              measures:
                - tool_hallucination
          metrics:
            - name: rate
              type: pass_rate
              source_measure: tool_hallucination
        """)
    cfg_path = tmp_path / "compiled.yaml"
    cfg_path.write_text(config_text)
    monkeypatch.setattr(pipeline, "_load_local_measures", lambda cfg, **kw: None)
    monkeypatch.setattr(registry, "_MEASURES", {"tool_hallucination": lambda ctx: {"name": "tool_hallucination", "passed": True}})

    async def fake_run_scenario(agent_yaml, scenario, defaults, eval_cfg, workflow_name, **kw):
        return {
            "scenario": scenario["name"],
            "prompt": scenario["prompt"],
            "session_id": "s",
            "final_assistant_text": "done",
            "answered": True,
            "measures": [{"name": "tool_hallucination", "passed": True}],
            "history_len": 1,
            "metadata": {},
        }

    monkeypatch.setattr(pipeline, "_run_scenario", fake_run_scenario)
    result = asyncio.run(pipeline.run_evaluation_async(cfg_path))
    assert sorted(r["trial"] for r in result["runs"]) == [0, 1, 2]
    assert all(r["repeats"] == 3 for r in result["runs"])
    assert result["stats"]["repeats"] == 3
    assert result["stats"]["answered"] == 3
    (metric,) = result["metrics"]
    assert metric["runs"] == 3 and metric["interval"] is not None
    assert metric["slices"]["expect.tone"]["12"]["runs"] == 3
