import asyncio
import json
import sys
import types

import yaml

from omniagents.core.eval import scenarios


def test_generate_scenarios_filters_dimensions_and_emits_manifest(
    tmp_path, monkeypatch
):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    compiled_path = cfg_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "synthetic_data_generation": {
                        "dimensions": [
                            {
                                "name": "Persona",
                                "values": [
                                    {
                                        "name": "Expert",
                                        "description": "Expert persona",
                                        "examples": ["Example"],
                                    }
                                ],
                            },
                            {
                                "name": "Topic",
                                "values": [
                                    {
                                        "name": "Quality",
                                        "description": "Quality focus",
                                    },
                                    {
                                        "name": "Security",
                                        "description": "Security focus",
                                    },
                                ],
                            },
                        ],
                        "generator": {
                            "agent": {"name": "Generator"},
                            "input": {"template": "prompt-template", "max_turns": 1},
                        },
                    }
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    render_calls = []

    def fake_render(template, **ctx):
        render_calls.append(ctx)
        mapping = ctx["selected_mapping"]
        suffix = "|" + "|".join(f"{k}={mapping[k]}" for k in sorted(mapping))
        return f"prompt{suffix}"

    monkeypatch.setattr(scenarios, "render_template", fake_render)

    async def fake_build_agent(base_dir, agent_cfg, dims_catalog=None, raw_dims=None):
        return object()

    monkeypatch.setattr(scenarios, "_build_generator_agent", fake_build_agent)

    async def fake_run_generation(agent, prompt, max_turns=1):
        suffix = prompt.split("|", 1)[1]
        slug = suffix.replace("=", "-").replace("|", "_")
        return [
            {
                "name": f"name-{slug}",
                "prompt": f"generated-{slug}",
            }
        ]

    monkeypatch.setattr(scenarios, "_run_generation", fake_run_generation)

    progress_events = []

    scenario_list, output_path, ts = asyncio.run(
        scenarios.generate_scenarios(
            compiled_yaml=compiled_path,
            count=2,
            seed=7,
            set_name="demo-set",
            workflow_name="wf",
            dimension_filters={
                "Persona": ["Expert"],
                "Topic": ["Quality", "Security"],
            },
            progress_cb=progress_events.append,
        )
    )

    assert len(scenario_list) == 2
    assert progress_events[0]["event"] == "start"
    assert progress_events[-1]["event"] == "done"
    topics = {item["dimensions"]["Topic"] for item in scenario_list}
    assert topics == {"Quality", "Security"}
    for ctx in render_calls:
        persona_entry = next(
            it for it in ctx["selected_values"] if it["dimension"] == "Persona"
        )
        assert persona_entry["description"] == "Expert persona"
        assert persona_entry["examples"] == ["Example"]
    for sc in scenario_list:
        expected_id = scenarios._compute_scenario_id(sc["prompt"], sc["dimensions"])
        assert sc["id"] == expected_id
    output_data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert len(output_data["scenarios"]) == 2
    manifest = json.loads(
        (output_path.parent / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["total"] == 2
    assert manifest["workflow_generate"] == "wf"


def test_run_generation_parses_outputs_correctly(monkeypatch):
    outputs = [
        {"name": "A", "prompt": "one"},
        [
            {"name": "B", "prompt": "two"},
            "ignored",
        ],
        '```json\n{"name": "C", "prompt": "three"}\n```',
        "not json",
    ]

    agents_mod = types.ModuleType("agents")

    class Runner:
        call_count = 0

        @staticmethod
        async def run(agent, prompt, max_turns):
            data = outputs[Runner.call_count]
            Runner.call_count += 1
            return types.SimpleNamespace(final_output=data)

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    async def exercise():
        res1 = await scenarios._run_generation(object(), "p1")
        res2 = await scenarios._run_generation(object(), "p2")
        res3 = await scenarios._run_generation(object(), "p3")
        res4 = await scenarios._run_generation(object(), "p4")
        return res1, res2, res3, res4

    res1, res2, res3, res4 = asyncio.run(exercise())

    assert res1 == [{"name": "A", "prompt": "one"}]
    assert res2 == [{"name": "B", "prompt": "two"}]
    assert res3 == [{"name": "C", "prompt": "three"}]
    assert res4 == []


def test_generate_scenarios_skips_outputs_without_prompt(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    compiled_path = cfg_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "synthetic_data_generation": {
                        "dimensions": [
                            {
                                "name": "Persona",
                                "values": [{"name": "Expert"}],
                            }
                        ],
                        "generator": {
                            "agent": {"name": "Generator"},
                            "input": {"template": "template"},
                        },
                    }
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(scenarios, "render_template", lambda template, **ctx: template)

    async def fake_build(base_dir, agent_cfg, dims_catalog=None, raw_dims=None):
        return object()

    monkeypatch.setattr(scenarios, "_build_generator_agent", fake_build)

    async def fake_run(agent, prompt, max_turns=1):
        return [{"name": "sample"}]

    monkeypatch.setattr(scenarios, "_run_generation", fake_run)

    result, output_path, ts = asyncio.run(
        scenarios.generate_scenarios(
            compiled_yaml=compiled_path,
            count=1,
            seed=3,
            set_name="no-prompts",
        )
    )

    assert result == []
    output_data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert output_data["scenarios"] == []


def test_build_generator_agent_uses_default_instructions(monkeypatch, tmp_path):
    agents_mod = types.ModuleType("agents")

    class Runner:
        pass

    agents_mod.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    captured_specs = []

    class DummyAgentSpec:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.output_type = kwargs.get("output_type")
            self.output_type_strict = kwargs.get("output_type_strict")

        def get_agent_instructions(self):
            return self.kwargs["get_agent_instructions"]()

    async def fake_default_build(
        settings, mcp_servers, spec, approval_callback, _loading_stack=None
    ):
        captured_specs.append(spec)
        return object()

    monkeypatch.setattr("omniagents.core.agents.specs.AgentSpec", DummyAgentSpec)
    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_default_build
    )
    monkeypatch.setattr(scenarios, "render_template", lambda template, **ctx: template)

    async def exercise():
        return await scenarios._build_generator_agent(
            tmp_path,
            {"name": "Generator"},
            dims_catalog=[],
            raw_dims=[],
        )

    agent = asyncio.run(exercise())

    assert captured_specs
    default_text = captured_specs[0].get_agent_instructions()
    assert "You generate realistic user prompts" in default_text
    assert agent is not None


def test_enumerate_combos_applies_filters_and_preserves_dimension_names():
    dims = [
        {
            "name": "Persona",
            "values": [{"name": "Expert"}, {"name": "Novice"}],
        },
        {
            "name": "Topic",
            "values": [{"name": "Quality"}, {"name": "Security"}],
        },
    ]
    combos = scenarios._enumerate_combos(
        dims,
        {
            "Persona": ["Expert"],
            "Topic": ["Security"],
        },
    )
    assert combos == [{"Persona": "Expert", "Topic": "Security"}]


def test_enumerate_combos_returns_empty_when_filters_remove_all_options():
    dims = [
        {
            "name": "Persona",
            "values": [{"name": "Expert"}],
        }
    ]
    combos = scenarios._enumerate_combos(dims, {"Persona": ["Generalist"]})
    assert combos == []


def test_compute_scenario_id_is_deterministic_and_sensitive_to_dimensions():
    prompt = "Evaluate response"
    mapping_one = {"topic": "security", "difficulty": "hard"}
    mapping_two = {"difficulty": "hard", "topic": "security"}
    mapping_three = {"topic": "quality", "difficulty": "hard"}
    first = scenarios._compute_scenario_id(prompt, mapping_one)
    second = scenarios._compute_scenario_id(prompt, mapping_two)
    third = scenarios._compute_scenario_id(prompt, mapping_three)
    assert first == second
    assert third != first
