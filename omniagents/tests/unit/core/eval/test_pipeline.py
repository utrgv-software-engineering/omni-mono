import asyncio
import textwrap

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
