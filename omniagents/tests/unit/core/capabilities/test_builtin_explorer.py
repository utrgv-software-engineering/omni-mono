"""Builtin agents: the framework ships stock sub-agent specs under
omniagents/builtin/agents/ that merge into every project's agents_map
(project definitions shadow by key), and the builtin `exploration`
capability pack exposes the builtin `explorer` as an `explore` tool."""

from __future__ import annotations

import textwrap
from pathlib import Path

import omniagents
from omniagents.core.project.runtime import (
    build_spec_from_project,
    load_project,
    resolve_agent,
)


BUILTIN_EXPLORER_YML = (
    Path(omniagents.__file__).parent / "builtin" / "agents" / "explorer" / "agent.yml"
).resolve()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _make_min_project(tmp_path: Path, project_yaml: str = "name: test\n") -> Path:
    """Smallest valid project layout: project.yml + agents/main/agent.yml."""
    _write(tmp_path / "project.yml", project_yaml)
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        """,
    )
    return tmp_path


def test_builtin_explorer_resolves_without_project_definition(tmp_path):
    """A project that defines no `explorer` agent still resolves the builtin
    one by name — from the packaged builtin/agents/ directory."""
    _make_min_project(tmp_path)

    proj = load_project(str(tmp_path))

    assert "explorer" in proj.agents_map
    key, yaml_path = resolve_agent(proj, "explorer")
    assert key == "explorer"
    assert yaml_path == BUILTIN_EXPLORER_YML
    # Its instructions file ships next to the spec.
    assert (yaml_path.parent / "instructions.md").is_file()


def test_builtin_agent_does_not_change_entrypoint_semantics(tmp_path):
    """Single-agent projects keep their auto-entrypoint even though the
    builtin explorer also lands in agents_map."""
    _make_min_project(tmp_path)

    proj = load_project(str(tmp_path))
    assert proj.entrypoint == "main"


def test_project_defined_explorer_shadows_builtin(tmp_path):
    """A project agents/explorer/agent.yml takes over the key entirely —
    the builtin spec is never consulted."""
    _make_min_project(tmp_path)
    _write(
        tmp_path / "agents" / "explorer" / "agent.yml",
        """
        name: explorer
        instructions: custom project explorer
        """,
    )

    proj = load_project(str(tmp_path))

    key, yaml_path = resolve_agent(proj, "explorer")
    assert key == "explorer"
    assert yaml_path == (tmp_path / "agents" / "explorer" / "agent.yml").resolve()
    assert yaml_path != BUILTIN_EXPLORER_YML


def test_exclude_glob_opts_out_of_builtin_agent(tmp_path):
    """agents.exclude globs apply to builtin agents too, so a project can
    remove the stock explorer from its agent namespace."""
    _make_min_project(
        tmp_path,
        """
        name: test
        agents:
          exclude: [explorer]
        """,
    )

    proj = load_project(str(tmp_path))
    assert "explorer" not in proj.agents_map
    # Without the agent, the builtin exploration pack cannot resolve its
    # agent_tools entry and is skipped rather than failing the project.
    assert "exploration" not in proj.capability_registry


def test_exploration_pack_wires_explore_tool(tmp_path):
    """The builtin `exploration` capability exposes the builtin explorer as
    an `explore` agent tool."""
    _make_min_project(tmp_path)

    proj = load_project(str(tmp_path))

    cap = proj.capability_registry["exploration"]
    assert cap.tools() == []
    configs = cap.agent_tool_configs
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.tool_name == "explore"
    assert Path(cfg.yaml_path).resolve() == BUILTIN_EXPLORER_YML
    assert cfg.needs_approval is False
    assert cfg.max_turns == 50
    assert "explore" in (cap.instructions_text or "")


def test_exploration_pack_prefers_shadowing_explorer(tmp_path):
    """When the project shadows `explorer`, the builtin exploration pack's
    `explore` tool points at the project's definition."""
    _make_min_project(tmp_path)
    _write(
        tmp_path / "agents" / "explorer" / "agent.yml",
        """
        name: explorer
        instructions: custom project explorer
        """,
    )

    proj = load_project(str(tmp_path))

    cap = proj.capability_registry["exploration"]
    cfg = cap.agent_tool_configs[0]
    assert Path(cfg.yaml_path).resolve() == (
        tmp_path / "agents" / "explorer" / "agent.yml"
    ).resolve()


def test_agent_using_exploration_capability_builds_spec(tmp_path):
    """An agent listing the exploration capability resolves it and inherits
    the pack's agent_tool config on its spec."""
    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        capabilities: [exploration]
        """,
    )

    proj = load_project(str(tmp_path))
    spec = build_spec_from_project(proj)

    cap_names = [c.capability_name for c in spec.resolved_capabilities]
    assert cap_names == ["exploration"]
    at_tools = [c.tool_name for c in spec.agent_tool_configs]
    assert "explore" in at_tools


def test_builtin_explorer_has_no_model_resolver():
    """The builtin explorer must not depend on a product model resolver —
    it inherits the orchestrator/session default model instead."""
    import yaml

    data = yaml.safe_load(BUILTIN_EXPLORER_YML.read_text(encoding="utf-8"))
    assert "model_config_resolver" not in data
    assert data.get("model") in (None, "")
    # Read-only surface only.
    assert data["capabilities"] == [
        "filesystem_readonly",
        "shell_readonly",
        "notebook_readonly",
    ]
