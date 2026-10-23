"""Editing an agent's files changes only what was edited, and never writes
an agent that would not load with everything it names."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omniagents.core.project.agent_files import (
    AgentEditError,
    create_agent,
    delete_agent,
    read_agent,
    update_agent,
    write_agent_yaml,
)

AGENT_YML = """\
name: main
label: Main
# The model every run uses.
model: gpt-5.2
instructions_file: instructions.md

capabilities:
    - filesystem   # reading the workspace

model_settings:
    # Low for repeatable answers.
    temperature: 0.2
    parallel_tool_calls: true
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project.yml").write_text(
        textwrap.dedent("""\
            name: demo
            # Which agent runs by default.
            agents:
              entrypoint: main
            """),
        encoding="utf-8",
    )
    main = tmp_path / "agents" / "main"
    main.mkdir(parents=True)
    (main / "agent.yml").write_text(AGENT_YML, encoding="utf-8")
    (main / "instructions.md").write_text("Be helpful.\n", encoding="utf-8")
    helper = tmp_path / "agents" / "helper"
    helper.mkdir()
    (helper / "agent.yml").write_text(
        "name: helper\ninstructions: |\n  Help.\n", encoding="utf-8"
    )
    return tmp_path / "project.yml"


def test_an_edit_changes_only_the_edited_settings(tmp_path):
    manifest = _project(tmp_path)

    saved = update_agent(
        manifest,
        "main",
        changes={"model": "gpt-5.5", "model_settings": {"temperature": 0.7}},
    )

    expected = AGENT_YML.replace("model: gpt-5.2", "model: gpt-5.5").replace(
        "    temperature: 0.2\n    parallel_tool_calls: true\n",
        "    temperature: 0.7\n",
    )
    assert saved.yaml_text == expected
    assert (tmp_path / "agents/main/agent.yml").read_text() == expected


def test_instructions_are_saved_where_they_live(tmp_path):
    manifest = _project(tmp_path)

    update_agent(manifest, "main", instructions="Be brief.\n")
    update_agent(manifest, "helper", instructions="Help.\nThen stop.\n")

    assert (tmp_path / "agents/main/instructions.md").read_text() == "Be brief.\n"
    assert "instructions:" not in (tmp_path / "agents/main/agent.yml").read_text()
    helper = read_agent(manifest, "helper")
    assert helper.instructions_path is None
    assert helper.instructions == "Help.\nThen stop.\n"
    assert "instructions: |" in helper.yaml_text


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"capabilities": ["filesystem", "no_such_pack"]}, "no_such_pack"),
        ({"tools": ["no_such_tool"]}, "no_such_tool"),
        ({"input_guardrails": ["no_such_guardrail"]}, "no_such_guardrail"),
        ({"handoffs": [{"agent": "nobody"}]}, "nobody"),
    ],
)
def test_an_edit_naming_something_the_project_lacks_is_refused(
    tmp_path, changes, problem
):
    manifest = _project(tmp_path)

    with pytest.raises(AgentEditError) as refused:
        update_agent(manifest, "main", changes=changes)

    assert any(problem in message for message in refused.value.problems)
    assert (tmp_path / "agents/main/agent.yml").read_text() == AGENT_YML


def test_a_problem_the_file_already_had_does_not_block_other_edits(tmp_path):
    manifest = _project(tmp_path)
    agent = tmp_path / "agents/main/agent.yml"
    agent.write_text(AGENT_YML + "tools:\n    - long_gone\n", encoding="utf-8")

    update_agent(manifest, "main", changes={"label": "Renamed"})

    assert "label: Renamed" in agent.read_text()
    assert "long_gone" in agent.read_text()


def test_invalid_files_and_templates_are_refused(tmp_path):
    manifest = _project(tmp_path)

    for text in ("name: [unclosed", "- a list", AGENT_YML.replace("main", "other")):
        with pytest.raises(AgentEditError):
            write_agent_yaml(manifest, "main", text)
    with pytest.raises(AgentEditError, match="template"):
        update_agent(manifest, "main", instructions="{% if %}")
    with pytest.raises(AgentEditError, match="name"):
        update_agent(manifest, "main", changes={"name": "other"})

    assert (tmp_path / "agents/main/agent.yml").read_text() == AGENT_YML
    assert (tmp_path / "agents/main/instructions.md").read_text() == "Be helpful.\n"


def test_the_whole_file_can_be_replaced(tmp_path):
    manifest = _project(tmp_path)
    text = AGENT_YML.replace("label: Main", "label: Main  # shown in pickers")

    saved = write_agent_yaml(manifest, "main", text)

    assert saved.yaml_text == text


def test_agents_are_created_and_deleted(tmp_path):
    manifest = _project(tmp_path)

    created = create_agent(
        manifest, "analyst", model="gpt-5.5", label="Analyst", description="Reads data"
    )
    assert "label: Analyst" in created.yaml_text
    assert "model: gpt-5.5" in created.yaml_text
    assert created.instructions_path == tmp_path / "agents/analyst/instructions.md"
    with pytest.raises(AgentEditError, match="already exists"):
        create_agent(manifest, "analyst", model="gpt-5.5")
    with pytest.raises(AgentEditError, match="valid agent key"):
        create_agent(manifest, "Bad Key", model="gpt-5.5")

    assert delete_agent(manifest, "main") == "analyst"
    assert not (tmp_path / "agents/main").exists()
    project_yml = (tmp_path / "project.yml").read_text()
    assert "# Which agent runs by default." in project_yml
    assert "entrypoint: analyst" in project_yml
    delete_agent(manifest, "helper")
    with pytest.raises(AgentEditError, match="only agent"):
        delete_agent(manifest, "analyst")
