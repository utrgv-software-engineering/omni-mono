"""A worker with `inherit: spawner` is built from the agent that spawned it."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from omniagents.core.project.runtime import build_spec_from_project, load_project


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    return path


@pytest.fixture
def project(tmp_path):
    _write(
        tmp_path / "project.yml",
        """
        agents: {entrypoint: gym}
        paths:
          agents: [agents, base/agents]
        memory:
          url: http://memory.test/mcp
          stores: {agent: {access: write}}
    """,
    )
    _write(
        tmp_path / "base/agent.yml",
        """
        name: base
        model: gpt-base
        instructions: "Base. {{ role }}"
        capabilities: [chat, filesystem, shell, workers]
        use_safe_agent: true
        safe_agent_options: {safe_tool_names: [read_file]}
        mcp_servers:
          - name: github
            type: streamable_http
            params: {url: "http://github.test/mcp"}
    """,
    )
    _write(
        tmp_path / "agents/gym/agent.yml",
        """
        name: gym
        label: Gym
        extends: base
        instructions: "Run the gym."
        voice_backend: codex-live
        capabilities: [web]
        mcp_servers:
          - name: indiegym
            type: streamable_http
            params: {url: "http://indiegym.test/mcp"}
        agent_tools:
          - agent: events
            tool_name: events
            mode: conversation
          - agent: events
            tool_name: ask_events
    """,
    )
    (tmp_path / "agents/gym/skills/billing").mkdir(parents=True)
    _write(
        tmp_path / "agents/gym/skills/billing/SKILL.md",
        "---\nname: billing\ndescription: Billing.\n---\nBill.\n",
    )
    _write(
        tmp_path / "agents/events/agent.yml",
        """
        name: events
        extends: base
        instructions: "Run events."
        without_capabilities: [shell]
    """,
    )
    _write(
        tmp_path / "base/agents/worker/agent.yml",
        """
        name: worker
        label: Worker
        instructions: "You are a worker."
        inherit: spawner
        without_capabilities: [chat]
        max_depth: 2
        max_turns: 200
        model_settings: {reasoning: {effort: low}}
    """,
    )
    return load_project(str(tmp_path / "project.yml"))


def _caps(spec):
    return [c.capability_name for c in spec.resolved_capabilities]


def _instructions(spec) -> str:
    from omniagents.core.utils.jinja_instructions import process_instructions

    fn = process_instructions(
        spec.get_agent_instructions(), search_paths=spec.instructions_search_paths
    )
    wrapper = type("W", (), {"context": {}})()
    return asyncio.run(fn(wrapper, None))


def test_a_worker_has_its_spawners_tools_servers_memory_and_skills(project):
    spec = build_spec_from_project(project, "worker", spawner="gym", worker_depth=1)
    assert _caps(spec) == ["filesystem", "shell", "workers", "web"]
    assert [s.name for s in spec.mcp_servers] == ["indiegym"]
    assert spec.memory_config["agent"] == "gym"
    assert spec.use_safe_agent is True
    assert any(r.endswith("agents/gym/skills") for r in spec.skill_roots)
    # Its own definition: instructions and limits.
    assert spec.name == "worker"
    assert spec.max_turns == 200
    assert _instructions(spec) == "You are a worker."
    assert spec.voice_backend != "codex-live"


def test_a_worker_calls_agents_but_does_not_hand_off_conversations(project):
    spec = build_spec_from_project(project, "worker", spawner="gym", worker_depth=1)
    assert [(t.tool_name, t.mode) for t in spec.agent_tool_configs] == [
        ("ask_events", "call")
    ]


def test_spawning_stops_at_the_workers_max_depth(project):
    second = build_spec_from_project(project, "worker", spawner="gym", worker_depth=2)
    assert "workers" not in _caps(second)
    assert "web" in _caps(second)


def test_the_spawners_removed_capabilities_stay_removed(project):
    spec = build_spec_from_project(project, "worker", spawner="events", worker_depth=1)
    assert _caps(spec) == ["filesystem", "workers"]


def test_an_agent_can_remove_a_capability_its_base_gives_it(project):
    assert _caps(build_spec_from_project(project, "events")) == [
        "chat",
        "filesystem",
        "workers",
    ]


def test_without_a_spawner_a_worker_is_its_own_definition(project):
    spec = build_spec_from_project(project, "worker")
    assert _caps(spec) == []
    assert spec.mcp_servers == []
