"""Skills that belong to an agent definition: agents/<name>/skills/, a base's, the project's."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.run_context import RunContextWrapper

from omniagents.core.agents.builder import _bind_agent_skill_roots
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.core.skills import agent_skill_roots, skill_roots


def _skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\n\n# {name}\n"
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.yml").write_text("agents: {entrypoint: foreman}\n")
    for name, extra in (("foreman", ""), ("implementer", "extends: base\n")):
        d = tmp_path / "agents" / name
        d.mkdir(parents=True)
        (d / "agent.yml").write_text(
            f"name: {name}\n{extra}instructions: 'Hi. {{{{ available_skills_block }}}}'\n"
        )
    (tmp_path / "base").mkdir()
    (tmp_path / "base/agent.yml").write_text("name: base\ninstructions: '{{ role }}'\n")
    _skill(tmp_path / "skills", "github")  # every agent
    _skill(tmp_path / "base/skills", "coding")  # agents built on the base
    _skill(
        tmp_path / "agents/implementer/skills", "testing-standards"
    )  # the implementer only
    workspace = tmp_path / "ws"
    _skill(workspace / ".omniagents/skills", "repo-local")
    return tmp_path


def _roots(project, key):
    return build_spec_from_project(
        load_project(str(project / "project.yml")), key
    ).skill_roots


def test_each_agent_brings_its_own_base_and_project_skills(project):
    assert _roots(project, "implementer") == [
        str((project / "agents/implementer/skills").resolve()),
        str((project / "base/skills").resolve()),
        str((project / "skills").resolve()),
    ]
    assert _roots(project, "foreman") == [str((project / "skills").resolve())]


def test_agent_roots_come_after_the_workspace_and_only_while_set(project):
    ws = str(project / "ws")
    agent = [project / "agents/implementer/skills"]
    assert agent[0] not in skill_roots(ws)
    with agent_skill_roots(agent):
        roots = skill_roots(ws)
        assert roots[0] == project / "ws/.omniagents/skills"
        assert roots[1] == agent[0]
    assert agent[0] not in skill_roots(ws)


def _load_skill_tool():
    from omniagents.builtin.tools.skill_tool import load_skill

    return load_skill


def _call(tool, name, ws):
    from agents.tool_context import ToolContext

    args = json.dumps({"name": name})
    ctx = ToolContext(
        context={"workspace_root": ws},
        tool_name="load_skill",
        tool_call_id="c1",
        tool_arguments=args,
    )
    return str(asyncio.run(tool.on_invoke_tool(ctx, args)))


def test_load_skill_sees_the_agents_skills_only_when_bound(project):
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "implementer"
    )
    ws = str(project / "ws")
    tool = _load_skill_tool()
    _, [bound] = _bind_agent_skill_roots(spec, None, [tool])
    assert "# testing-standards" in str(_call(bound, "testing-standards", ws))
    assert "not found" in str(
        _call(tool, "testing-standards", ws)
    )  # unbound: workspace + global only
    assert "# repo-local" in str(
        _call(bound, "repo-local", ws)
    )  # the workspace's still work


def test_instructions_list_the_rendering_agents_skills(project):
    # The run context's block was built for the run's top agent (shared by a
    # sub-agent called as a tool); each agent's render rebuilds it with its own.
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "implementer"
    )

    async def render(wrapper, agent):
        return wrapper.context["available_skills_block"]

    instructions, _ = _bind_agent_skill_roots(spec, render, [])
    wrapper = RunContextWrapper(
        context={
            "workspace_root": str(project / "ws"),
            "available_skills_block": "foreman's list",
        }
    )
    block = asyncio.run(instructions(wrapper, None))
    assert all(
        n in block for n in ("testing-standards", "coding", "github", "repo-local")
    )
    assert (
        wrapper.context["available_skills_block"] == "foreman's list"
    )  # the shared context is untouched


def test_agents_without_skill_folders_are_unchanged(project):
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "foreman"
    )
    spec.skill_roots = []
    tool = _load_skill_tool()
    instructions = object()
    assert _bind_agent_skill_roots(spec, instructions, [tool]) == (instructions, [tool])


def test_sandbox_agents_skill_pack_searches_the_agents_skills(project):
    # A SandboxAgent collects its capability packs' tools at run time, so the
    # pack itself carries the bound load_skill.
    from omniagents.core.agents.builder import _capabilities_for_agent
    from omniagents.core.capabilities.yaml_capability import _YamlCapability

    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "implementer"
    )
    pack = _YamlCapability(
        capability_name="skills", resolved_tools=[_load_skill_tool()]
    )
    [bound] = _capabilities_for_agent(spec, [pack])
    [tool] = bound.tools()
    ws = str(project / "ws")
    assert "# testing-standards" in _call(tool, "testing-standards", ws)
    assert "not found" in _call(pack.tools()[0], "testing-standards", ws)
