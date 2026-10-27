"""`extends:` on agent definitions, and `paths.context` context factories."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.project.runtime import build_spec_from_project, load_project


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    return path


def _render(spec, **context) -> str:
    from omniagents.core.utils.jinja_instructions import process_instructions

    fn = process_instructions(
        spec.get_agent_instructions(), search_paths=spec.instructions_search_paths
    )
    wrapper = type("W", (), {"context": context})()
    return asyncio.run(fn(wrapper, None))


@pytest.fixture
def project(tmp_path):
    """A project with a base definition in base/ and roles in agents/."""
    _write(
        tmp_path / "project.yml",
        """
        agents: {entrypoint: reviewer}
        paths:
          agents: [agents, base/agents]
          instruction_fragments: [base/fragments]
          context: [base]
    """,
    )
    _write(
        tmp_path / "base/agent.yml",
        """
        name: base
        model: gpt-base
        instructions_file: instructions.md
        capabilities: [filesystem, shell]
        context: build_ctx
        max_turns: 50
        model_settings: {parallel_tool_calls: true}
    """,
    )
    _write(
        tmp_path / "base/instructions.md",
        """
        {% include "identity.md" %}
        Date: {{ today }}

        {{ role }}

        End of base.
    """,
    )
    _write(tmp_path / "base/fragments/identity.md", "You are a factory agent.")
    _write(
        tmp_path / "base/context.py",
        """
        from omniagents import context_factory

        @context_factory
        def build_ctx(variables=None):
            return {"today": "2026-10-23", **(variables or {})}
    """,
    )
    _write(
        tmp_path / "agents/reviewer/agent.yml",
        """
        name: reviewer
        extends: base
        instructions_file: instructions.md
        model: gpt-review
        capabilities: [shell, web]
    """,
    )
    _write(
        tmp_path / "agents/reviewer/instructions.md",
        "# Reviewer\n\nReview adversarially.\n",
    )
    _write(
        tmp_path / "base/agents/worker/agent.yml",
        """
        name: worker
        instructions: "Worker on {{ today }}."
        context: build_ctx
    """,
    )
    return tmp_path


def test_role_keys_override_and_capabilities_add(project):
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "reviewer"
    )
    assert spec.model_name == "gpt-review"  # the role's own key wins
    assert spec.max_turns == 50  # inherited from the base
    names = [c.capability_name for c in spec.resolved_capabilities]
    assert names == ["filesystem", "shell", "web"]  # base first, no duplicates


def test_role_fills_the_base_role_slot(project):
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "reviewer"
    )
    text = _render(spec, today="2026-10-23")
    assert (
        text.index("You are a factory agent.")
        < text.index("# Reviewer")
        < text.index("End of base.")
    )
    assert "{{ role }}" not in text


def test_base_context_factory_is_found_for_the_role(project):
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "reviewer"
    )
    assert spec.build_context is not None
    assert spec.build_context({})["today"] == "2026-10-23"


def test_paths_context_serves_agents_that_do_not_extend(project):
    spec = build_spec_from_project(load_project(str(project / "project.yml")), "worker")
    assert spec.build_context is not None
    assert spec.build_context({})["today"] == "2026-10-23"


def test_loader_finds_the_project_without_project_defaults(project):
    # Sub-agent and evaluation loads call the loader without project settings.
    spec = load_agent_spec_from_yaml(str(project / "agents/reviewer/agent.yml"))
    assert spec.model_name == "gpt-review"
    assert spec.build_context is not None


def test_base_without_a_slot_gets_the_role_appended(project):
    _write(project / "base/instructions.md", "Base text.\n")
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "reviewer"
    )
    text = _render(spec)
    assert text.index("Base text.") < text.index("# Reviewer")


def test_a_base_can_extend_another_base(project):
    _write(
        project / "core/agent.yml",
        """
        name: core
        instructions: "Core says hi. {{ role }}"
        capabilities: [tasks]
        max_turns: 7
    """,
    )
    base = (
        (project / "base/agent.yml")
        .read_text()
        .replace("name: base\n", "name: base\nextends: core\n")
    )
    base = base.replace("max_turns: 50\n", "")
    (project / "base/agent.yml").write_text(base)
    spec = build_spec_from_project(
        load_project(str(project / "project.yml")), "reviewer"
    )
    assert [c.capability_name for c in spec.resolved_capabilities] == [
        "tasks",
        "filesystem",
        "shell",
        "web",
    ]
    assert spec.max_turns == 7
    text = _render(spec, today="x")
    assert (
        text.index("Core says hi.")
        < text.index("You are a factory agent.")
        < text.index("# Reviewer")
    )


def test_missing_base_is_a_clear_error(project):
    agent = project / "agents/reviewer/agent.yml"
    agent.write_text(agent.read_text().replace("extends: base", "extends: nowhere"))
    with pytest.raises(ValueError, match="relative to the project root"):
        load_agent_spec_from_yaml(str(agent))


def test_circular_extends_is_rejected(project):
    base = project / "base/agent.yml"
    base.write_text("extends: base\n" + base.read_text())
    with pytest.raises(ValueError, match="Circular 'extends'"):
        load_agent_spec_from_yaml(str(project / "agents/reviewer/agent.yml"))
