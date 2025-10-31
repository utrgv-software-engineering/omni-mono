from omniagents.core.eval import pipeline


def test_failure_category_map_supports_multiple_shapes():
    cfg_dict = {
        "failure_categories": {
            "safety": {"measures": ["safety_agent", "tool_hallucination"]},
            "quality": ["quality_agent"],
        }
    }
    cfg_list = {
        "failure_categories": [
            {"name": "latency", "measures": ["latency_agent"]},
            {"key": "reliability", "measures": ["reliability_agent"]},
        ]
    }

    result_dict = pipeline._failure_category_map(cfg_dict)
    result_list = pipeline._failure_category_map(cfg_list)

    assert result_dict == {
        "safety": ["safety_agent", "tool_hallucination"],
        "quality": ["quality_agent"],
    }
    assert result_list["latency"] == ["latency_agent"]
    assert result_list["reliability"] == ["reliability_agent"]


def test_collect_judges_uses_measures_and_overrides():
    eval_cfg = {
        "judges": {
            "quality": {"agent": {"name": "Quality"}},
            "safety": {"agent": {"name": "Safety"}},
            "latency": {"agent": {"name": "Latency"}},
        }
    }
    scenario_auto = {"measures": ["quality_agent", "latency_agent"]}
    scenario_override = {
        "measures": ["quality_agent"],
        "judges": ["safety"],
    }
    scenario_none = {"measures": ["quality_agent"], "judges": None}

    auto = pipeline._collect_judges_for_scenario(eval_cfg, scenario_auto)
    override = pipeline._collect_judges_for_scenario(eval_cfg, scenario_override)
    none_case = pipeline._collect_judges_for_scenario(eval_cfg, scenario_none)

    assert [key for key, _ in auto] == ["quality", "latency"]
    assert [key for key, _ in override] == ["quality", "safety"]
    assert none_case == []


def test_derive_judge_keys_from_measures_deduplicates():
    measures = ["quality_agent", "quality_agent", "safety_agent", "tool"]
    result = pipeline._derive_judge_keys_from_measures(measures)
    assert result == ["quality", "safety"]
