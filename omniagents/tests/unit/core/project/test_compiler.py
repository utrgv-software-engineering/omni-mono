from __future__ import annotations

from pathlib import Path

import json
import pytest
import yaml

from omniagents.core.project.compiler import compile_project


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
    agent_tools: bool = False,
    project_guardrails: bool = False,
    server_functions: bool = False,
    include_measures: bool = False,
    include_optimizers: bool = False,
    scenario_directory: bool = False,
) -> Path:
    root = tmp_path / project_name
    agents_dir = root / "agents" / "alpha"
    tools_dir = root / "tools"
    eval_dir = root / "evaluations"
    target_dir = root / "target"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tools_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        root / "project.yml",
        {
            "name": project_name,
            "paths": {
                "agents": "agents",
                "evaluations": "evaluations",
                "target": "target",
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
    if agent_tools:
        agent_tools_dir = agents_dir / "tools"
        agent_tools_dir.mkdir(parents=True, exist_ok=True)
        (agent_tools_dir / "__init__.py").write_text("", encoding="utf-8")
        (agent_tools_dir / "local_tool.py").write_text(
            "def local(value):\n    return value\n", encoding="utf-8"
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
    if project_guardrails:
        guardrails_dir = root / "guardrails"
        guardrails_dir.mkdir(parents=True, exist_ok=True)
        (guardrails_dir / "__init__.py").write_text("", encoding="utf-8")
        (guardrails_dir / "input_guard.py").write_text(
            "from agents.guardrail import InputGuardrail\ninput_guard = InputGuardrail()\n",
            encoding="utf-8",
        )
    if server_functions:
        server_dir = root / "server_functions"
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "__init__.py").write_text("", encoding="utf-8")
        (server_dir / "echo.py").write_text(
            "from omniagents.core.server_functions.discovery import server_function\n"
            "@server_function\n"
            "def ping(value: str) -> str:\n"
            "    return value\n",
            encoding="utf-8",
        )
    return root


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_compile_project_generates_compiled_artifacts(tmp_path: Path):
    project_dir = _create_project(tmp_path)
    compiled_path, graph = compile_project(str(project_dir))
    compiled_data = _read_yaml(compiled_path)
    assert compiled_path.parent == project_dir / "target"
    assert compiled_data["evaluation"]["scenarios"][0]["name"] == "scenario-alpha"
    assert graph["project_name"] == "demo_project"
    assert graph["resources"]["scenarios"] == ["scenario-alpha"]
    assert graph["resources"]["metrics"] == ["score"]
    assert graph["resources"]["judges"] == ["primary"]
    graph_path = compiled_path.parent / "graph.json"
    assert graph_path.is_file()


def test_compile_project_partial_rebuild_preserves_target(tmp_path: Path):
    project_dir = _create_project(tmp_path)
    target_dir = project_dir / "target"
    sentinel = target_dir / "preserve.txt"
    sentinel.write_text("keep", encoding="utf-8")
    compile_project(str(project_dir))
    scenarios_path = project_dir / "evaluations" / "scenarios.yml"
    payload = _read_yaml(scenarios_path)
    payload["scenarios"].append({"name": "scenario-beta", "description": "world"})
    _write_yaml(scenarios_path, payload)
    compiled_path, graph = compile_project(str(project_dir))
    compiled_data = _read_yaml(compiled_path)
    scenarios = [item["name"] for item in compiled_data["evaluation"]["scenarios"]]
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert scenarios == ["scenario-alpha", "scenario-beta"]
    assert graph["resources"]["scenarios"] == ["scenario-alpha", "scenario-beta"]


def test_compile_project_copies_when_symlinks_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project_dir = _create_project(tmp_path)

    def _broken_symlink(self: Path, target: Path, target_is_directory: bool = False):
        raise OSError("symlinks disabled")

    monkeypatch.setattr(Path, "symlink_to", _broken_symlink, raising=False)
    compile_project(str(project_dir))
    target_dir = project_dir / "target"
    instructions_copy = target_dir / "instructions.md"
    tools_copy = target_dir / "tools"
    assert instructions_copy.is_file()
    assert not instructions_copy.is_symlink()
    assert instructions_copy.read_text(encoding="utf-8") == (
        project_dir / "agents" / "alpha" / "instructions.md"
    ).read_text(encoding="utf-8")
    assert tools_copy.is_dir()
    assert not tools_copy.is_symlink()
    assert (tools_copy / "echo.py").is_file()


def test_compile_project_rejects_scenarios_missing_identifier(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        scenarios=[{"description": "no identifiers"}],
    )
    with pytest.raises(ValueError) as excinfo:
        compile_project(str(project_dir))
    assert "Scenario 0" in str(excinfo.value)


def test_compile_project_rejects_metrics_missing_identifier(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        metrics=[{"threshold": 0.8}],
    )
    with pytest.raises(ValueError) as excinfo:
        compile_project(str(project_dir))
    assert "Metric 0" in str(excinfo.value)


def test_compile_project_filters_with_select_and_exclude(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        scenarios=[
            {"name": "scenario-alpha", "description": "a"},
            {"name": "scenario-beta", "description": "b"},
            {"name": "scenario-gamma", "description": "c"},
        ],
    )
    compiled_path, graph = compile_project(
        str(project_dir),
        select="name:scenario-*",
        exclude="name:scenario-gamma",
    )
    compiled_data = _read_yaml(compiled_path)
    names = [item["name"] for item in compiled_data["evaluation"]["scenarios"]]
    assert names == ["scenario-alpha", "scenario-beta"]
    assert graph["resources"]["scenarios"] == ["scenario-alpha", "scenario-beta"]


def test_compile_project_links_optional_resources(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        agent_tools=True,
        project_guardrails=True,
        server_functions=True,
        include_measures=True,
        include_optimizers=True,
        scenario_directory=True,
    )
    compiled_path, graph = compile_project(str(project_dir / "project.yml"))
    compiled_data = _read_yaml(compiled_path)
    target_dir = project_dir / "target"
    instructions_copy = target_dir / "instructions.md"
    tools_symlink = target_dir / "tools"
    project_tools = target_dir / "project_tools"
    guardrails_link = target_dir / "guardrails"
    server_functions_link = target_dir / "server_functions"
    measures_file = target_dir / "measures.py"
    agents_manifest = target_dir / "agents.json"
    assert instructions_copy.is_symlink()
    assert tools_symlink.is_symlink()
    assert (tools_symlink / "local_tool.py").is_file()
    assert project_tools.is_symlink()
    assert guardrails_link.is_symlink()
    assert server_functions_link.is_symlink()
    assert measures_file.is_symlink() or measures_file.is_file()
    assert agents_manifest.is_file()
    agents_data = json.loads(agents_manifest.read_text(encoding="utf-8"))
    assert agents_data["entrypoint"] == "alpha"
    assert agents_data["agents"][0]["key"] == "alpha"
    names = {item["name"] for item in compiled_data["evaluation"]["scenarios"]}
    assert names == {"scenario-alpha", "scenario-gamma"}
    assert "optimizers" in compiled_data["evaluation"]
    assert compiled_data["evaluation"]["optimizers"]["grid"]["strategy"] == "grid"
    assert graph["resources"]["metrics"] == ["score"]
    assert graph["resources"]["scenarios"] == ["scenario-alpha", "scenario-gamma"]


def test_compile_project_rejects_duplicate_scenarios(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        scenarios=[
            {"name": "dup", "description": "a"},
            {"name": "dup", "description": "b"},
        ],
    )
    with pytest.raises(ValueError) as excinfo:
        compile_project(str(project_dir))
    assert "Duplicate scenario identifier 'dup'" in str(excinfo.value)


def test_compile_project_rejects_duplicate_metrics(tmp_path: Path):
    project_dir = _create_project(
        tmp_path,
        metrics=[
            {"name": "score", "type": "number"},
            {"label": "score"},
        ],
    )
    with pytest.raises(ValueError) as excinfo:
        compile_project(str(project_dir))
    assert "Duplicate metric identifier 'score'" in str(excinfo.value)


def test_compile_project_rejects_invalid_judge_key(tmp_path: Path):
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
    with pytest.raises(ValueError) as excinfo:
        compile_project(str(project_dir))
    assert "Judge keys must be non-empty strings" in str(excinfo.value)
