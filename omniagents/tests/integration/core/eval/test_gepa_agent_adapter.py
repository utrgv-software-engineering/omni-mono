import textwrap
from pathlib import Path

import pytest

from omniagents.core.eval.gepa_agent_adapter import OmniAgentsAgentAdapter


def _write_agent_files(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "agent"
    cfg_dir.mkdir()
    measures = cfg_dir / "measures.py"
    measures.write_text(
        textwrap.dedent("""
            from omniagents.core.evaluation import evaluation_measure, pass_reason, fail_reason

            @evaluation_measure
            def numeric_accuracy(ctx):
                return {**pass_reason("numeric_ok"), "name": "numeric_accuracy"}

            @evaluation_measure
            def formatting_consistency(ctx):
                return {**pass_reason("format_ok"), "name": "formatting_consistency"}

            @evaluation_measure
            def tool_compliance(ctx):
                return {**fail_reason("tool_failure"), "name": "tool_compliance"}
            """),
        encoding="utf-8",
    )
    config = cfg_dir / "compiled.yaml"
    config.write_text(
        textwrap.dedent("""
            name: Sample Agent
            label: Sample Label
            description: Sample Description
            evaluation:
              failure_categories:
                arithmetic:
                  - numeric_accuracy
                  - formatting_consistency
                  - tool_compliance
            """),
        encoding="utf-8",
    )
    return config


def _build_adapter(tmp_path: Path, **kwargs) -> OmniAgentsAgentAdapter:
    cfg_path = _write_agent_files(tmp_path)
    return OmniAgentsAgentAdapter(cfg_path, "arithmetic", **kwargs)


def test_evaluate_collects_scores_and_trajectories(monkeypatch, tmp_path):
    """Full evaluation should score passes, capture trajectories, and surface failures."""
    adapter = _build_adapter(tmp_path, score_mode="fraction")
    calls = []
    scenarios = {
        "What is 2+2?": {
            "text": "4",
            "measures": [
                {"name": "numeric_accuracy", "passed": True, "reason": "exact"},
                {"name": "formatting_consistency", "passed": True, "reason": "clean"},
                {"name": "tool_compliance", "passed": True},
            ],
        },
        "List pi to three decimals": {
            "text": "pi is 3.141",
            "measures": [
                {"name": "numeric_accuracy", "passed": False, "reason": "rounded"},
                {"name": "formatting_consistency", "passed": True, "reason": "clean"},
                {"name": "tool_compliance", "passed": True},
            ],
        },
    }

    async def fake_run_once(self, instructions, prompt):
        payload = scenarios[prompt]
        calls.append((instructions, prompt))
        return {
            "history": [{"role": "assistant", "content": payload["text"]}],
            "final_assistant_text": payload["text"],
            "measures": payload["measures"],
        }

    monkeypatch.setattr(
        OmniAgentsAgentAdapter, "_run_once", fake_run_once, raising=False
    )

    batch = [
        {"id": "s1", "prompt": "What is 2+2?", "name": "addition"},
        {"id": "s2", "prompt": "List pi to three decimals", "name": "pi"},
    ]
    candidate = {"instructions": "Solve carefully and prefer explicit answers."}

    result = adapter.evaluate(batch, candidate, capture_traces=True)

    assert [round(s, 2) for s in result.scores] == [1.0, 0.67]
    first, second = result.outputs
    assert first["violation"] is False and first["failures"] == []
    assert (
        second["violation"] is True
        and second["failures"][0]["name"] == "numeric_accuracy"
    )
    assert len(result.trajectories) == 2
    assert calls == [
        (candidate["instructions"], "What is 2+2?"),
        (candidate["instructions"], "List pi to three decimals"),
    ]


def test_make_reflective_dataset_prioritizes_violations(monkeypatch, tmp_path):
    """Reflective dataset should highlight violations while keeping minimal supporting examples."""
    adapter = _build_adapter(tmp_path, score_mode="fraction")
    scenarios = {
        "Add three and four": {
            "text": "7",
            "measures": [
                {"name": "numeric_accuracy", "passed": False, "reason": "off"},
                {"name": "formatting_consistency", "passed": True},
                {"name": "tool_compliance", "passed": False, "reason": "skipped"},
            ],
        },
        "Square nine": {
            "text": "81",
            "measures": [
                {"name": "numeric_accuracy", "passed": True},
                {"name": "formatting_consistency", "passed": True},
                {"name": "tool_compliance", "passed": True},
            ],
        },
        "Half twelve": {
            "text": "6",
            "measures": [
                {"name": "numeric_accuracy", "passed": False, "reason": "half"},
                {"name": "formatting_consistency", "passed": True},
                {"name": "tool_compliance", "passed": True},
            ],
        },
    }

    async def fake_run_once(self, instructions, prompt):
        payload = scenarios[prompt]
        return {
            "history": [{"role": "assistant", "content": payload["text"]}],
            "final_assistant_text": payload["text"],
            "measures": payload["measures"],
        }

    monkeypatch.setattr(
        OmniAgentsAgentAdapter, "_run_once", fake_run_once, raising=False
    )

    batch = [
        {"id": "a", "prompt": "Add three and four", "name": "addition"},
        {"id": "b", "prompt": "Square nine", "name": "square"},
        {"id": "c", "prompt": "Half twelve", "name": "division"},
    ]
    candidate = {"instructions": "Do arithmetic accurately."}

    eval_result = adapter.evaluate(batch, candidate, capture_traces=True)
    dataset = adapter.make_reflective_dataset(candidate, eval_result, ["instructions"])

    assert "instructions" in dataset
    entries = dataset["instructions"]
    assert len(entries) == 3
    failure_feedback = [
        item["Feedback"] for item in entries if "Measures failed" in item["Feedback"]
    ]
    assert len(failure_feedback) == 2
    support_feedback = [
        item for item in entries if "All measures passed" in item["Feedback"]
    ]
    assert len(support_feedback) == 1
    for item in entries:
        assert item["Inputs"]["text"]
        assert item["Generated Outputs"]["final_assistant_text"]


def test_evaluate_handles_missing_prompts_and_runtime_errors(monkeypatch, tmp_path):
    """Evaluation should mark missing prompts and execution failures as violations with zero score."""
    adapter = _build_adapter(tmp_path, score_mode="fraction")

    async def failing_run_once(self, instructions, prompt):
        if prompt == "Compute 1/0":
            raise RuntimeError("division by zero")
        return {
            "history": [{"role": "assistant", "content": "ok"}],
            "final_assistant_text": "ok",
            "measures": [
                {"name": "numeric_accuracy", "passed": True},
                {"name": "formatting_consistency", "passed": True},
                {"name": "tool_compliance", "passed": True},
            ],
        }

    monkeypatch.setattr(
        OmniAgentsAgentAdapter, "_run_once", failing_run_once, raising=False
    )

    batch = [
        {"id": "missing", "prompt": "", "name": "empty"},
        {"id": "failing", "prompt": "Compute 1/0", "name": "division"},
        {"id": "passing", "prompt": "Compute 1+1", "name": "addition"},
    ]
    candidate = {"instructions": "Follow math rules."}

    result = adapter.evaluate(batch, candidate, capture_traces=False)

    outputs = result.outputs
    assert outputs[0]["violation"] is True
    assert outputs[0]["failures"][0] == {"name": "prompt", "reason": "missing_prompt"}
    assert outputs[0]["score"] == 0.0
    assert outputs[1]["violation"] is True
    assert outputs[1]["failures"][0]["reason"] == "division by zero"
    assert outputs[1]["score"] == 0.0
    assert outputs[2]["violation"] is False
    assert outputs[2]["score"] == 1.0
    assert result.trajectories is None
    assert [round(s, 1) for s in result.scores] == [0.0, 0.0, 1.0]
