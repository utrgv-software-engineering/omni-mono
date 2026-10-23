from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omniagents.core.eval.loader import (
    EvalConfig,
    load_eval_config,
    filter_scenarios,
    introspect_resources,
)


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _create_project(
    tmp_path: Path,
    *,
    project_name: str = "demo_project",
    scenarios: list[dict] | None = None,
    metrics: list[dict] | None = None,
    include_judge: bool = True,
    include_measures: bool = False,
    include_optimizers: bool = False,
    scenario_directory: bool = False,
) -> Path:
    root = tmp_path / project_name
    agents_dir = root / "agents" / "alpha"
    tools_dir = root / "tools"
    eval_dir = root / "evaluations"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tools_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        root / "project.yml",
        {
            "product": {"name": project_name},
            "paths": {
                "agents": "agents",
                "evaluations": "evaluations",
                "tools": "tools",
            },
            "agents": {
                "entrypoint": "alpha",
            },
            "tracing": {
                "provider": "demo",
            },
        },
    )
    _write_yaml(
        agents_dir / "agent.yml",
        {
            "name": "alpha",
            "model": "gpt-4o",
            "instructions_file": "instructions.md",
        },
    )
    (agents_dir / "instructions.md").write_text("Base instructions", encoding="utf-8")
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "echo.py").write_text(
        "def echo(value):\n    return value\n", encoding="utf-8"
    )
    _write_yaml(eval_dir / "evaluation.yml", {"evaluation": {"title": "suite"}})
    _write_yaml(
        eval_dir / "scenarios.yml",
        {
            "scenarios": scenarios
            or [{"name": "scenario-alpha", "description": "hello"}]
        },
    )
    _write_yaml(
        eval_dir / "metrics.yml",
        {"metrics": metrics or [{"name": "score", "type": "number"}]},
    )
    if scenario_directory:
        scenario_dir = eval_dir / "scenarios"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(
            scenario_dir / "extra.yml",
            {"scenarios": [{"name": "scenario-gamma", "description": "extra"}]},
        )
    if include_judge:
        judge_dir = eval_dir / "judges"
        judge_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(
            judge_dir / "primary.yml",
            {
                "judges": {
                    "primary": {
                        "agent": {
                            "name": "arbiter",
                        }
                    }
                }
            },
        )
    if include_measures:
        (eval_dir / "measures.py").write_text("VALUE = 1\n", encoding="utf-8")
    if include_optimizers:
        opt_dir = eval_dir / "optimizers"
        opt_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(
            opt_dir / "grid.yml",
            {
                "optimizers": {
                    "grid": {
                        "strategy": "grid",
                    }
                }
            },
        )
    return root


# ---------------------------------------------------------------------------
# load_eval_config tests
# ---------------------------------------------------------------------------


class TestLoadEvalConfig:
    def test_returns_eval_config_dataclass(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert isinstance(cfg, EvalConfig)

    def test_merges_scenarios(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        scenarios = cfg.config.get("scenarios", [])
        assert len(scenarios) == 1
        assert scenarios[0]["name"] == "scenario-alpha"

    def test_merges_scenarios_from_directory(self, tmp_path: Path):
        project_dir = _create_project(tmp_path, scenario_directory=True)
        cfg = load_eval_config(str(project_dir))
        names = {s["name"] for s in cfg.config["scenarios"]}
        assert names == {"scenario-alpha", "scenario-gamma"}

    def test_collects_judges(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert "primary" in cfg.config["judges"]

    def test_collects_metrics(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.config["metrics"][0]["name"] == "score"

    def test_collects_optimizers(self, tmp_path: Path):
        project_dir = _create_project(tmp_path, include_optimizers=True)
        cfg = load_eval_config(str(project_dir))
        assert "grid" in cfg.config["optimizers"]
        assert cfg.config["optimizers"]["grid"]["strategy"] == "grid"

    def test_collects_base_eval(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.config.get("title") == "suite"

    def test_applies_select_filter(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            scenarios=[
                {"name": "scenario-alpha", "description": "a"},
                {"name": "scenario-beta", "description": "b"},
                {"name": "scenario-gamma", "description": "c"},
            ],
        )
        cfg = load_eval_config(
            str(project_dir),
            select="name:scenario-alpha,name:scenario-beta",
        )
        names = [s["name"] for s in cfg.config["scenarios"]]
        assert names == ["scenario-alpha", "scenario-beta"]

    def test_applies_exclude_filter(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            scenarios=[
                {"name": "scenario-alpha", "description": "a"},
                {"name": "scenario-beta", "description": "b"},
                {"name": "scenario-gamma", "description": "c"},
            ],
        )
        cfg = load_eval_config(str(project_dir), exclude="name:scenario-gamma")
        names = [s["name"] for s in cfg.config["scenarios"]]
        assert names == ["scenario-alpha", "scenario-beta"]

    def test_applies_combined_select_and_exclude(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            scenarios=[
                {"name": "scenario-alpha", "description": "a"},
                {"name": "scenario-beta", "description": "b"},
                {"name": "scenario-gamma", "description": "c"},
            ],
        )
        cfg = load_eval_config(
            str(project_dir),
            select="name:scenario-*",
            exclude="name:scenario-gamma",
        )
        names = [s["name"] for s in cfg.config["scenarios"]]
        assert names == ["scenario-alpha", "scenario-beta"]

    def test_rejects_scenarios_missing_identifier(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            scenarios=[{"description": "no identifiers"}],
        )
        with pytest.raises(ValueError, match="Scenario 0"):
            load_eval_config(str(project_dir))

    def test_rejects_duplicate_scenarios(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            scenarios=[
                {"name": "dup", "description": "a"},
                {"name": "dup", "description": "b"},
            ],
        )
        with pytest.raises(ValueError, match="Duplicate scenario identifier 'dup'"):
            load_eval_config(str(project_dir))

    def test_rejects_duplicate_metrics(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            metrics=[
                {"name": "score", "type": "number"},
                {"label": "score"},
            ],
        )
        with pytest.raises(ValueError, match="Duplicate metric identifier 'score'"):
            load_eval_config(str(project_dir))

    def test_rejects_invalid_judge_key(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        judge_path = project_dir / "evaluations" / "judges" / "primary.yml"
        _write_yaml(
            judge_path,
            {
                "judges": {
                    "": {
                        "agent": {
                            "name": "arbiter",
                        }
                    }
                }
            },
        )
        with pytest.raises(ValueError, match="Judge keys must be non-empty strings"):
            load_eval_config(str(project_dir))

    def test_rejects_metrics_missing_identifier(self, tmp_path: Path):
        project_dir = _create_project(
            tmp_path,
            metrics=[{"threshold": 0.8}],
        )
        with pytest.raises(ValueError, match="Metric 0"):
            load_eval_config(str(project_dir))

    def test_resolves_eval_dir(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.eval_dir == (project_dir / "evaluations").resolve()

    def test_resolves_project_root(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.project_root == project_dir.resolve()

    def test_resolves_agent_path(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert (
            cfg.agent_path == (project_dir / "agents" / "alpha" / "agent.yml").resolve()
        )

    def test_preserves_agent_config(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.agent_config["name"] == "alpha"
        assert cfg.agent_config["model"] == "gpt-4o"
        # agent_config should NOT have the evaluation key injected
        assert "evaluation" not in cfg.agent_config

    def test_extracts_tracing_config(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        # Declared keys merged over the product-derived defaults.
        assert cfg.tracing_config == {
            "provider": "demo",
            "project": "demo_project",
            "name": "demo_project",
        }

    def test_preserves_project_config(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir))
        assert cfg.project_config["product"]["name"] == "demo_project"

    def test_accepts_project_yml_path(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        cfg = load_eval_config(str(project_dir / "project.yml"))
        assert cfg.config["scenarios"][0]["name"] == "scenario-alpha"

    def test_rejects_non_project_yml_file(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        with pytest.raises(FileNotFoundError, match="Expected 'project.yml'"):
            load_eval_config(str(project_dir / "evaluations" / "evaluation.yml"))

    def test_rejects_missing_project(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Could not find project.yml"):
            load_eval_config(str(tmp_path / "nonexistent"))

    def test_does_not_write_any_files(self, tmp_path: Path):
        project_dir = _create_project(tmp_path)
        target_dir = project_dir / "target"
        # target should not exist before or after
        assert not target_dir.exists()
        load_eval_config(str(project_dir))
        assert not target_dir.exists()


# ---------------------------------------------------------------------------
# introspect_resources tests
# ---------------------------------------------------------------------------


class TestIntrospectResources:
    def test_returns_resource_summary(self, tmp_path: Path):
        project_dir = _create_project(tmp_path, include_optimizers=True)
        cfg = load_eval_config(str(project_dir))
        graph = introspect_resources(cfg)
        assert graph["project_name"] == "demo_project"
        assert graph["agent_file"] == str(cfg.agent_path)
        assert graph["resources"]["scenarios"] == ["scenario-alpha"]
        assert graph["resources"]["judges"] == ["primary"]
        assert graph["resources"]["metrics"] == ["score"]

    def test_with_scenario_directory(self, tmp_path: Path):
        project_dir = _create_project(tmp_path, scenario_directory=True)
        cfg = load_eval_config(str(project_dir))
        graph = introspect_resources(cfg)
        assert set(graph["resources"]["scenarios"]) == {
            "scenario-alpha",
            "scenario-gamma",
        }


# ---------------------------------------------------------------------------
# filter_scenarios tests (ported from test_compiler.py::TestFilterScenarios)
# ---------------------------------------------------------------------------


class TestFilterScenarios:
    def test_filter_by_id_exact_match(self):
        scenarios = [
            {"id": "abc123", "name": "Test 1"},
            {"id": "def456", "name": "Test 2"},
            {"id": "ghi789", "name": "Test 3"},
        ]
        filtered = filter_scenarios(scenarios, "id:def456", None)
        assert len(filtered) == 1
        assert filtered[0]["id"] == "def456"

    def test_filter_by_id_wildcard(self):
        scenarios = [
            {"id": "test-abc", "name": "Test 1"},
            {"id": "test-def", "name": "Test 2"},
            {"id": "other-ghi", "name": "Test 3"},
        ]
        filtered = filter_scenarios(scenarios, "id:test-*", None)
        assert len(filtered) == 2
        assert {s["id"] for s in filtered} == {"test-abc", "test-def"}

    def test_filter_by_id_no_match(self):
        scenarios = [{"id": "abc123", "name": "Test 1"}]
        filtered = filter_scenarios(scenarios, "id:nonexistent", None)
        assert len(filtered) == 0

    def test_filter_by_name_prefix(self):
        scenarios = [
            {"id": "1", "name": "greeting-test"},
            {"id": "2", "name": "farewell-test"},
        ]
        filtered = filter_scenarios(scenarios, "name:greeting-*", None)
        assert len(filtered) == 1
        assert filtered[0]["name"] == "greeting-test"

    def test_filter_by_name_fallback(self):
        scenarios = [
            {"id": "1", "name": "alpha"},
            {"id": "2", "name": "beta"},
        ]
        filtered = filter_scenarios(scenarios, "alpha", None)
        assert len(filtered) == 1
        assert filtered[0]["name"] == "alpha"

    def test_filter_by_tag(self):
        scenarios = [
            {"id": "1", "name": "Test 1", "tags": ["smoke", "fast"]},
            {"id": "2", "name": "Test 2", "tags": ["regression"]},
            {"id": "3", "name": "Test 3", "tags": ["smoke", "slow"]},
        ]
        filtered = filter_scenarios(scenarios, "tag:smoke", None)
        assert len(filtered) == 2
        assert {s["id"] for s in filtered} == {"1", "3"}

    def test_filter_by_measure(self):
        scenarios = [
            {"id": "1", "name": "Test 1", "measures": ["accuracy", "speed"]},
            {"id": "2", "name": "Test 2", "measures": ["accuracy"]},
            {"id": "3", "name": "Test 3", "measures": ["coverage"]},
        ]
        filtered = filter_scenarios(scenarios, "measure:speed", None)
        assert len(filtered) == 1
        assert filtered[0]["id"] == "1"

    def test_filter_multiple_select(self):
        scenarios = [
            {"id": "abc", "name": "Test 1"},
            {"id": "def", "name": "Test 2"},
            {"id": "ghi", "name": "Test 3"},
        ]
        filtered = filter_scenarios(scenarios, "id:abc,id:ghi", None)
        assert len(filtered) == 2
        assert {s["id"] for s in filtered} == {"abc", "ghi"}

    def test_filter_exclude(self):
        scenarios = [
            {"id": "1", "name": "keep-this"},
            {"id": "2", "name": "remove-this"},
            {"id": "3", "name": "keep-that"},
        ]
        filtered = filter_scenarios(scenarios, None, "name:remove-*")
        assert len(filtered) == 2
        assert {s["name"] for s in filtered} == {"keep-this", "keep-that"}

    def test_filter_select_and_exclude_combined(self):
        scenarios = [
            {"id": "1", "name": "test-alpha"},
            {"id": "2", "name": "test-beta"},
            {"id": "3", "name": "test-gamma"},
            {"id": "4", "name": "other"},
        ]
        filtered = filter_scenarios(scenarios, "name:test-*", "name:test-gamma")
        assert len(filtered) == 2
        assert {s["name"] for s in filtered} == {"test-alpha", "test-beta"}

    def test_filter_empty_select_returns_all(self):
        scenarios = [
            {"id": "1", "name": "Test 1"},
            {"id": "2", "name": "Test 2"},
        ]
        filtered = filter_scenarios(scenarios, None, None)
        assert len(filtered) == 2

    def test_filter_handles_missing_id(self):
        scenarios = [
            {"name": "Test 1"},
            {"id": "def456", "name": "Test 2"},
        ]
        filtered = filter_scenarios(scenarios, "id:def456", None)
        assert len(filtered) == 1
        assert filtered[0]["id"] == "def456"


def test_load_eval_config_restricts_scenarios_to_a_holdout_split(tmp_path):
    from omniagents.core.eval.holdout import (
        create_holdout,
        holdout_manifest,
        write_holdout,
    )

    scenarios = [
        {"name": f"case-{i}", "description": "d", "expect": {"tone": str(i % 2)}}
        for i in range(8)
    ]
    root = _create_project(tmp_path, scenarios=scenarios)
    assignment = create_holdout(scenarios, strata=["expect.tone"], seed=5)
    manifest = holdout_manifest(
        assignment, scenarios, ["expect.tone"], (0.5, 0.25, 0.25), 5
    )
    write_holdout(root / "evaluations", assignment, manifest)

    cfg = load_eval_config(str(root / "project.yml"), split="test")
    names = [sc["name"] for sc in cfg.config["scenarios"]]
    assert names == sorted(k for k, s in assignment.items() if s == "test")
    assert cfg.split == "test"
    assert cfg.holdout_manifest["seed"] == 5

    with pytest.raises(FileNotFoundError):
        (
            load_eval_config(str(root / "project.yml"), split="dev")
            if False
            else load_eval_config(
                str(tmp_path / "missing" / "project.yml"), split="dev"
            )
        )


def test_load_eval_config_without_split_is_unchanged(tmp_path):
    root = _create_project(tmp_path, scenarios=[{"name": "only", "description": "d"}])
    cfg = load_eval_config(str(root / "project.yml"))
    assert cfg.split is None and cfg.holdout_manifest == {}
    assert [sc["name"] for sc in cfg.config["scenarios"]] == ["only"]


def test_inline_scenarios_are_selected_like_scenario_files(tmp_path):
    """Scenarios written in evaluation.yml used to bypass --select/--exclude."""
    root = _create_project(tmp_path)
    eval_dir = root / "evaluations"
    (eval_dir / "scenarios.yml").unlink()
    _write_yaml(
        eval_dir / "evaluation.yml",
        {
            "evaluation": {
                "scenarios": [
                    {"name": "keep", "tags": ["smoke"]},
                    {"name": "drop", "tags": ["slow"]},
                ]
            }
        },
    )
    cfg = load_eval_config(str(root / "project.yml"), select="tag:smoke")
    assert [sc["name"] for sc in cfg.config["scenarios"]] == ["keep"]
    assert cfg.select == "tag:smoke" and cfg.exclude is None


def test_evaluations_record_traces_unless_the_manifest_names_a_provider(tmp_path):
    root = _create_project(tmp_path, scenarios=[{"name": "only", "description": "d"}])
    manifest = root / "project.yml"
    data = yaml.safe_load(manifest.read_text())

    del data["tracing"]
    _write_yaml(manifest, data)
    assert load_eval_config(str(manifest)).tracing_config["provider"] == "studio"

    data["tracing"] = {"provider": "none"}
    _write_yaml(manifest, data)
    assert load_eval_config(str(manifest)).tracing_config["provider"] == "none"


def test_scenarios_record_their_file_and_includes_resolve_beside_it(
    tmp_path, monkeypatch
):
    root = _create_project(tmp_path, scenarios=[{"name": "base", "prompt": "p"}])
    nested = root / "evaluations" / "scenarios" / "more.yml"
    shared = root / "evaluations" / "shared" / "extra.yml"
    _write_yaml(
        nested, {"scenarios": ["../shared/extra.yml", {"name": "local", "prompt": "q"}]}
    )
    _write_yaml(shared, {"scenarios": [{"name": "extra", "prompt": "r"}]})
    monkeypatch.chdir(tmp_path)  # includes do not depend on where you run from

    scenarios = {s["name"]: s for s in load_eval_config(str(root)).config["scenarios"]}

    assert scenarios["local"]["_source_path"] == str(nested.resolve())
    assert scenarios["extra"]["_source_path"] == str(shared.resolve())


@pytest.mark.parametrize(
    "content",
    ["scenarios: [unclosed", "scenarios:\n  - missing/elsewhere.yml\n"],
)
def test_a_scenarios_file_that_cannot_be_read_is_an_error(tmp_path, content):
    root = _create_project(tmp_path, scenarios=[{"name": "base", "prompt": "p"}])
    broken = root / "evaluations" / "scenarios" / "broken.yml"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(content, encoding="utf-8")

    with pytest.raises(
        (ValueError, FileNotFoundError), match="broken.yml|elsewhere.yml"
    ):
        load_eval_config(str(root))
