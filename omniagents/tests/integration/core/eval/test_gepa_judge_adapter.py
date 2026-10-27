import asyncio
import textwrap
import types

from omniagents.core.eval.gepa_judge_adapter import OmniAgentsJudgeAdapter


def _create_adapter(tmp_path, *, category=None):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    instructions_path = cfg_dir / "instructions.txt"
    instructions_path.write_text(
        "Guidance for {{ agent_label }} in {{ category }}", encoding="utf-8"
    )
    template_path = cfg_dir / "template.md"
    template_path.write_text(
        "Prompt: {{ text }} :: {{ history_json }}", encoding="utf-8"
    )
    config_text = f"""
    name: Integration Agent
    label: Integration Label
    evaluation:
      judges:
        accuracy:
          agent:
            name: Accuracy Judge
            label: Accuracy Label
            instructions_file: {instructions_path.name}
            output_type_strict: true
            output_schema:
              properties:
                answer:
                  type: string
                correct:
                  type: boolean
          input:
            template_file: {template_path.name}
    """
    compiled_path = cfg_dir / "compiled.yaml"
    compiled_path.write_text(textwrap.dedent(config_text), encoding="utf-8")
    return OmniAgentsJudgeAdapter(compiled_path, "accuracy", {}, category=category)


def test_build_judge_agent_creates_typed_dict_output(monkeypatch, tmp_path):
    adapter = _create_adapter(tmp_path, category="safety")
    captured = {}

    async def fake_build(settings, mcp_servers, spec, approval_callback):
        captured["spec"] = spec
        captured["settings"] = settings
        return "agent"

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build
    )

    instructions = adapter._read_text_file(
        adapter.compiled_yaml.parent, adapter._judge_cfg["agent"]["instructions_file"]
    )
    result = asyncio.run(adapter._build_judge_agent(instructions))
    spec = captured["spec"]
    annotations = getattr(spec.output_type, "__annotations__", {})
    assert annotations.get("answer") is str
    assert annotations.get("correct") is bool
    assert spec.output_type_strict is True
    assert spec.get_agent_instructions() == "Guidance for Integration Label in safety"
    assert result == "agent"
    assert captured["settings"] == {}


def test_evaluate_full_workflow_scores(monkeypatch, tmp_path):
    adapter = _create_adapter(tmp_path, category="compliance")
    runs_content = {
        "runs": [
            {
                "scenario_id": "tp",
                "final_assistant_text": "alpha",
                "history": [{"role": "assistant", "content": "alpha"}],
            },
            {
                "scenario_id": "tn",
                "final_assistant_text": "beta",
                "history": [{"role": "assistant", "content": "beta"}],
            },
            {
                "scenario_id": "fp",
                "final_assistant_text": "gamma",
                "history": [{"role": "assistant", "content": "gamma"}],
            },
            {
                "scenario_id": "fn",
                "final_assistant_text": "delta",
                "history": [{"role": "assistant", "content": "delta"}],
            },
        ]
    }
    adapter._runs_by_id = {r["scenario_id"]: r for r in runs_content["runs"]}
    captured = {}

    async def fake_build(settings, mcp_servers, spec, approval_callback):
        captured["instructions"] = spec.get_agent_instructions()
        return "agent"

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build
    )

    payloads = iter(
        [
            '{"answer": "Fail"}',
            '```json\n{"correct": false}\n```',
            {"should_decline": False},
            '{"verdict": "Pass"}',
        ]
    )
    prompts = []

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns, context=None):
            # The judge path anchors workspace_root via the context kwarg.
            prompts.append(prompt)
            value = next(payloads)
            return types.SimpleNamespace(final_output=value)

    import agents

    monkeypatch.setattr(agents, "Runner", Runner)

    batch = [
        {"id": "tp", "label": True, "name": "tp"},
        {"id": "tn", "label": False, "name": "tn"},
        {"id": "fp", "label": False, "name": "fp"},
        {"id": "fn", "label": True, "name": "fn"},
    ]
    result = adapter.evaluate(batch, {}, capture_traces=True)

    assert (
        captured.get("instructions") == "Guidance for Integration Label in compliance"
    )
    assert result.scores == [1.0, 0.0, 0.5, 0.0]
    assert [o["kind"] for o in result.outputs] == ["TP", "TN", "FP", "FN"]
    assert len(result.trajectories) == 4
    assert prompts and "alpha" in prompts[0] and "gamma" in prompts[2]


def test_make_reflective_dataset_includes_examples(tmp_path):
    adapter = _create_adapter(tmp_path)
    trajectories = [
        {
            "id": "fp",
            "label": False,
            "pred": True,
            "kind": "FP",
            "prompt": "p1",
            "judge_output": {"reasoning": "r1"},
        },
        {
            "id": "fn",
            "label": True,
            "pred": False,
            "kind": "FN",
            "prompt": "p2",
            "judge_output": {"reasoning": "r2"},
        },
        {
            "id": "tp",
            "label": True,
            "pred": True,
            "kind": "TP",
            "prompt": "p3",
            "judge_output": {"reasoning": "r3"},
        },
        {
            "id": "tn",
            "label": False,
            "pred": False,
            "kind": "TN",
            "prompt": "p4",
            "judge_output": {"reasoning": "r4"},
        },
        {
            "id": "unk",
            "label": True,
            "pred": None,
            "kind": "UNK",
            "prompt": "p5",
            "judge_output": {"reasoning": "r5"},
        },
    ]
    eval_batch = types.SimpleNamespace(trajectories=trajectories)
    candidate = {"instructions": "instr", "template": "tmpl"}
    reflective = adapter.make_reflective_dataset(
        candidate,
        eval_batch,
        ["instructions", "template"],
    )

    assert "instructions" in reflective and "template" in reflective
    assert len(reflective["instructions"]) == 4
    assert any(
        "False Positive" in item["Feedback"] for item in reflective["instructions"]
    )
    assert any(
        "False Negative" in item["Feedback"] for item in reflective["instructions"]
    )
    assert all(
        "Inputs" in item and "Generated Outputs" in item
        for item in reflective["instructions"]
    )
    assert all("current_template" in x["Inputs"] for x in reflective["template"])
