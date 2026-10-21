import asyncio
import json
import textwrap
import types

import pytest

from omniagents.core.eval.gepa_judge_adapter import OmniAgentsJudgeAdapter


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"answer": "Fail"}, True),
        ({"answer": "Pass"}, False),
        ({"correct": True}, True),
        ({"correct": False}, False),
        ('```json\n{"correct": true}\n```', True),
        ('{"correct": true}', True),
        ("true", True),
        ("False", False),
        (True, True),
        (False, False),
        ({"should_decline": True}, False),
        ({"should_decline": False}, True),
        ('{"answer": "Fail"}', True),
    ],
)
def test_predict_label_from_various_outputs(raw, expected):
    assert OmniAgentsJudgeAdapter._predict_label_from_judge_output(raw) is expected


@pytest.mark.parametrize(
    "raw",
    ["", "maybe", {"answer": "unknown"}, [], {"data": {"value": "?"}}],
)
def test_predict_label_returns_none_for_unknown(raw):
    assert OmniAgentsJudgeAdapter._predict_label_from_judge_output(raw) is None


def _write_adapter_files(tmp_path, config_text, runs):
    config_path = tmp_path / "compiled.yaml"
    config_path.write_text(textwrap.dedent(config_text), encoding="utf-8")
    runs_path = tmp_path / "runs.json"
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    return config_path, runs_path


def test_evaluate_applies_scoring_matrix(monkeypatch, tmp_path):
    config_text = """
    name: Sample Agent
    label: Sample Label
    evaluation:
      judges:
        accuracy:
          agent:
            name: Accuracy Judge
          input:
            template: "{{ text }}"
    """
    runs = {
        "runs": [
            {"scenario_id": "tp", "final_assistant_text": "one"},
            {"scenario_id": "tn", "final_assistant_text": "two"},
            {"scenario_id": "fp", "final_assistant_text": "three"},
            {"scenario_id": "fn", "final_assistant_text": "four"},
        ]
    }
    compiled_path, runs_path = _write_adapter_files(tmp_path, config_text, runs)
    adapter = OmniAgentsJudgeAdapter(compiled_path, "accuracy", runs_path)

    async def fake_build(self, instructions):
        return object()

    monkeypatch.setattr(OmniAgentsJudgeAdapter, "_build_judge_agent", fake_build)

    payloads = iter(
        [
            {"answer": "Fail"},
            '{"answer": "Pass"}',
            "true",
            '{"correct": false}',
        ]
    )
    prompts = []

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns, context=None, **_kwargs):
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
    candidate = {"instructions": "use", "template": "{{ text }}"}
    result = adapter.evaluate(batch, candidate, capture_traces=True)

    assert result.outputs[0]["kind"] == "TP"
    assert result.outputs[1]["kind"] == "TN"
    assert result.outputs[2]["kind"] == "FP"
    assert result.outputs[3]["kind"] == "FN"
    assert result.scores == [1.0, 0.0, 0.5, 0.0]
    assert len(result.trajectories) == 4
    assert prompts and "one" in prompts[0]


def test_build_judge_agent_renders_agent_metadata(monkeypatch, tmp_path):
    config_text = """
    name: Example Agent
    label: Example Label
    description: Example Description
    evaluation:
      judges:
        accuracy:
          agent:
            name: Accuracy Judge
            label: Accuracy Label
            output_schema:
              properties:
                answer:
                  type: string
                correct:
                  type: boolean
          input:
            template: "{{ text }}"
    """
    compiled_path, runs_path = _write_adapter_files(tmp_path, config_text, {"runs": []})
    adapter = OmniAgentsJudgeAdapter(compiled_path, "accuracy", runs_path)
    captured = {}

    async def fake_build(settings, mcp_servers, spec, approval_callback):
        captured["spec"] = spec
        return "agent"

    monkeypatch.setattr(
        "omniagents.core.agents.builder._default_build_agent", fake_build
    )

    result = asyncio.run(adapter._build_judge_agent("Hello {{ agent_label }}"))
    spec = captured["spec"]
    annotations = getattr(spec.output_type, "__annotations__", {})
    assert annotations.get("answer") is str
    assert annotations.get("correct") is bool
    assert spec.get_agent_instructions() == "Hello Example Label"
    assert result == "agent"
